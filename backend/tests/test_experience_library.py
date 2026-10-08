"""Experience lifecycle, optimistic review, applicability, expiry, and persistent recall."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from experience_library import ExperienceLibrary, ExperienceProposal, ExperienceDecision
from agents.specialists import route_fingerprint
from agent_memory import retrieve_memory
from service import PlanningService, JobCache
from tests.test_production_reviews import workflow_state, request
from workflow import JobStore
from models.process import ProcessOperation


def propose(library, state):
    return library.propose(
        "review-test",
        ExperienceProposal(
            title="Datum recovery lesson",
            lesson="Verify the datum drawing before confirming the support arrangement.",
            route_fingerprint=route_fingerprint(state["process_route"]),
        ),
    )


def decision(version=1, **changes):
    return ExperienceDecision(
        **{
            "expected_version": version,
            "decision": "approved",
            "reviewer": "Test engineer",
            "source_reference": "Fixture procedure FP-001 revision 2",
            "rationale": "Applicable within the recorded input scope.",
            "valid_until": datetime.now(timezone.utc) + timedelta(days=7),
            **changes,
        }
    )


def test_review_required_and_matching_scope_expiry_and_retirement(monkeypatch):
    _, store, state = workflow_state()
    library = ExperienceLibrary(store)
    card = propose(library, state)
    remote = retrieve_memory(state["request"])
    assert not library.snapshot(state["request"], remote)["items"]
    approved = library.review(card["experience_id"], decision())
    snapshot = library.snapshot(state["request"], remote)
    assert snapshot["local_experience_count"] == 1
    assert snapshot["items"][0]["evidence_id"].endswith("@v2")
    assert not library.snapshot({**state["request"], "material": "40Cr"}, remote)["items"]
    changed = deepcopy(state["request"])
    changed["global_requirements"]["heat_treatment"] = "induction_hardening"
    assert not library.snapshot(changed, remote)["items"]
    with pytest.raises(ValueError, match="version changed"):
        library.review(card["experience_id"], decision())
    retired = library.review(card["experience_id"], decision(2, decision="retired"))
    assert retired["version"] == 3 and not library.active()
    assert snapshot["items"][0]["version"] == approved["version"]


def test_approval_rejects_missing_deadline_and_changed_route():
    _, store, state = workflow_state()
    library = ExperienceLibrary(store)
    card = propose(library, state)
    with pytest.raises(ValueError, match="future validity"):
        library.review(card["experience_id"], decision(valid_until=None))
    changed = deepcopy(state)
    changed["process_route"][0]["description"] += " changed"
    store.update("review-test", result=changed, route_revision=1)
    with pytest.raises(ValueError, match="Source route changed"):
        library.review(card["experience_id"], decision())


def test_expired_card_is_not_recalled(monkeypatch):
    _, store, state = workflow_state()
    library = ExperienceLibrary(store)
    card = propose(library, state)
    library.review(card["experience_id"], decision())
    monkeypatch.setattr(
        "experience_library.now", lambda: datetime.now(timezone.utc) + timedelta(days=10)
    )
    assert not library.active()


def test_experience_persists_independently_of_source_job_cleanup(tmp_path):
    _, store, state = workflow_state()
    db_path = str(tmp_path / "jobs.sqlite3")
    persistent = JobStore(db_path=db_path)
    persistent.create("review-test", state["request"])
    persistent.update("review-test", status="completed", result=state)
    library = ExperienceLibrary(persistent)
    card = propose(library, state)
    library.review(card["experience_id"], decision())
    persistent.connection.close()
    restored = JobStore(db_path=db_path)
    try:
        assert (
            ExperienceLibrary(restored).active()[0]["source"]["route_fingerprint"]
            == card["source"]["route_fingerprint"]
        )
    finally:
        restored.connection.close()


def test_real_service_pins_local_experience_and_reuses_original_memory():
    flow, store, state = workflow_state()
    library = ExperienceLibrary(store)
    card = propose(library, state)
    library.review(card["experience_id"], decision())
    svc = PlanningService.__new__(PlanningService)
    svc.store, svc.workflow, svc.experience_library = store, flow, library
    svc.job_cache = JobCache(max_entries=0)
    store.create("recall", request().model_dump())
    svc._initial("recall", request().model_dump())
    job = store.get("recall")
    assert job["status"] == "completed"
    assert job["memory_context"]["local_experience_count"] == 1
    report = job["result"]["quality_review"]
    assert any(key.startswith("experience:") for key in report["evidence"])
    snapshot = deepcopy(job["memory_context"])
    library.review(card["experience_id"], decision(2, decision="retired"))
    svc.customize_route("recall", [ProcessOperation(**op) for op in job["result"]["process_route"]])
    assert store.get("recall")["memory_context"] == snapshot


def test_experience_api_requires_authorization_and_checks_versions(monkeypatch):
    import app as module

    _, store, state = workflow_state()
    monkeypatch.setattr(module.service, "experience_library", ExperienceLibrary(store))
    client = TestClient(module.app)
    assert client.get("/api/v1/experiences").status_code == 401
    headers = {"x-local-api-token": module.LOCAL_API_TOKEN}
    proposal = ExperienceProposal(
        title="Lesson",
        lesson="Check datum applicability before reuse.",
        route_fingerprint=route_fingerprint(state["process_route"]),
    )
    response = client.post(
        "/api/v1/jobs/review-test/experiences", json=proposal.model_dump(), headers=headers
    )
    assert response.status_code == 201
    card = response.json()
    review = client.post(
        f"/api/v1/experiences/{card['experience_id']}/review",
        json=decision().model_dump(mode="json"),
        headers=headers,
    )
    assert review.status_code == 200 and review.json()["status"] == "approved"
    stale = client.post(
        f"/api/v1/experiences/{card['experience_id']}/review",
        json=decision().model_dump(mode="json"),
        headers=headers,
    )
    assert stale.status_code == 409
