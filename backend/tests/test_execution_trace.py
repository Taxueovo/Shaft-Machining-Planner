from types import SimpleNamespace

import pytest

from models.workflow import traced
from workflow.job_store import JobStore


def test_snapshots_are_detached_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("JOB_DB_FILE", str(tmp_path / "jobs.sqlite3"))
    store = JobStore()
    store.create("job", {})
    owner = SimpleNamespace(store=store)
    state = {"job_id": "job", "request": {"values": [1]}, "execution_trace": [{"old": 1}]}

    @traced("node", ["request"])
    def node(self, state):
        state["request"]["values"].append(2)
        return {"route": state["request"]["values"], "_tool_calls": [{"tool": "lookup"}]}

    result = node(owner, state)
    result["route"].append(3)
    entry = JobStore().get("job")["execution_trace"][0]
    assert entry["inputs"]["request"]["values"] == [1]
    assert entry["outputs"] == {"route": [1, 2]}
    assert "execution_trace" not in entry["inputs"]
    assert entry["tool_calls"] == [{"tool": "lookup"}]
    assert entry["status"] == "success"


def test_failure_trace_survives_result_overwrite(monkeypatch):
    monkeypatch.setenv("JOB_DB_FILE", ":memory:")
    store = JobStore()
    store.create("job", {})

    @traced("broken")
    def node(self, state):
        raise ValueError("bad route")

    with pytest.raises(ValueError, match="bad route"):
        node(SimpleNamespace(store=store), {"job_id": "job", "request": {"x": 1}})
    store.update("job", result={"traceback": "failure"})
    entry = store.get("job")["execution_trace"][0]
    assert entry["inputs"]["request"] == {"x": 1}
    assert entry["outputs"] is None
    assert entry["status"] == "error"
    assert entry["error"] == "bad route"


def test_nested_wrappers_only_record_once(monkeypatch):
    monkeypatch.setenv("JOB_DB_FILE", ":memory:")
    store = JobStore()
    store.create("job", {})

    @traced("inner")
    def inner(self, state):
        return {"answer": 42}

    @traced("outer")
    def outer(self, state):
        return inner(self, state)

    outer(SimpleNamespace(store=store), {"job_id": "job"})
    assert len(store.get("job")["execution_trace"]) == 1


def test_interrupt_and_retry_have_separate_records(monkeypatch):
    from langgraph.errors import GraphInterrupt

    monkeypatch.setenv("JOB_DB_FILE", ":memory:")
    store = JobStore()
    store.create("job", {})
    owner = SimpleNamespace(store=store)

    @traced("choice")
    def node(self, state):
        if not state.get("answer"):
            raise GraphInterrupt(())
        return {"selected": state["answer"]}

    with pytest.raises(GraphInterrupt):
        node(owner, {"job_id": "job"})
    node(owner, {"job_id": "job", "answer": "grind"})
    entries = store.get("job")["execution_trace"]
    assert [e["status"] for e in entries] == ["interrupted", "success"]
    assert entries[0]["trace_id"] != entries[1]["trace_id"]
    assert entries[1]["outputs"] == {"selected": "grind"}
