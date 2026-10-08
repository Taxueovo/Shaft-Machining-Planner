"""Runtime failure injection: real graph boundaries, durable counters and cancellation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from langgraph.types import Command
from fastapi.testclient import TestClient

import agents.planner as planner
import llm_client
import workflow.task_scheduler as scheduler
from models.input import ChoicesRequest
from models.tasks import EngineeringAnswersRequest
from models.workflow import traced
from service import PlanningService, JobCache
from workflow import JobStore, Workflow
from workflow.harness import (
    RunControl,
    HarnessPolicy,
    HarnessStopped,
    classify_failure,
    ToolPolicyError,
    execute_action,
)
from tests.test_production_reviews import request


@pytest.fixture
def runtime():
    store = JobStore(db_path=":memory:")
    payload = request().model_dump()
    store.create("run", payload)
    flow = Workflow(store)
    service = PlanningService.__new__(PlanningService)
    service.store, service.workflow = store, flow
    service.job_cache = JobCache(max_entries=0)
    from experience_library import ExperienceLibrary

    service.experience_library = ExperienceLibrary(store)
    yield flow, store, service, payload
    flow.checkpoint_connection.close()
    store.connection.close()


def test_real_graph_shares_control_across_parallel_workers(runtime):
    flow, store, _, payload = runtime
    result = flow.invoke(
        {"job_id": "run", "request": payload}, {"configurable": {"thread_id": "run"}}
    )
    h = store.get("run")["harness"]
    traces = store.get("run")["execution_trace"]
    assert result["status"] == "completed" and not h["active"]
    assert h["usage"]["nodes"] == len(traces)
    assert h["usage"]["tool_calls"] > 0 and h["usage"]["model_calls"] == 0
    assert all(t["run_id"] == h["run_id"] for t in traces)
    assert all(t["invocation_id"] == h["invocation_id"] for t in traces)


def test_route_action_checks_time_before_publication(runtime, monkeypatch):
    flow, store, _, _ = runtime
    store.update("run", status="completed", result={"process_route": ["original"]})
    clock = [0.0]
    monkeypatch.setattr("workflow.harness.time.monotonic", lambda: clock[0])
    monkeypatch.setenv("HARNESS_MAX_ACTIVE_SECONDS", "1")
    published = []

    def prepare():
        clock[0] = 2.0
        return {"process_route": ["candidate"]}

    with pytest.raises(ValueError, match="active_time_budget_exhausted"):
        execute_action(flow, "run", prepare, publish=published.append)
    assert not published
    assert store.get("run")["result"]["process_route"] == ["original"]
    assert not store.get("run")["harness"]["active"]


def test_model_planner_not_called_on_ordinary_waves(runtime, monkeypatch):
    flow, _, _, payload = runtime
    calls = []
    monkeypatch.setattr(planner, "llm_available", lambda: True)

    def proposal(*args, **kwargs):
        calls.append(1)
        return planner.baseline_plan(
            {
                "request": payload,
                "geometry": {"total_length_mm": 100, "segments": [], "features": []},
            }
        ).model_dump()

    monkeypatch.setattr(planner, "chat_json", proposal)
    result = flow.invoke(
        {"job_id": "run", "request": payload}, {"configurable": {"thread_id": "run"}}
    )
    assert len(calls) == result["planner_calls"] == 1
    assert result["scheduler_waves"] > 1
    assert any(e["mode"] == "reuse_plan" for e in result["planner_events"])


def test_decision_changes_only_for_meaningful_evidence():
    state = {"request": {"material": "45"}}
    base = planner.planning_decision_key(state, {})
    assert planner.planning_decision_key({**state, "scheduler_waves": 3}, {}) == base
    assert planner.planning_decision_key({**state, "repair_count": 1}, {}) != base
    assert (
        planner.planning_decision_key({**state, "engineering_answers": {"fixture": "drawing"}}, {})
        != base
    )
    assert (
        planner.planning_decision_key(
            state,
            {
                "resources": {
                    "worker": "resource_selection",
                    "status": "infeasible",
                    "artifact": {"gaps": [1]},
                }
            },
        )
        != base
    )


def test_parallel_reservations_never_exceed_budget(runtime):
    _, store, _, _ = runtime
    control = RunControl(store, "run", HarnessPolicy(max_model_calls=3))
    control.start()

    def reserve(_):
        try:
            control.reserve("model_calls", "fake")
            return True
        except HarnessStopped:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(20))) == 3
    assert store.get("run")["harness"]["usage"]["model_calls"] == 3
    control.finish("finished")


def test_service_stops_on_node_budget_without_publishing_result(runtime, monkeypatch):
    _, store, service, payload = runtime
    monkeypatch.setenv("HARNESS_MAX_NODES", "2")
    service._invoke("run", {"job_id": "run", "request": payload})
    job = store.get("run")
    assert job["status"] == "failed" and job["result"] is None
    assert job["error"] == "nodes_budget_exhausted"
    assert job["harness"]["usage"]["nodes"] == 2 and not job["harness"]["active"]


def test_active_time_budget_is_persisted_and_not_reset(runtime):
    _, store, _, _ = runtime
    control = RunControl(store, "run", HarnessPolicy(max_active_seconds=1))
    control.start()
    control.finish("waiting")
    h = store.get("run")["harness"]
    h["active_seconds"] = 2
    store.update("run", harness=h)
    resumed = RunControl(store, "run")
    resumed.start()
    with pytest.raises(HarnessStopped, match="active_time"):
        resumed.check()
    assert resumed.policy.max_active_seconds == 1
    resumed.finish("failed")


def test_cancel_running_graph_cannot_publish_late_result(runtime, monkeypatch):
    _, store, service, payload = runtime
    entered, release = Event(), Event()
    original = scheduler.execute_contract

    def slow(flow, task, state, version, attempt):
        if task.worker == "process_planning":
            entered.set()
            assert release.wait(5)
        return original(flow, task, state, version, attempt)

    monkeypatch.setattr(scheduler, "execute_contract", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service._invoke, "run", {"job_id": "run", "request": payload})
        assert entered.wait(5)
        try:
            assert service.cancel("run") == "cancelling"
        finally:
            release.set()
        future.result(timeout=5)
    job = store.get("run")
    assert job["status"] == "cancelled" and job["result"] is None
    assert not job["harness"]["active"] and job["error"] == "cancel_requested"


def test_cancel_queued_job_prevents_execution(runtime):
    _, store, service, payload = runtime
    assert service.cancel("run") == "cancelled"
    service._invoke("run", {"job_id": "run", "request": payload})
    job = store.get("run")
    assert job["status"] == "cancelled" and job["result"] is None
    assert "execution_trace" not in job


@pytest.mark.parametrize(
    "error,retryable",
    [
        (TimeoutError("temporary"), True),
        (ValueError("invalid"), False),
        (ToolPolicyError("denied"), False),
    ],
)
def test_failure_classification_controls_real_worker_retries(
    runtime, monkeypatch, error, retryable
):
    flow, _, _, payload = runtime
    calls = []

    def fail(state):
        calls.append(1)
        raise error

    monkeypatch.setattr(flow, "process_planning", fail)
    result = flow.invoke(
        {"job_id": "run", "request": payload}, {"configurable": {"thread_id": "run"}}
    )
    failed = result["worker_results"]["process_planning"]
    assert result["status"] == "failed" and failed["retryable"] is retryable
    assert len(calls) == (2 if retryable else 1)
    assert failed["error_category"] == classify_failure(error)[0]


def test_manifest_change_refuses_checkpoint_reuse(runtime):
    flow, store, _, payload = runtime
    flow.invoke({"job_id": "run", "request": payload}, {"configurable": {"thread_id": "run"}})
    store.update("run", request={**payload, "material": "40Cr"})
    with pytest.raises(HarnessStopped, match="configuration_changed"):
        flow.invoke(None, {"configurable": {"thread_id": "run"}})


def test_durable_resume_keeps_budget_and_run_id(runtime, monkeypatch, tmp_path):
    flow, store, service, _ = runtime
    payload = request(
        global_requirements={"heat_treatment": "quench_temper"},
        features=[
            dict(
                feature_id="F1",
                feature_type="keyway",
                positioning_mode="global_absolute",
                global_position_mm=20,
                keyway_width_mm=8,
                keyway_depth_mm=3,
                feature_length_mm=20,
                roughness_ra=0.4,
            )
        ],
    ).model_dump()
    store.update("run", request=payload)
    monkeypatch.setenv("HARNESS_MAX_NODES", "4")
    import agent_memory

    original_read = agent_memory.retrieve_memory
    reads = []

    def memory_read(req):
        reads.append(1)
        return original_read(req)

    monkeypatch.setattr(agent_memory, "retrieve_memory", memory_read)
    service._invoke("run", {"job_id": "run", "request": payload})
    before = store.get("run")
    assert before["status"] == "waiting_user_choice"
    monkeypatch.setenv("AGENT_PROMPT_PROFILE", str(tmp_path / "nonexistent-profile.json"))
    response = {
        "choices": [
            {"feature_id": "F1", "processing_timing": before["pending_choices"][0]["recommended"]}
        ]
    }
    service._invoke("run", Command(resume=response))
    after = store.get("run")
    assert after["status"] == "failed" and after["error"] == "nodes_budget_exhausted"
    assert after["harness"]["run_id"] == before["harness"]["run_id"]
    assert after["harness"]["invocations"] == 2 and after["harness"]["usage"]["nodes"] == 4
    assert len(reads) == 1 and after["prompt_snapshot"] == before["prompt_snapshot"]


def test_custom_route_budget_failure_preserves_published_plan(runtime, monkeypatch):
    from models.process import ProcessOperation

    flow, store, service, payload = runtime
    service._invoke("run", {"job_id": "run", "request": payload})
    before = store.get("run")
    h = before["harness"]
    h["usage"]["tool_calls"] = h["policy"]["max_tool_calls"]
    store.update("run", harness=h)
    with pytest.raises(ValueError, match="tool_calls_budget_exhausted"):
        service.customize_route(
            "run", [ProcessOperation(**op) for op in before["result"]["process_route"]]
        )
    after = store.get("run")
    assert after["result"] == before["result"] and not after.get("custom_result")
    assert after["status"] == "completed" and not after["harness"]["active"]


def test_evaluation_budget_stop_becomes_inspectable_badcase(monkeypatch):
    from evaluation.harness import EvalCase, run_case

    monkeypatch.setenv("HARNESS_MAX_NODES", "1")
    case = EvalCase(
        case_id="runtime-limit",
        family="test-limit",
        split="test",
        request=request().model_dump(),
        expected={},
    )
    report = run_case(case)
    assert not report["passed"] and report["outcome"] == "failed"
    assert report["runtime_harness"]["usage"]["nodes"] == 1
    assert any("nodes_budget_exhausted" in f for f in report["failures"])


def test_resume_is_atomic_and_submission_failure_restores_wait(runtime, monkeypatch):
    _, store, service, _ = runtime
    store.update(
        "run", status="waiting_engineering_input", pending_engineering=[{"task_id": "workholding"}]
    )
    monkeypatch.setattr(service, "_has_checkpoint", lambda _: True)
    submitted = []
    service.executor = SimpleNamespace(submit=lambda *args: submitted.append(args))
    answers = EngineeringAnswersRequest(defer=True)

    def resume(_):
        try:
            service.resume_engineering("run", answers)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(resume, range(2))) == 1
    assert len(submitted) == 1
    store.update(
        "run", status="waiting_engineering_input", pending_engineering=[{"task_id": "workholding"}]
    )

    def stopped(*args):
        raise RuntimeError("executor unavailable")

    service.executor = SimpleNamespace(submit=stopped)
    with pytest.raises(ValueError, match="queue continuation"):
        service.resume_engineering("run", answers)
    assert store.get("run")["status"] == "waiting_engineering_input"
    assert store.get("run")["pending_engineering"] == [{"task_id": "workholding"}]


def test_unsupported_choice_rejected_without_consuming_checkpoint(runtime):
    _, store, service, _ = runtime
    store.update(
        "run",
        status="waiting_user_choice",
        pending_choices=[
            {"feature_id": "F1", "options": [{"value": "before_and_after_heat_treatment"}]}
        ],
    )
    with pytest.raises(ValueError, match="allowed options"):
        service.resume(
            "run",
            ChoicesRequest(
                choices=[{"feature_id": "F1", "processing_timing": "before_heat_treatment"}]
            ),
        )
    assert store.get("run")["status"] == "waiting_user_choice"


def test_idempotent_admission_and_queue_limit(runtime, monkeypatch):
    _, store, service, _ = runtime
    store.update("run", status="completed")
    monkeypatch.setenv("HARNESS_MAX_PENDING_JOBS", "1")
    submitted = []
    service.executor = SimpleNamespace(submit=lambda *args: submitted.append(args))
    jid = service.create(request(), "retry-key")
    assert service.create(request(), "retry-key") == jid and len(submitted) == 1
    with pytest.raises(ValueError, match="different input"):
        service.create(request(material="40Cr"), "retry-key")
    with pytest.raises(ValueError, match="queue is full"):
        service.create(request())


def test_model_calls_are_bounded_and_sdk_retries_disabled(runtime, monkeypatch):
    flow, store, _, payload = runtime
    options, calls = [], []

    class Client:
        def with_options(self, **kw):
            options.append(kw)
            return self

        @property
        def chat(self):
            return SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(
                usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
            )

    monkeypatch.setattr(llm_client, "_get_client", lambda: Client())
    monkeypatch.setattr(
        llm_client, "_runtime_config", lambda: ("https://example.invalid", "test", "mock")
    )
    monkeypatch.setenv("HARNESS_MAX_MODEL_CALLS", "1")

    @traced("fake_model")
    def run(self, state):
        llm_client.chat(
            [{"role": "user", "content": "synthetic"}], timeout_seconds=120, max_tokens=16000
        )
        llm_client.chat([{"role": "user", "content": "synthetic"}])

    monkeypatch.setattr(
        flow.graph, "invoke", lambda *a, **kw: run(flow, {"job_id": "run", "request": payload})
    )
    with pytest.raises(HarnessStopped, match="model_calls_budget"):
        flow.invoke({"job_id": "run", "request": payload}, {"configurable": {"thread_id": "run"}})
    assert len(calls) == 1 and options[0]["max_retries"] == 0
    assert options[0]["timeout"] <= 30 and calls[0]["max_tokens"] == 4096
    assert store.get("run")["harness"]["usage"]["model_calls"] == 1


def test_api_cancel_and_harness_require_auth_and_exclude_drawing(runtime, monkeypatch):
    import app as api

    _, store, service, _ = runtime
    monkeypatch.setattr(api, "service", service)
    with TestClient(api.app) as client:
        assert client.post("/api/v1/jobs/run/cancel").status_code == 401
        headers = {"x-local-api-token": api.LOCAL_API_TOKEN}
        assert (
            client.post("/api/v1/jobs/run/cancel", headers=headers).json()["status"] == "cancelled"
        )
        status = client.get("/api/v1/jobs/run/harness", headers=headers)
        assert status.status_code == 200 and "blank_diameter_mm" not in status.text
        assert client.get("/api/v1/jobs/missing/harness", headers=headers).status_code == 404


def test_worker_send_snapshot_omits_unrelated_history_and_versions_match(runtime):
    flow, _, _, payload = runtime
    result = flow.invoke(
        {"job_id": "run", "request": payload}, {"configurable": {"thread_id": "run"}}
    )
    plan = planner.TaskPlan.model_validate(result["task_plan"])
    task = next(t for t in plan.tasks if t.worker == "quality_review")
    snapshot = scheduler.worker_snapshot(task, result)
    assert (
        not {"execution_trace", "planner_events", "task_execution", "machining_review"}
        & snapshot.keys()
    )
    assert snapshot["worker_results"].keys() == set(task.depends_on)
    assert scheduler.context_version(task, snapshot) == scheduler.context_version(task, result)
