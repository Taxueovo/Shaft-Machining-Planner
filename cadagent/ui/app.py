"""
Chainlit Frontend - 3D Model Feature Analyzer (Refactored)
==========================================================

Features:
1. Non-blocking file upload via chat input (.stp/.brep files)
2. Side panel display for multi-view renders and B-Rep features
3. Multimodal context auto-passed to LLM for Q&A

Architecture:
- Chainlit: UI layer with side panel display
- FastAPI: Business logic (see main.py)

Start:
    chainlit run ui/app.py -w

Dependencies:
    pip install chainlit httpx python-multipart
"""

# ==============================================================================
# Standard Library Imports
# ==============================================================================
import os
import sys
import json
import base64
import asyncio
import logging
from contextlib import aclosing  # PEP 525 GC-task mismatch fix for async generators
from typing import Dict, Any, Optional, AsyncGenerator, List
from pathlib import Path

# ==============================================================================
# Chainlit Imports
# ==============================================================================
import chainlit as cl

# ==============================================================================
# Data Layer (Chat History Persistence)
# ==============================================================================
# Configure Chainlit's data layer for persistent chat history.
# Supports SQLite (default) and PostgreSQL (via CHAINLIT_DB_URL env var).
#
# IMPORTANT: Chainlit's ``SQLAlchemyDataLayer`` does NOT automatically create
# the required tables.  Without explicit ``Base.metadata.create_all()`` the
# data layer logs ``SQLAlchemyDataLayer storage client is not initialized``
# and every step/thread insert fails with ``no such table: threads``.  The
# helper below ensures all tables exist *before* the data layer is handed
# back to Chainlit.
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer


def _ensure_sql_alchemy_tables(conninfo: str) -> None:
    """
    Best-effort creation of the tables Chainlit's SQLAlchemyDataLayer expects.

    Chainlit's ``SQLAlchemyDataLayer`` exposes ``.base`` (the SQLAlchemy
    declarative Base that owns the metadata) and ``.engine`` (a SQLAlchemy
    ``Engine``).  We use a synchronous connection for the create_all call
    because aiosqlite cannot be used with sync ``Engine`` and we want this
    code path to be lightweight and robust.  If the data layer doesn't expose
    these attributes (older versions) we fall back to running the statement
    via a sync sqlite3 connection, which always works.
    """
    # --- Try the data layer's own engine / Base first ---
    try:
        dummy = SQLAlchemyDataLayer(conninfo=conninfo)
        base = getattr(dummy, "base", None)
        engine = getattr(dummy, "engine", None)
        if base is not None and engine is not None:
            try:
                base.metadata.create_all(engine)
                logger.info("SQLAlchemy data layer tables ensured via data layer engine")
                return
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"create_all via data layer engine failed: {exc}")
    except Exception as exc:
        logger.warning(f"Could not instantiate data layer for table creation: {exc}")

    # --- Fallback: use a sync sqlite3 connection directly ---
    try:
        import sqlite3
        if conninfo.startswith("sqlite"):
            # Strip the SQLAlchemy driver prefix, e.g. ``sqlite+aiosqlite:///``
            path = conninfo.split("///", 1)[-1]
            # Remove any query string
            path = path.split("?", 1)[0]
            # Ensure parent directory exists
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with sqlite3.connect(path) as conn:
                cur = conn.cursor()

                # ---- threads ----
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS threads (
                        id          TEXT PRIMARY KEY,
                        createdAt   TEXT,
                        name        TEXT,
                        userId      TEXT,
                        metadata    TEXT
                    )
                """)

                # ---- steps (with migration for existing tables) ----
                # Create the table if it doesn't exist; otherwise ALTER to add
                # the columns that newer Chainlit versions require.
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS steps (
                        id            TEXT PRIMARY KEY,
                        threadId      TEXT,
                        parentId      TEXT,
                        createdAt     TEXT,
                        start         TEXT,
                        "end"         TEXT,
                        input         TEXT,
                        output        TEXT,
                        name          TEXT,
                        type          TEXT,
                        streaming     INTEGER,
                        waitForAnswer INTEGER,
                        isError       INTEGER,
                        metadata      TEXT,
                        generation    TEXT,
                        defaultOpen   INTEGER DEFAULT 0,
                        autoCollapse  INTEGER DEFAULT 0,
                        showInput     INTEGER DEFAULT 0
                    )
                """)
                # Add new columns to existing tables (no-op if column already exists)
                for col_def in [
                    "defaultOpen   INTEGER DEFAULT 0",
                    "autoCollapse  INTEGER DEFAULT 0",
                    "showInput     INTEGER DEFAULT 0",
                ]:
                    col_name = col_def.split()[0]
                    cur.execute(f"SELECT COUNT(*) FROM pragma_table_info('steps') WHERE name = ?", (col_name,))
                    if cur.fetchone()[0] == 0:
                        cur.execute(f"ALTER TABLE steps ADD COLUMN {col_def}")

                # ---- elements ----
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS elements (
                        id          TEXT PRIMARY KEY,
                        threadId    TEXT,
                        type        TEXT,
                        url         TEXT,
                        chainlitKey TEXT,
                        name        TEXT,
                        display     TEXT,
                        size        TEXT,
                        language    TEXT,
                        mime        TEXT,
                        objectKey   TEXT,
                        data        TEXT,
                        metadata    TEXT,
                        forId       TEXT
                    )
                """)

                # ---- feedbacks ----
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS feedbacks (
                        id        TEXT PRIMARY KEY,
                        stepId    TEXT,
                        name      TEXT,
                        value     INTEGER,
                        comment   TEXT,
                        createdAt TEXT,
                        metadata  TEXT
                    )
                """)

                conn.commit()
            logger.info("SQLAlchemy data layer tables ensured via sqlite3 fallback")
    except Exception as exc:
        logger.warning(f"Failed to create SQLite tables: {exc}")


@cl.data_layer
def data_layer():
    """Initialize the Chainlit data layer for chat history persistence."""
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "chainlit.db"
    default_conninfo = f"sqlite+aiosqlite:///{db_path.resolve()}"
    conninfo = os.getenv("CHAINLIT_DB_URL", default_conninfo)

    # Make sure tables exist before Chainlit starts using the data layer.
    _ensure_sql_alchemy_tables(conninfo)

    return SQLAlchemyDataLayer(conninfo=conninfo)


# ==============================================================================
# HTTP Client
# ==============================================================================
import httpx

# ==============================================================================
# Proxy Configuration
# ==============================================================================
# Security: credentials are NEVER hardcoded. They are read from environment
# variables HTTP_PROXY_USER / HTTP_PROXY_PASSWORD (see cadagent.config.settings).
# In a TTY, apply_proxy_settings() will prompt via getpass; in non-TTY
# contexts (e.g. this Chainlit web UI), it logs a WARNING and skips
# proxy setup so startup is not blocked.
from cadagent.config.settings import apply_proxy_settings

apply_proxy_settings()

# ==============================================================================
# Logging Configuration
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==============================================================================
# Constants
# ==============================================================================
API_BASE_URL = "http://localhost:8001"
ALLOWED_EXTENSIONS = ['.stp', '.step', '.brep']
ALLOWED_MIME_TYPES = ['model/step', 'model/iges', 'application/octet-stream']


# ==============================================================================
# Shared httpx AsyncClient (singleton)
# ==============================================================================
# httpx 0.28+ emits "Attempted to exit cancel scope in a different task" warnings
# when an AsyncClient is created and destroyed per-request, because httpx defers
# connection pool cleanup to background tasks that can outlive the request task.
# The fix: keep a SINGLE AsyncClient alive for the process lifetime and reuse it.
# See: https://github.com/encode/httpx/issues/2873
_shared_client: Optional[httpx.AsyncClient] = None


def _get_shared_client() -> httpx.AsyncClient:
    """Get or create the shared AsyncClient singleton."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            base_url=API_BASE_URL,
            timeout=600.0,  # 10 minutes for heavy operations
        )
    return _shared_client


async def _close_shared_client() -> None:
    """Close the shared client (call on app shutdown)."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None


# ==============================================================================
# API Client
# ==============================================================================

class APIClient:
    """Async HTTP client for FastAPI backend.

    Wraps the shared ``httpx.AsyncClient`` singleton to avoid the
    "cancel scope in different task" warnings that httpx 0.28+ emits when
    an AsyncClient is created/destroyed per-request.
    """

    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url
        self.client: httpx.AsyncClient = _get_shared_client()

    async def __aenter__(self):
        # Client is already created by _get_shared_client(); just return self.
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Do NOT close the client here — it's shared and managed globally.
        pass

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    async def post(
        self,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """Generic JSON POST helper used by callers that don't need a
        dedicated method (e.g. ``/generate_summary``)."""
        if self.client is None:
            raise RuntimeError("APIClient must be used as an async context manager")
        return await self.client.post(path, json=json, params=params)

    # ------------------------------------------------------------------
    # Endpoint-specific helpers
    # ------------------------------------------------------------------
    async def upload_and_render(self, file_content: bytes, filename: str) -> Dict[str, Any]:
        """Upload CAD file and render multi-view images."""
        content_type = "application/octet-stream"
        if filename.lower().endswith(('.stp', '.step')):
            content_type = "model/step"
        elif filename.lower().endswith('.brep'):
            content_type = "model/iges"

        files = {
            "file": (filename, file_content, content_type)
        }
        response = await self.client.post("/upload_and_render", files=files)
        response.raise_for_status()
        return response.json()

    async def extract_features(self, session_id: str) -> Dict[str, Any]:
        """Extract B-Rep features from uploaded model."""
        payload = {"session_id": session_id}
        response = await self.client.post("/extract_features", json=payload)
        response.raise_for_status()
        return response.json()

    async def chat_stream(
        self,
        session_id: str,
        message: str,
        images: Optional[Dict[str, str]] = None,
        features_json: Optional[Dict[str, Any]] = None,
        initialize_context: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Send chat message and yield streaming response.

        The backend uses ``AsyncOpenAI`` so SSE chunks are flushed promptly;
        the frontend reads them with ``aiter_lines()``.

        Implementation notes:
            * ``httpx.AsyncClient.stream()`` returns an
              ``_AsyncGeneratorContextManager``, NOT an awaitable. We use
              ``async with self.client.stream(...) as response:``; doing
              ``await self.client.stream(...)`` raises
              ``TypeError: object _AsyncGeneratorContextManager can't be used
              in 'await' expression``.

            * Two-layer ``contextlib.aclosing`` defence against PEP 525 GC-task
              mismatch. The OUTER ``aclosing`` is in the consumer
              (``handle_unified_message``); it protects OUR ``chat_stream()``
              generator. The INNER ``aclosing`` (here) protects httpx's
              ``response.aiter_lines()`` generator. Together they ensure
              neither async generator can be left for the GC's finalizer
              task to clean up, which would cause anyio to raise
              ``RuntimeError: Attempted to exit cancel scope in a different
              task than it was entered in``.

            * Non-SSE lines (e.g. a corporate proxy returning a single JSON
              blob instead of a real SSE stream) are logged at WARNING level
              so the operator can see what the proxy actually returned
              instead of silently dropping them.
        """
        payload = {
            "session_id": session_id,
            "message": message,
            "images": images or {},
            "features_json": features_json,
            "initialize_context": initialize_context,
        }

        response = None
        try:
            async with self.client.stream("POST", "/chat", json=payload) as response:
                response.raise_for_status()
                try:
                    # ------------------------------------------------------
                    # INNER aclosing: closes ``response.aiter_lines()`` in
                    # this task on every exit path. Without this, httpx's
                    # internal async generator gets abandoned and Python's
                    # GC finalizer task closes it, which makes anyio refuse
                    # to exit the cancel scope.
                    # ------------------------------------------------------
                    async with aclosing(response.aiter_lines()) as lines:
                        async for line in lines:
                            # Diagnostic: corporate proxies sometimes
                            # downgrade stream=True to a single JSON blob.
                            # We log (not silently drop) so we can see what
                            # actually came back when "0 chars" happens.
                            if not line.startswith("data: "):
                                if line.strip():
                                    # Corporate proxies may return a plain JSON blob
                                    # instead of SSE when blocking requests (e.g.,
                                    # token limit, content filter, timeout).
                                    # If it's valid JSON with an "error" field,
                                    # raise so the UI displays it to the user
                                    # instead of silently producing a blank bubble.
                                    try:
                                        non_sse_obj = json.loads(line)
                                        if "error" in non_sse_obj:
                                            raise Exception(non_sse_obj["error"])
                                    except json.JSONDecodeError:
                                        pass  # Not JSON; fall through to log
                                    logger.warning(
                                        f"[DEBUG] Non-SSE line from /chat: {line!r}"
                                    )
                                continue

                            data_content = line[6:]  # strip "data: " prefix
                            if data_content == "[DONE]":
                                break

                            try:
                                data_obj = json.loads(data_content)
                            except json.JSONDecodeError:
                                # Pass through raw content if it's not JSON (defensive)
                                yield data_content
                                continue

                            if "error" in data_obj:
                                raise Exception(data_obj["error"])
                            if "content" in data_obj:
                                yield data_obj["content"]
                except (GeneratorExit, asyncio.CancelledError):
                    # Consumer (Chainlit) closed mid-stream or its WebSocket
                    # task was cancelled. The outer ``async with`` will still
                    # release the underlying connection in this same task;
                    # we just log and propagate so the caller can react.
                    logger.warning("Streaming iterator closed/cancelled by consumer")
                    raise
        except (GeneratorExit, asyncio.CancelledError):
            # Outer safety net: catches the rare case where the ``async with``
            # itself is interrupted before ``__aenter__`` completes.
            logger.warning("Streaming connection closed/cancelled by consumer")
            raise
        finally:
            # --------------------------------------------------------------
            # CRITICAL FIX: Explicitly close the response in the SAME task
            # that entered the cancel scope. This prevents httpx/httpcore
            # from deferring the connection cleanup to a background GC task,
            # which would cross task boundaries and trigger:
            #   RuntimeError: Attempted to exit cancel scope in a different
            #                task than it was entered in
            #
            # The `aclosing` context manager provides protection when the loop
            # exits normally, but the explicit aclose() here handles:
            #   1. Early break on [DONE]
            #   2. GeneratorExit raised by the consumer
            #   3. Any exception propagating out
            # --------------------------------------------------------------
            if response is not None:
                try:
                    await response.aclose()
                except Exception as close_exc:
                    # Swallow close errors (connection already closed, etc.)
                    logger.debug(f"Response aclose() raised (non-fatal): {close_exc}")


# ==============================================================================
# File Handling Utilities
# ==============================================================================

def is_3d_model_file(element) -> bool:
    """Check if an element is a 3D model file (.stp/.step/.brep)."""
    if not hasattr(element, 'name'):
        return False

    file_name = element.name.lower()
    file_ext = Path(file_name).suffix.lower()

    return file_ext in ALLOWED_EXTENSIONS


async def get_file_content(file_element) -> tuple[bytes, str]:
    """Extract file content and name from a Chainlit file element."""
    file_name = file_element.name if hasattr(file_element, 'name') else "unknown"

    logger.info(f"File element type: {type(file_element)}")
    logger.info(f"File element attributes: {[attr for attr in dir(file_element) if not attr.startswith('_')]}")

    try:
        # Method 1: path attribute (most reliable for uploaded files)
        path = getattr(file_element, 'path', None)
        if path:
            logger.info(f"Reading file from path: {path}")
            with open(path, 'rb') as f:
                content = f.read()
            logger.info(f"Successfully read {len(content)} bytes from path")
            return content, file_name

        # Method 2: uri attribute (sometimes used instead of path)
        uri = getattr(file_element, 'uri', None)
        if uri:
            logger.info(f"Reading file from uri: {uri}")
            if uri.startswith('file://'):
                path = uri[7:]
            else:
                path = uri
            with open(path, 'rb') as f:
                content = f.read()
            logger.info(f"Successfully read {len(content)} bytes from uri")
            return content, file_name

        # Method 3: content attribute (newer Chainlit versions)
        content = getattr(file_element, 'content', None)
        if content is not None:
            logger.info(f"Using content attribute, size: {len(content) if content else 0}")
            return content, file_name

        # Method 4: get_content method (async)
        if hasattr(file_element, 'get_content'):
            logger.info("Trying get_content method")
            content = await file_element.get_content()
            if content is not None:
                logger.info(f"Successfully got content via get_content, size: {len(content)}")
                return content, file_name

        # Method 5: path attribute even if falsy
        if hasattr(file_element, 'path'):
            path = file_element.path
            if path:
                with open(path, 'rb') as f:
                    content = f.read()
                return content, file_name

    except Exception as e:
        logger.error(f"Failed to extract file content: {e}", exc_info=True)
        raise

    logger.error(f"Cannot extract content. Available attributes: {[a for a in dir(file_element) if not a.startswith('_')]}")
    raise ValueError(f"Cannot extract content from file element: {type(file_element)}")


# ==============================================================================
# Side Panel Display Functions
# ==============================================================================

async def _send_images_to_sidebar(images: Dict[str, str]):
    """Send images to sidebar only (internal function, no chat message)."""
    if not images:
        return

    elements = []
    for view_name, base64_str in images.items():
        if not base64_str:
            continue

        view_display_names = {
            'front': 'Front View',
            'top': 'Top View',
            'right': 'Right View',
            'isometric': 'Isometric View'
        }
        display_name = view_display_names.get(view_name, view_name.title())
        image_bytes = base64.b64decode(base64_str)

        elements.append(cl.Image(
            name=f"Render-{display_name}",
            content=image_bytes,
            display="side",
        ))

    if elements:
        cl.user_session.set("sidebar_images", elements)


async def _send_features_to_sidebar(features_json: Dict[str, Any]):
    """Send features JSON to sidebar only (internal function, no chat message)."""
    if not features_json:
        return

    json_str = json.dumps(features_json, ensure_ascii=False, indent=2)
    display_content = json_str
    if len(json_str) > 10000:
        display_content = json_str[:10000] + "\n\n*(Truncated due to length)*"

    feature_element = cl.Text(
        name="B-Rep Features",
        content=display_content,
        display="side",
    )

    cl.user_session.set("sidebar_features", feature_element)


async def display_rendered_images_side(images: Dict[str, str]):
    """Display rendered multi-view images in the side panel with message."""
    await _send_images_to_sidebar(images)
    if images:
        await cl.Message(content="## 🖼️ Multi-View Renders (See Right Sidebar)").send()


async def display_features_side(features_json: Dict[str, Any]):
    """Display B-Rep features JSON in the side panel with message."""
    await _send_features_to_sidebar(features_json)
    if features_json:
        await cl.Message(content="## 📋 B-Rep Feature Data (See Right Sidebar)").send()


# ==============================================================================
# Model Upload Handler
# ==============================================================================

async def handle_model_upload(file_element) -> bool:
    """Handle 3D model file upload, render, and feature extraction."""
    try:
        file_content, file_name = await get_file_content(file_element)
    except Exception as e:
        await cl.Message(content=f"❌ Failed to read file: {str(e)}").send()
        return False

    logger.info(f"Processing uploaded file: {file_name}")

    file_ext = Path(file_name).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        await cl.Message(
            content=f"⚠️ Unsupported file format: {file_ext}\nSupported: {', '.join(ALLOWED_EXTENSIONS)}"
        ).send()
        return False

    processing_msg = cl.Message(content="⏳ Uploading and processing 3D model...")
    await processing_msg.send()

    try:
        async with APIClient() as client:
            render_response = await client.upload_and_render(
                file_content=file_content,
                filename=file_name,
            )

        if not render_response.get("success"):
            await cl.Message(
                content=f"❌ Upload failed: {render_response.get('message', 'Unknown error')}"
            ).send()
            return False

        session_id = render_response["session_id"]
        images_data = render_response.get("images", {})

        images = {}
        for view_name, view_data in images_data.items():
            if view_data.get("success") and view_data.get("base64"):
                images[view_name] = view_data["base64"]

        cl.user_session.set("session_id", session_id)
        cl.user_session.set("images", images)
        cl.user_session.set("upload_completed", True)
        cl.user_session.set("features_json", None)

        processing_msg.content = "✅ Model uploaded successfully! Extracting features..."
        await processing_msg.update()

        await display_rendered_images_side(images)

        try:
            async with APIClient() as client:
                features_response = await client.extract_features(session_id)

            if features_response.get("extraction_success") and features_response.get("features_json"):
                features_json = features_response["features_json"]
                cl.user_session.set("features_json", features_json)
                await display_features_side(features_json)

                processing_msg.content = "✅ Processing complete! You can now view the renders and features in the right sidebar, and ask me questions about this part."
                await processing_msg.update()

                await cl.Message(
                    content="📎 Click the button below to view renders and feature data",
                    actions=[
                        cl.Action(
                            name="show_sidebar",
                            payload={"action": "show"},
                            description="📋 View Sidebar",
                        )
                    ],
                ).send()
            else:
                error = features_response.get("error", "Unknown error")
                processing_msg.content = f"⚠️ Feature extraction failed: {error}\n\nRenders are shown on the right. You can continue asking questions based on the images."
                await processing_msg.update()

        except Exception as e:
            logger.error(f"Feature extraction failed: {e}")
            processing_msg.content = f"⚠️ Feature extraction failed: {str(e)}\n\nRenders are shown on the right. You can continue asking questions based on the images."
            await processing_msg.update()

        logger.info(f"Model upload completed: session_id={session_id}")
        return True

    except httpx.HTTPError as e:
        logger.error(f"HTTP error during upload: {e}")
        await cl.Message(
            content=f"❌ Cannot connect to backend service: {str(e)}\n\n"
                    f"Please ensure FastAPI backend is running (uvicorn main:app --port 8001)"
        ).send()
        return False
    except Exception as e:
        logger.error(f"Upload processing failed: {e}", exc_info=True)
        await cl.Message(content=f"❌ Processing failed: {str(e)}").send()
        return False


# ==============================================================================
# Unified Multimodal Message Handler (Phase 1 + Phase 2)
# ==============================================================================

async def handle_unified_message(message: cl.Message):
    """Unified handler for multimodal input: supports file + text simultaneously."""
    elements = message.elements or []
    user_text = message.content or ""
    model_files = [e for e in elements if is_3d_model_file(e)]

    # ========== Case 1: File + Text (or File only) ==========
    if model_files:
        file_element = model_files[0]
        file_name = file_element.name if hasattr(file_element, 'name') else "unknown"
        file_ext = Path(file_name).suffix.lower()

        try:
            file_content, file_name = await get_file_content(file_element)
        except Exception as e:
            await cl.Message(content=f"❌ Failed to read file: {str(e)}").send()
            return

        if file_ext not in ALLOWED_EXTENSIONS:
            await cl.Message(
                content=f"⚠️ Unsupported file format: {file_ext}\nSupported: {', '.join(ALLOWED_EXTENSIONS)}"
            ).send()
            return

        user_question = user_text.strip()
        if not user_question:
            user_question = "Analyze the key features and design considerations of this part."
            logger.info("No text provided, using default question")

        processing_msg = cl.Message(content="⏳ Processing 3D model and analyzing...")
        await processing_msg.send()

        try:
            async with APIClient() as client:
                render_response = await client.upload_and_render(
                    file_content=file_content,
                    filename=file_name,
                )

            if not render_response.get("success"):
                await cl.Message(
                    content=f"❌ Upload failed: {render_response.get('message', 'Unknown error')}"
                ).send()
                return

            session_id = render_response["session_id"]
            images_data = render_response.get("images", {})

            images = {}
            for view_name, view_data in images_data.items():
                if view_data.get("success") and view_data.get("base64"):
                    images[view_name] = view_data["base64"]

            cl.user_session.set("session_id", session_id)
            cl.user_session.set("images", images)
            cl.user_session.set("upload_completed", True)
            cl.user_session.set("features_json", None)
            cl.user_session.set("context_initialized", False)

            processing_msg.content = "✅ Model uploaded! Extracting features..."
            await processing_msg.update()

            await display_rendered_images_side(images)

            features_json = None
            try:
                async with APIClient() as client:
                    features_response = await client.extract_features(session_id)

                if features_response.get("extraction_success") and features_response.get("features_json"):
                    features_json = features_response["features_json"]
                    cl.user_session.set("features_json", features_json)
                    await display_features_side(features_json)
                    processing_msg.content = "✅ Analysis ready! Generating response..."
                    await processing_msg.update()

                    # Phase 2: Generate feature summary for long-term memory
                    # (uses the new APIClient.post() helper so it actually works).
                    try:
                        async with APIClient() as client:
                            summary_response = await client.post(
                                "/generate_summary",
                                json={"session_id": session_id},
                            )
                            if summary_response.status_code == 200:
                                logger.info("Feature summary generated for long-term memory")
                            else:
                                logger.warning(
                                    f"generate_summary returned HTTP "
                                    f"{summary_response.status_code}"
                                )
                    except Exception as e:
                        logger.warning(f"Summary generation failed: {e}")
            except Exception as e:
                logger.warning(f"Feature extraction failed: {e}")
                processing_msg.content = "⚠️ Feature extraction failed, analyzing with images only..."
                await processing_msg.update()

            processing_msg.content = "🤖 AI is analyzing your question..."
            await processing_msg.update()

            msg = cl.Message(content="")
            await msg.send()

            # ------------------------------------------------------------------
            # OUTER aclosing: closes our ``chat_stream()`` generator in this
            # consumer task (the Chainlit WebSocket task). Combined with the
            # INNER aclosing inside ``chat_stream``, no async generator in
            # this call stack can be left for the PEP 525 GC finalizer task.
            # ------------------------------------------------------------------
            async with APIClient() as client:
                async with aclosing(client.chat_stream(
                    session_id=session_id,
                    message=user_question,
                    images=images,
                    features_json=features_json,
                    initialize_context=True,
                )) as stream:
                    async for chunk in stream:
                        await msg.stream_token(chunk)

            await msg.update()
            cl.user_session.set("context_initialized", True)

            # Save assistant response to history (duplicate guarded server-side).
            # NOTE: ``/chat_end`` now expects a JSON body (instead of query
            # parameters) so very long assistant responses are not truncated
            # by URL-length limits on proxies.
            try:
                async with APIClient() as client:
                    await client.post(
                        "/chat_end",
                        json={"session_id": session_id, "assistant_response": msg.content},
                    )
            except Exception as e:
                logger.warning(f"Failed to save to history: {e}")

            await cl.Message(
                content="📎 Click to view renders and feature data in sidebar",
                actions=[
                    cl.Action(
                        name="show_sidebar",
                        payload={"action": "show"},
                        description="📋 View Sidebar",
                    )
                ],
            ).send()

            logger.info(f"Unified processing completed: session_id={session_id}")

        except httpx.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            await cl.Message(
                content=f"❌ Cannot connect to backend service: {str(e)}\n\n"
                        f"Please ensure FastAPI backend is running."
            ).send()
        except Exception as e:
            logger.error(f"Processing failed: {e}", exc_info=True)
            await cl.Message(content=f"❌ Processing failed: {str(e)}").send()

        return

    # ========== Case 2: Text only (no file) ==========
    session_id = cl.user_session.get("session_id")

    if not session_id:
        await cl.Message(
            content="⚠️ No model uploaded yet.\n\n"
                    "Please upload a 3D model file (.stp/.step/.brep) first.\n"
                    "You can attach files directly in the chat input."
        ).send()
        return

    if not user_text.strip():
        await cl.Message(content="Please enter your question before sending.").send()
        return

    try:
        msg = cl.Message(content="")
        await msg.send()

        # Same OUTER + INNER aclosing pair as Case 1.
        async with APIClient() as client:
            async with aclosing(client.chat_stream(
                session_id=session_id,
                message=user_text,
                images={},
                features_json=None,
                initialize_context=False,
            )) as stream:
                async for chunk in stream:
                    await msg.stream_token(chunk)

        await msg.update()

        # Save assistant response to history (duplicate guarded server-side).
        # NOTE: ``/chat_end`` now expects a JSON body (instead of query
        # parameters) so very long assistant responses are not truncated
        # by URL-length limits on proxies.
        try:
            async with APIClient() as client:
                await client.post(
                    "/chat_end",
                    json={"session_id": session_id, "assistant_response": msg.content},
                )
        except Exception as e:
            logger.warning(f"Failed to save to history: {e}")

        await cl.Message(
            content="📎 Click to view renders and feature data",
            actions=[
                cl.Action(
                    name="show_sidebar",
                    payload={"action": "show"},
                    description="📋 View Sidebar",
                )
            ],
        ).send()

        logger.info("Multi-turn chat completed")

    except httpx.HTTPError as e:
        logger.error(f"HTTP error during chat: {e}")
        await cl.Message(content=f"❌ Backend request failed: {str(e)}").send()
    except Exception as e:
        logger.error(f"Chat processing failed: {e}", exc_info=True)
        await cl.Message(content=f"❌ Failed to process message: {str(e)}").send()


# ==============================================================================
# Legacy Text Message Handler (Kept for compatibility)
# ==============================================================================

async def handle_text_question(user_text: str):
    """Handle text question with multimodal context (legacy)."""
    class MockMessage:
        def __init__(self, content, elements=None):
            self.content = content
            self.elements = elements or []

    await handle_unified_message(MockMessage(user_text, []))


# ==============================================================================
# Logo Helper Function
# ==============================================================================

def _get_logo_element(theme: Optional[str] = None) -> Optional[cl.Image]:
    """Get the application logo as a Chainlit Image element."""
    public_dir = Path(__file__).parent / "public"

    if theme is None:
        try:
            theme = cl.user_session.get("theme")
        except Exception:
            theme = None

    if theme == "dark":
        priority = ["logo_dark.png", "logo_light.png"]
    else:
        priority = ["logo_light.png", "logo_dark.png"]

    for logo_name in priority:
        logo_path = public_dir / logo_name
        if not logo_path.exists():
            continue
        try:
            with open(logo_path, "rb") as f:
                logo_bytes = f.read()
            logger.info(f"Loaded logo: {logo_path} ({len(logo_bytes)} bytes)")
            return cl.Image(
                name=f"app-logo-{logo_name.rsplit('.', 1)[0]}",
                content=logo_bytes,
                display="inline",
                size="medium",
            )
        except Exception as e:
            logger.warning(f"Failed to load logo {logo_name}: {e}")
            continue

    logger.warning("No logo found in ui/public folder (looked for: %s)", priority)
    return None


# ==============================================================================
# Chainlit Event Handlers
# ==============================================================================

@cl.on_chat_start
async def on_chat_start():
    """Initialize chat session with welcome message."""
    logger.info("Chainlit session starting...")

    cl.user_session.set("session_id", None)
    cl.user_session.set("images", {})
    cl.user_session.set("features_json", None)
    cl.user_session.set("context_initialized", False)
    cl.user_session.set("upload_completed", False)
    cl.user_session.set("theme", None)

    logo_element = _get_logo_element()
    elements = [logo_element] if logo_element else []

    await cl.Message(
        content="""
## 📊 3D Part Knowledge Management System

### Welcome!

This is a multimodal 3D CAD model analysis assistant.

**How to use:**

**Supported analysis:**
- Part geometry feature recognition
- Design rationality assessment
- Key dimension analysis
- Engineering improvement suggestions

---
*Tip: You can upload a new part file anytime to start a new analysis.*
""",
        elements=elements,
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    """Unified message handler - supports file + text simultaneously."""
    await handle_unified_message(message)


@cl.action_callback("upload_new_model")
async def on_upload_new_model(action: cl.Action):
    """Callback for action button to upload new model."""
    await cl.Message(
        content="""
## 📎 Upload New Model

Attach your 3D model file (.stp / .step / .brep) in the input box below.

The system will automatically:
1. Upload and render multi-view images
2. Extract B-Rep features
3. Display results in the right sidebar
"""
    ).send()


@cl.action_callback("show_sidebar")
async def on_show_sidebar(action: cl.Action):
    """Callback for show sidebar action button."""
    images = cl.user_session.get("images", {})
    features = cl.user_session.get("features_json")

    all_elements = []

    if images:
        for view_name, base64_str in images.items():
            if not base64_str:
                continue
            view_display_names = {
                'front': 'Front View',
                'top': 'Top View',
                'right': 'Right View',
                'isometric': 'Isometric View'
            }
            display_name = view_display_names.get(view_name, view_name.title())
            image_bytes = base64.b64decode(base64_str)
            all_elements.append(cl.Image(
                name=f"Render-{display_name}",
                content=image_bytes,
                display="side",
            ))

    if features:
        json_str = json.dumps(features, ensure_ascii=False, indent=2)
        display_content = json_str
        if len(json_str) > 10000:
            display_content = json_str[:10000] + "\n\n*(Truncated due to length)*"
        all_elements.append(cl.Text(
            name="B-Rep Features",
            content=display_content,
            display="side",
        ))

    if all_elements:
        await cl.ElementSidebar.set_elements(all_elements)
    elif not images and not features:
        await cl.Message(content="⚠️ No sidebar content yet. Please upload a 3D model file first.").send()


# ==============================================================================
# Application Entry Point
# ==============================================================================

if __name__ == "__main__":
    # Chainlit handles the application startup
    pass