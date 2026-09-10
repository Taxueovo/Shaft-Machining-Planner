# 按依赖和版本调度任务，复用有效结果，统一收集输出并处理工程信息中断。
"""Dynamic, bounded DAG scheduling using LangGraph Send and a single result writer."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
import repositories

from langgraph.types import Send, interrupt
from agents.planner import propose_plan
from agents.specialists import coordinate_reviews
from models.tasks import TaskPlan, TaskContract, TaskResult, EngineeringAnswersRequest
from models.workflow import ExecutionTrace
from .task_workers import execute_contract

MAX_WAVES = 16


# 对任务合同、输入、依赖产物及资源文件计算版本；变化后旧结果不能继续复用。
def context_version(task, state):
    """Conservative dependency fingerprints prevent stale results from being reused."""
    library_versions = {}
    if task.worker in {
        "resource_selection",
        "alternative_resources",
        "machining_review",
        "workholding",
    }:
        for path in (repositories.MACHINE_FILE, repositories.TOOL_FILE):
            filename = str(path)
            library_versions[filename] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            )
    data = {
        "protocol_version": 1,
        "task": task.model_dump(),
        "request": state["request"],
        "geometry": state["geometry"],
        "heat_treatment_decision": state.get("heat_treatment_decision"),
        "choices": state.get("user_choices"),
        "libraries": library_versions,
        "answer": state.get("engineering_answers", {}).get(task.task_id),
        "dependency_results": {
            tid: state.get("worker_results", {}).get(tid, {}) for tid in task.depends_on
        },
    }
    # 路线生成任务不能把自身尚未生成的路线作为缓存输入；下游任务必须绑定路线。
    if task.worker != "process_planning":
        data["route"] = state.get("process_route", [])
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


# 沿依赖顺序筛选版本一致的结果；上游失效时其后代结果也不再有效。
def current_results(plan, state):
    valid = {}
    pending = list(plan.tasks)
    while pending:
        # 只沿有效上游结果继续遍历，因此上游失效会自动阻断其后代的旧结果复用。
        ready = [task for task in pending if all(dep in valid for dep in task.depends_on)]
        if not ready:
            break
        for task in ready:
            pending.remove(task)
            result = state.get("worker_results", {}).get(task.task_id)
            if result and result["context_version"] == context_version(task, state):
                valid[task.task_id] = result
    return valid


# 提供确定性调度、并行执行、结果收集及人工补充节点。
class TaskSchedulerMixin:
    # 更新计划和任务账本，选择当前可运行任务，并将等待及复用情况写入看板。
    def plan_tasks(self, state):
        count = state.get("scheduler_waves", 0) + 1
        if count > MAX_WAVES:
            raise ValueError(
                "Dynamic task scheduling exhausted its wave budget; engineering review required."
            )
        old = state.get("task_plan")
        snapshot = dict(state)
        # A repaired route is the new proposal; never schedule the route generator over it.
        seeded = {}
        # 修复节点已产出新路线，把它登记为有效提案；不能再次运行生成器覆盖修复。
        if old and state.get("tasks_repair_count", 0) != state.get("repair_count", 0):
            plan = TaskPlan.model_validate(old)
            route_task = next(t for t in plan.tasks if t.worker == "process_planning")
            seeded[route_task.task_id] = TaskResult(
                task_id=route_task.task_id,
                worker=route_task.worker,
                status="succeeded",
                context_version=context_version(route_task, state),
                attempt=1,
                summary="Repaired route preserved as the current proposal.",
                state_updates={"process_route": state["process_route"]},
            ).model_dump()
            snapshot["worker_results"] = {**state.get("worker_results", {}), **seeded}
        valid = current_results(TaskPlan.model_validate(old), snapshot) if old else {}
        plan, calls, mode, error = propose_plan(snapshot, old, valid, state.get("planner_calls", 0))
        valid = current_results(plan, snapshot)
        ready, blocked = [], []
        for task in plan.tasks:
            result = valid.get(task.task_id)
            # 同版本成功或需人工输入的结果直接保留；工具失败最多允许第二次尝试。
            if result and not (result["status"] == "tool_failed" and result["attempt"] < 2):
                continue
            if all(dep in valid for dep in task.depends_on):
                if task.requires_success and any(
                    valid[d]["status"] != "succeeded" for d in task.depends_on
                ):
                    blocked.append(task.task_id)
                else:
                    ready.append(task.task_id)
        pending = [
            {
                "task_id": t.task_id,
                "question": "\n".join(valid[t.task_id].get("missing_information", [])),
                "objective": t.objective,
            }
            for t in plan.tasks
            if t.blocking
            and t.task_id in valid
            and valid[t.task_id]["status"] == "needs_input"
            and t.task_id not in state.get("deferred_tasks", [])
        ]
        # 优先执行仍可推进的任务；无就绪任务后才等待人工，最后才进行必需任务验收。
        action = "dispatch" if ready else "human" if pending else "finish"
        event = {
            "wave": count,
            "mode": mode,
            "rationale": plan.rationale,
            "error": error,
            "dispatched": ready[:4],
            "reused": list(valid),
            "blocked": blocked,
            "action": action,
        }
        updates = {
            "task_plan": plan.model_dump(),
            "planner_calls": calls,
            "scheduler_waves": count,
            "task_action": action,
            "ready_tasks": ready[:4],
            "pending_engineering": pending,
            "planner_events": [*state.get("planner_events", []), event],
            "tasks_repair_count": state.get("repair_count", 0),
            "worker_results": seeded,
        }
        board = {
            "plan": plan.model_dump(),
            "results": valid,
            "events": updates["planner_events"],
            "mode": mode,
        }
        updates["task_execution"] = board
        self.store.update(
            state["job_id"],
            current_step="planner",
            status="running",
            message=plan.rationale,
            task_execution=board,
        )
        return updates

    # 按调度决定路由到人工输入、收尾节点，或使用 Send 分发就绪任务。
    @staticmethod
    def dispatch_tasks(state):
        if state["task_action"] == "human":
            return "engineering_input"
        if state["task_action"] == "finish":
            return "finish_tasks"
        plan = TaskPlan.model_validate(state["task_plan"])
        by_id = {task.task_id: task for task in plan.tasks}
        return [
            Send("execute_task", {"task": by_id[tid].model_dump(), "snapshot": state})
            for tid in state["ready_tasks"]
        ]

    # 计算当前任务版本和尝试次数，在隔离环境执行并返回单任务增量及轨迹。
    def execute_task(self, envelope):
        task = TaskContract.model_validate(envelope["task"])
        state = envelope["snapshot"]
        version = context_version(task, state)
        previous = state.get("worker_results", {}).get(task.task_id, {})
        attempt = (
            previous.get("attempt", 0) + 1 if previous.get("context_version") == version else 1
        )
        trace = ExecutionTrace.start(task.worker, ["task_contract", "dependency_results"])
        result = execute_contract(self, task, state, version, attempt)
        ExecutionTrace.finish(
            trace,
            ["worker_results"],
            result.tool_calls,
            result.summary if result.status == "tool_failed" else None,
        )
        return {"worker_results": {task.task_id: result.model_dump()}, "execution_trace": [trace]}

    # 只发布当前版本结果中属于该角色的输出字段，阻止越权或冲突写入。
    def collect_tasks(self, state):
        """Only publish recognized output keys from results for the current input version."""
        plan = TaskPlan.model_validate(state["task_plan"])
        valid = current_results(plan, state)
        # 并行任务只写自己的结果槽；此处是共享业务字段的统一发布入口。
        ownership = {
            "process_planning": {"process_route"},
            "resource_selection": {"capability", "resource_selection"},
            "machining_review": {"machining_review"},
            "quality_review": {"quality_review"},
            "heat_review": {"heat_review"},
            "workholding": set(),
            "alternative_resources": set(),
        }
        updates = {}
        for result in valid.values():
            if result["status"] in {"succeeded", "infeasible"}:
                for key, value in result["state_updates"].items():
                    if key in ownership[result["worker"]]:
                        if key in updates:
                            raise ValueError(
                                "Concurrent workers attempted to publish the same output"
                            )
                        updates[key] = deepcopy(value)
        return updates

    # 先保存待回答状态再中断；恢复后校验回答范围，并记录回答或明确暂缓。
    def engineering_input(self, state):
        self.store.update(
            state["job_id"],
            status="waiting_engineering_input",
            current_step="engineering_input",
            pending_engineering=state["pending_engineering"],
            message="Planner needs engineering information to continue. You may explicitly defer to a draft.",
        )
        # 中断恢复会重新进入节点；只有恢复返回值通过合同校验后才写入工程回答。
        response = EngineeringAnswersRequest.model_validate(
            interrupt(
                {"type": "engineering_information", "questions": state["pending_engineering"]}
            )
        )
        pending = {q["task_id"] for q in state["pending_engineering"]}
        provided = {a.task_id for a in response.answers}
        if not provided <= pending or (not response.defer and provided != pending):
            raise ValueError("Answers must cover the pending tasks only")
        answers = {
            **state.get("engineering_answers", {}),
            **{a.task_id: a.answer for a in response.answers},
        }
        return {
            "engineering_answers": answers,
            "deferred_tasks": list(
                set(state.get("deferred_tasks", [])) | (pending if response.defer else set())
            ),
            "pending_engineering": [],
        }

    # 确认必需任务已完成后协调领域审查；必需任务缺失时关闭成功路径。
    def finish_tasks(self, state):
        plan = TaskPlan.model_validate(state["task_plan"])
        valid = current_results(plan, state)
        failed = [
            t.task_id
            for t in plan.tasks
            if t.worker
            in {
                "process_planning",
                "resource_selection",
                "machining_review",
                "quality_review",
                "heat_review",
            }
            and (
                t.task_id not in valid
                or valid[t.task_id]["status"] not in {"succeeded", "infeasible"}
            )
        ]
        board = {
            **state["task_execution"],
            "results": valid,
            "unresolved_tasks": [
                t.task_id
                for t in plan.tasks
                if t.task_id not in valid or valid[t.task_id]["status"] != "succeeded"
            ],
        }
        # 缺少必需结果时明确失败，不能仅凭部分专家完成就进入正常验收。
        if failed:
            return {
                "status": "failed",
                "task_execution": board,
                "verification": {
                    "conclusion": "failed",
                    "message": "Required worker tasks incomplete: " + ", ".join(failed),
                },
                "task_action": "failed",
            }
        merged = coordinate_reviews(state)
        merged.update(task_execution=board, task_action="verify")
        return merged
