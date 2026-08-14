"""Process planning nodes: precision choice, process route generation."""

from __future__ import annotations

import logging
from typing import Any, Optional

from langgraph.types import interrupt
from models.process import ProcessStage, ProcessOperation, MANDATORY_OPERATION_NAMES
from models.workflow import WorkflowState, traced
from rules import (
    FEATURE_NAME, FEATURE_SUPPORTS_SPLIT, HEAT_NAME, SURFACE_NAME,
    build_route,
)
from llm_client import chat_json, llm_available
from rag.workflow_integration import build_rag_context

logger = logging.getLogger(__name__)


class ProcessNodesMixin:
    """Mixin for precision choice and process route generation nodes."""

    @traced("precision_choice", ["geometry", "request"])
    def precision_choice(self, state: WorkflowState) -> dict[str, Any]:
        self.progress(state, 20, "precision_choice", "Checking high-precision features.")
        if state["request"]["global_requirements"]["heat_treatment"] == "none":
            return {"pending_choices": [], "user_choices": {}}

        auto_choices: dict[str, str] = {}
        pending = []
        for feature in state["geometry"]["features"]:
            if not feature["high_precision"] or feature["processing_timing"] != "undecided":
                continue
            feature_type = feature["feature_type"]
            if not FEATURE_SUPPORTS_SPLIT.get(feature_type, False):
                auto_choices[feature["feature_id"]] = "before_heat_treatment"
                continue
            pending.append({
                "feature_id": feature["feature_id"], "feature_type": feature_type,
                "feature_name": FEATURE_NAME[feature_type],
                "global_position_mm": feature["global_position_mm"],
                "tolerance_upper_mm": feature.get("tolerance_upper_mm"),
                "tolerance_lower_mm": feature.get("tolerance_lower_mm"),
                "roughness_ra": feature.get("roughness_ra"),
                "recommended": "before_and_after_heat_treatment",
                "options": [{"value": "before_and_after_heat_treatment", "label": "Rough before heat + finish after heat", "description": "Recommended for best precision and roughness."}],
            })

        if not pending:
            return {"pending_choices": [], "user_choices": auto_choices}

        self.store.update(state["job_id"], status="waiting_user_choice", progress=50, current_step="precision_choice",
                          message="High-precision features detected, please select processing timing.", pending_choices=pending)
        response = interrupt({"type": "precision_choices", "pending_choices": pending})
        user_choices = {item["feature_id"]: item["processing_timing"] for item in response.get("choices", [])}
        missing = [item["feature_id"] for item in pending if item["feature_id"] not in user_choices]
        if missing:
            raise ValueError("Missing feature choices: " + ", ".join(missing))
        user_choices.update(auto_choices)
        return {"pending_choices": [], "user_choices": user_choices}

    @traced("process_planning", ["request", "geometry", "user_choices", "heat_treatment_decision"])
    def process_planning(self, state: WorkflowState) -> dict[str, Any]:
        # In the repair loop, retry_count is never incremented; the actual loop counter
        # is repair_count. Use repair_count to trigger the Replan Hint so that replanning
        # carries the failure reasons from the previous verification.
        retry_count = state.get("retry_count", 0) or state.get("repair_count", 0)
        msg = f"Regenerating process route (attempt {retry_count})." if retry_count > 0 else "Generating process route."
        self.progress(state, 50, "process_planning", msg)

        request, geometry, choices = state["request"], state["geometry"], state.get("user_choices", {})
        verification = state.get("verification")
        route_request = {**request, "heat_treatment_plan": state.get("heat_treatment_decision", {})}
        base_route = build_route(route_request, geometry, choices)

        if llm_available():
            try:
                heat_decision = state.get("heat_treatment_decision", {})
                self.store.update(state["job_id"], current_step="process_planning",
                                  message="Retrieving reference process cases (RAG)...")
                rag_context = build_rag_context(
                    request, geometry, choices, heat_decision,
                    top_k=3, max_chars=3000,
                )
                self.store.update(state["job_id"], current_step="process_planning",
                                  message="AI optimizing process route (may take 10-20s)...")
                patched = self._llm_process_planning(
                    request, geometry, choices, verification, retry_count,
                    base_route, rag_context,
                )
                if patched:
                    return {"process_route": patched}
            except Exception:
                self.store.update(state["job_id"], message="LLM route correction failed, keeping rule-based route.")

        return {"process_route": base_route}

    def _llm_process_planning(
        self, request: dict[str, Any], geometry: dict[str, Any],
        choices: dict[str, str], verification: Optional[dict[str, Any]],
        retry_count: int, base_route: list[dict[str, Any]],
        rag_context: str = "",
    ) -> Optional[list[dict[str, Any]]]:
        segments, features = request["segments"], geometry.get("features", [])
        global_req = request["global_requirements"]

        segment_desc = "\n".join(f"  - {s['segment_id']}: {s['diameter_mm']}mm x {s['length_mm']}mm" for s in segments)
        feature_desc = "\n".join(
            f"  - {f['feature_id']}: {FEATURE_NAME.get(f['feature_type'], f['feature_type'])}, pos {f['global_position_mm']}mm"
            + (" [high-precision]" if f.get("high_precision") else "")
            for f in features
        ) or "  None"
        base_route_desc = "\n".join(
            f"  {op['operation_no']}. {op['name']} ({op['stage']})"
            + (f" [{op.get('process_category') or '-'}]" if op.get('process_category') else "")
            + (" [conditional]" if op.get("conditional") else "")
            for op in base_route
        )

        retry_context = ""
        if retry_count > 0 and verification:
            issues = verification.get("validation_issues", [])
            if issues:
                retry_context = "\n\n[Replan Hint] Previous verification issues:\n"
                for iss in issues:
                    retry_context += f"- [{iss.get('error_code', '')}] {iss.get('message', '')}\n"
                retry_context += "Please fix above issues via patches. Mandatory operations cannot be deleted or renamed.\n"
            else:
                failed_checks = [c["name"] for c in verification.get("checks", []) if not c["passed"]]
                retry_context = f"\n\n[Replan Hint] Previous verification failed: {', '.join(failed_checks)}. Please correct."

        messages = self.prompt_manager.render_messages("process_planning", {
            "material": request["material"], "blank_diameter_mm": request["blank_diameter_mm"],
            "total_length_mm": geometry["total_length_mm"], "segment_desc": segment_desc,
            "feature_desc": feature_desc,
            "heat_treatment": HEAT_NAME.get(global_req["heat_treatment"], global_req["heat_treatment"]),
            "surface_treatment": SURFACE_NAME.get(global_req["surface_treatment"], global_req["surface_treatment"]),
            "batch_quantity": global_req["batch_quantity"], "choices": choices or "None",
            "base_route_desc": base_route_desc, "retry_context": retry_context,
            "rag_context": rag_context,
        })

        result = chat_json(messages, temperature=0.2)
        if not isinstance(result, dict):
            return None
        patches = result.get("patches", [])
        if not patches or not isinstance(patches, list):
            return None
        return self._apply_route_patches(base_route, patches)

    @staticmethod
    def _apply_route_patches(base_route: list[dict[str, Any]], patches: list[dict[str, Any]]) -> Optional[list[dict[str, Any]]]:
        route = [dict(op) for op in base_route]
        route_by_no = {op["operation_no"]: op for op in route}
        insertions: list[tuple[int, dict[str, Any]]] = []

        for patch in patches:
            action, target_no, op_data = patch.get("action"), patch.get("target_operation_no"), patch.get("operation", {})
            if action == "remove":
                if target_no in route_by_no and route_by_no[target_no]["name"] not in MANDATORY_OPERATION_NAMES:
                    del route_by_no[target_no]
            elif action == "insert" and op_data:
                new_op = {"operation_no": 0, "name": op_data.get("name", ""), "stage": op_data.get("stage", "inspection"),
                          "description": op_data.get("description", ""), "process_category": op_data.get("process_category"),
                          "feature_id": op_data.get("feature_id"), "conditional": op_data.get("conditional", False)}
                try:
                    ProcessStage(new_op["stage"])
                except ValueError:
                    continue
                insertions.append((target_no or 0, new_op))
            elif action == "update" and target_no in route_by_no and op_data:
                if route_by_no[target_no]["name"] in MANDATORY_OPERATION_NAMES and "name" in op_data and op_data["name"] != route_by_no[target_no]["name"]:
                    continue
                for key in ("name", "stage", "description", "process_category", "feature_id", "conditional"):
                    if key in op_data:
                        route_by_no[target_no][key] = op_data[key]

        result = sorted(route_by_no.values(), key=lambda op: op["operation_no"])
        for after_no, new_op in insertions:
            insert_idx = len(result)
            for i, op in enumerate(result):
                if op["operation_no"] == after_no:
                    insert_idx = i + 1
                    break
            result.insert(insert_idx, new_op)

        for i, op in enumerate(result, start=1):
            op["operation_no"] = i

        try:
            for op in result:
                ProcessOperation(**op)
        except Exception as exc:
            logger.warning("LLM patch validation failed, falling back to rule route: %s", exc)
            return None
        return result
