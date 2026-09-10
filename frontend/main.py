"""Shaft Machining Planner 前端服务（FastAPI 应用入口）。

页面路由负责渲染 Jinja 模板；/api/* 路径统一转发到本地后端服务
（BACKEND_URL），转发时附加 x-local-api-token 头用于本地鉴权。
前端刻意只面向本机回环地址运行，作为本地演示/工具入口。
"""

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


# 按静态文件生成稳定摘要，作为浏览器资源 URL 的缓存版本。
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


# 为带版本的静态资源设置长期缓存头，减少重复下载。
class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Serve static assets with long immutable caching (URL carries the version hash)."""

    # 先执行请求，再只对静态资源响应追加不可变缓存策略。
    async def dispatch(self, request: Request, call_next):
        request.state.csp_nonce = secrets.token_urlsafe(18)
        response = await call_next(request)
        if request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# 在请求代理前拒绝不可信来源的写操作，并添加浏览器安全响应头。
class LocalOriginMiddleware(BaseHTTPMiddleware):
    """Reject cross-site state-changing requests before they reach the local proxy."""

    # 校验请求来源并附加安全头；不可信写请求在代理前直接返回拒绝。
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
    """应用生命周期：启动时创建到后端的共享 HTTP 客户端，进程退出时关闭。"""
    app.state.backend = httpx.AsyncClient(
        base_url=BACKEND_URL,
        timeout=90.0,
    )
    yield
    await app.state.backend.aclose()


app = FastAPI(title="Shaft Machining Planner Frontend", version="1.0.0", lifespan=lifespan)
# 中间件按注册顺序由外向内执行：StaticCacheMiddleware 先写入 csp_nonce，
# 后执行的 LocalOriginMiddleware 组装 CSP 响应头时才能读到该随机值，顺序不可互换。
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
    """首页：启动时探测后端 /health，把连接状态传给模板渲染顶部横幅。"""
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
    """任务详情页：仅输出页面骨架，运行数据由前端脚本异步拉取并渲染。"""
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
    """把前端请求代理到后端，并统一转换为 HTTP 错误。

    - 始终附带 x-local-api-token 头（本地服务间鉴权）；
    - 后端返回的 4xx/5xx 尽量透传其 detail；
    - 无法连通后端时统一返回 503。
    """
    try:
        response = await request.app.state.backend.request(
            method,
            path,
            json=payload,
            headers={"x-local-api-token": LOCAL_API_TOKEN},
            timeout=300.0 if path.endswith("/process-route/customize") else 90.0,
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


# 将浏览器查询参数带到后端路径，避免代理时丢失筛选和分页条件。
def with_query(request: Request, path: str) -> str:
    """Preserve browser query parameters when proxying GET requests."""
    return f"{path}?{request.url.query}" if request.url.query else path


# ============================================================
# 工艺规划相关 API 代理：任务(jobs)、工艺路线、材料、刀具等
# 全部 1:1 透传至后端 /api/v1/*，鉴权与错误映射统一收敛于 forward()
# ============================================================


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.post("/api/jobs")
async def create_job(request: Request) -> dict[str, Any]:
    return await forward(
        request,
        "POST",
        "/api/v1/jobs",
        await request.json(),
    )


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(request, "GET", f"/api/v1/jobs/{job_id}")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.post("/api/jobs/{job_id}/choices")
async def submit_choices(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(
        request,
        "POST",
        f"/api/v1/jobs/{job_id}/choices",
        await request.json(),
    )


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/jobs/{job_id}/result")
async def get_result(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(request, "GET", f"/api/v1/jobs/{job_id}/result")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.post("/api/jobs/{job_id}/process-card/export")
async def export_process_card(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(request, "POST", f"/api/v1/jobs/{job_id}/process-card/export")


@app.get("/api/jobs/{job_id}/process-card/download")
async def download_process_card(request: Request, job_id: str) -> Response:
    """流式转发后端生成的工艺卡片 Excel，避免整份文件驻留内存。"""
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

    # 分段传输后端下载响应，并在结束或异常时释放上游流。
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


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.post("/api/jobs/{job_id}/engineering")
async def submit_engineering(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(
        request, "POST", f"/api/v1/jobs/{job_id}/engineering", await request.json()
    )


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.post("/api/jobs/{job_id}/process-route/customize")
async def customize_process_route(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(
        request, "POST", f"/api/v1/jobs/{job_id}/process-route/customize", await request.json()
    )


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.delete("/api/jobs/{job_id}/process-route/customization")
async def reset_process_route(request: Request, job_id: str) -> dict[str, Any]:
    return await forward(request, "DELETE", f"/api/v1/jobs/{job_id}/process-route/customization")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.post("/api/preview-route")
async def preview_route(request: Request) -> dict[str, Any]:
    return await forward(request, "POST", "/api/v1/preview-route", await request.json())


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/materials")
async def list_materials(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/materials")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/materials/price")
async def get_material_price(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, "/api/v1/materials/price"))


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/tools")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.post("/api/tools/{tool_name}")
async def call_tool(request: Request, tool_name: str) -> dict[str, Any]:
    return await forward(request, "POST", f"/api/v1/tools/{tool_name}", await request.json())


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/agents")
async def list_agents(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/agents")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/orchestrator/status")
async def orchestrator_status(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/orchestrator/status")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/prompts")
async def list_prompts(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/prompts")


# ============================================================
# Taxonomy & Case Library Pages
# ============================================================


# 渲染案例分类管理页面。
@app.get("/taxonomy", response_class=HTMLResponse)
async def taxonomy_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="taxonomy.html",
    )


# 渲染案例列表页面，数据由页面脚本另行请求。
@app.get("/cases", response_class=HTMLResponse)
async def cases_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="cases.html",
    )


# 加载指定案例并渲染详情，处理不存在的案例标识。
@app.get("/cases/{case_id}", response_class=HTMLResponse)
async def case_detail_page(request: Request, case_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="case_detail.html",
        context={"case_id": case_id},
    )


@app.get("/custom", response_class=HTMLResponse)
async def custom_planning_page(request: Request) -> HTMLResponse:
    """自定义工艺规划页：先探测后端健康状态，用于控制表单能否提交。"""
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


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/taxonomy")
async def get_taxonomy(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/taxonomy")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/taxonomy/{node_id}")
async def get_taxonomy_node(request: Request, node_id: str) -> dict[str, Any]:
    return await forward(request, "GET", f"/api/v1/taxonomy/{node_id}")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/taxonomy/{node_id}/cases")
async def get_taxonomy_cases(request: Request, node_id: str) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, f"/api/v1/taxonomy/{node_id}/cases"))


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/cases")
async def list_cases(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, "/api/v1/cases"))


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/cases/filters")
async def get_case_filters(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/cases/filters")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/cases/{case_id}")
async def get_case(request: Request, case_id: str) -> dict[str, Any]:
    return await forward(request, "GET", f"/api/v1/cases/{case_id}")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
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


# 渲染知识库管理页面，由前端脚本读取索引状态。
@app.get("/rag", response_class=HTMLResponse)
async def rag_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="rag.html")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/rag/status")
async def rag_status(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/rag/status")


# 转发知识库构建请求及其通道参数。
@app.post("/api/rag/build")
async def rag_build(request: Request) -> dict[str, Any]:
    return await forward(request, "POST", with_query(request, "/api/v1/rag/build"))


# 转发指定知识索引的清理请求。
@app.delete("/api/rag/clear")
async def rag_clear(request: Request) -> dict[str, Any]:
    return await forward(request, "DELETE", with_query(request, "/api/v1/rag/clear"))


# 转发知识检索查询参数，保持后端返回结构。
@app.get("/api/rag/search")
async def rag_search(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, "/api/v1/rag/search"))


# 转发已索引分块的抽样查询。
@app.get("/api/rag/chunks")
async def rag_chunks(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", with_query(request, "/api/v1/rag/chunks"))


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
@app.get("/api/rag-health")
async def rag_health(request: Request) -> dict[str, Any]:
    return await forward(request, "GET", "/api/v1/rag-health")


# 通过统一代理转发对应后端请求，鉴权令牌留在服务端，保留返回状态和数据。
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


# 处理用户的关闭请求，安排服务退出并先返回确认响应。
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
    # 延迟结束前端进程，让当前关闭响应先发送给浏览器。
    async def _delayed_shutdown() -> None:
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_delayed_shutdown())
    return {"status": "shutting_down", "message": "System is shutting down."}
