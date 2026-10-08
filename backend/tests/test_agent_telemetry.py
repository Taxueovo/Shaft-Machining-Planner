from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json

import pytest

import llm_client
from models.workflow import traced
from observability import summarize_model_calls
from workflow import JobStore


@pytest.fixture
def fake_client(monkeypatch):
    def create(**kwargs):
        if kwargs["messages"][-1]["content"] == "fail":
            raise RuntimeError("credential-marker must not enter telemetry")
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
            model="mock-model",
            system_fingerprint="mock-fingerprint",
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        )

    monkeypatch.setattr(
        llm_client,
        "_get_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    monkeypatch.setattr(
        llm_client, "_runtime_config", lambda: ("https://example.invalid", "test", "mock")
    )


def test_model_usage_is_persisted_without_prompt_text(fake_client):
    store = JobStore(db_path=":memory:")
    store.create("telemetry", {})

    @traced("model")
    def node(self, state):
        return {"answer": llm_client.chat([{"role": "user", "content": "private-input-marker"}])}

    node(SimpleNamespace(store=store), {"job_id": "telemetry"})
    traces = store.get("telemetry")["execution_trace"]
    metrics = summarize_model_calls(traces + traces)
    assert metrics["total_tokens"] == 14 and metrics["call_count"] == 1
    assert metrics["cost_usd"] is None
    assert "private-input-marker" not in json.dumps(traces[0]["model_calls"])


def test_failed_attempt_records_type_and_unknown_usage(fake_client):
    store = JobStore(db_path=":memory:")
    store.create("failure", {})

    @traced("model")
    def node(self, state):
        return {"answer": llm_client.chat([{"role": "user", "content": "fail"}])}

    with pytest.raises(RuntimeError):
        node(SimpleNamespace(store=store), {"job_id": "failure"})
    traces = store.get("failure")["execution_trace"]
    assert traces[0]["model_calls"][0]["error_type"] == "RuntimeError"
    assert "credential-marker" not in json.dumps(traces[0]["model_calls"])
    assert summarize_model_calls(traces)["total_tokens"] is None
    assert summarize_model_calls(traces)["failed_calls"] == 1


def test_parallel_nodes_do_not_share_model_call_records(fake_client):
    store = JobStore(db_path=":memory:")
    store.create("parallel", {})
    owner = SimpleNamespace(store=store)

    @traced("parallel-model")
    def node(self, state):
        return {"answer": llm_client.chat([{"role": "user", "content": state["input"]}])}

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(lambda value: node(owner, {"job_id": "parallel", "input": value}), ["a", "b"])
        )
    traces = store.get("parallel")["execution_trace"]
    assert len(traces) == 2 and all(len(t["model_calls"]) == 1 for t in traces)
    assert summarize_model_calls(traces)["total_tokens"] == 28


def test_rules_only_is_not_reported_as_measured_zero_tokens():
    metrics = summarize_model_calls([])
    assert metrics["call_count"] == 0 and metrics["total_tokens"] is None
    assert not metrics["usage_complete"]
