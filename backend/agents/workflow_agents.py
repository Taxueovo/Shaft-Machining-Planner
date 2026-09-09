"""Agent subclasses - wrap Workflow nodes as the standard Agent interface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseAgent, AgentCapability, AgentResult

if TYPE_CHECKING:
    from workflow.graph import Workflow


class TaskPlanningAgent(BaseAgent):
    """Task planning agent."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__("task_planning")
        self._workflow = workflow

    def capabilities(self) -> AgentCapability:
        return AgentCapability(
            name="task_planning",
            description="Analyze request and create execution plan.",
            input_schema={"request": "object"},
            output_schema={"plan": "object", "retry_count": "integer"},
            required_state_keys=["job_id", "request"],
            produces_state_keys=["plan", "retry_count"],
            tags=["planner", "priority:high"],
        )

    def execute(self, state: dict[str, Any]) -> AgentResult:
        """调用任务规划节点，分析请求并生成执行计划写入状态。"""
        return AgentResult(success=True, state_updates=self._workflow.task_planning(state))


class FeatureAnalysisAgent(BaseAgent):
    """Feature analysis agent."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__("feature_analysis")
        self._workflow = workflow

    def capabilities(self) -> AgentCapability:
        return AgentCapability(
            name="feature_analysis",
            description="Calculate segment and feature coordinates.",
            required_state_keys=["job_id", "request"],
            produces_state_keys=["geometry"],
            tags=["worker", "priority:high"],
        )

    def execute(self, state: dict[str, Any]) -> AgentResult:
        """调用特征分析节点，计算各轴段与特征坐标生成几何模型。"""
        return AgentResult(success=True, state_updates=self._workflow.feature_analysis(state))


class HeatTreatmentPlanningAgent(BaseAgent):
    """Heat-treatment decision agent."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__("heat_treatment_planning")
        self._workflow = workflow

    def capabilities(self) -> AgentCapability:
        return AgentCapability(
            name="heat_treatment_planning",
            description="Decide heat-treatment process family and route constraints.",
            required_state_keys=["job_id", "request", "geometry"],
            produces_state_keys=["heat_treatment_decision"],
            tags=["worker", "decision", "priority:high"],
        )

    def execute(self, state: dict[str, Any]) -> AgentResult:
        """调用热处理规划节点，判定热处理工艺家族与路线约束。"""
        return AgentResult(
            success=True, state_updates=self._workflow.heat_treatment_planning(state)
        )


class PrecisionChoiceAgent(BaseAgent):
    """Precision choice agent."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__("precision_choice")
        self._workflow = workflow

    def capabilities(self) -> AgentCapability:
        return AgentCapability(
            name="precision_choice",
            description="Detect high-precision features, trigger user choice.",
            required_state_keys=["job_id", "geometry", "request"],
            produces_state_keys=["pending_choices", "user_choices"],
            tags=["worker", "hitl"],
        )

    def execute(self, state: dict[str, Any]) -> AgentResult:
        """调用精度选择节点，识别高精度特征并产出待人工确认项。"""
        return AgentResult(success=True, state_updates=self._workflow.precision_choice(state))


class ProcessPlanningAgent(BaseAgent):
    """Process planning agent."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__("process_planning")
        self._workflow = workflow

    def capabilities(self) -> AgentCapability:
        return AgentCapability(
            name="process_planning",
            description="Generate process route.",
            required_state_keys=["job_id", "request", "geometry", "user_choices"],
            produces_state_keys=["process_route"],
            tags=["worker", "planning"],
        )

    def execute(self, state: dict[str, Any]) -> AgentResult:
        """调用工艺规划节点，基于几何与用户选择生成工艺路线。"""
        return AgentResult(success=True, state_updates=self._workflow.process_planning(state))


class ResourceSelectionAgent(BaseAgent):
    """Resource selection agent."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__("resource_selection")
        self._workflow = workflow

    def capabilities(self) -> AgentCapability:
        return AgentCapability(
            name="resource_selection",
            description="Query machine/tool databases and match resources per operation.",
            required_state_keys=["job_id", "request", "process_route", "geometry"],
            produces_state_keys=["capability", "resource_selection"],
            tags=["worker", "resource"],
        )

    def execute(self, state: dict[str, Any]) -> AgentResult:
        """调用资源选择节点，为各工序匹配机床/刀具等制造资源。"""
        return AgentResult(success=True, state_updates=self._workflow.resource_selection(state))


class VerificationAgent(BaseAgent):
    """Plan verification agent."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__("verification")
        self._workflow = workflow

    def capabilities(self) -> AgentCapability:
        return AgentCapability(
            name="verification",
            description="Verify plan completeness.",
            required_state_keys=[
                "job_id",
                "request",
                "process_route",
                "geometry",
                "capability",
                "resource_selection",
            ],
            produces_state_keys=["verification", "status", "route_hashes"],
            tags=["worker", "verification"],
        )

    def execute(self, state: dict[str, Any]) -> AgentResult:
        """调用验证节点，检查工艺计划各环节的完整性与一致性。"""
        return AgentResult(success=True, state_updates=self._workflow.verification(state))


class RepairAgent(BaseAgent):
    """Process repair agent."""

    def __init__(self, workflow: Workflow) -> None:
        super().__init__("repair")
        self._workflow = workflow

    def capabilities(self) -> AgentCapability:
        return AgentCapability(
            name="repair",
            description="Repair process route based on verification feedback.",
            required_state_keys=["job_id", "process_route", "verification", "geometry", "request"],
            produces_state_keys=["process_route", "repair_count"],
            tags=["worker", "repair"],
        )

    def execute(self, state: dict[str, Any]) -> AgentResult:
        """调用修复节点，依据验证反馈修订工艺路线并计数。"""
        return AgentResult(success=True, state_updates=self._workflow.repair(state))


# 全部流程代理类清单：上层据此批量实例化（注入 Workflow）并注册到 AgentRegistry
ALL_AGENTS = [
    TaskPlanningAgent,
    FeatureAnalysisAgent,
    HeatTreatmentPlanningAgent,
    PrecisionChoiceAgent,
    ProcessPlanningAgent,
    ResourceSelectionAgent,
    VerificationAgent,
    RepairAgent,
]
