# 在隔离快照中执行任务合同，审计工具调用，返回结构化结果供调度器发布。
"""Execute one contract in isolation; only the collector may publish its updates."""

from __future__ import annotations

from copy import copy, deepcopy

from agents.specialists import SpecialistAgent
from models.tasks import TaskContract, TaskResult
from workflow.tool_registry import ToolRegistry


# 按任务合同维护工具白名单和累计调用次数。
class ToolBudget:
    # 绑定当前合同并初始化空调用账本。
    def __init__(self, contract):
        self.contract, self.calls = contract, []

    # 工具调用前检查授权与剩余预算，再记录调用名称及参数。
    def record(self, name, arguments):
        if name not in self.contract.allowed_tools:
            raise ValueError(f"Tool {name} is not authorized by the task contract")
        if len(self.calls) >= self.contract.max_tool_calls:
            raise ValueError("Task tool budget exhausted")
        self.calls.append({"tool": name, "arguments": arguments})


# 仅暴露受审计的查询入口，为隔离任务绑定工具权限和预算。
class RepositoryView:
    """Expose only audited query methods, not arbitrary repository mutation."""

    # 保存真实仓储及任务预算，所有代理查询都先经过预算检查。
    def __init__(self, repository, budget):
        self.repository, self.budget = repository, budget

    # 记录并校验车床查询权限后，再调用真实仓储。
    def search_turning(self, *args, **kwargs):
        self.budget.record("query_turning_machines", {"args": list(args), **kwargs})
        return self.repository.search_turning(*args, **kwargs)

    # 记录并校验工艺机床查询权限后，原样传递尺寸和能力条件。
    def search_process(self, *args, **kwargs):
        self.budget.record("query_process_machines", {"args": list(args), **kwargs})
        return self.repository.search_process(*args, **kwargs)

    # 记录刀具查询的权限和预算，再把参数交给真实刀具仓储。
    def search(self, *args, **kwargs):
        self.budget.record("query_cutting_tools", {"args": list(args), **kwargs})
        return self.repository.search(*args, **kwargs)


# 丢弃任务内部的进度写入，统一由调度器发布共享状态。
class SilentStore:
    # 有意丢弃工作线程内的共享状态写入，由结果收集器统一发布。
    def update(self, *args, **kwargs):
        pass


# 复制工作流及输入快照，按角色执行并返回结构化产物；异常转为可重试失败。
def execute_contract(
    workflow, task: TaskContract, snapshot: dict, version: str, attempt: int
) -> TaskResult:
    flow = copy(workflow)
    flow.store = SilentStore()
    budget = ToolBudget(task)
    flow.task_tool_budget = budget
    flow.machine_repo = RepositoryView(workflow.machine_repo, budget)
    flow.tool_repo = RepositoryView(workflow.tool_repo, budget)
    flow.tool_registry = ToolRegistry(flow.machine_repo, flow.tool_repo)
    # 每个任务读取独立快照；工具仓储共享底层只读数据，但预算与状态写入相互隔离。
    state = deepcopy(snapshot)
    state["_worker_contract"] = task.model_dump()
    state["skip_advisory_llm"] = True
    status, missing, updates, artifact, summary = "succeeded", [], {}, {}, ""
    try:
        if task.worker == "process_planning":
            budget.record("build_process_route", {"source": "validated_request"})
            updates = flow.process_planning(state)
            summary = "Candidate route proposed; independent verification required."
            artifact = {"operation_count": len(updates["process_route"])}
        elif task.worker == "resource_selection":
            updates = flow.resource_selection(state)
            gaps = [
                r
                for r in updates["resource_selection"]["operation_resources"]
                if r["verification_status"] not in {"satisfied", "not_applicable"}
            ]
            if gaps or updates["capability"]["machine"]["conclusion"] != "satisfied":
                status = "infeasible"
            summary = (
                "Local resource screening complete."
                if status == "succeeded"
                else "Local capability gaps require alternative-resource analysis."
            )
            artifact = {
                "gaps": [
                    {
                        "process": r["process_category"],
                        "operation_no": r["operation_no"],
                        "reason": r["note"],
                    }
                    for r in gaps
                ]
            }
        elif task.worker in {"machining_review", "quality_review", "heat_review"}:
            response = SpecialistAgent(flow, task.worker).safe_execute(state)
            if not response.success:
                raise ValueError(response.error)
            updates = response.state_updates
            report = updates[task.worker]
            artifact = {"findings": report["findings"], "mode": report["mode"]}
            summary = report["summary"]
            budget.calls = report["tool_calls"]
        # 装夹产物是候选分析和待补充事项，当前实现不自动验证夹具能力。
        elif task.worker == "workholding":
            hollow = state["request"].get("blank_type") == "hollow"
            answer = state.get("engineering_answers", {}).get(task.task_id)
            artifact = {
                "candidate_arrangements": [
                    "Expanding mandrel using a controlled finished bore",
                    "Engineered end plugs with verified support",
                ]
                if hollow
                else [
                    "Centers or chuck plus tailstock, subject to available locating surfaces",
                    "Steady-rest support if required by stiffness assessment",
                ],
                "required_checks": [
                    "Locating surface geometry and tolerance",
                    "Clamp force and deformation",
                    "Tool access and clearance",
                    "Datum transfer and runout acceptance",
                ],
                "source": "deterministic_candidate_generation",
                "production_release": False,
                "user_statement": answer,
                "statement_verified": False,
            }
            # 收到说明只表示信息已记录，statement_verified 与 production_release 仍保持否定。
            if answer:
                summary = "Fixture information recorded for engineering review; supplied claims are not automatically verified."
            else:
                status = "needs_input"
                missing = [
                    "Provide the available fixture/mandrel arrangement and locating-surface requirements, or defer the task to engineering review."
                ]
                summary = "Workholding candidates generated; fixture evidence is missing."
        else:
            cap = state.get("capability", {})
            processes = {
                r["process_category"]
                for r in state.get("resource_selection", {}).get("operation_resources", [])
                if r.get("process_category")
                and r["verification_status"] not in {"satisfied", "not_applicable"}
            }
            if cap.get("machine", {}).get("conclusion") != "satisfied":
                processes.add("ISO Turning")
            candidates = {}
            # 扩展查询数量但保留尺寸、重量、模数及精度约束，不放宽条件来制造匹配。
            for process in sorted(processes - {"Heat Treatment"}):
                candidates[process] = flow.machine_repo.search_process(
                    process,
                    state["geometry"]["total_length_mm"],
                    state["request"]["blank_diameter_mm"],
                    top_n=20,
                    required_weight_kg=state["request"].get("estimated_workpiece_weight_kg"),
                    required_module=max(
                        (
                            float(f.get(k) or 0)
                            for f in state["geometry"]["features"]
                            for k in ("gear_module", "spline_module", "worm_module")
                        ),
                        default=0,
                    )
                    or None
                    if process in {"Gear Hobbing", "Gear Grinding"}
                    else None,
                    high_precision_required=any(
                        f.get("high_precision") for f in state["geometry"]["features"]
                    ),
                )
            artifact = {
                "local_candidates": candidates,
                "external_provider_verified": False,
                "required_processes": sorted(processes),
                "source": "local_public_capability_library",
            }
            missing = [
                "Provide approved local asset/configuration or subcontractor capability evidence for unresolved processes."
            ]
            status, summary = (
                "needs_input",
                "Expanded local screening complete; no external capacity is assumed available.",
            )
            if state.get("engineering_answers", {}).get(task.task_id):
                status, missing = "succeeded", []
                artifact["user_statement"] = state["engineering_answers"][task.task_id]
                artifact["statement_verified"] = False
                summary = "Capacity information recorded but not yet verified against asset or supplier records."
        return TaskResult(
            task_id=task.task_id,
            worker=task.worker,
            status=status,
            context_version=version,
            attempt=attempt,
            summary=summary,
            artifact=artifact,
            state_updates=updates,
            missing_information=missing,
            tool_calls=budget.calls,
        )
    # 任务异常转成结构化失败供调度器有限重试，不将不完整输出写回主状态。
    except Exception as exc:
        return TaskResult(
            task_id=task.task_id,
            worker=task.worker,
            status="tool_failed",
            context_version=version,
            attempt=attempt,
            summary=type(exc).__name__ + ": " + str(exc)[:800],
            tool_calls=budget.calls,
        )
