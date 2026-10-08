"""Meaningful local product contracts: history, restart recovery and safe status."""

import io
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import app, LOCAL_API_TOKEN
import app as backend_app
from frontend.main import app as frontend_app, LOCAL_API_TOKEN as frontend_token
from service import PlanningService, HEARTBEAT_TIMEOUT
from workflow import JobStore
from experience_library import ExperienceLibrary
from tests.test_production_reviews import request


def test_history_search_pagination_and_summary_excludes_evidence():
    store = JobStore(db_path=":memory:")
    try:
        for jid, title, status in [
            ("a", "主轴 A", "completed"),
            ("b", "主轴 B", "running"),
            ("c", "传动轴", "waiting_user_choice"),
        ]:
            store.create(jid, request(part_name=title).model_dump())
            store.update(
                jid,
                status=status,
                execution_trace=[{"content": "private-evidence"}],
                error="private-error",
            )
        page = store.list_jobs(search="主轴", limit=1)
        assert page["total"] == 2 and len(page["items"]) == 1
        assert (
            store.list_jobs(search="主轴", limit=1, offset=1)["items"][0]["job_id"]
            != page["items"][0]["job_id"]
        )
        assert store.list_jobs(status="active")["items"][0]["job_id"] == "b"
        assert store.list_jobs(status="waiting")["items"][0]["job_id"] == "c"
        assert "private-" not in str(page) and "request" not in page["items"][0]
        page["counts"]["running"] = 100
        assert store.list_jobs()["counts"]["running"] == 1
    finally:
        store.connection.close()


def test_restart_exposes_inflight_jobs_and_preserves_pending_questions(tmp_path):
    path = str(tmp_path / "jobs.sqlite")
    store = JobStore(db_path=path)
    for jid, status in [("a", "running"), ("b", "queued"), ("c", "waiting_engineering_input")]:
        store.create(jid, request().model_dump())
        store.update(
            jid,
            status=status,
            pending_engineering=[{"task_id": "workholding", "question": "夹具资料"}],
        )
    store.connection.close()
    reopened = JobStore(db_path=path)
    try:
        assert reopened.interrupt_inflight() == 2
        assert reopened.get("a")["status"] == "interrupted"
        assert reopened.get("c")["status"] == "waiting_engineering_input"
        assert reopened.get("c")["pending_engineering"][0]["question"] == "夹具资料"
        assert reopened.interrupt_inflight() == 0
        assert reopened.stats()["active_jobs"] == 1
    finally:
        reopened.connection.close()


def test_idle_shutdown_is_opt_in_and_never_interrupts_active_work():
    service = object.__new__(PlanningService)
    service.store = JobStore(db_path=":memory:")
    service.auto_shutdown_on_idle = False
    elapsed = HEARTBEAT_TIMEOUT + 1
    try:
        assert not service.should_shutdown_for_idle(elapsed)
        service.auto_shutdown_on_idle = True
        assert service.should_shutdown_for_idle(elapsed)
        service.store.create("active", {})
        assert not service.should_shutdown_for_idle(elapsed)
        service.store.update("active", status="waiting_engineering_input")
        assert not service.should_shutdown_for_idle(elapsed)
    finally:
        service.store.connection.close()


def test_api_history_input_validation_and_system_is_private(monkeypatch):
    store = JobStore(db_path=":memory:")
    payload = request(part_name="车间主轴").model_dump()
    store.create("saved", payload)
    monkeypatch.setattr(
        backend_app,
        "service",
        SimpleNamespace(
            store=store,
            auto_shutdown_on_idle=False,
            experience_library=ExperienceLibrary(store),
        ),
    )
    monkeypatch.setenv("AGENT_MEMORY_API_KEY", "credential-marker")
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/jobs").status_code == 401
            headers = {"x-local-api-token": LOCAL_API_TOKEN}
            assert client.get("/api/v1/jobs?limit=101", headers=headers).status_code == 422
            assert client.get("/api/v1/jobs?status=made_up", headers=headers).status_code == 422
            assert client.get("/api/v1/jobs?search=车间", headers=headers).json()["total"] == 1
            assert (
                client.get("/api/v1/jobs/saved/input", headers=headers).json()["request"] == payload
            )
            assert client.get("/api/v1/jobs/missing/input", headers=headers).status_code == 404
            status = client.get("/api/v1/system", headers=headers)
            assert status.json()["product"]["version"] == app.version
            assert "credential-marker" not in status.text and "endpoint" not in status.text
            assert client.get("/health").json()["status"] == "ok"
    finally:
        store.connection.close()


def test_frontend_shell_and_proxy_preserve_filters_without_token_in_html():
    seen = []

    def handle(req):
        seen.append(req)
        return httpx.Response(200, json={"status": "ok", "items": [], "total": 0})

    with TestClient(frontend_app) as client:
        for path in ["/jobs", "/system", "/custom", "/jobs/saved", "/cases", "/taxonomy", "/rag"]:
            # Render without connecting to a live backend (custom also probes health).
            with pytest.MonkeyPatch.context() as patch:
                original = frontend_app.state.backend
                mock = httpx.AsyncClient(
                    base_url="http://backend.invalid", transport=httpx.MockTransport(handle)
                )
                patch.setattr(frontend_app.state, "backend", mock)
                page = client.get(path)
            assert page.status_code == 200 and 'aria-label="主导航"' in page.text
            assert frontend_token not in page.text and 'id="main-content"' in page.text
            # Closing the mock client through the TestClient event loop avoids leaked transports.
            client.portal.call(mock.aclose)
        frontend_app.state.backend = httpx.AsyncClient(
            base_url="http://backend.invalid", transport=httpx.MockTransport(handle)
        )
        client.portal.call(original.aclose)
        client.get("/api/jobs?status=waiting&search=45&offset=20")
        forwarded = seen[-1]
        assert forwarded.url.path == "/api/v1/jobs" and forwarded.url.params["offset"] == "20"
        assert forwarded.headers["x-local-api-token"] == frontend_token


def test_launcher_does_not_accept_an_unrelated_server(monkeypatch):
    import start_shaftplanner as launcher

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(
        launcher.urllib.request, "urlopen", lambda *a, **kw: Response(b'{"name":"other-app"}')
    )
    assert not launcher._ready("http://local.invalid", 0.01)
    monkeypatch.setattr(
        launcher.urllib.request,
        "urlopen",
        lambda *a, **kw: Response(b'{"name":"shaftmachiningplanner"}'),
    )
    assert launcher._ready("http://local.invalid", 0.01)
    monkeypatch.setattr(launcher, "_ready", lambda *a: True)
    assert launcher._wait_all([("backend", "http://local.invalid")], 0.1)
    assert not launcher._wait_all([("backend", "http://local.invalid")], 0)
