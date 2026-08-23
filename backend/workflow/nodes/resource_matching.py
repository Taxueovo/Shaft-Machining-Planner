"""Resource selection nodes: machine/tool queries, per-operation resource matching, and LLM ranking."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from models.process import ResourceStatus
from models.workflow import ExecutionTrace, WorkflowState, traced
from llm_client import chat_json, llm_available
from rag.workflow_integration import build_rag_context

logger = logging.getLogger(__name__)


class SelectionNodesMixin:
    """Mixin for resource selection nodes."""

    @traced("resource_selection", ["process_route", "request", "geometry"])
    def resource_selection(self, state: WorkflowState) -> dict[str, Any]:
        self.progress(
            state, 75, "resource_selection", "Querying resources and matching per operation."
        )

        request, geometry = state["request"], state["geometry"]

        # -- Query machines --
        machine = self.machine_repo.search_turning(
            geometry["total_length_mm"], float(request["blank_diameter_mm"])
        )

        # -- Query tools --
        processes = {
            operation["process_category"]
            for operation in state["process_route"]
            if operation.get("process_category")
        }
        machine_processes = processes - {"Heat Treatment"}
        tool_processes = machine_processes
        required_module = max(
            [
                float(feature[key])
                for feature in geometry["features"]
                for key in ("gear_module", "spline_module", "worm_module")
                if feature.get(key) is not None
            ],
            default=None,
        )
        high_precision_required = any(
            bool(feature.get("high_precision")) for feature in geometry["features"]
        )
        required_weight_kg = request.get("estimated_workpiece_weight_kg")
        tool_checks = {
            p: self.tool_repo.search(request["material"], p) for p in sorted(tool_processes)
        }
        machine_checks = {
            p: self.machine_repo.search_process(
                p,
                geometry["total_length_mm"],
                float(request["blank_diameter_mm"]),
                required_weight_kg=required_weight_kg,
                required_module=required_module if p in {"Gear Hobbing", "Gear Grinding"} else None,
                high_precision_required=high_precision_required,
            )
            for p in sorted(machine_processes)
        }
        notes = []
        if any(f["feature_type"] == "knurl" for f in geometry["features"]):
            notes.append(
                "Knurl tool specs not covered in current tool material table, needs engineer confirmation."
            )

        # -- Merge resource data --
        # A machine "size/capacity gap" is downgraded to partial + a warning instead of a
        # hard failure: the route is still generated, and the verification layer marks it
        # conditional_pass, signaling that external capacity / engineer confirmation is needed.
        machine_ok = machine["conclusion"] == "satisfied"
        # "ISO Turning" may be absent when a route builder / LLM patch drops every turning
        # operation; treat that as not_covered instead of crashing the whole job.
        tool_turning = tool_checks.get(
            "ISO Turning", {"conclusion": ResourceStatus.not_covered.value}
        )
        tool_critical_ok = tool_turning["conclusion"] == "satisfied"
        if not machine_ok:
            notes.append(
                f"No local turning machine covers blank Ø{float(request['blank_diameter_mm']):g}mm x "
                f"{geometry['total_length_mm']:g}mm; route generated, external capacity / engineer confirmation required."
            )
        critical_ok = tool_critical_ok
        capability = {
            "critical_ok": critical_ok,
            "overall": "satisfied" if (critical_ok and machine_ok) else "not_satisfied",
            "machine": machine,
            "machine_checks": machine_checks,
            "tool_checks": tool_checks,
            "notes": notes,
        }

        # -- Match resources per operation --
        machine_candidates = machine.get("active_matches", [])
        operation_resources, partial, tool_calls = [], 0, []

        t0 = datetime.now(timezone.utc)
        for operation in state["process_route"]:
            process = operation.get("process_category")
            if process is None:
                status, recommendations, machine_recommendations, note = (
                    ResourceStatus.not_applicable.value,
                    [],
                    [],
                    "No tool/equipment verification needed.",
                )
            elif process == "Heat Treatment":
                status, recommendations, machine_recommendations, note = (
                    ResourceStatus.not_applicable.value,
                    [],
                    [],
                    "Heat-treatment resource matching is intentionally out of scope for this route.",
                )
            elif process in tool_checks:
                tool_result = tool_checks[process]
                machine_result = machine_checks[process]
                recommendations = tool_result["recommendations"]
                machine_recommendations = machine_result.get("active_matches", [])
                if machine_result["conclusion"] != ResourceStatus.satisfied.value:
                    status = ResourceStatus.not_satisfied.value
                else:
                    status = tool_result["conclusion"]
                note = f"Machine: {machine_result['message']} Tool: {tool_result['message']}"
            else:
                status, recommendations, machine_recommendations, note = (
                    ResourceStatus.not_covered.value,
                    [],
                    [],
                    "Current Excel does not cover this process.",
                )
            if status not in (ResourceStatus.satisfied.value, ResourceStatus.not_applicable.value):
                partial += 1
            operation_resources.append(
                {
                    "operation_no": operation["operation_no"],
                    "operation_name": operation["name"],
                    "process_category": process,
                    "verification_status": status,
                    "tool_recommendations": recommendations,
                    "machine_recommendations": machine_recommendations,
                    "note": note,
                    "llm_ranking": None,
                    "recommendation_provenance": {
                        "method": "deterministic_rule_filter",
                        "machine_data": "manufacturer_public_data",
                        "tool_data": "manufacturer_public_data",
                        "confidence": "screening_only"
                        if any(
                            item.get("unverified_constraints") for item in machine_recommendations
                        )
                        else "verified_against_published_limits",
                    },
                }
            )
        t1 = datetime.now(timezone.utc)
        ExecutionTrace.record_tool(
            tool_calls,
            "rule_resource_filter",
            {"processes": [op.get("process_category") for op in state["process_route"]]},
            f"Filter complete, {len(operation_resources)} operations, {partial} not covered",
            (t1 - t0).total_seconds() * 1000,
        )

        llm_analysis = None
        if llm_available() and operation_resources:
            try:
                self.store.update(
                    state["job_id"],
                    current_step="resource_selection",
                    message="Ranking machines/tools with AI...",
                )
                llm_analysis = self._llm_resource_ranking(
                    state, operation_resources, machine_candidates, tool_calls
                )
            except Exception as exc:
                logger.warning("LLM resource ranking failed, skipping: %s", exc)

        return {
            "capability": capability,
            "resource_selection": {
                "turning_machine_candidates": machine_candidates,
                "operation_resources": operation_resources,
                "partial_verification_count": partial,
                "scope_note": "Current tool table verifies cutting-tool grades only. Heat-treatment resource matching is intentionally skipped.",
                "llm_analysis": llm_analysis,
            },
            "_tool_calls": tool_calls,
        }

    def _llm_resource_ranking(
        self,
        state: WorkflowState,
        operation_resources: list[dict[str, Any]],
        machine_candidates: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        machine_desc = (
            "\n".join(
                f"  - {m['designation']} ({m['manufacturer']}), length {m['turning_length_mm']}mm, rod {m.get('max_turning_diameter_rod_mm', '-')}mm"
                for m in machine_candidates[:5]
            )
            or "  No candidates"
        )
        op_desc = "\n".join(
            f"  {r['operation_no']}. {r['operation_name']} [{r['process_category'] or '-'}] -> {r['verification_status']}"
            + (
                f" Recommended: {', '.join(t['cutting_tool_grade'] for t in r['tool_recommendations'][:3])}"
                if r["tool_recommendations"]
                else ""
            )
            for r in operation_resources
        )
        rag_context = build_rag_context(
            state["request"],
            state["geometry"],
            state.get("user_choices", {}),
            state.get("heat_treatment_decision", {}),
            top_k=3,
            max_chars=3000,
        )
        messages = self.prompt_manager.render_messages(
            "resource_ranking",
            {
                "machine_desc": machine_desc,
                "op_desc": op_desc,
                "material": state["request"]["material"],
                "batch_quantity": state["request"]["global_requirements"]["batch_quantity"],
                "rag_context": rag_context,
            },
        )
        t0 = datetime.now(timezone.utc)
        result = chat_json(messages, temperature=0.3)
        t1 = datetime.now(timezone.utc)
        ExecutionTrace.record_tool(
            tool_calls,
            "llm_resource_ranking",
            {"operation_count": len(operation_resources)},
            f"LLM evaluation complete, score {result.get('overall_score', '?')}",
            (t1 - t0).total_seconds() * 1000,
        )
        return {
            **result,
            "provenance": "model_generated_advisory",
            "confidence": "requires_engineer_review",
        }
