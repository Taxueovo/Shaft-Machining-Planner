from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware


FRONTEND_DIR = Path(__file__).resolve().parent

# Unified environment configuration lives in the project-root .env (one level above frontend)
load_dotenv(FRONTEND_DIR.parent / ".env")
load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")
#: cad_agent service address (HTTP target for the frontend "Import from CAD" feature)
CAD_AGENT_URL = os.getenv("CAD_AGENT_URL", "http://127.0.0.1:8100")

#: In-memory CAD session store — keeps render images imported from CAD for the result page 3D view
_cad_sessions: dict[str, dict[str, Any]] = {}
_cad_session_lock = threading.Lock()


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Middleware to disable static file caching."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.backend = httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=90.0,
    )
    app.state.cad_agent = httpx.AsyncClient(
        base_url=CAD_AGENT_URL,
        timeout=180.0,
    )
    yield
    await app.state.backend.aclose()
    await app.state.cad_agent.aclose()


app = FastAPI(title="ShaftPlanner Frontend", version="1.0.0", lifespan=lifespan)
app.add_middleware(NoCacheMiddleware)
templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))
app.mount(
    "/static",
    StaticFiles(directory=str(FRONTEND_DIR / "static")),
    name="static",
)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    backend_ok = False
    detail = ""
    try:
        response = await request.app.state.backend.get("/health")
        response.raise_for_status()
        data = response.json()
        backend_ok = data.get("status") in {"ok", "degraded"}
        if data.get("status") == "degraded":
            detail = "Backend started, but the capability library file check failed."
    except Exception as error:
        detail = f"Backend connection failed: {error}"

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"backend_ok": backend_ok, "backend_detail": detail},
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="job.html",
        context={"job_id": job_id},
    )


async def forward(
    request: Request,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        response = await request.app.state.backend.request(
            method,
            path,
            json=payload,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        try:
            detail = error.response.json()
        except ValueError:
            detail = error.response.text
        raise HTTPException(
            status_code=error.response.status_code,
            detail=detail,
        ) from error
    except httpx.RequestError as error:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to backend service: {error}",
        ) from error


def with_query(request: Request, path: str) -> str:
    """Preserve browser query parameters when proxying GET requests."""
    return f"{path}?{request.url.query}" if request.url.query else path


@app.post("/api/jobs")
async def create_job(request: Request) -> dict[str, Any]:
    return await forward(
        request,
        "POST",
        "/api/v1/jobs",
        await request.json(),
    )


@app.get("/api/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(request, "GET", f"/api/v1/jobs/{job_id}")


@app.post("/api/jobs/{job_id}/choices")
async def submit_choices(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(
        request,
        "POST",
        f"/api/v1/jobs/{job_id}/choices",
        await request.json(),
    )


@app.get("/api/jobs/{job_id}/result")
async def get_result(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(request, "GET", f"/api/v1/jobs/{job_id}/result")


@app.post("/api/jobs/{job_id}/process-card/export")
async def export_process_card(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(request, "POST", f"/api/v1/jobs/{job_id}/process-card/export")


@app.post("/api/jobs/{job_id}/process-route/customize")
async def customize_process_route(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(
        request, "POST", f"/api/v1/jobs/{job_id}/process-route/customize", await request.json()
    )


@app.delete("/api/jobs/{job_id}/process-route/customization")
async def reset_process_route(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(request, "DELETE", f"/api/v1/jobs/{job_id}/process-route/customization")


@app.post("/api/preview-route")
async def preview_route(request: Request) -> dict[str, Any]:
    return await forward(request, "POST", "/api/v1/preview-route", await request.json())


@app.get("/api/materials")
async def list_materials(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/materials")


@app.get("/api/materials/price")
async def get_material_price(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, "/api/v1/materials/price"))


@app.get("/api/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/tools")


@app.post("/api/tools/{tool_name}")
async def call_tool(request: Request, tool_name: str) -> dict[str, Any]:
    return await forward(
        request, "POST", f"/api/v1/tools/{tool_name}", await request.json()
    )


@app.get("/api/agents")
async def list_agents(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/agents")


@app.get("/api/orchestrator/status")
async def orchestrator_status(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/orchestrator/status")


@app.get("/api/prompts")
async def list_prompts(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/prompts")


# ============================================================
# Taxonomy & Case Library Pages
# ============================================================


@app.get("/taxonomy", response_class=HTMLResponse)
async def taxonomy_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="taxonomy.html",
    )


@app.get("/cases", response_class=HTMLResponse)
async def cases_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="cases.html",
    )


@app.get("/cases/{case_id}", response_class=HTMLResponse)
async def case_detail_page(request: Request, case_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="case_detail.html",
        context={"case_id": case_id},
    )


@app.get("/custom", response_class=HTMLResponse)
async def custom_planning_page(request: Request) -> HTMLResponse:
    backend_ok = False
    detail = ""
    try:
        response = await request.app.state.backend.get("/health")
        response.raise_for_status()
        data = response.json()
        backend_ok = data.get("status") in {"ok", "degraded"}
    except Exception as error:
        detail = f"Backend connection failed: {error}"

    return templates.TemplateResponse(
        request=request,
        name="custom.html",
        context={"backend_ok": backend_ok, "backend_detail": detail},
    )


# ============================================================
# Taxonomy & Case Library API Proxies
# ============================================================


@app.get("/api/taxonomy")
async def get_taxonomy(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/taxonomy")


@app.get("/api/taxonomy/{node_id}")
async def get_taxonomy_node(request: Request, node_id: str) -> dict[str, Any]:
    return await forward(request, "GET", f"/api/v1/taxonomy/{node_id}")


@app.get("/api/taxonomy/{node_id}/cases")
async def get_taxonomy_cases(request: Request, node_id: str) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, f"/api/v1/taxonomy/{node_id}/cases"))


@app.get("/api/cases")
async def list_cases(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, "/api/v1/cases"))


@app.get("/api/cases/filters")
async def get_case_filters(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/cases/filters")


@app.get("/api/cases/{case_id}")
async def get_case(request: Request, case_id: str) -> dict[str, Any]:
    return await forward(request, "GET", f"/api/v1/cases/{case_id}")


@app.post("/api/cases/save-from-form")
async def save_case_from_form(request: Request) -> dict[str, Any]:
    return await forward(
        request,
        "POST",
        "/api/v1/cases/save-from-form",
        await request.json(),
    )


# ============================================================
# RAG Management Page (optional module — the page shows a notice when the backend RAG is unavailable)
# ============================================================


@app.get("/rag", response_class=HTMLResponse)
async def rag_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="rag.html")


@app.get("/api/rag/status")
async def rag_status(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/rag/status")


@app.post("/api/rag/build")
async def rag_build(request: Request) -> dict[str, Any]:
    return await forward(request, "POST", with_query(request, "/api/v1/rag/build"))


@app.delete("/api/rag/clear")
async def rag_clear(request: Request) -> dict[str, Any]:
    return await forward(request, "DELETE", with_query(request, "/api/v1/rag/clear"))


@app.get("/api/rag/search")
async def rag_search(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, "/api/v1/rag/search"))


@app.get("/api/rag/chunks")
async def rag_chunks(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, "/api/v1/rag/chunks"))


@app.get("/api/rag-health")
async def rag_health(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/rag-health")


@app.post("/api/heartbeat")
async def heartbeat(request: Request) -> dict[str, str]:
    """Forward heartbeat to backend watchdog."""
    try:
        await request.app.state.backend.post("/api/v1/heartbeat")
    except Exception:
        pass
    return {"status": "ok"}


@app.post("/api/shutdown")
async def shutdown(request: Request) -> dict[str, str]:
    """Shutdown backend and frontend services."""
    import os
    import signal
    import asyncio

    # 1. Notify backend to shutdown
    try:
        await request.app.state.backend.post("/api/v1/shutdown")
    except Exception:
        pass  # Backend may already be shutdown

    # 2. Delay frontend shutdown (let response send first)
    async def _delayed_shutdown() -> None:
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_delayed_shutdown())
    return {"status": "shutting_down", "message": "System is shutting down."}


# ============================================================
# CAD session (stores CAD import render images)
# After the form page imports CAD, the render images are kept in memory; the result page fetches them by token for 3D display.
# ============================================================


@app.get("/api/cad-agent-status")
async def cad_agent_status(request: Request) -> dict[str, Any]:
    """Probe whether the cad_agent service is reachable (so the frontend can show import availability)."""
    try:
        response = await request.app.state.cad_agent.get("/health", timeout=5.0)
        data = response.json()
        return {"available": response.status_code == 200, "service": data.get("service")}
    except Exception as error:
        return {"available": False, "error": str(error)}


@app.post("/api/cad-session")
async def store_cad_session(request: Request) -> dict[str, str]:
    """Store a CAD import session (render images, etc.) and return a token."""
    data = await request.json()
    token = uuid.uuid4().hex
    with _cad_session_lock:
        _cad_sessions[token] = data
    return {"token": token}


@app.get("/api/cad-session/{token}")
async def get_cad_session(token: str) -> dict[str, Any]:
    """Fetch CAD session data by token."""
    data = _cad_sessions.get(token)
    if not data:
        raise HTTPException(status_code=404, detail="CAD session not found or expired.")
    return data


@app.delete("/api/cad-session/{token}")
async def delete_cad_session(token: str) -> dict[str, str]:
    """Delete a CAD session (can be cleaned up after the result page consumes it)."""
    with _cad_session_lock:
        _cad_sessions.pop(token, None)
    return {"status": "ok"}
