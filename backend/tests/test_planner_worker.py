"""Behavioral checks for task planning, isolated execution and durable human input."""

from copy import deepcopy
from collections import Counter

import pytest
from langgraph.types import Command
from pydantic import ValidationError

import agents.planner as planner
import workflow.task_scheduler as scheduler
from models.tasks import TaskPlan, EngineeringAnswersRequest
from workflow import Workflow, JobStore
from service import PlanningService
from tests.test_production_reviews import request, workflow_state


def test_plan_adapts_to_part():
    _, _, solid = workflow_state()
    _, _, hollow = workflow_state(request(blank_type="hollow", blank_inner_diameter_mm=20))
    assert "workholding" not in {t["worker"] for t in solid["task_plan"]["tasks"]}
    assert "workholding" in {t["worker"] for t in hollow["task_plan"]["tasks"]}
    assert hollow["worker_results"]["workholding"]["status"] == "needs_input"
    assert hollow["release_status"] == "engineering_review_required"


@pytest.mark.parametrize("mutation", ["cycle", "missing", "tool", "duplicate", "ungated"])
def test_planner_contract_rejects_invalid_dags(mutation):
    _, _, state = workflow_state()
    data = deepcopy(state["task_plan"])
    if mutation == "cycle":
        data["tasks"][0]["depends_on"] = [data["tasks"][1]["task_id"]]
    elif mutation == "missing":
        data["tasks"].pop()
    elif mutation == "tool":
        data["tasks"][0]["allowed_tools"] = ["execute_shell"]
    elif mutation == "duplicate":
        data["tasks"].append(data["tasks"][0])
    else:
        data["tasks"][1]["depends_on"] = []
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(data)


def test_invalid_model_plan_falls_back_with_explicit_mode(monkeypatch):
    _, _, state = workflow_state()
    monkeypatch.setattr(planner, "llm_available", lambda: True)
    monkeypatch.setattr(planner, "chat_json", lambda *a, **k: {"tasks": [], "rationale": "bad"})
    plan, calls, mode, error = planner.propose_plan(state, None, {}, 0)
    assert len(plan.tasks) >= 5 and calls == 1 and mode == "degraded" and error
    _, calls, mode, _ = planner.propose_plan(state, None, {}, 4)
    assert calls == 4 and mode == "budget_exhausted"


def test_model_cannot_rewrite_completed_contract(monkeypatch):
    _, _, state = workflow_state()
    proposed = deepcopy(state["task_plan"])
    proposed["tasks"][0]["objective"] = "Overwrite a completed route"
    monkeypatch.setattr(planner, "llm_available", lambda: True)
    monkeypatch.setattr(planner, "chat_json", lambda *a, **k: proposed)
    plan, _, mode, _ = planner.propose_plan(state, state["task_plan"], state["worker_results"], 0)
    assert mode == "degraded"
    assert plan.tasks[0].objective != proposed["tasks"][0]["objective"]


def test_resource_failure_adds_tasks_and_reuses_completed_work(monkeypatch):
    original = scheduler.execute_contract
    counts = Counter()

    def execute(flow, task, state, version, attempt):
        counts[task.worker] += 1
        result = original(flow, task, state, version, attempt)
        if task.worker == "resource_selection":
            result.status = "infeasible"
            result.state_updates["capability"]["machine"]["conclusion"] = "not_satisfied"
        return result

    monkeypatch.setattr(scheduler, "execute_contract", execute)
    _, _, state = workflow_state()
    assert {"alternative_resources", "workholding"} <= set(counts)
    assert all(n == 1 for n in counts.values())
    assert state["worker_results"]["alternative_resources"]["status"] == "needs_input"
    assert not state["worker_results"]["alternative_resources"]["artifact"][
        "external_provider_verified"
    ]


def test_tool_failure_retries_once_then_fails_closed(monkeypatch):
    original = scheduler.execute_contract
    counts = Counter()

    def execute(flow, task, state, version, attempt):
        counts[task.worker] += 1
        result = original(flow, task, state, version, attempt)
        if task.worker == "process_planning":
            result.status = "tool_failed"
        return result

    monkeypatch.setattr(scheduler, "execute_contract", execute)
    _, _, state = workflow_state()
    assert counts == {"process_planning": 2}
    assert state["status"] == "failed"
    assert "incomplete" in state["verification"]["message"]


def test_dependency_changes_invalidate_descendants():
    _, _, state = workflow_state()
    plan = TaskPlan.model_validate(state["task_plan"])
    assert len(scheduler.current_results(plan, state)) == 5
    state["worker_results"]["process_planning"]["artifact"]["changed"] = True
    assert set(scheduler.current_results(plan, state)) == {"process_planning"}
    state["request"]["material"] = "40Cr"
    assert not scheduler.current_results(plan, state)


@pytest.mark.parametrize("defer", [False, True])
def test_durable_engineering_pause_and_resume_reuses_work(tmp_path, monkeypatch, defer):
    monkeypatch.setenv("JOB_DB_FILE", str(tmp_path / "jobs.sqlite3"))
    original_plan = planner.baseline_plan

    def blocking(state):
        plan = original_plan(state)
        next(t for t in plan.tasks if t.worker == "workholding").blocking = True
        return plan

    monkeypatch.setattr(planner, "baseline_plan", blocking)
    original_execute = scheduler.execute_contract
    counts = Counter()

    def execute(flow, task, state, version, attempt):
        counts[task.worker] += 1
        return original_execute(flow, task, state, version, attempt)

    monkeypatch.setattr(scheduler, "execute_contract", execute)
    req = request(blank_type="hollow", blank_inner_diameter_mm=20).model_dump()
    store = JobStore()
    store.create("durable", req)
    first = Workflow(store)
    config = {"configurable": {"thread_id": "durable"}}
    paused = first.graph.invoke({"job_id": "durable", "request": req}, config)
    assert paused["__interrupt__"]
    assert store.get("durable")["status"] == "waiting_engineering_input"
    first.checkpoint_connection.close()
    store.connection.close()
    restored = Workflow(JobStore())
    response = (
        {"defer": True}
        if defer
        else {
            "answers": [{"task_id": "workholding", "answer": "Mandrel drawing supplied for review"}]
        }
    )
    finished = restored.graph.invoke(Command(resume=response), config)
    assert not finished.get("__interrupt__")
    assert finished["status"] == "completed"
    assert all(counts[role] == 1 for role in counts if role != "workholding")
    assert counts["workholding"] == (1 if defer else 2)
    assert finished["release_status"] == "engineering_review_required"
    if not defer:
        assert not finished["worker_results"]["workholding"]["artifact"]["statement_verified"]
    assert (tmp_path / "jobs.sqlite3").stat().st_mode & 0o777 == 0o600
    restored.checkpoint_connection.close()
    restored.store.connection.close()


def test_service_rejects_invalid_answers_before_resuming():
    service = PlanningService.__new__(PlanningService)
    service.store = JobStore()
    service.store.create("paused", request().model_dump())
    service.store.update(
        "paused",
        status="waiting_engineering_input",
        pending_engineering=[{"task_id": "workholding"}],
    )
    with pytest.raises(ValueError, match="pending tasks"):
        service.resume_engineering(
            "paused", EngineeringAnswersRequest(answers=[{"task_id": "unknown", "answer": "x"}])
        )
    assert service.store.get("paused")["status"] == "waiting_engineering_input"


def test_valid_model_plan_can_add_blocking_worker(monkeypatch):
    _, _, state = workflow_state()
    proposed = planner.baseline_plan(state)
    proposed.tasks.append(planner.make_task("workholding", ["process_planning"], blocking=True))
    monkeypatch.setattr(planner, "llm_available", lambda: True)
    monkeypatch.setattr(planner, "chat_json", lambda *a, **k: proposed.model_dump())
    plan, calls, mode, error = planner.propose_plan(state, None, {}, 0)
    assert mode == "model_plan" and calls == 1 and error is None
    assert next(t for t in plan.tasks if t.worker == "workholding").blocking


def test_worker_cannot_call_tool_outside_contract():
    from workflow.task_workers import execute_contract

    flow, _, state = workflow_state()
    task = planner.make_task("resource_selection", ["process_planning"])
    task.allowed_tools = []
    result = execute_contract(flow, task, state, "test", 1)
    assert result.status == "tool_failed"
    assert "not authorized" in result.summary
    assert result.tool_calls == []


def test_collector_ignores_worker_writes_to_unowned_fields():
    flow, _, state = workflow_state()
    state["worker_results"]["quality_review"]["state_updates"]["request"] = {"material": "invented"}
    updates = flow.collect_tasks(state)
    assert "request" not in updates
    assert state["request"]["material"] == "45"
