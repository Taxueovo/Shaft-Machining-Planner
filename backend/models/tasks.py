# 定义任务合同、依赖图、执行结果和工程回答；模型输出不能绕过权限与依赖校验。
"""Validated contracts for the planner/worker protocol."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

WorkerName = Literal[
    "process_planning",
    "resource_selection",
    "machining_review",
    "quality_review",
    "heat_review",
    "workholding",
    "alternative_resources",
]
# 角色工具白名单是权限上限；模型给出的 allowed_tools 只能缩小范围。
WORKER_TOOLS = {
    "process_planning": {"build_process_route", "retrieve_references"},
    "resource_selection": {
        "query_turning_machines",
        "query_process_machines",
        "query_cutting_tools",
    },
    "machining_review": {
        "inspect_route",
        "query_turning_machines",
        "query_cutting_tools",
        "retrieve_references",
    },
    "quality_review": {"inspect_route", "retrieve_references"},
    "heat_review": {"inspect_route", "retrieve_references"},
    "workholding": {"inspect_route", "query_turning_machines", "retrieve_references"},
    "alternative_resources": {"query_process_machines"},
}


# 定义单任务的角色、依赖、目标、工具权限、预算和是否阻塞规划。
class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
    worker: WorkerName
    objective: str = Field(min_length=1, max_length=1200)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    requires_success: bool = True
    blocking: bool = False
    allowed_tools: list[str] | None = None
    max_tool_calls: int = Field(default=4, ge=1, le=64)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)

    # 确保合同工具属于角色白名单，并拒绝自身依赖和重复依赖。
    @model_validator(mode="after")
    def permission_scope(self):
        allowed = WORKER_TOOLS[self.worker]
        if self.allowed_tools is None:
            self.allowed_tools = sorted(allowed)
        if not set(self.allowed_tools).issubset(allowed):
            raise ValueError(f"{self.worker}: tool permission exceeds registered capabilities")
        if self.task_id in self.depends_on or len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Invalid task dependency")
        return self


# 封装任务集合与规划理由，校验依赖图和必需角色覆盖。
class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[TaskContract] = Field(min_length=1, max_length=12)
    rationale: str = Field(min_length=1, max_length=2000)

    # 验证任务唯一性、已知依赖、无环性、必需角色及路线前置关系。
    @model_validator(mode="after")
    def validate_dag(self):
        ids = {task.task_id for task in self.tasks}
        if len(ids) != len(self.tasks):
            raise ValueError("Task IDs must be unique")
        workers = [task.worker for task in self.tasks]
        if len(workers) != len(set(workers)):
            raise ValueError("Only one task per worker and route revision is supported")
        edges = {task.task_id: set(task.depends_on) for task in self.tasks}
        if any(not parents.issubset(ids) for parents in edges.values()):
            raise ValueError("Unknown task dependency")
        # 使用拓扑消解检查环：仍有任务但没有可加入节点时，依赖图无效。
        pending, visited = dict(edges), set()
        while pending:
            ready = [key for key, parents in pending.items() if parents <= visited]
            if not ready:
                raise ValueError("Task dependency graph contains a cycle")
            for key in ready:
                visited.add(key)
                del pending[key]
        # 必需角色是确定性完成门槛，模型不能省略资源检查或独立领域审查。
        required = {
            "process_planning",
            "resource_selection",
            "machining_review",
            "quality_review",
            "heat_review",
        }
        if not required.issubset(workers):
            raise ValueError("Required planning/review workers may not be omitted")
        route_id = next(task.task_id for task in self.tasks if task.worker == "process_planning")
        if edges[route_id]:
            raise ValueError("Route proposal cannot depend on its reviewers")

        # 递归展开已验证无环的依赖图，用于检查路线任务是否为前置依赖。
        def ancestors(key):
            return edges[key] | {a for parent in edges[key] for a in ancestors(parent)}

        for task in self.tasks:
            if task.worker != "process_planning" and route_id not in ancestors(task.task_id):
                raise ValueError("Workers must depend on the proposed route")
        return self


# 保存任务状态、输入版本、尝试次数、产物和待补充信息，供调度器复用。
class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    worker: WorkerName
    status: Literal["succeeded", "needs_input", "infeasible", "tool_failed", "blocked"]
    context_version: str
    attempt: int = Field(ge=1, le=2)
    summary: str
    artifact: dict[str, Any] = Field(default_factory=dict)
    state_updates: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


# 记录一个待补充任务的工程回答，限制标识及文本长度。
class EngineeringAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1, max_length=40)
    answer: str = Field(min_length=1, max_length=3000)


# 接收多任务回答或明确暂缓决定，禁止重复回答同一任务。
class EngineeringAnswersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[EngineeringAnswer] = Field(default_factory=list, max_length=12)
    defer: bool = False

    # 要求提供回答或明确暂缓，且回答中的任务标识不能重复。
    @model_validator(mode="after")
    def validate_answers(self):
        if not self.defer and not self.answers:
            raise ValueError("Provide answers or explicitly defer engineering review")
        ids = [a.task_id for a in self.answers]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate task answers")
        return self


# 按任务标识合并并行结果，新结果替换同一任务的旧账目。
def merge_task_results(existing: dict, new: dict) -> dict:
    return {**existing, **new}
