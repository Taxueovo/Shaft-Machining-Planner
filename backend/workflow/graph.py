"""LangGraph workflow definition: graph structure, node registration, routing logic."""

from __future__ import annotations

import logging
from typing import Any, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from models.workflow import WorkflowState
from repositories import MachineRepository, ToolRepository
from providers import HeatTreatmentProvider
from agents import AgentRegistry, Guardrails, Orchestrator, PromptManager
from agents import ALL_AGENTS

from .tool_registry import ToolRegistry
from .job_store import JobStore
from .nodes import (
    PlanningNodesMixin,
    ProcessNodesMixin,
    SelectionNodesMixin,
    VerificationNodesMixin,
)

logger = logging.getLogger(__name__)


class Workflow(
    PlanningNodesMixin,
    ProcessNodesMixin,
    SelectionNodesMixin,
    VerificationNodesMixin,
):
    """LangGraph workflow: 10-node process planning pipeline."""

    def __init__(self, store: JobStore) -> None:
        self.store = store
        self.machine_repo = MachineRepository()
        self.tool_repo = ToolRepository()
        self.heat_treatment_provider = HeatTreatmentProvider()
        self.tool_registry = ToolRegistry(self.machine_repo, self.tool_repo)

        self.agent_registry = AgentRegistry()
        self.guardrails = Guardrails()
        self.prompt_manager = PromptManager()
        self.orchestrator = Orchestrator(self.agent_registry, self.guardrails, self.prompt_manager)

        for agent_cls in ALL_AGENTS:
            self.agent_registry.register(agent_cls(self))

        self._register_prompt_templates()

        self.guardrails.add_rule(lambda s: "request" not in s and "Missing input request" or None)
        self.orchestrator.register_fallback("process_planning", [])

        def _validate_geometry_rule(state: dict[str, Any]) -> Optional[str]:
            # geometry is created later in the pipeline (feature_analysis), so absence is
            # expected at early nodes; only validate a geometry that is already present.
            geom = state.get("geometry")
            if not geom:
                return None
            errors = Guardrails.validate_geometry(geom)
            return errors[0] if errors else None

        self.guardrails.add_rule(_validate_geometry_rule)

        def _make_agent_node(agent_name: str):
            def node(state: WorkflowState) -> dict[str, Any]:
                # Guardrail layer: fail fast on state-integrity violations instead of
                # silently planning with a malformed request/geometry.
                errors = self.guardrails.check_all(dict(state))
                if errors:
                    raise RuntimeError(f"Guardrail violation: {'; '.join(errors)}")
                result = self.orchestrator.execute_with_recovery(agent_name, state)
                if not result.success:
                    raise RuntimeError(result.error or f"{agent_name} execution failed")
                return result.state_updates

            node.__name__ = agent_name
            return node

        builder = StateGraph(WorkflowState)
        builder.add_node("task_planning", _make_agent_node("task_planning"))
        builder.add_node("feature_analysis", _make_agent_node("feature_analysis"))
        builder.add_node("heat_treatment_planning", _make_agent_node("heat_treatment_planning"))
        builder.add_node("precision_choice", _make_agent_node("precision_choice"))
        builder.add_node("process_planning", _make_agent_node("process_planning"))
        builder.add_node("resource_selection", _make_agent_node("resource_selection"))
        builder.add_node("verification", _make_agent_node("verification"))
        builder.add_node("repair", _make_agent_node("repair"))

        # Linear backbone + precision choice branch
        builder.add_edge(START, "task_planning")
        builder.add_edge("task_planning", "feature_analysis")
        # Precision choice (user_choices are required before route planning)
        builder.add_edge("feature_analysis", "heat_treatment_planning")
        builder.add_edge("heat_treatment_planning", "precision_choice")
        builder.add_edge("precision_choice", "process_planning")
        # Resource matching: includes machine query, tool query, process matching
        builder.add_edge("process_planning", "resource_selection")
        # Verification -> repair -> replan -> verify again: after repair, return to
        # process_planning with the failure reasons for replanning
        builder.add_edge("resource_selection", "verification")
        builder.add_conditional_edges(
            "verification",
            self._route_after_verification,
            {"pass": END, "repair": "repair", "failed": END},
        )
        builder.add_edge("repair", "process_planning")
        self.graph = builder.compile(checkpointer=InMemorySaver())

    def _register_prompt_templates(self) -> None:
        self.prompt_manager.register(
            name="process_planning",
            system=(
                "You are a motor shaft process planning expert. The rule engine has generated a basic process route. "
                "You need to propose constrained corrections.\n\n"
                "Requirements:\n"
                '1. Output JSON: {"patches": [...]}\n'
                "2. Each patch: action (insert/update/remove), target_operation_no, operation details\n"
                "3. Mandatory operations cannot be deleted: Blanking, Face Turning, Center Drilling, Rough Turning, Semi-finish Turning, Finish Turning, Final Inspection\n"
                "4. stage values: blank/datum/rough/semi_finish/feature_before_heat/"
                "pre_heat_treatment/heat_treatment/datum_recovery/finish/"
                "feature_after_heat/precision_finish/precision_feature/"
                "feature_before_inspection/surface_treatment/inspection\n"
                "5. process_category values: ISO Turning/Drilling/Indexable Milling/Threading/Boring/Taper Turning/Grooving/Cylindrical Grinding/Gear Grinding/Cam Grinding/Worm Grinding/Fillet Rolling/Heat Treatment/null\n"
                "6. If the base route is reasonable, return empty patches array\n"
                "7. Use reference knowledge (standards/cases) from below to improve the route when applicable\n"
                "8. Always respond in English. All free-text fields (descriptions, notes, operation names) must be written in English, never in Chinese"
            ),
            user=(
                "Material: {material}\nBar diameter: {blank_diameter_mm}mm\nTotal length: {total_length_mm}mm\n\n"
                "Segments:\n{segment_desc}\n\nFeatures:\n{feature_desc}\n\n"
                "Heat Treatment: {heat_treatment}\nSurface Treatment: {surface_treatment}\n"
                "Batch: {batch_quantity}\n\nPrecision choices: {choices}\n\n"
                "Base route:\n{base_route_desc}\n{retry_context}\n\n"
                "Reference knowledge:\n{rag_context}\n\n"
                'Return correction patches JSON. If no correction needed, return {{"patches": []}}.'
            ),
            version="3.0",
        )
        self.prompt_manager.register(
            name="resource_ranking",
            user=(
                "Machine and tool selection. Material: {material}, Batch: {batch_quantity}\n\n"
                "Machine candidates:\n{machine_desc}\n\nOperation matching:\n{op_desc}\n\n"
                "Reference knowledge (standards/cases) from below can inform machine/tool selection when applicable.\n"
                "{rag_context}\n\n"
                "Evaluate efficiency, setup cost, resource match. Return JSON:\n"
                '{{"recommended_machine":"model+reason","process_consolidation_suggestions":["suggestion"],'
                '"risk_operations":[{{"operation_no":1,"risk":"","mitigation":""}}],'
                '"overall_score":85,"summary":"one line"}}\n'
                "Always respond in English. All free-text fields (recommended_machine, suggestions, risk, mitigation, summary) must be written in English, never in Chinese"
            ),
            version="1.2",
        )
        self.prompt_manager.register(
            name="verification_analysis",
            user=(
                "Process plan review. Conclusion: {conclusion}\n\nRoute:\n{route_desc}\n\n"
                "Checks:\n{check_desc}\n\nFeatures:\n{feature_desc}\nMissing:\n{missing_desc}\n\n"
                "Reference knowledge (standards/cases) from below can inform the review when applicable.\n"
                "{rag_context}\n\n"
                "Return JSON:\n"
                '{{"overall_assessment":"one line","risk_items":["risk"],'
                '"improvement_suggestions":["suggestion"],"engineering_notes":"notes"}}\n'
                "Always respond in English. All free-text fields (overall_assessment, risk_items, improvement_suggestions, engineering_notes) must be written in English, never in Chinese"
            ),
            version="1.2",
        )
        self.prompt_manager.register(
            name="repair",
            system=(
                "You are a motor shaft process repair expert. Fix the process route based on verification feedback.\n\n"
                "Requirements:\n"
                '1. Output JSON: {"process_route": [...]}\n'
                "2. Keep correct operations, only fix errors\n"
                "3. Ensure all features are covered\n"
                "4. Ensure operation numbers are continuous\n"
                "5. Mandatory operations cannot be deleted\n"
                "6. stage/process_category values same as above\n"
                "7. Always respond in English. All free-text fields (descriptions, notes) must be written in English, never in Chinese"
            ),
            user=(
                "Current route:\n{route_desc}\n\nFeatures:\n{feature_desc}\n\n"
                "Issues:\n{issues_desc}\n\nChecks:\n{checks_desc}\n\n"
                "Repair count: {retry_count}\n\n"
                "Reference knowledge:\n{rag_context}\n\n"
                'Return repaired route JSON: {{"process_route": [...]}}\n'
                "Each op: operation_no, name, stage, description, process_category, feature_id, conditional"
            ),
            version="1.1",
        )
