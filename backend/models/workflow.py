# 定义规划请求、图状态及执行轨迹，明确并行节点输出的合并方式。
"""Workflow data models: PlanningRequest, WorkflowState, ExecutionTrace."""

from __future__ import annotations

import functools
import logging
import operator
import json
from uuid import uuid4
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, model_validator
from langgraph.errors import GraphInterrupt

from models.tasks import merge_task_results
from models.input import ShaftSegment, FeatureInput, GlobalRequirements
from rules.geometry import validate_manufacturing_geometry

logger = logging.getLogger(__name__)
_trace_active = ContextVar("trace_active", default=False)


# ============================================================
# PlanningRequest
# ============================================================


# 约束规划输入，确保毛坯、轴段、特征和全局要求相互一致。
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
        """模型级校验：段/特征 ID 唯一、毛坯与成品几何自洽，保留图纸热处理要求。"""
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

        validate_manufacturing_geometry(self.model_dump())
        return self


# ============================================================
# Workflow State & Execution Trace
# ============================================================

MAX_REPLAN_RETRIES = 3


def _merge_traces(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并执行轨迹：作为 LangGraph reducer，将各节点写入的 trace 追加进 workflow state。"""
    # 以 (节点名, 开始时间) 为唯一键去重，防止重试/重放时同一次执行被重复记录
    seen = {(e.get("node"), e.get("start_time")) for e in existing}
    merged = list(existing)
    for entry in new:
        key = (entry.get("node"), entry.get("start_time"))
        if key not in seen:
            merged.append(entry)
            seen.add(key)
    return merged


# 定义图节点共享状态；带合并器的字段可接收并行节点增量。
class WorkflowState(TypedDict, total=False):
    """LangGraph workflow state definition."""

    task_plan: dict[str, Any]
    worker_results: Annotated[dict[str, Any], merge_task_results]
    planner_calls: int
    scheduler_waves: int
    task_action: str
    ready_tasks: list[str]
    pending_engineering: list[dict[str, Any]]
    engineering_answers: dict[str, str]
    deferred_tasks: list[str]
    planner_events: list[dict[str, Any]]
    tasks_repair_count: int
    task_execution: dict[str, Any]
    job_id: str
    request: dict[str, Any]
    plan: dict[str, Any]
    geometry: dict[str, Any]
    heat_treatment_decision: dict[str, Any]
    capability: dict[str, Any]
    pending_choices: list[dict[str, Any]]
    user_choices: dict[str, str]
    process_route: list[dict[str, Any]]
    resource_selection: dict[str, Any]
    verification: dict[str, Any]
    retry_count: int
    repair_count: int
    # LangGraph reducer：route_hashes 用加合并累积历史路由哈希，execution_trace 用 _merge_traces 去重合并各节点轨迹
    route_hashes: Annotated[list[str], operator.add]
    machining_review: dict[str, Any]
    quality_review: dict[str, Any]
    heat_review: dict[str, Any]
    agent_collaboration: dict[str, Any]
    release_status: str
    status: str
    execution_trace: Annotated[list[dict[str, Any]], _merge_traces]


# 记录节点开始、结束、输入输出键和工具调用，供结果页面追溯。
class ExecutionTrace:
    """Execution trace utility class."""

    @staticmethod
    def start(node_name: str, state_keys: list[str], inputs=None) -> dict[str, Any]:
        """创建一条 running 状态的执行记录，并快照该节点将要读取的输入键清单。"""
        return {
            "trace_id": uuid4().hex,
            "node": node_name,
            "inputs": ExecutionTrace.snapshot(inputs),
            "outputs": None,
            "input_keys": state_keys,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "duration_ms": None,
            "status": "running",
            "tool_calls": [],
            "output_keys": [],
            "error": None,
        }

    @staticmethod
    def snapshot(value):
        """Detached JSON snapshot; omit trace history to prevent recursive growth."""

        def clean(item):
            if isinstance(item, BaseModel):
                return clean(item.model_dump(mode="json"))
            if isinstance(item, dict):
                return {
                    str(k): clean(v)
                    for k, v in item.items()
                    if k not in {"execution_trace", "_tool_calls"}
                }
            if isinstance(item, (list, tuple)):
                return [clean(v) for v in item]
            return item

        return json.loads(json.dumps(clean(value), ensure_ascii=False, default=str))

    @staticmethod
    def finish(
        entry: dict[str, Any],
        output_keys: list[str],
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
        outputs=None,
    ) -> dict[str, Any]:
        """结束一条执行记录：补齐耗时、输出键与工具调用，并按是否出错标注状态。"""
        end = datetime.now(timezone.utc)
        start = datetime.fromisoformat(entry["start_time"])
        entry["end_time"] = end.isoformat()
        entry["duration_ms"] = round((end - start).total_seconds() * 1000)
        entry["output_keys"] = output_keys
        entry["outputs"] = ExecutionTrace.snapshot(outputs)
        if tool_calls is not None:
            entry["tool_calls"] = ExecutionTrace.snapshot(tool_calls)
        entry["status"] = "error" if error else "success"
        entry["error"] = error
        return entry

    @staticmethod
    def record_tool(
        tool_calls: list[dict[str, Any]],
        name: str,
        params: dict[str, Any],
        result_summary: str,
        duration_ms: float,
    ) -> None:
        """向执行记录的工具调用列表追加一次 LLM 工具调用（含参数摘要与耗时）。"""
        tool_calls.append(
            {
                "tool": name,
                "params": params,
                "result_summary": result_summary,
                "duration_ms": round(duration_ms),
            }
        )


# 为工作流节点附加执行轨迹，同时保留原函数的调用元信息。
def traced(node_name: str, input_keys: list[str] | None = None):
    """Workflow node execution tracing decorator."""

    # 捕获节点名称与输入要求，生成实际包裹目标函数的装饰器。
    def decorator(func):
        # 调用原节点并合并执行轨迹；失败时记录错误后继续向上抛出。
        @functools.wraps(func)
        def wrapper(self, state: WorkflowState) -> dict[str, Any]:
            if _trace_active.get():
                return func(self, state)
            keys = input_keys or list(state.keys())
            entry = ExecutionTrace.start(node_name, keys, state)
            job_id = state.get("job_id") or state.get("snapshot", {}).get("job_id")
            store = getattr(self, "store", None)

            def persist():
                if store is not None and job_id and hasattr(store, "save_trace"):
                    store.save_trace(job_id, entry)

            persist()
            token = _trace_active.set(True)
            try:
                result = func(self, state)
                # 约定：节点可在返回 dict 中通过 _tool_calls 附带工具调用明细，先取出再写入 trace
                extra_tool_calls = result.pop("_tool_calls", [])
                for child in result.get("execution_trace", []):
                    extra_tool_calls.extend(child.get("tool_calls", []))
                ExecutionTrace.finish(
                    entry, list(result.keys()), tool_calls=extra_tool_calls, outputs=result
                )
                persist()
                result["execution_trace"] = [entry]
                return result
            except GraphInterrupt:
                ExecutionTrace.finish(entry, [])
                entry["status"] = "interrupted"
                persist()
                raise
            except Exception as exc:
                ExecutionTrace.finish(entry, [], error=str(exc))
                persist()
                raise
            except BaseException as exc:
                # Preserve cancellation/termination attempts before propagating them.
                ExecutionTrace.finish(entry, [])
                entry["status"] = "interrupted"
                entry["error"] = type(exc).__name__
                persist()
                raise
            finally:
                _trace_active.reset(token)

        return wrapper

    return decorator
