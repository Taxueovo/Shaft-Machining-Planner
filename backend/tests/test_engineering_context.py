"""Review context, procedure snapshot, and source authority regression tests."""

from copy import deepcopy
import json

import pytest

from agents.specialists import SpecialistAgent
from engineering_skills import load_skills, job_skills, skill_for
from evidence_context import EvidenceLedger, MAX_CONTEXT_CHARS
from evaluation.trace_grading import grade_traces
from tests.test_production_reviews import workflow_state


def test_large_reference_is_addressable_and_json_remains_valid():
    records = {
        "input": {"dimension": 12.345},
        "route": [{"name": "Keep full route"}],
        "tool_1": {"matches": [{"id": i, "note": "x" * 1000} for i in range(30)]},
    }
    ledger = EvidenceLedger(records)
    view = json.loads(json.dumps(ledger.model_view()))
    assert view["input"] == records["input"] and view["route"] == records["route"]
    assert not view["tool_1"]["complete"]
    assert ledger.read("tool_1", ["matches"], 20, 2)["items"][0]["id"] == 20
    assert ledger.read("tool_1", ["matches"], 20, 2)["next_offset"] == 22
    assert ledger.records["tool_1"] == records["tool_1"]
    with pytest.raises(ValueError, match="not acquired"):
        ledger.read("other-run")
    with pytest.raises(ValueError, match="Unknown evidence path"):
        ledger.read("tool_1", ["invented"])


def test_protected_engineering_input_is_never_silently_summarized():
    with pytest.raises(ValueError, match="context limit"):
        EvidenceLedger({"input": {"drawing": "x" * MAX_CONTEXT_CHARS}}).model_view()


def test_paged_advice_retains_original_authority():
    ledger = EvidenceLedger({"experience:case@v2": {"content": "Reviewed advice"}, "route": []})
    ledger.add("tool_1", ledger.read("experience:case@v2"))
    ledger.add("tool_2", ledger.read("tool_1"))
    assert ledger.reference_only("tool_2")
    assert not ledger.reference_only("route")


def test_job_snapshot_freezes_procedure_contents_across_continuation(monkeypatch):
    flow, store, state = workflow_state()
    with job_skills(store, "review-test"):
        original = skill_for("quality_review")
    changed = deepcopy(load_skills())
    changed["items"]["quality-review"]["instructions"] = "Changed procedure"
    monkeypatch.setattr("engineering_skills.load_skills", lambda: changed)
    with job_skills(store, "review-test"):
        assert skill_for("quality_review") == original
        report = (
            SpecialistAgent(flow, "quality_review").execute(state).state_updates["quality_review"]
        )
        assert report["skill"]["digest"] == original["digest"]


def test_specialist_can_read_an_offloaded_tool_page(monkeypatch):
    flow, _, state = workflow_state()
    import agents.specialists as specialists

    calls = []
    monkeypatch.setattr(specialists, "llm_available", lambda: True)
    large = {"matches": [{"id": i, "note": "x" * 1500} for i in range(20)]}
    monkeypatch.setattr(SpecialistAgent, "_tool", lambda *args: large)

    def respond(messages, **kwargs):
        evidence = json.loads(messages[1]["content"])
        calls.append(evidence)
        if len(calls) == 1:
            return {"tools": [{"name": "query_turning_machines"}]}
        if len(calls) == 2:
            assert not evidence["tool_1"]["complete"]
            return {
                "tools": [
                    {
                        "name": "read_evidence",
                        "evidence_id": "tool_1",
                        "path": ["matches"],
                        "offset": 10,
                        "limit": 1,
                    }
                ]
            }
        assert evidence["tool_2"]["items"][0]["id"] == 10
        return {
            "findings": [
                {
                    "code": "CAPABILITY_REVIEW",
                    "message": "Check candidate 10.",
                    "evidence_ids": ["tool_2"],
                    "recommendation": "Confirm configuration.",
                }
            ]
        }

    monkeypatch.setattr(specialists, "chat_json", respond)
    report = (
        SpecialistAgent(flow, "machining_review").execute(state).state_updates["machining_review"]
    )
    assert report["mode"] == "model_review" and len(report["tool_calls"]) == 2
    assert report["evidence"]["tool_1"] == large


def test_skill_procedure_cannot_supply_route_repair_authority(monkeypatch):
    flow, _, state = workflow_state()
    import agents.specialists as specialists

    eid = skill_for("quality_review")["evidence_id"]
    monkeypatch.setattr(specialists, "llm_available", lambda: True)
    responses = iter(
        [
            {"tools": [{"name": "read_evidence", "evidence_id": eid, "path": ["instructions"]}]},
            {
                "findings": [
                    {
                        "code": "UNSUPPORTED_REPAIR",
                        "message": "Change route.",
                        "severity": "error",
                        "disposition": "route_repair",
                        "operation_nos": [1],
                        "evidence_ids": ["tool_1"],
                        "recommendation": "Change operation 1.",
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(specialists, "chat_json", lambda *a, **k: next(responses))
    report = SpecialistAgent(flow, "quality_review").execute(state).state_updates["quality_review"]
    assert report["mode"] == "degraded"
    assert all(f["source"] == "deterministic" for f in report["findings"])


def test_trace_grading_preserves_failed_attempt_and_stale_evidence():
    state = {
        "agent_collaboration": {"route_fingerprint": "new"},
        "quality_review": {
            "route_fingerprint": "old",
            "mode": "model_review",
            "evidence": {},
            "findings": [{"evidence_ids": ["missing"]}],
            "skill": {"digest": "abc"},
        },
    }
    traces = [
        {"node": "repair", "status": "error", "trace_id": "failed"},
        {"node": "repair", "status": "success", "trace_id": "later"},
    ]
    grading = grade_traces(state, traces)
    assert len(grading["failures"]) == 2
    assert "stale_route_evidence" in grading["failures"][-1]["reasons"]
    assert "unacquired_evidence_citation" in grading["failures"][-1]["reasons"]
