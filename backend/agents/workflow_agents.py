# 将工作流节点包装成统一智能体接口，声明各节点所需输入和产出字段。
"""Agent subclasses - wrap Workflow nodes as the standard Agent interface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseAgent, AgentCapability, AgentResult

if TYPE_CHECKING:
    from workflow.graph import Workflow


# 适配任务摘要节点，将其输入和输出纳入统一智能体协议。
class TaskPlanningAgent(BaseAgent):
    """Task planning agent."""

    # 绑定工作流实例并设置当前角色名称，执行时委托给相应节点。
    def __init__(self, workflow: Workflow) -> None:
        super().__init__("task_planning")
        self._workflow = workflow

    # 声明该智能体读取和产出的状态键，供执行前后检查。
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


# 适配几何分析节点，输出后续规划使用的轴段及特征信息。
class FeatureAnalysisAgent(BaseAgent):
    """Feature analysis agent."""

    # 绑定工作流实例并设置当前角色名称，执行时委托给相应节点。
    def __init__(self, workflow: Workflow) -> None:
        super().__init__("feature_analysis")
        self._workflow = workflow

    # 声明该智能体读取和产出的状态键，供执行前后检查。
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


# 适配热处理决策节点，保留输入要求和待确认的处理参数。
class HeatTreatmentPlanningAgent(BaseAgent):
    """Heat-treatment decision agent."""

    # 绑定工作流实例并设置当前角色名称，执行时委托给相应节点。
    def __init__(self, workflow: Workflow) -> None:
        super().__init__("heat_treatment_planning")
        self._workflow = workflow

    # 声明该智能体读取和产出的状态键，供执行前后检查。
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


# 适配人工加工时机选择节点，允许图中断并等待选择结果。
class PrecisionChoiceAgent(BaseAgent):
    """Precision choice agent."""

    # 绑定工作流实例并设置当前角色名称，执行时委托给相应节点。
    def __init__(self, workflow: Workflow) -> None:
        super().__init__("precision_choice")
        self._workflow = workflow

    # 声明该智能体读取和产出的状态键，供执行前后检查。
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


# 适配工艺路线生成节点，要求成功结果包含路线字段。
class ProcessPlanningAgent(BaseAgent):
    """Process planning agent."""

    # 绑定工作流实例并设置当前角色名称，执行时委托给相应节点。
    def __init__(self, workflow: Workflow) -> None:
        super().__init__("process_planning")
        self._workflow = workflow

    # 声明该智能体读取和产出的状态键，供执行前后检查。
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


# 适配机床和刀具匹配节点，声明能力结论及逐工序资源输出。
class ResourceSelectionAgent(BaseAgent):
    """Resource selection agent."""

    # 绑定工作流实例并设置当前角色名称，执行时委托给相应节点。
    def __init__(self, workflow: Workflow) -> None:
        super().__init__("resource_selection")
        self._workflow = workflow

    # 声明该智能体读取和产出的状态键，供执行前后检查。
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


# 适配综合验证节点，将检查结果和任务终态交回图流程。
class VerificationAgent(BaseAgent):
    """Plan verification agent."""

    # 绑定工作流实例并设置当前角色名称，执行时委托给相应节点。
    def __init__(self, workflow: Workflow) -> None:
        super().__init__("verification")
        self._workflow = workflow

    # 声明该智能体读取和产出的状态键，供执行前后检查。
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


# 适配路线修复节点，输出修订路线及受限的修复计数。
class RepairAgent(BaseAgent):
    """Process repair agent."""

    # 绑定工作流实例并设置当前角色名称，执行时委托给相应节点。
    def __init__(self, workflow: Workflow) -> None:
        super().__init__("repair")
        self._workflow = workflow

    # 声明该智能体读取和产出的状态键，供执行前后检查。
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
