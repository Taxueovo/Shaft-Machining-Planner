"""Planning nodes: task planning, feature analysis."""

from __future__ import annotations

from typing import Any

from models.workflow import WorkflowState, traced
from rules import (
    FEATURE_NAME, HEAT_NAME, SURFACE_NAME,
    is_high_precision, is_feature_high_precision,
)


class PlanningNodesMixin:
    """Mixin for task planning and feature analysis nodes."""

    def progress(self, state: WorkflowState, value: int, step: str, message: str) -> None:
        self.store.update(state["job_id"], status="running", progress=value, current_step=step, message=message)

    @traced("task_planning", ["request"])
    def task_planning(self, state: WorkflowState) -> dict[str, Any]:
        self.progress(state, 5, "task_planning", "Analyzing task and creating execution plan.")
        request = state["request"]
        segments = request["segments"]
        features = request.get("features", [])
        global_req = request["global_requirements"]

        subtasks: list[dict[str, Any]] = [
            {"id": "ST01", "name": "Geometry modeling", "description": "Calculate segment coordinates and feature positions.", "agent": "feature_analysis", "priority": 1},
            {"id": "ST02", "name": "Heat treatment decision", "description": "Apply material and requirement knowledge to determine heat-treatment constraints.", "agent": "heat_treatment_planning", "priority": 2},
        ]

        has_high_precision = any(
            is_feature_high_precision(f)
            for f in features
        )
        needs_hitl = has_high_precision and global_req["heat_treatment"] != "none"
        if needs_hitl:
            subtasks.append({"id": "ST03", "name": "Precision choice", "description": "User selects processing timing for high-precision features.", "agent": "precision_choice", "priority": 3})

        planning_start = 4 if needs_hitl else 3
        subtasks.extend([
            {"id": f"ST{planning_start:02d}", "name": "Process route generation", "description": "Generate process route based on features and requirements.", "agent": "process_planning", "priority": planning_start},
            {"id": f"ST{planning_start + 1:02d}", "name": "Resource matching", "description": "Query machine/tool database and match per operation.", "agent": "resource_selection", "priority": planning_start + 1},
            {"id": f"ST{planning_start + 2:02d}", "name": "Verification", "description": "Verify route completeness and resource coverage.", "agent": "verification", "priority": planning_start + 2},
        ])

        material = request["material"]
        feature_types = [FEATURE_NAME.get(f["feature_type"], f["feature_type"]) for f in features]
        summary_parts = [f"Material {material}", f"{len(segments)}-segment stepped shaft"]
        if features:
            summary_parts.append(f"{len(features)} features ({', '.join(feature_types)})")
        if global_req["heat_treatment"] != "none":
            summary_parts.append(f"Heat Treatment: {HEAT_NAME[global_req['heat_treatment']]}")
        if global_req["surface_treatment"] != "none":
            summary_parts.append(f"Surface Treatment: {SURFACE_NAME[global_req['surface_treatment']]}")

        return {
            "plan": {
                "task_summary": ". ".join(summary_parts) + ".",
                "subtasks": subtasks, "total_subtasks": len(subtasks),
                "needs_human_interaction": needs_hitl,
                "available_tools": self.tool_registry.list_tools(),
            },
            "retry_count": 0,
        }

    @traced("feature_analysis", ["request"])
    def feature_analysis(self, state: WorkflowState) -> dict[str, Any]:
        self.progress(state, 15, "feature_analysis", "Calculating segment and feature coordinates.")
        request = state["request"]
        segments = []
        cursor = 0.0
        for source in request["segments"]:
            item = dict(source)
            item["global_start_mm"] = round(cursor, 3)
            cursor += float(item["length_mm"])
            item["global_end_mm"] = round(cursor, 3)
            item["high_precision"] = is_high_precision(
                item.get("diameter_upper_deviation_mm"),
                item.get("diameter_lower_deviation_mm"),
                item.get("roughness_ra"),
            )
            segments.append(item)

        total = round(cursor, 3)
        features = []
        warnings = []
        for source in request.get("features", []):
            item = dict(source)
            if item["positioning_mode"] == "segment_relative":
                index = int(item["segment_index"])
                if index > len(segments):
                    raise ValueError(f"{item['feature_id']} references non-existent segment {index}.")
                segment = segments[index - 1]
                offset = float(item["segment_offset_mm"])
                if offset > float(segment["length_mm"]):
                    raise ValueError(f"{item['feature_id']} offset exceeds segment {index} length.")
                position = float(segment["global_start_mm"]) + offset
                remaining = float(segment["global_end_mm"]) - position
                item["resolved_segment_id"] = segment["segment_id"]
            else:
                position = float(item["global_position_mm"])
                if position > total:
                    raise ValueError(f"{item['feature_id']} global position exceeds total part length.")
                segment = next(
                    (s for s in segments if s["global_start_mm"] <= position <= s["global_end_mm"]), None
                )
                item["resolved_segment_id"] = segment["segment_id"] if segment else None
                remaining = total - position

            length = item.get("feature_length_mm")
            if length is not None and float(length) > remaining + 1e-9:
                raise ValueError(f"{item['feature_id']} feature length exceeds valid range.")

            item["global_position_mm"] = round(position, 3)
            item["high_precision"] = is_feature_high_precision(item)
            if item["high_precision"] and request["global_requirements"]["heat_treatment"] == "none":
                warnings.append(f"{item['feature_id']} is high-precision but no heat treatment; system will arrange after OD finishing.")
            features.append(item)

        return {
            "geometry": {
                "total_length_mm": total,
                "max_finished_diameter_mm": max(float(s["diameter_mm"]) for s in segments),
                "blank_diameter_mm": request["blank_diameter_mm"],
                "segments": segments, "features": features, "warnings": warnings,
            }
        }

    @traced("heat_treatment_planning", ["request", "geometry"])
    def heat_treatment_planning(self, state: WorkflowState) -> dict[str, Any]:
        """Create a traceable heat-treatment decision before route generation."""
        self.progress(state, 20, "heat_treatment_planning", "Evaluating heat treatment requirements and constraints.")
        decision = self.heat_treatment_provider.recommend(state["request"], state["geometry"])
        return {"heat_treatment_decision": decision}
