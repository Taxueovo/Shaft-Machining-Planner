"""LangGraph workflow definition: graph structure, node registration, routing logic."""

from __future__ import annotations

import logging
from typing import Any, Optional

import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from models.workflow import WorkflowState
from repositories import MachineRepository, ToolRepository
from providers import HeatTreatmentProvider
from agents import AgentRegistry, Guardrails, Orchestrator, PromptManager
from agents import ALL_AGENTS
from agents.specialists import SpecialistAgent, SCOPES, ReviewCoordinatorAgent

from .task_scheduler import TaskSchedulerMixin
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
    TaskSchedulerMixin,
    PlanningNodesMixin,
    ProcessNodesMixin,
    SelectionNodesMixin,
    VerificationNodesMixin,
):
    """Route proposal, parallel specialist review, coordination and bounded repair."""

    def __init__(self, store: JobStore) -> None:
        """初始化各领域依赖（仓储/规则引擎/LLM 代理）并编译整条 LangGraph 流程。"""
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

        for name in SCOPES:
            self.agent_registry.register(SpecialistAgent(self, name))

        self.agent_registry.register(ReviewCoordinatorAgent())
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

        # 闭包工厂：为每个 agent 名生成语义一致的图节点（先过守卫、再执行、失败即抛错）。
        def _make_agent_node(agent_name: str):
            def node(state: WorkflowState) -> dict[str, Any]:
                # Guardrail layer: fail fast on state-integrity violations instead of
                # silently planning with a malformed request/geometry.
                errors = self.guardrails.check_all(dict(state))
                if errors:
                    raise RuntimeError(f"Guardrail violation: {'; '.join(errors)}")
                from models.workflow import ExecutionTrace

                entry = ExecutionTrace.start(
                    agent_name,
                    self.agent_registry.get(agent_name).capabilities().required_state_keys,
                )
                result = self.orchestrator.execute_with_recovery(agent_name, state)
                if not result.success:
                    raise RuntimeError(result.error or f"{agent_name} execution failed")
                if "execution_trace" not in result.state_updates:
                    ExecutionTrace.finish(entry, list(result.state_updates), result.tool_calls)
                    entry["duration_ms"] = result.metadata.get("duration_ms", 0)
                    result.state_updates["execution_trace"] = [entry]
                return result.state_updates

            node.__name__ = agent_name
            return node

        builder = StateGraph(WorkflowState)
        builder.add_node("task_planning", _make_agent_node("task_planning"))
        builder.add_node("feature_analysis", _make_agent_node("feature_analysis"))
        builder.add_node("heat_treatment_planning", _make_agent_node("heat_treatment_planning"))
        builder.add_node("precision_choice", _make_agent_node("precision_choice"))
        builder.add_node("verification", _make_agent_node("verification"))
        builder.add_node("repair", _make_agent_node("repair"))
        for name in (
            "plan_tasks",
            "execute_task",
            "collect_tasks",
            "engineering_input",
            "finish_tasks",
        ):
            builder.add_node(name, getattr(self, name))
        builder.add_edge(START, "task_planning")
        builder.add_edge("task_planning", "feature_analysis")
        builder.add_edge("feature_analysis", "heat_treatment_planning")
        builder.add_edge("heat_treatment_planning", "precision_choice")
        builder.add_edge("precision_choice", "plan_tasks")
        builder.add_conditional_edges(
            "plan_tasks", self.dispatch_tasks, ["execute_task", "engineering_input", "finish_tasks"]
        )
        builder.add_edge("execute_task", "collect_tasks")
        builder.add_edge("collect_tasks", "plan_tasks")
        builder.add_edge("engineering_input", "plan_tasks")
        builder.add_conditional_edges(
            "finish_tasks", lambda s: s["task_action"], {"failed": END, "verify": "verification"}
        )
        builder.add_conditional_edges(
            "verification",
            self._route_after_verification,
            {"pass": END, "repair": "repair", "failed": END},
        )
        builder.add_edge("repair", "plan_tasks")
        # Checkpoints share the local job database, including its file permissions.
        self.checkpoint_connection = sqlite3.connect(store.db_path, check_same_thread=False)
        saver = SqliteSaver(self.checkpoint_connection)
        saver.setup()
        store._secure_files()
        self.graph = builder.compile(checkpointer=saver).with_config({"recursion_limit": 128})

    def _register_prompt_templates(self) -> None:
        """注册各节点使用的提示词模板（工艺修正/资源排序/校验评审/修复）。"""
        self.prompt_manager.register(
            name="process_planning",
            system=(
                "You are a motor shaft process planning expert. The rule engine has generated a basic process route. "
                "You need to propose constrained corrections.\n\n"
                "Requirements:\n"
                '1. Output JSON: {"patches": [...]}\n'
                "2. Each patch: action (insert/update/remove), target_operation_no, operation details\n"
                "3. Preserve mandatory operations already present: Blanking, Face Turning, Center Drilling (solid shafts) or Prepare Workholding (hollow shafts), Rough Turning, Semi-finish Turning, Finish Turning, Final Inspection\n"
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
