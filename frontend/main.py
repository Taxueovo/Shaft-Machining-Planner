from __future__ import annotations

import hashlib
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware


FRONTEND_DIR = Path(__file__).resolve().parent

# Unified environment configuration lives in the project-root .env (one level above frontend)
load_dotenv(FRONTEND_DIR.parent / ".env")
load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8001")
LOCAL_API_TOKEN = os.getenv("LOCAL_API_TOKEN", "")
if not LOCAL_API_TOKEN:
    raise RuntimeError(
        "LOCAL_API_TOKEN is required. Start the application with frontend/run_frontend.py."
    )
_ALLOWED_ORIGINS = {"http://127.0.0.1:8000", "http://localhost:8000"}
MAX_REQUEST_BYTES = 2_000_000


def _compute_static_version() -> str:
    """A stable build hash for static asset URLs.

    Computed once at startup from the static files' content; templates append it as
    ``?v=<hash>`` so browsers cache assets immutably and a changed asset gets a new URL.
    """
    digest = hashlib.sha1()
    try:
        for path in sorted((FRONTEND_DIR / "static").rglob("*")):
            if path.is_file():
                digest.update(path.name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
    except OSError:
        pass
    return digest.hexdigest()[:12]


STATIC_VERSION = _compute_static_version()


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Serve static assets with long immutable caching (URL carries the version hash)."""

    async def dispatch(self, request: Request, call_next):
        request.state.csp_nonce = secrets.token_urlsafe(18)
        response = await call_next(request)
        if request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class LocalOriginMiddleware(BaseHTTPMiddleware):
    """Reject cross-site state-changing requests before they reach the local proxy."""

    async def dispatch(self, request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                content_length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return Response("Invalid Content-Length", status_code=400)
            if content_length > MAX_REQUEST_BYTES:
                return Response("Request body too large", status_code=413)
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site")
            if (origin and origin not in _ALLOWED_ORIGINS) or fetch_site == "cross-site":
                return Response("Cross-site request rejected", status_code=403)
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; script-src 'self' 'nonce-{request.state.csp_nonce}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.backend = httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=90.0,
    )
    yield
    await app.state.backend.aclose()


app = FastAPI(title="Shaft Machining Planner Frontend", version="1.0.0", lifespan=lifespan)
app.add_middleware(StaticCacheMiddleware)
app.add_middleware(LocalOriginMiddleware)
templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))
templates.env.globals["static_version"] = STATIC_VERSION
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
            headers={"x-local-api-token": LOCAL_API_TOKEN},
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


@app.get("/api/jobs/{job_id}/process-card/download")
async def download_process_card(request: Request, job_id: str) -> Response:
    stream_context = request.app.state.backend.stream(
        "GET",
        f"/api/v1/jobs/{job_id}/process-card/download",
        headers={"x-local-api-token": LOCAL_API_TOKEN},
    )
    upstream = await stream_context.__aenter__()
    if upstream.status_code != 200:
        await stream_context.__aexit__(None, None, None)
        raise HTTPException(
            status_code=upstream.status_code, detail="Process card is not available."
        )

    async def chunks():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await stream_context.__aexit__(None, None, None)

    # job_id is URL-derived and unvalidated; sanitize so it cannot inject a malformed header.
    safe_job_id = re.sub(r"[^\w.-]", "", job_id)[:64]
    headers = {"Content-Disposition": f'attachment; filename="process_card_{safe_job_id}.xlsx"'}
    if upstream.headers.get("content-length"):
        headers["Content-Length"] = upstream.headers["content-length"]
    return StreamingResponse(
        chunks(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


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
    return await forward(request, "POST", f"/api/v1/tools/{tool_name}", await request.json())


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
        await request.app.state.backend.post(
            "/api/v1/heartbeat", headers={"x-local-api-token": LOCAL_API_TOKEN}
        )
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
        await request.app.state.backend.post(
            "/api/v1/shutdown", headers={"x-local-api-token": LOCAL_API_TOKEN}
        )
    except Exception:
        pass  # Backend may already be shutdown

    # 2. Delay frontend shutdown (let response send first)
    async def _delayed_shutdown() -> None:
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_delayed_shutdown())
    return {"status": "shutting_down", "message": "System is shutting down."}
