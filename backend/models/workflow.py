"""Workflow data models: PlanningRequest, WorkflowState, ExecutionTrace."""

from __future__ import annotations

import functools
import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, model_validator

from models.input import ShaftSegment, FeatureInput, GlobalRequirements
from rules import get_material_properties, is_feature_high_precision

logger = logging.getLogger(__name__)


# ============================================================
# PlanningRequest
# ============================================================

class PlanningRequest(BaseModel):
    """Process planning request."""

    material: str = Field(min_length=1, max_length=100)
    blank_type: Literal["solid", "hollow"] = "solid"
    blank_diameter_mm: float = Field(gt=0)
    blank_inner_diameter_mm: Optional[float] = Field(default=None, gt=0)
    estimated_workpiece_weight_kg: Optional[float] = Field(default=None, gt=0)
    segments: list[ShaftSegment] = Field(min_length=1, max_length=50)
    features: list[FeatureInput] = Field(default_factory=list, max_length=100)
    global_requirements: GlobalRequirements = Field(default_factory=GlobalRequirements)
    # Additional geometry information (input-provided, preserved when the request round-trips)
    main_axis: Optional[list[float]] = Field(default=None, max_length=3)
    geometry_statistics: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_request(self) -> "PlanningRequest":
        segment_ids = [item.segment_id for item in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("Segment IDs must be unique.")
        feature_ids = [item.feature_id for item in self.features]
        if len(feature_ids) != len(set(feature_ids)):
            raise ValueError("Feature IDs must be unique.")
        maximum = max(item.diameter_mm for item in self.segments)
        if self.blank_diameter_mm < maximum:
            raise ValueError(f"Blank diameter is less than max finished diameter {maximum} mm.")
        if self.blank_type == "hollow":
            if self.blank_inner_diameter_mm is None:
                raise ValueError("Hollow blank requires inner diameter.")
            if self.blank_inner_diameter_mm >= self.blank_diameter_mm:
                raise ValueError("Inner diameter must be less than outer diameter.")

        material_props = get_material_properties(self.material)
        has_high_precision = any(
            is_feature_high_precision(f)
            for f in self.features
        )
        if has_high_precision and self.global_requirements.heat_treatment == "none":
            recommended_heat = material_props.get("recommended_heat_treatment", "quench_temper")
            self.global_requirements.heat_treatment = recommended_heat
            logger.info("High-precision feature detected, auto-setting heat treatment to %s.", recommended_heat)
        return self


# ============================================================
# Workflow State & Execution Trace
# ============================================================

MAX_REPLAN_RETRIES = 3


def _merge_traces(
    existing: list[dict[str, Any]], new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = {(e.get("node"), e.get("start_time")) for e in existing}
    merged = list(existing)
    for entry in new:
        key = (entry.get("node"), entry.get("start_time"))
        if key not in seen:
            merged.append(entry)
            seen.add(key)
    return merged


class WorkflowState(TypedDict, total=False):
    """LangGraph workflow state definition."""

    job_id: str
    request: dict[str, Any]
    plan: dict[str, Any]
    geometry: dict[str, Any]
    heat_treatment_decision: dict[str, Any]
    capability: dict[str, Any]
    machine_check: dict[str, Any]
    tool_check: dict[str, Any]
    pending_choices: list[dict[str, Any]]
    user_choices: dict[str, str]
    process_route: list[dict[str, Any]]
    resource_selection: dict[str, Any]
    verification: dict[str, Any]
    retry_count: int
    repair_count: int
    route_hashes: Annotated[list[str], operator.add]
    status: str
    execution_trace: Annotated[list[dict[str, Any]], _merge_traces]


class ExecutionTrace:
    """Execution trace utility class."""

    @staticmethod
    def start(node_name: str, state_keys: list[str]) -> dict[str, Any]:
        return {
            "node": node_name, "input_keys": state_keys,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None, "duration_ms": None, "status": "running",
            "tool_calls": [], "output_keys": [], "error": None,
        }

    @staticmethod
    def finish(
        entry: dict[str, Any], output_keys: list[str],
        tool_calls: list[dict[str, Any]] | None = None, error: str | None = None,
    ) -> dict[str, Any]:
        end = datetime.now(timezone.utc)
        start = datetime.fromisoformat(entry["start_time"])
        entry["end_time"] = end.isoformat()
        entry["duration_ms"] = round((end - start).total_seconds() * 1000)
        entry["output_keys"] = output_keys
        if tool_calls is not None:
            entry["tool_calls"] = tool_calls
        entry["status"] = "error" if error else "success"
        entry["error"] = error
        return entry

    @staticmethod
    def record_tool(
        tool_calls: list[dict[str, Any]], name: str, params: dict[str, Any],
        result_summary: str, duration_ms: float,
    ) -> None:
        tool_calls.append({
            "tool": name, "params": params,
            "result_summary": result_summary, "duration_ms": round(duration_ms),
        })


def traced(node_name: str, input_keys: list[str] | None = None):
    """Workflow node execution tracing decorator."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, state: WorkflowState) -> dict[str, Any]:
            keys = input_keys or list(state.keys())
            entry = ExecutionTrace.start(node_name, keys)
            try:
                result = func(self, state)
                extra_tool_calls = result.pop("_tool_calls", [])
                ExecutionTrace.finish(entry, list(result.keys()), tool_calls=extra_tool_calls)
                result["execution_trace"] = [entry]
                return result
            except Exception as exc:
                ExecutionTrace.finish(entry, [], error=str(exc))
                raise
        return wrapper
    return decorator
