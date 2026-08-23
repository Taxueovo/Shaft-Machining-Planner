"""Verification and repair nodes: process route verification, topology check, auto repair."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional

from models.process import (
    ProcessStage,
    ProcessOperation,
    ValidationIssue,
    STAGE_DEPENDENCY_RULES,
    MANDATORY_OPERATION_NAMES,
)
from models.workflow import WorkflowState, traced, MAX_REPLAN_RETRIES
from rules import (
    FEATURE_NAME,
    FEATURE_REQUIRED_PROCESS,
    HEAT_NAME,
    build_route,
    requires_grinding,
)
from agents import Guardrails
from llm_client import chat_json, llm_available
from rag.workflow_integration import build_rag_context

logger = logging.getLogger(__name__)


class VerificationNodesMixin:
    """Mixin for verification and repair nodes."""

    def _route_after_verification(self, state: WorkflowState) -> str:
        verification = state.get("verification", {})
        conclusion = verification.get("conclusion", "pass")
        if conclusion in ("pass", "conditional_pass"):
            return "pass"
        # Duplicate-route / retry-exhausted terminations are marked explicitly by the
        # verification node; do not send the identical route back for another repair round.
        if verification.get("repair_terminated"):
            return "failed"
        if state.get("repair_count", 0) >= MAX_REPLAN_RETRIES:
            return "failed"
        if state.get("status") == "failed":
            return "failed"
        return "repair"

    @traced("verification", ["process_route", "geometry", "capability"])
    def verification(self, state: WorkflowState) -> dict[str, Any]:
        retry_count = state.get("retry_count", 0)
        replan_label = f"(attempt {retry_count + 1})" if retry_count else ""
        self.progress(state, 94, "verification", f"Verifying plan completeness{replan_label}.")
        route = state["process_route"]
        geometry = state["geometry"]
        global_req = state["request"]["global_requirements"]
        heat_decision = state.get("heat_treatment_decision", {})
        validation_issues: list[dict[str, Any]] = []

        # Check 1: Mandatory operations
        route_names = {item["name"] for item in route}
        route_stages = {item["stage"] for item in route}
        mandatory_missing = MANDATORY_OPERATION_NAMES - route_names
        basic_check = {
            "name": "Mandatory Operations",
            "passed": not mandatory_missing,
            "message": "Basic route contains mandatory operations."
            if not mandatory_missing
            else "Missing: " + ", ".join(sorted(mandatory_missing)),
        }
        if mandatory_missing:
            for name in mandatory_missing:
                validation_issues.append(
                    ValidationIssue(
                        error_code="MISSING_MANDATORY_OP",
                        object_id=name,
                        message=f"Missing mandatory operation: {name}",
                    ).model_dump()
                )

        # Check 2: Semantic completeness
        semantic_issues = []
        if global_req["heat_treatment"] != "none":
            if "heat_treatment" not in route_stages:
                semantic_issues.append(
                    "Heat treatment required but missing heat treatment operation."
                )
            if (
                heat_decision.get("requires_datum_recovery", True)
                and "datum_recovery" not in route_stages
            ):
                semantic_issues.append(
                    "Heat treatment present but missing center hole repair operation."
                )
        if global_req["surface_treatment"] != "none" and "surface_treatment" not in route_stages:
            semantic_issues.append(
                "Surface treatment required but missing surface treatment operation."
            )
        grinding_segments = [
            s["segment_id"]
            for s in geometry["segments"]
            if requires_grinding(
                s.get("diameter_upper_deviation_mm"),
                s.get("diameter_lower_deviation_mm"),
                s.get("roughness_ra"),
            )
        ]
        if grinding_segments and "precision_finish" not in route_stages:
            semantic_issues.append(
                f"Segments {', '.join(grinding_segments)} need grinding but missing finish grind operation."
            )
        if heat_decision.get("pre_treatment") and "pre_heat_treatment" not in route_stages:
            semantic_issues.append("Heat treatment decision requires a pre-treatment operation.")
        semantic_check = {
            "name": "Semantic Completeness",
            "passed": not semantic_issues,
            "message": "Passed." if not semantic_issues else "; ".join(semantic_issues),
        }
        for msg in semantic_issues:
            validation_issues.append(
                ValidationIssue(error_code="SEMANTIC_INCOMPLETE", message=msg).model_dump()
            )

        # Check 3: Feature coverage
        feature_coverage_issues = []
        for feature in geometry["features"]:
            fid, ftype = feature["feature_id"], feature["feature_type"]
            required_processes = FEATURE_REQUIRED_PROCESS.get(ftype, set())
            matching_ops = [op for op in route if op.get("feature_id") == fid]
            if not matching_ops:
                feature_coverage_issues.append(f"Feature {fid}({ftype}) not covered.")
                validation_issues.append(
                    ValidationIssue(
                        error_code="FEATURE_NOT_COVERED",
                        object_id=fid,
                        message="Feature not covered.",
                    ).model_dump()
                )
            elif required_processes:
                op_categories = {op.get("process_category") for op in matching_ops}
                if not (required_processes & op_categories):
                    feature_coverage_issues.append(
                        f"Feature {fid}({ftype}) needs {required_processes}, got {op_categories}."
                    )
        feature_check = {
            "name": "Feature Coverage",
            "passed": not feature_coverage_issues,
            "message": "All features covered."
            if not feature_coverage_issues
            else "; ".join(feature_coverage_issues),
        }

        # Check 4: Critical turning resources
        cap = state["capability"]
        machine_conclusion = (cap.get("machine") or {}).get("conclusion")
        if machine_conclusion == "satisfied":
            resource_message = "Turning machine and tool material adaptation passed."
        elif cap.get("critical_ok"):
            resource_message = "No local turning machine matches the part size; external capacity / engineer confirmation required."
        else:
            resource_message = "Turning machine and/or tool material not covered for this part."
        resource_check = {
            "name": "Critical Turning Resources",
            "passed": cap.get("critical_ok"),
            "message": resource_message,
        }

        # Check 5: Topology sort
        topo_check = self._topological_verify(route)
        topo_result = {
            "name": "Topology Sort",
            "passed": topo_check["passed"],
            "message": topo_check["message"],
        }
        if not topo_check["passed"]:
            validation_issues.append(
                ValidationIssue(
                    error_code="TOPOLOGY_CONFLICT", message=topo_check["message"]
                ).model_dump()
            )

        # Check 6: Route structure
        route_errors = Guardrails.validate_route(route)
        route_check = {
            "name": "Route Structure",
            "passed": not route_errors,
            "message": "Passed." if not route_errors else "; ".join(route_errors),
        }
        for err in route_errors:
            validation_issues.append(
                ValidationIssue(error_code="ROUTE_STRUCTURE", message=err).model_dump()
            )

        checks = [
            basic_check,
            semantic_check,
            feature_check,
            resource_check,
            topo_result,
            route_check,
        ]
        hard_fail = any(not c["passed"] for c in checks)
        partial = state["resource_selection"]["partial_verification_count"] > 0 or bool(
            state["capability"]["notes"]
        )

        route_hash = hashlib.md5(
            json.dumps(route, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        previous_hashes = state.get("route_hashes", [])
        is_duplicate = route_hash in previous_hashes
        repair_count = state.get("repair_count", 0)

        terminating = False
        if hard_fail:
            if is_duplicate:
                conclusion, message, final_status = (
                    "failed",
                    "Verification failed and route identical to previous attempt, repair terminated.",
                    "failed",
                )
            elif repair_count < MAX_REPLAN_RETRIES:
                conclusion, message, final_status = (
                    "failed",
                    f"Verification failed, auto-repairing ({repair_count + 1}/{MAX_REPLAN_RETRIES}).",
                    "running",
                )
            else:
                conclusion, message, final_status = (
                    "failed",
                    "Verification failed, max repair attempts reached.",
                    "failed",
                )
            terminating = is_duplicate or repair_count >= MAX_REPLAN_RETRIES
            self.store.update(
                state["job_id"],
                status="failed" if terminating else "running",
                current_step="finalizing" if terminating else "verification",
                message=message,
            )
        elif partial:
            conclusion, message, final_status = (
                "conditional_pass",
                "Conditional pass; resources not covered need engineer confirmation.",
                "completed",
            )
            self.store.update(
                state["job_id"],
                status="running",
                current_step="finalizing",
                message="Verification complete, finalizing.",
            )
        else:
            conclusion, message, final_status = "pass", "Verification passed.", "completed"
            self.store.update(
                state["job_id"],
                status="running",
                current_step="finalizing",
                message="Verification complete, finalizing.",
            )

        warnings = (
            state["geometry"]["warnings"]
            + state["capability"]["notes"]
            + heat_decision.get("trace", {}).get("warnings", [])
        )

        llm_analysis = None
        # AI review toggle: when the route passes cleanly ("pass"), skip the LLM review
        # by default, saving about 9s per clean job.
        # To restore the old behavior (review even on pass), set
        # SKIP_AI_REVIEW_ON_CLEAN_PASS=false in .env.
        skip_ai_on_clean_pass = os.getenv(
            "SKIP_AI_REVIEW_ON_CLEAN_PASS", "true"
        ).strip().lower() in ("1", "true", "yes", "on")
        if skip_ai_on_clean_pass and conclusion == "pass":
            logger.info(
                "Verification passed cleanly; skipping LLM AI review "
                "(SKIP_AI_REVIEW_ON_CLEAN_PASS=true)."
            )
        elif llm_available():
            try:
                self.store.update(
                    state["job_id"],
                    current_step="verification",
                    message="Reviewing route with AI...",
                )
                uncovered = [
                    iss["object_id"]
                    for iss in validation_issues
                    if iss.get("error_code") == "FEATURE_NOT_COVERED" and iss.get("object_id")
                ]
                llm_analysis = self._llm_verification_analysis(state, checks, conclusion, uncovered)
            except Exception as exc:
                logger.warning("LLM verification analysis failed, skipping: %s", exc)

        return {
            "verification": {
                "conclusion": conclusion,
                "message": message,
                "checks": checks,
                "warnings": warnings,
                "validation_issues": validation_issues,
                "llm_analysis": llm_analysis,
                "heat_treatment_type": global_req["heat_treatment"],
                "heat_treatment_decision": heat_decision,
                "surface_treatment_type": global_req["surface_treatment"],
                "repair_terminated": bool(terminating),
            },
            "status": final_status,
            "route_hashes": [route_hash],
        }

    @staticmethod
    def _topological_verify(route: list[dict[str, Any]]) -> dict[str, Any]:
        for op in route:
            try:
                ProcessStage(op.get("stage", "inspection"))
            except ValueError:
                return {
                    "passed": False,
                    "message": f"Operation {op.get('operation_no')} stage '{op.get('stage')}' is not a valid enum value.",
                }

        all_known_stages = {s.value for s in ProcessStage}
        stage_graph: dict[str, set[str]] = {s: set() for s in all_known_stages}
        for pre, post in STAGE_DEPENDENCY_RULES:
            stage_graph[post].add(pre)

        stage_in_degree: dict[str, int] = {s: 0 for s in all_known_stages}
        for s, deps in stage_graph.items():
            for d in deps:
                if d in stage_in_degree:
                    stage_in_degree[s] += 1

        stage_queue = [s for s, deg in stage_in_degree.items() if deg == 0]
        stage_global_order: dict[str, int] = {}
        order_idx = 0
        while stage_queue:
            s = stage_queue.pop(0)
            stage_global_order[s] = order_idx
            order_idx += 1
            for other, deps in stage_graph.items():
                if s in deps:
                    stage_in_degree[other] -= 1
                    if stage_in_degree[other] == 0:
                        stage_queue.append(other)
        for s in all_known_stages:
            if s not in stage_global_order:
                stage_global_order[s] = 999

        edges: dict[int, set[int]] = {op["operation_no"]: set() for op in route}
        stage_ops: dict[str, list[int]] = {}
        for op in route:
            stage_ops.setdefault(op["stage"], []).append(op["operation_no"])

        for op in route:
            no, stage = op["operation_no"], op["stage"]
            ops_in_stage = stage_ops.get(stage, [])
            idx = ops_in_stage.index(no)
            if idx > 0:
                edges[no].add(ops_in_stage[idx - 1])
            my_order = stage_global_order.get(stage, 0)
            for other_stage, other_ops in stage_ops.items():
                if stage_global_order.get(other_stage, 0) < my_order:
                    for other_no in other_ops:
                        edges[no].add(other_no)

        in_degree: dict[int, int] = {op["operation_no"]: 0 for op in route}
        for node, deps in edges.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[node] += 1

        queue = [no for no, deg in in_degree.items() if deg == 0]
        sorted_nodes: list[int] = []
        while queue:
            node = queue.pop(0)
            sorted_nodes.append(node)
            for other, deps in edges.items():
                if node in deps:
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)

        if len(sorted_nodes) != len(route):
            cycle_nodes = [
                op["operation_no"] for op in route if op["operation_no"] not in sorted_nodes
            ]
            return {
                "passed": False,
                "message": f"Circular dependency detected in operations: {cycle_nodes}.",
            }

        input_nos = [op["operation_no"] for op in route]
        position = {no: i for i, no in enumerate(input_nos)}
        conflicts = []
        for node, deps in edges.items():
            for dep in deps:
                if dep in position and position.get(node, 0) < position[dep]:
                    node_op = next((op for op in route if op["operation_no"] == node), None)
                    dep_op = next((op for op in route if op["operation_no"] == dep), None)
                    if node_op and dep_op:
                        conflicts.append(
                            f"Op {node}({node_op['name']}/{node_op['stage']}) depends on {dep}({dep_op['name']}/{dep_op['stage']}), but order is wrong"
                        )

        if conflicts:
            return {"passed": False, "message": "Stage inversion: " + "; ".join(conflicts[:5])}

        return {
            "passed": True,
            "message": f"Topology sort passed, {len(route)} operations, no circular dependency or stage inversion.",
        }

    def _llm_verification_analysis(
        self,
        state: WorkflowState,
        checks: list[dict[str, Any]],
        conclusion: str,
        missing: list[str],
    ) -> dict[str, Any]:
        route = state["process_route"]
        route_desc = "\n".join(
            f"  {op['operation_no']}. {op['name']} ({op.get('stage', '-')})"
            + (" [Conditional]" if op.get("conditional") else "")
            for op in route
        )
        check_desc = "\n".join(
            f"  - {c['name']}: {'Pass' if c['passed'] else 'Fail'} {c['message']}" for c in checks
        )
        feature_desc = (
            "\n".join(
                f"  - {f['feature_id']}: {FEATURE_NAME.get(f['feature_type'], f['feature_type'])}"
                + (" [high-precision]" if f.get("high_precision") else "")
                for f in state["geometry"].get("features", [])
            )
            or "  None"
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
            "verification_analysis",
            {
                "route_desc": route_desc,
                "conclusion": conclusion,
                "check_desc": check_desc,
                "feature_desc": feature_desc,
                "missing_desc": ", ".join(missing) if missing else "None",
                "rag_context": rag_context,
            },
        )
        return chat_json(messages, temperature=0.3)

    @traced("repair", ["process_route", "verification", "geometry"])
    def repair(self, state: WorkflowState) -> dict[str, Any]:
        repair_count = state.get("repair_count", 0)
        self.progress(state, 90, "repair", f"Repairing process route ({repair_count + 1} attempt).")

        verification, current_route, geometry, request = (
            state["verification"],
            state["process_route"],
            state["geometry"],
            state["request"],
        )
        route_request = {**request, "heat_treatment_plan": state.get("heat_treatment_decision", {})}
        repaired_route = self._rule_based_repair(
            current_route, geometry, route_request, state.get("user_choices", {}), verification
        )

        if llm_available():
            try:
                self.store.update(
                    state["job_id"], current_step="repair", message="Fixing route issues with AI..."
                )
                heat_decision = state.get("heat_treatment_decision", {})
                rag_context = build_rag_context(
                    request,
                    geometry,
                    state.get("user_choices", {}),
                    heat_decision,
                    top_k=3,
                    max_chars=3000,
                )
                llm_repaired = self._llm_repair(
                    repaired_route,
                    geometry,
                    request,
                    verification,
                    repair_count,
                    rag_context,
                )
                if llm_repaired:
                    repaired_route = llm_repaired
            except Exception as exc:
                logger.warning("LLM repair failed, keeping rule-based repair: %s", exc)

        try:
            for op in repaired_route:
                ProcessOperation(**op)
        except Exception as exc:
            logger.warning("Repaired route validation failed: %s", exc)
            return {"process_route": current_route, "repair_count": repair_count + 1}

        return {"process_route": repaired_route, "repair_count": repair_count + 1}

    def _rule_based_repair(
        self,
        route: list[dict[str, Any]],
        geometry: dict[str, Any],
        request: dict[str, Any],
        choices: dict[str, str],
        verification: dict[str, Any],
    ) -> list[dict[str, Any]]:
        issues = verification.get("validation_issues", [])

        # Feature placement is determined by the central rule engine.  Rebuild
        # the route instead of using a second, stale feature-to-stage mapping.
        if any(issue.get("error_code") == "FEATURE_NOT_COVERED" for issue in issues):
            return build_route(request, geometry, choices)

        repaired = [dict(op) for op in route]
        route_stages = {op["stage"] for op in repaired}

        for issue in issues:
            if issue.get("error_code") != "SEMANTIC_INCOMPLETE":
                continue
            message = issue.get("message", "")

            if "Heat Treatment" in message and "missing heat treatment operation" in message:
                if "heat_treatment" not in route_stages:
                    insert_idx = next(
                        (i + 1 for i, op in enumerate(repaired) if op["stage"] == "semi_finish"),
                        len(repaired),
                    )
                    heat_type = verification.get("heat_treatment_type", "none")
                    decision = verification.get("heat_treatment_decision", {})
                    repaired.insert(
                        insert_idx,
                        {
                            "operation_no": 0,
                            "name": "Heat Treatment",
                            "stage": "heat_treatment",
                            "description": decision.get("description")
                            or HEAT_NAME.get(heat_type, "Heat Treatment"),
                            "process_category": "Heat Treatment",
                            "feature_id": None,
                            "conditional": False,
                        },
                    )

            elif "Repair Center Holes" in message:
                if "datum_recovery" not in route_stages:
                    insert_idx = next(
                        (i + 1 for i, op in enumerate(repaired) if op["stage"] == "heat_treatment"),
                        len(repaired),
                    )
                    repaired.insert(
                        insert_idx,
                        {
                            "operation_no": 0,
                            "name": "Repair Center Holes",
                            "stage": "datum_recovery",
                            "description": "Recover finishing datum after heat treatment.",
                            "process_category": None,
                            "feature_id": None,
                            "conditional": False,
                        },
                    )

            elif "pre-treatment operation" in message:
                if "pre_heat_treatment" not in route_stages:
                    insert_idx = next(
                        (i + 1 for i, op in enumerate(repaired) if op["stage"] == "semi_finish"),
                        len(repaired),
                    )
                    decision = verification.get("heat_treatment_decision", {})
                    pre_treatment = decision.get("pre_treatment", {})
                    repaired.insert(
                        insert_idx,
                        {
                            "operation_no": 0,
                            "name": pre_treatment.get("name", "Heat-treatment Pre-treatment"),
                            "stage": "pre_heat_treatment",
                            "description": pre_treatment.get(
                                "description", "Apply required heat-treatment pre-treatment."
                            ),
                            "process_category": "Heat Treatment",
                            "feature_id": None,
                            "conditional": True,
                        },
                    )

            elif "Surface Treatment" in message:
                if "surface_treatment" not in route_stages:
                    insert_idx = next(
                        (
                            i + 1
                            for i, op in enumerate(repaired)
                            if op["stage"] in ("feature_before_inspection", "finish")
                        ),
                        len(repaired),
                    )
                    repaired.insert(
                        insert_idx,
                        {
                            "operation_no": 0,
                            "name": "Surface Treatment",
                            "stage": "surface_treatment",
                            "description": "Apply surface treatment.",
                            "process_category": None,
                            "feature_id": None,
                            "conditional": True,
                        },
                    )

            elif "grinding" in message and "finish grind" in message:
                if "precision_finish" not in route_stages:
                    insert_idx = next(
                        (i + 1 for i, op in enumerate(repaired) if op["stage"] == "finish"),
                        len(repaired),
                    )
                    grinding_segments = [
                        s["segment_id"]
                        for s in geometry.get("segments", [])
                        if requires_grinding(
                            s.get("diameter_upper_deviation_mm"),
                            s.get("diameter_lower_deviation_mm"),
                            s.get("roughness_ra"),
                        )
                    ]
                    repaired.insert(
                        insert_idx,
                        {
                            "operation_no": 0,
                            "name": "Finish Grind OD",
                            "stage": "precision_finish",
                            "description": f"High-precision segment grinding: {', '.join(grinding_segments) or 'high-precision segments'}",
                            "process_category": None,
                            "feature_id": None,
                            "conditional": True,
                        },
                    )

        for i, op in enumerate(repaired, start=1):
            op["operation_no"] = i
        return repaired

    def _llm_repair(
        self,
        route: list[dict[str, Any]],
        geometry: dict[str, Any],
        request: dict[str, Any],
        verification: dict[str, Any],
        retry_count: int,
        rag_context: str = "",
    ) -> Optional[list[dict[str, Any]]]:
        route_desc = "\n".join(
            f"  {op['operation_no']}. {op['name']} ({op['stage']})"
            + (" [Conditional]" if op.get("conditional") else "")
            + (f" [Feature: {op['feature_id']}]" if op.get("feature_id") else "")
            for op in route
        )
        feature_desc = (
            "\n".join(
                f"  - {f['feature_id']}: {FEATURE_NAME.get(f['feature_type'], f['feature_type'])}, pos {f['global_position_mm']}mm"
                + (" [high-precision]" if f.get("high_precision") else "")
                for f in geometry.get("features", [])
            )
            or "  None"
        )
        issues_desc = (
            "\n".join(
                f"  - [{iss.get('error_code', '')}] {iss.get('message', '')}"
                for iss in verification.get("validation_issues", [])
            )
            or "  None"
        )
        checks_desc = "\n".join(
            f"  - {c['name']}: {'Pass' if c['passed'] else 'Fail'} {c['message']}"
            for c in verification.get("checks", [])
        )

        messages = self.prompt_manager.render_messages(
            "repair",
            {
                "route_desc": route_desc,
                "feature_desc": feature_desc,
                "issues_desc": issues_desc,
                "checks_desc": checks_desc,
                "retry_count": retry_count,
                "rag_context": rag_context,
            },
        )

        result = chat_json(messages, temperature=0.2)
        if not isinstance(result, dict):
            return None
        repaired_route = result.get("process_route")
        if not repaired_route or not isinstance(repaired_route, list):
            return None
        for op in repaired_route:
            try:
                ProcessStage(op.get("stage", "inspection"))
            except ValueError:
                logger.warning("LLM repair stage invalid: %s", op.get("stage"))
                return None
        return repaired_route
