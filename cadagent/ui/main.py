"""
FastAPI Backend - 3D Model Feature Analyzer
==========================================

Pure backend service for 3D CAD model analysis.
Provides API endpoints for:
- /upload_and_render: Upload CAD file and render multi-view images
- /extract_features: Extract B-Rep features from CAD model
- /chat: AI CAE expert chat with streaming response
- /debug_proxy: Raw probe of the corporate LLM proxy

Start:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Dependencies:
    pip install fastapi uvicorn python-multipart httpx sse-starlette
"""

# ==============================================================================
# Standard Library Imports
# ==============================================================================
import os
import base64
import hashlib
import uuid
import tempfile
import json
import asyncio
import threading
import time
from copy import deepcopy
from typing import Dict, Any, List, Optional
from pathlib import Path
from contextlib import asynccontextmanager

# ==============================================================================
# FastAPI Imports
# ==============================================================================
from fastapi import FastAPI, UploadFile, File, HTTPException, Body, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# Third-party
import httpx

# ==============================================================================
# Internal Imports (Business Logic)
# ==============================================================================
from .schemas import (
    UploadRenderResponse,
    ExtractFeaturesResponse,
    ImageViewData,
    SessionData,
    ExtractFeaturesRequest,
    PlanningInputResponse,
)

# NOTE: convert_brep (pythonocc) is a heavyweight native dependency; it is
# lazy-loaded inside the request handler so the service does not force-import
# it at module load time, which significantly speeds up cadagent cold start.
# from cadagent.services.convert_brep import convert_stp_to_brep_occ

# Import CAD -> PE PlanningRequest mapper (best-effort; falls back to
# plain ``services`` import for non-aliased layouts, e.g. pytest)
try:
    from cadagent.services.cad_mapper import map_features_to_planning_request, validate_with_peagent
except ImportError:  # pragma: no cover
    from services.cad_mapper import map_features_to_planning_request, validate_with_peagent

# Import LLM engineering-intent suggester (optional; degrades gracefully)
try:
    from cadagent.services.llm_suggester import suggest_engineering_fields, VALID_HEAT_TREATMENT
except ImportError:  # pragma: no cover
    from services.llm_suggester import suggest_engineering_fields, VALID_HEAT_TREATMENT

# peagent material candidate list (used to validate LLM material suggestions) --
# aligned with PE backend/rules/constants.py
PEAGENT_MATERIAL_CANDIDATES = [
    "45", "40Cr", "42CrMo", "35CrMo", "20Cr", "20CrMnTi", "Q235", "45Mn2",
    "303", "304", "316", "2Cr13", "1Cr17Ni2", "GCr15", "GCr15SiMn",
    "6061", "7075", "H62",
]

# ==============================================================================
# Shared Configuration (from cadagent.config)
# ==============================================================================
from cadagent.config import (
    setup_logger,
    CAE_SYSTEM_PROMPT,
    create_openai_client,
    create_async_openai_client,  # Added: async client to avoid blocking the event loop
    close_llm_clients,  # Added: closes LLM clients on shutdown
    LLM_MODEL,
    LLM_TEMPERATURE,
)

logger = setup_logger(__name__)

# ==============================================================================
# Constants
# ==============================================================================
ALLOWED_EXTENSIONS = ['.stp', '.step', '.brep']

# Raw LLM proxy endpoint — used only by the ``/debug_proxy`` probe.
# Kept here (instead of read from ``cadagent.config.llm_config``) so that
# the probe is fully decoupled from the OpenAI SDK and shows the *true*
# behaviour of the upstream gateway.
LLM_BASE_URL_RAW = "https://api.openai.com/v1"
LLM_API_KEY_ENV = "OPENAI_API_KEY"

# ==============================================================================
# Session Storage
# ==============================================================================
sessions: Dict[str, SessionData] = {}

# CAD import progress storage (keyed by the session_id passed by the frontend).
# A single-process dict suffices; writes are atomic under the GIL.
# Written by report() while /api/v1/planning-input runs, read by the polling
# endpoint /api/v1/planning-progress/{id}, and removed in the final `finally`
# block so it never leaks.
CAD_PROGRESS: Dict[str, dict] = {}

# ==============================================================================
# Utility Functions
# ==============================================================================

def allowed_extensions() -> list:
    """Return allowed file extensions."""
    return ALLOWED_EXTENSIONS


def compute_file_hash(file_content: bytes) -> str:
    """Compute SHA256 hash of file content."""
    return hashlib.sha256(file_content).hexdigest()


# ==============================================================================
# CAD import cache (demo/development use): file-content hash -> detection/import result
# ==============================================================================
# Same idea as the backend A1 job-level cache, but attached to cadagent's
# /planning_input: identical STP files have identical SHA256 hashes, so a cache
# hit skips both the OCC B-Rep detection (which can take 1-2 minutes for gear
# shafts) and the multi-view rendering, returning in seconds.
# In-memory, process-local, cleared on restart; enable/ttl/capacity from .env:
#   CAD_IMPORT_CACHE_ENABLED      true/false, default true
#   CAD_IMPORT_CACHE_TTL_SECONDS  default 3600
#   CAD_IMPORT_CACHE_MAX_ENTRIES  default 50

_CAD_CACHE_ENABLED = (
    os.getenv("CAD_IMPORT_CACHE_ENABLED", "true").strip().lower()
    in ("1", "true", "yes", "on")
)
_CAD_CACHE_TTL_SECONDS = float(os.getenv("CAD_IMPORT_CACHE_TTL_SECONDS", "3600"))
_CAD_CACHE_MAX_ENTRIES = int(os.getenv("CAD_IMPORT_CACHE_MAX_ENTRIES", "50"))


class CadImportCache:
    """Thread-safe in-memory cache: key -> deep-copied value (TTL + size limit).

    Both reads and writes return deep copies of the value so concurrent
    requests never share and mutate the same object.
    """

    def __init__(self, max_entries: int = _CAD_CACHE_MAX_ENTRIES,
                 ttl_seconds: float = _CAD_CACHE_TTL_SECONDS) -> None:
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.enabled = _CAD_CACHE_ENABLED and max_entries > 0 and ttl_seconds > 0

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if time.time() - entry["stored_at"] > self._ttl:
                del self._data[key]
                return None
            return deepcopy(entry["value"])

    def put(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            # Evict the oldest entry (by write time) when the limit is exceeded
            if key not in self._data and len(self._data) >= self._max_entries:
                oldest_key = min(self._data, key=lambda k: self._data[k]["stored_at"])
                del self._data[oldest_key]
            self._data[key] = {"value": deepcopy(value), "stored_at": time.time()}

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


# Detection cache: file_hash -> {features_result, render_images}. The OCC
# detection and rendering results can be reused for the same file regardless
# of material/suggest changes.
extraction_cache = CadImportCache()
# Full response cache: file_hash|material|suggest -> final response fields.
# Re-importing the same file with the same parameters skips even the LLM
# completion and returns in seconds.
response_cache = CadImportCache()


async def run_in_thread_pool(sync_func, *args, **kwargs):
    """Run synchronous function in thread pool to avoid blocking."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: sync_func(*args, **kwargs))


# ==============================================================================
# FastAPI Application Lifecycle
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI application lifecycle manager."""
    logger.info("=" * 50)
    logger.info("FastAPI Backend Starting...")
    logger.info("=" * 50)

    yield

    logger.info("=" * 50)
    logger.info("FastAPI Backend Shutting Down...")
    logger.info("=" * 50)

    # Close LLM clients to properly release httpx connection pool resources
    # This prevents "cancel scope in different task" warnings on shutdown
    await close_llm_clients()


# ==============================================================================
# Create FastAPI Application
# ==============================================================================
app = FastAPI(
    title="3D Model Feature Analyzer API",
    description="Backend service for CAD model analysis with multi-modal AI support",
    version="1.0.0",
    lifespan=lifespan
)

# ==============================================================================
# CORS Configuration
# ==============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("CORS middleware configured (allow all origins)")


# ==============================================================================
# API Endpoints
# ==============================================================================

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "3D Model Feature Analyzer API",
        "version": "1.0.0"
    }


@app.post("/upload_and_render", tags=["File Processing"], response_model=UploadRenderResponse)
async def upload_and_render(file: UploadFile = File(...)):
    """
    Upload CAD file and render multi-view images.
    """
    logger.info(f"Received upload request: {file.filename}")

    # ---------- Step 1: Validate file extension ----------
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_extensions():
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {file_ext}. Supported: {', '.join(allowed_extensions())}"
        )

    # ---------- Step 2: Read and save file ----------
    try:
        file_content = await file.read()
        file_hash = compute_file_hash(file_content)

        temp_dir = tempfile.mkdtemp(prefix="cad_upload_")
        file_path = os.path.join(temp_dir, file.filename)

        with open(file_path, "wb") as f:
            f.write(file_content)

        logger.info(f"File saved: {file_path}")

    except Exception as e:
        logger.error(f"File save failed: {e}")
        raise HTTPException(status_code=500, detail=f"File save failed: {str(e)}")

    # ---------- Step 3: Generate renders ----------
    images_result = {}

    try:
        from cadagent.services.renderer import generate_multi_view_images

        logger.info("Starting multi-view rendering...")

        render_results = await run_in_thread_pool(
            generate_multi_view_images,
            file_path,
            (1200, 900)
        )

        for view_name, view_data in render_results.items():
            if view_data.get('success') and view_data.get('image'):
                base64_image = base64.b64encode(view_data['image']).decode('utf-8')
                images_result[view_name] = ImageViewData(
                    base64=base64_image,
                    success=True,
                    name=view_data.get('name', view_name.title()),
                    error=None
                )
            else:
                images_result[view_name] = ImageViewData(
                    base64=None,
                    success=False,
                    name=view_data.get('name', view_name.title()),
                    error=view_data.get('error', 'Unknown error')
                )

        render_success = sum(1 for v in images_result.values() if v.success)
        logger.info(f"Rendering complete: {render_success}/4 views successful")

    except ImportError as e:
        logger.error(f"Renderer module import failed: {e}")
        for view_name in ['front', 'top', 'right', 'isometric']:
            images_result[view_name] = ImageViewData(
                base64=None, success=False, name=view_name.title(),
                error="Renderer module not available"
            )
        render_success = False
    except Exception as e:
        logger.error(f"Rendering failed: {e}")
        for view_name in ['front', 'top', 'right', 'isometric']:
            if view_name not in images_result:
                images_result[view_name] = ImageViewData(
                    base64=None, success=False, name=view_name.title(),
                    error=str(e)
                )
        render_success = False

    # ---------- Step 4: Create session ----------
    session_id = str(uuid.uuid4())
    session_data = SessionData(
        session_id=session_id,
        file_path=file_path,
        file_hash=file_hash,
        file_name=file.filename,
        render_completed=bool(render_success)
    )
    sessions[session_id] = session_data

    logger.info(f"Session created: {session_id}")

    # ---------- Step 5: Return response ----------
    return UploadRenderResponse(
        success=True,
        session_id=session_id,
        file_hash=file_hash,
        file_path=file_path,
        file_name=file.filename,
        images=images_result,
        render_success=bool(render_success > 0),
        message="File uploaded and rendered"
    )


@app.post("/extract_features", tags=["Feature Extraction"], response_model=ExtractFeaturesResponse)
async def extract_features(request: ExtractFeaturesRequest):
    """
    Extract B-Rep features from uploaded CAD model.
    """
    session_id = request.session_id
    logger.info(f"Feature extraction request: session_id={session_id}")

    # ---------- Step 1: Get session ----------
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found. Please upload file first.")

    session = sessions[session_id]

    # ---------- Step 1.5: read-only hit on the extraction cache (skips OCC
    # detection for the same file; nothing is written back here, so the
    # planning_input cache's rendered images are not overwritten) ----------
    ext_cached = extraction_cache.get(session.file_hash) if session.file_hash else None
    if ext_cached is not None and ext_cached.get("features_result"):
        features_result = ext_cached["features_result"]
        session.features_json = features_result
        session.extraction_completed = True
        logger.info("extract_features cache HIT (%s) — returning cached features",
                    session.file_hash[:8])
        return ExtractFeaturesResponse(
            session_id=session_id,
            features_json=features_result,
            extraction_success=True,
            error=None,
            message="Feature extraction completed (cached)"
        )

    # ---------- Step 2: Extract features ----------
    features_result = None
    error_message = None

    try:
        from cadagent.services.extractor import extract_features

        target_file_path = session.file_path

        # Convert STP/STEP to BREP when necessary
        if target_file_path.lower().endswith(('.stp', '.step')):
            from cadagent.services.convert_brep import convert_stp_to_brep_occ

            brep_path = target_file_path + ".brep"
            logger.info(f"Converting STEP to BREP: {target_file_path} -> {brep_path}")

            conversion_success = await run_in_thread_pool(
                convert_stp_to_brep_occ,
                target_file_path,
                brep_path
            )

            if not conversion_success:
                raise ValueError(f"Unable to convert the STEP file to BREP format for feature extraction.")

            target_file_path = brep_path
            logger.info("Conversion successful, proceeding with BREP extraction.")

        logger.info(f"Starting feature extraction for: {target_file_path}")

        success, result, error = await run_in_thread_pool(
            extract_features,
            target_file_path,
            600,
            False
        )

        if success and result:
            features_result = result
            session.features_json = result
            session.extraction_completed = True
            logger.info("Feature extraction completed")
        else:
            error_message = error
            logger.warning(f"Feature extraction failed: {error}")

    except ImportError as e:
        logger.error(f"Extractor module import failed: {e}")
        error_message = "Feature extraction module not available"
    except Exception as e:
        logger.error(f"Feature extraction failed: {e}")
        error_message = str(e)

    # ---------- Step 3: Return response ----------
    return ExtractFeaturesResponse(
        session_id=session_id,
        features_json=features_result,
        extraction_success=features_result is not None,
        error=error_message,
        message="Feature extraction completed" if features_result else "Feature extraction failed"
    )


def _merge_llm_suggestions(
    planning_request: Dict[str, Any],
    confidence: Dict[str, Any],
    warnings: List[str],
    suggestions: Dict[str, Any],
    explicit_material: Optional[str],
) -> tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """Merge the LLM engineering-intent suggestions into planning_request and
    update the confidence/warnings accordingly."""
    # 1. Material: apply the LLM suggestion only when material was not passed explicitly
    if not planning_request.get("material") and suggestions.get("material_suggestion"):
        planning_request["material"] = suggestions["material_suggestion"]
        confidence["material"] = "suggested"
        warnings.append(
            f"AI suggests material {suggestions['material_suggestion']}, confirm against the drawing."
        )
    elif explicit_material:
        confidence["material"] = "suggested"

    # 2. Step-segment tolerances / roughness
    seg_specs = suggestions.get("segment_specs", {})
    for seg in planning_request.get("segments", []):
        spec = seg_specs.get(seg["segment_id"])
        if not spec:
            continue
        applied = False
        if spec.get("diameter_upper_deviation_mm") is not None:
            seg["diameter_upper_deviation_mm"] = spec["diameter_upper_deviation_mm"]
            applied = True
        if spec.get("diameter_lower_deviation_mm") is not None:
            seg["diameter_lower_deviation_mm"] = spec["diameter_lower_deviation_mm"]
            applied = True
        if spec.get("roughness_ra") is not None:
            seg["roughness_ra"] = spec["roughness_ra"]
            applied = True
        if applied:
            seg["llm_suggested"] = True
    if seg_specs:
        confidence["tolerances"] = "suggested"
        confidence["roughness"] = "suggested"

    # 3. Heat treatment / hardness
    gr = planning_request.get("global_requirements", {})
    if suggestions.get("heat_treatment_suggestion"):
        gr["heat_treatment"] = suggestions["heat_treatment_suggestion"]
        confidence["heat_treatment"] = "suggested"
        warnings.append(
            f"AI suggests heat treatment {suggestions['heat_treatment_suggestion']}, confirm against the drawing."
        )
    if suggestions.get("target_hardness_hrc"):
        gr["target_hardness_hrc"] = suggestions["target_hardness_hrc"]

    # 4. Notes -> warnings
    for note in suggestions.get("notes", []):
        warnings.append(f"AI note: {note}")

    return planning_request, confidence, warnings


@app.get("/api/v1/planning-progress/{session_id}", tags=["Planning Input"])
async def planning_progress(session_id: str):
    """
    Query CAD import progress. The frontend polls this endpoint roughly every
    700ms while POST /api/v1/planning-input is running.

    Returns:
        {"progress": int, "current_step": str, "message": str}
    """
    entry = CAD_PROGRESS.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown planning session: {session_id}")
    return entry


@app.post("/api/v1/planning-input", tags=["Planning Input"], response_model=PlanningInputResponse)
async def planning_input(
    file: UploadFile = File(...),
    material: str = Form(default=None),
    suggest: bool = Form(default=True),
    session_id: str = Form(default=None),
):
    """
    Upload a CAD model and complete rendering + feature extraction + mapping
    to a peagent PlanningRequest draft in a single step.

    Called from the peagent frontend's "Import from CAD" entry point. The
    material suggestion is passed in through the ``material`` form field
    (optional); when omitted, ``confidence.material="required"`` is returned
    and the frontend asks the user to choose.

    Returns:
        PlanningInputResponse:
        - render_images: 4 base64 view images (for preview)
        - planning_request: peagent-compatible draft (material may be empty)
        - confidence: per-field confidence
        - warnings: items that need manual confirmation
        - validation: Pydantic validation result if the peagent backend is importable
    """
    def report(progress: int, current_step: str, message: str) -> None:
        """Write CAD import progress (only recorded when the frontend passed a
        session_id, so the polling endpoint can read it)."""
        if session_id:
            CAD_PROGRESS[session_id] = {
                "progress": progress,
                "current_step": current_step,
                "message": message,
            }

    def _gear_bridge(done: int, total: int) -> None:
        """Per-section callback for the gear Z-axis scan -> maps to the 25%-72% range."""
        if total > 0:
            p = 25 + int(47 * done / total)
            report(p, "extract", f"Scanning gear sections {done}/{total}...")

    try:
        report(0, "save", "Uploading & saving model...")
        # ---------- Step 1: Validate + save ----------
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions():
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format: {file_ext}. Supported: {', '.join(allowed_extensions())}"
            )
        try:
            file_content = await file.read()
            file_hash = compute_file_hash(file_content)
            temp_dir = tempfile.mkdtemp(prefix="cad_planning_input_")
            file_path = os.path.join(temp_dir, file.filename)
            with open(file_path, "wb") as f:
                f.write(file_content)
            logger.info(f"File saved: {file_path}")
        except Exception as e:
            logger.error(f"File save failed: {e}")
            raise HTTPException(status_code=500, detail=f"File save failed: {str(e)}")

        # ---------- Cache fast path: same file + same parameters (material/suggest) -> return previous result ----------
        resp_cache_key = f"{file_hash}|{material or ''}|{int(suggest)}"
        cached_resp = response_cache.get(resp_cache_key)
        if cached_resp is not None:
            logger.info("planning_input response cache HIT (%s, material=%r, suggest=%s) — "
                        "returning cached result, skipped render/OCC/LLM",
                        file_hash[:8], material, suggest)
            return PlanningInputResponse(
                success=True,
                source_file=file.filename,
                session_id=str(uuid.uuid4()),
                **cached_resp,
            )

        # ---------- Step 2+3: Render + Extract (extraction cache: same file skips OCC detection and rendering) ----------
        render_images: Dict[str, ImageViewData] = {}
        features_result = None
        extraction_error = None

        ext_cached = extraction_cache.get(file_hash)
        if ext_cached is not None:
            logger.info("planning_input extraction cache HIT (%s) — reusing render + features, "
                        "skipped OCC detection", file_hash[:8])
            render_images = ext_cached.get("render_images") or {}
            features_result = ext_cached.get("features_result")
        else:
            try:
                report(5, "render", "Rendering multi-view preview...")
                from cadagent.services.renderer import generate_multi_view_images
                render_results = await run_in_thread_pool(generate_multi_view_images, file_path, (1200, 900))
                for view_name, view_data in render_results.items():
                    if view_data.get('success') and view_data.get('image'):
                        render_images[view_name] = ImageViewData(
                            base64=base64.b64encode(view_data['image']).decode('utf-8'),
                            success=True, name=view_data.get('name', view_name.title()), error=None,
                        )
                    else:
                        render_images[view_name] = ImageViewData(
                            base64=None, success=False,
                            name=view_data.get('name', view_name.title()),
                            error=view_data.get('error', 'Unknown error'),
                        )
                report(20, "render", "Multi-view preview ready")
            except ImportError as e:
                logger.warning(f"Renderer unavailable, skipping render: {e}")
            except Exception as e:
                logger.warning(f"Rendering failed, continuing without images: {e}")

            try:
                from cadagent.services.extractor import extract_features as run_extraction

                target_file_path = file_path
                if target_file_path.lower().endswith(('.stp', '.step')):
                    from cadagent.services.convert_brep import convert_stp_to_brep_occ

                    brep_path = target_file_path + ".brep"
                    report(20, "convert", "Converting STEP to BREP...")
                    logger.info(f"Converting STEP -> BREP: {target_file_path} -> {brep_path}")
                    conversion_success = await run_in_thread_pool(
                        convert_stp_to_brep_occ, target_file_path, brep_path
                    )
                    if not conversion_success:
                        raise ValueError("Unable to convert the STEP file to BREP format for feature extraction.")
                    target_file_path = brep_path

                report(25, "extract", "Extracting features (gear scan may take 1-2 min)...")
                success, result, error = await run_in_thread_pool(
                    run_extraction, target_file_path, 600, False, progress_callback=_gear_bridge
                )
                if success and result:
                    features_result = result
                else:
                    extraction_error = error
            except Exception as e:
                logger.error(f"Feature extraction failed: {e}")
                extraction_error = str(e)

            # Only write back when detection succeeded (failed results are not
            # cached, to avoid reusing erroneous results)
            if features_result is not None:
                extraction_cache.put(file_hash, {
                    "features_result": features_result,
                    "render_images": render_images,
                })
                logger.info("planning_input extraction cache store (%s) — %d entries",
                            file_hash[:8], len(extraction_cache))

        # ---------- Step 4: Map to PlanningRequest ----------
        warnings: List[str] = []
        planning_request = None
        confidence = None
        validation_result = None

        if features_result:
            report(75, "map", "Mapping features to planning request...")
            mapped = map_features_to_planning_request(features_result, material=material)
            planning_request = mapped["planning_request"]
            confidence = mapped["confidence"]
            warnings = mapped["warnings"]
            report(80, "map", "Planning draft ready")

            # ---------- Step 5: LLM completion of engineering intent (material/tolerances/roughness/heat treatment) ----------
            if suggest and features_result:
                try:
                    report(80, "llm", "AI enriching engineering intent...")
                    # Pick one available rendered image as context (prefer isometric)
                    context_image = None
                    for view in ("isometric", "front"):
                        img = render_images.get(view)
                        if img and img.base64:
                            context_image = img.base64
                            break

                    suggestions = await suggest_engineering_fields(
                        features_result,
                        material_candidates=PEAGENT_MATERIAL_CANDIDATES,
                        isometric_image_base64=context_image,
                    )
                    if suggestions:
                        planning_request, confidence, warnings = _merge_llm_suggestions(
                            planning_request, confidence, warnings, suggestions, material
                        )
                except Exception as e:
                    logger.warning(f"LLM enrichment failed, skipped (does not affect geometric mapping): {e}")
                finally:
                    report(95, "llm", "AI enrichment done")

            # Strict validation against peagent's real model (best-effort)
            try:
                report(95, "validate", "Validating draft against peagent...")
                model, errors = validate_with_peagent(planning_request)
                validation_result = {
                    "peagent_valid": model is not None,
                    "errors": errors,
                }
                if errors:
                    warnings.append(f"peagent validation failed: {errors[0]}")
            except Exception as e:
                logger.warning(f"peagent validation skipped: {e}")
            finally:
                report(100, "done", "CAD analysis complete")

        resp_payload = {
            "render_images": render_images,
            "planning_request": planning_request,
            "confidence": confidence,
            "warnings": warnings,
            "extraction_success": features_result is not None,
            "validation": validation_result,
            "error": extraction_error,
            "message": "CAD analyzed and planning draft generated" if features_result else "CAD analysis failed",
        }
        # Only cache the full response when analysis succeeded (including the
        # LLM completion results); failed analyses are not cached
        if features_result is not None:
            response_cache.put(resp_cache_key, resp_payload)
            logger.info("planning_input response cache store (%s) — %d entries",
                        file_hash[:8], len(response_cache))

        return PlanningInputResponse(
            success=features_result is not None,
            source_file=file.filename,
            session_id=str(uuid.uuid4()),
            **resp_payload,
        )
    finally:
        if session_id:
            CAD_PROGRESS.pop(session_id, None)


def _build_system_prompt(session) -> str:
    """
    Build system prompt with feature summary for long-term memory.

    Instead of sending full features_json every time, we include a summary
    in the system prompt so LLM has background knowledge.
    """
    prompt = CAE_SYSTEM_PROMPT

    # Add feature summary if available
    if session.feature_summary:
        prompt += f"\n\n## Known Part Features (Background)\n{session.feature_summary}"
    elif session.features_json:
        # Fallback: use a brief summary from features_json
        try:
            dims = session.features_json.get('overall_dimensions', {})
            features = session.features_json.get('features', {})
            summary_parts = []

            if dims:
                summary_parts.append(f"Overall dimensions: {dims}")

            # Brief feature overview
            gear_count = len(features.get('gear_features', {}).get('parameters', []))
            if gear_count > 0:
                summary_parts.append(f"Gears: {gear_count}")

            cyl_count = len(features.get('outer_cylinders', []))
            if cyl_count > 0:
                summary_parts.append(f"Cylindrical sections: {cyl_count}")

            if summary_parts:
                prompt += f"\n\n## Part Background\n" + "\n".join(summary_parts)
        except Exception:
            pass

    return prompt


def _build_messages_for_llm(session, user_message: str, images: Dict = None,
                           features_json: Dict = None, initialize_context: bool = False) -> List[Dict]:
    """
    Build message list with layered memory management.

    Strategy:
    1. System prompt with feature summary (long-term memory)
    2. Conversation history with sliding window (short-term memory)
    3. Current user message (with optional images + features on first interaction).
       The actual user question is ALWAYS preserved — features and images are
       attached as supplementary context but never replace the user's intent.
    """
    messages = []

    # 1. System prompt with background
    messages.append({"role": "system", "content": _build_system_prompt(session)})

    # 2. Add conversation history (sliding window)
    history = session.get_history_for_llm()
    messages.extend(history)

    # 3. Build current user message.
    #    On the first interaction we attach features + images as supplementary
    #    multimodal context. The user's actual question is preserved as the
    #    first text part so the LLM understands what is being asked.
    if initialize_context and (images or features_json):
        content_parts: List[Dict[str, Any]] = []

        # 3a. User's actual question (always first and never lost)
        if user_message:
            content_parts.append({"type": "text", "text": user_message})

        # 3b. Attach feature JSON as supplementary text
        if features_json:
            features_text = (
                "\n\n## Supplementary Part Feature Data\n"
                "Use this data together with the rendered images and your "
                "engineering expertise to answer the question above.\n\n"
                "```json\n"
                f"{json.dumps(features_json, ensure_ascii=False, indent=2)}\n"
                "```"
            )
            content_parts.append({"type": "text", "text": features_text})

        # 3c. Attach rendered views as image_url parts
        if images:
            for view_name, base64_str in images.items():
                if base64_str:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_str}"},
                    })

        # Fallback: if neither question nor features/images produced any
        # content (shouldn't normally happen), still send something.
        if not content_parts:
            content_parts.append({"type": "text", "text": "(empty)"})

        # OpenAI API accepts either a string or a list of content parts for
        # the user message. Use the list form only when there are images;
        # otherwise a plain string keeps behaviour simple.
        if any(p.get("type") == "image_url" for p in content_parts):
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({
                "role": "user",
                "content": "\n".join(p["text"] for p in content_parts if p.get("type") == "text"),
            })
        session.context_initialized = True
    elif user_message:
        messages.append({"role": "user", "content": user_message})

    return messages


@app.post("/chat", tags=["AI Chat"])
async def chat(request: Request):
    """
    AI CAE expert chat with streaming response and layered memory management.

    Implementation notes:
    - The corporate LLM proxy does NOT support streaming; when the
      OpenAI SDK is invoked with ``stream=True`` the SDK silently discards
      the proxy's plain-JSON response and yields nothing. To work around
      this, we use ``stream=False`` to obtain the full response object,
      then wrap its ``choices[0].message.content`` in our own SSE envelope
      so the Chainlit frontend (which expects ``data: {...}\\n\\n`` lines)
      receives data promptly.
    - ``event_generator`` is wrapped in ``try/finally`` so the surrounding
      ``StreamingResponse`` always closes cleanly inside the request task,
      avoiding the PEP 525 GC-task mismatch that triggers anyio's
      "Attempted to exit cancel scope in a different task" RuntimeError.
    """
    try:
        body = await request.json()
        session_id = body.get('session_id')
        message = body.get('message', '')
        images = body.get('images', {}) or {}
        features_json = body.get('features_json')
        initialize_context = body.get('initialize_context', False)

        logger.info(f"Chat request: session_id={session_id}, "
                    f"init_ctx={initialize_context}, "
                    f"images={len(images)}, has_features={bool(features_json)}")

        if not message and not initialize_context:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        # Get session
        session = None
        if session_id and session_id in sessions:
            session = sessions[session_id]

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Store features_json if provided (long-term memory)
        if features_json and not session.features_json:
            session.features_json = features_json

        # Store images if provided (long-term memory)
        if images and not session.images:
            session.images = images

        # Build messages with layered memory
        llm_messages = _build_messages_for_llm(
            session=session,
            user_message=message,
            images=images if initialize_context else {},
            features_json=features_json if initialize_context else None,
            initialize_context=initialize_context,
        )

        logger.info(f"Building LLM request with {len(llm_messages)} messages, "
                   f"history turns: {len(session.conversation_history) // 2}")

        # Save the user's question to conversation history BEFORE streaming,
        # so that even if the client disconnects mid-stream the user's turn is
        # preserved.
        if message:
            session.add_message("user", message)

        # ---------- Async SSE generator ----------
        full_response = ""

        async def event_generator():
            nonlocal full_response
            # NOTE on httpx 0.28+ cancel-scope handling:
            #   Starlette injects ``GeneratorExit`` into this generator when
            #   the client disconnects. Wrapping the body in ``try/finally``
            #   guarantees cleanup happens in the *same* asyncio task that
            #   entered the cancel scope, so anyio stays happy and we don't
            #   trigger the "Attempted to exit cancel scope in a different
            #   task" RuntimeError.
            try:
                try:
                    # Use the async client so the SDK call is awaitable and
                    # the event loop stays free for Starlette to flush chunks.
                    client = create_async_openai_client()

                    # NOTE on gpt-5-nano (reasoning model):
                    #   * ``max_tokens`` is IGNORED by reasoning models — the
                    #     API only honours ``max_completion_tokens``, which
                    #     bounds the sum of reasoning tokens + visible answer
                    #     tokens.
                    #   * Without an explicit cap the model can spend the
                    #     entire budget on internal reasoning and never
                    #     produce any visible content.
                    # NOTE on streaming:
                    #   * We use ``stream=False`` because the corporate proxy
                    #     does not support streaming and silently drops the
                    #     response when stream=True is set. We then wrap the
                    #     full response in our own SSE envelope.
                    response = await client.chat.completions.create(
                        model=LLM_MODEL,
                        messages=llm_messages,
                        stream=False,
                        temperature=LLM_TEMPERATURE,
                        # NOTE: Removed max_completion_tokens constraint.
                        # Reasoning models (gpt-5-nano) may need unlimited output
                        # to produce visible content after internal reasoning.
                    )

                    # Extract the assistant text from the non-streaming response.
                    content = ""
                    raw_response = None
                    try:
                        # Diagnostic: log the raw response structure to understand what the API returns
                        raw_response = {
                            "model": getattr(response, 'model', None),
                            "choices": [
                                {
                                    "index": c.index,
                                    "message": {
                                        "role": getattr(c.message, 'role', None),
                                        "content": getattr(c.message, 'content', None),
                                    } if hasattr(c, 'message') else None,
                                    "finish_reason": getattr(c, 'finish_reason', None),
                                }
                                for c in (getattr(response, 'choices', []) or [])
                            ],
                            "usage": {
                                "prompt_tokens": getattr(response.usage, 'prompt_tokens', None) if hasattr(response, 'usage') else None,
                                "completion_tokens": getattr(response.usage, 'completion_tokens', None) if hasattr(response, 'usage') else None,
                                "total_tokens": getattr(response.usage, 'total_tokens', None) if hasattr(response, 'usage') else None,
                            }
                        }
                        logger.info(f"[DEBUG] LLM raw response structure: {json.dumps(raw_response, ensure_ascii=False)}")
                        
                        content = response.choices[0].message.content if response.choices else None
                        if content is None:
                            content = ""
                    except (AttributeError, IndexError, KeyError) as parse_exc:
                        logger.error(
                            f"Unexpected response shape from LLM: {parse_exc}; "
                            f"response={response!r}; raw_struct={raw_response!r}"
                        )

                    if content:
                        full_response = content
                        data_packet = {"content": content}
                        yield f"data: {json.dumps(data_packet, ensure_ascii=False)}\n\n"
                        logger.info(f"Streaming complete: {len(full_response)} chars")
                    else:
                        # Empty content: send error to frontend instead of silent [DONE]
                        # This prevents the frontend from showing a blank response bubble
                        usage_info = ""
                        if raw_response and raw_response.get("usage"):
                            u = raw_response["usage"]
                            usage_info = (
                                f" (usage: prompt={u.get('prompt_tokens')}, "
                                f"completion={u.get('completion_tokens')}, "
                                f"total={u.get('total_tokens')})"
                            )
                        
                        # Check if this looks like a reasoning model that spent all tokens on reasoning
                        if raw_response and raw_response.get("choices"):
                            finish_reason = raw_response["choices"][0].get("finish_reason", "")
                            if finish_reason == "length":
                                error_msg = (
                                    f"LLM response was truncated (finish_reason='length'). "
                                    f"The {LLM_MODEL} reasoning model may have exhausted its token budget "
                                    f"on internal reasoning, leaving no visible content. "
                                    f"Consider reducing the conversation history or features context."
                                )
                            else:
                                error_msg = (
                                    f"LLM returned empty content. "
                                    f"finish_reason={finish_reason!r}{usage_info}"
                                )
                        else:
                            error_msg = f"LLM returned empty content.{usage_info}"
                        
                        logger.warning(error_msg)
                        error_packet = {"error": error_msg}
                        yield f"data: {json.dumps(error_packet, ensure_ascii=False)}\n\n"

                    yield "data: [DONE]\n\n"

                    # Persist assistant reply in session memory.
                    if full_response:
                        session.add_message("assistant", full_response)

                except asyncio.CancelledError:
                    # Client disconnected mid-stream — log and propagate.
                    logger.warning("Streaming cancelled by client")
                    raise
                except GeneratorExit:
                    # Starlette closed the connection before we finished.
                    logger.warning("Streaming generator closed before completion")
                    raise
                except Exception as e:
                    logger.error(f"Streaming error: {e}", exc_info=True)
                    try:
                        error_packet = {"error": str(e)}
                        yield f"data: {json.dumps(error_packet, ensure_ascii=False)}\n\n"
                    except Exception:
                        # If the client has already gone away we cannot yield
                        # anything; swallow the secondary failure quietly.
                        pass
            finally:
                # Nothing explicit to close in the stream=False path, but
                # keep the finally block so the structure is robust if we
                # ever switch back to streaming.
                pass

        response = StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable proxy buffering
            },
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat_with_response", tags=["AI Chat"])
async def chat_with_response(request: Request):
    """
    Non-streaming chat endpoint for getting full response.
    Useful for generating feature summaries.
    """
    try:
        body = await request.json()
        session_id = body.get('session_id')
        message = body.get('message', '')
        images = body.get('images', {})
        features_json = body.get('features_json')
        initialize_context = body.get('initialize_context', False)

        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        session = None
        if session_id and session_id in sessions:
            session = sessions[session_id]

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Store features_json if provided
        if features_json and not session.features_json:
            session.features_json = features_json

        if images and not session.images:
            session.images = images

        # Build messages
        llm_messages = _build_messages_for_llm(
            session=session,
            user_message=message,
            images=images if initialize_context else {},
            features_json=features_json if initialize_context else None,
            initialize_context=initialize_context,
        )

        # Call LLM (non-streaming, async client keeps the event loop free)
        client = create_async_openai_client()
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=llm_messages,
            stream=False,
            temperature=LLM_TEMPERATURE,
        )

        ai_response = response.choices[0].message.content

        # Save to history
        if message:
            session.add_message("user", message)
        session.add_message("assistant", ai_response)

        return {"response": ai_response}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat_end", tags=["AI Chat"])
async def chat_end(payload: Dict[str, Any] = Body(...)):
    """
    Callback to save assistant response to conversation history.
    Called after streaming completes.

    NOTE: With the new async streaming implementation the assistant reply is
    already persisted by ``/chat`` itself.  This endpoint is kept for backward
    compatibility with the Chainlit frontend; duplicate adds are guarded.

    Payload (JSON body):
        {
            "session_id":          str,
            "assistant_response":  str  # may be very long (URL-encoded
                                        # query string used to truncate at
                                        # some proxies, so we now accept it
                                        # in the request body).
        }
    """
    session_id = payload.get("session_id")
    assistant_response = payload.get("assistant_response", "")
    if session_id and session_id in sessions:
        session = sessions[session_id]
        # Avoid duplicate append if the streaming endpoint already recorded it.
        last = session.conversation_history[-1] if session.conversation_history else None
        if not (last and last.role == "assistant" and last.content == assistant_response):
            session.add_message("assistant", assistant_response)
        logger.info(f"Saved assistant response to history, total turns: {len(session.conversation_history) // 2}")
    return {"success": True}


@app.post("/generate_summary", tags=["AI Chat"])
async def generate_summary(request: Request):
    """
    Generate a concise summary of features_json for long-term memory.
    This reduces token usage in subsequent requests.
    """
    try:
        body = await request.json()
        session_id = body.get('session_id')

        session = None
        if session_id and session_id in sessions:
            session = sessions[session_id]

        if not session or not session.features_json:
            raise HTTPException(status_code=404, detail="Session or features not found")

        # Generate summary using lightweight prompt
        summary_prompt = f"""Based on the following CAD part feature data, generate a concise summary
(within 200 characters) describing the key characteristics:

```json
{json.dumps(session.features_json, ensure_ascii=False, indent=2)}
```

Summary should include:
1. Part type and overall dimensions
2. Key geometric features (gears, cylinders, holes, etc.)
3. Important design parameters

Respond in English with a concise summary."""

        client = create_openai_client()
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            stream=False,
            temperature=0.3,  # Lower temp for summarization
            # NOTE: Removed max_completion_tokens constraint.
            # Reasoning models may need unlimited output.
        )

        summary = response.choices[0].message.content
        session.feature_summary = summary

        logger.info(f"Generated feature summary: {len(summary)} chars")
        return {"summary": summary}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Summary generation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/debug_proxy", tags=["Debug"])
async def debug_proxy(payload: Dict[str, Any] = Body(...)):
    """
    Raw diagnostic probe of the corporate LLM proxy.

    Bypasses the OpenAI SDK entirely and calls
    ``POST {LLM_BASE_URL_RAW}/chat/completions`` with a plain ``httpx``
    client and ``stream=False``.  The full response (status, headers,
    content-type, body) is logged and returned to the caller so we can see
    exactly what the upstream gateway sends.  Useful for diagnosing
    non-SSE proxies that silently drop streaming responses.

    Payload (JSON body):
        {
            "test_message": str  # optional, default "hello"
        }

    Returns:
        {
            "status_code": int,
            "headers": dict,
            "content_type": str,
            "body": str,           # raw response body
            "body_preview": str,   # first 500 chars (for quick inspection)
        }
    """
    api_key = os.getenv(LLM_API_KEY_ENV, "")
    base_url = LLM_BASE_URL_RAW
    test_message = payload.get("test_message", "hello")

    request_payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": test_message}],
        "stream": False,
        "temperature": LLM_TEMPERATURE,
        "max_completion_tokens": 256,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    logger.info("=" * 60)
    logger.info(f"/debug_proxy: probing {base_url}/chat/completions")
    logger.info(f"Authorization header present: {bool(api_key)} (length={len(api_key)})")
    logger.info(f"Payload model: {LLM_MODEL}, test_message: {test_message!r}")
    logger.info("=" * 60)

    # Use a short-lived httpx client so this endpoint never reuses the
    # SDK's connection pool or affects any in-flight streaming requests.
    async with httpx.AsyncClient(timeout=120.0) as probe_client:
        try:
            response = await probe_client.post(
                f"{base_url}/chat/completions",
                json=request_payload,
                headers=headers,
            )

            body_text = response.text
            content_type = response.headers.get("content-type", "")
            body_preview = body_text[:500]

            # Loud logging so the operator can see exactly what came back.
            logger.info(f"=== /debug_proxy response ===")
            logger.info(f"  status_code  = {response.status_code}")
            logger.info(f"  content_type = {content_type}")
            logger.info(f"  body length  = {len(body_text)} chars")
            logger.info(f"  body preview = {body_preview!r}")
            logger.info(f"  full headers = {dict(response.headers)}")
            logger.info(f"=== /debug_proxy end ===")

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "content_type": content_type,
                "body": body_text,
                "body_preview": body_preview,
            }
        except httpx.HTTPError as http_exc:
            logger.error(f"/debug_proxy HTTP error: {http_exc}", exc_info=True)
            return {
                "status_code": 0,
                "error": f"HTTP error: {http_exc!r}",
                "headers": {},
                "content_type": "",
                "body": "",
                "body_preview": "",
            }
        except Exception as exc:
            logger.error(f"/debug_proxy failed: {exc}", exc_info=True)
            return {
                "status_code": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "headers": {},
                "content_type": "",
                "body": "",
                "body_preview": "",
            }


@app.post("/clear_history", tags=["AI Chat"])
async def clear_history(session_id: str):
    """Clear conversation history for a session."""
    if session_id and session_id in sessions:
        session = sessions[session_id]
        session.clear_history()
        logger.info(f"Cleared conversation history for session {session_id}")
        return {"success": True}
    return {"success": False, "message": "Session not found"}


@app.get("/session/{session_id}", tags=["Session"])
async def get_session(session_id: str):
    """Get session data by ID."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = sessions[session_id]
    return {
        "session_id": session.session_id,
        "file_name": session.file_name,
        "file_hash": session.file_hash,
        "render_completed": session.render_completed,
        "extraction_completed": session.extraction_completed,
        "has_features": session.features_json is not None
    }


# ==============================================================================
# Application Entry Point
# ==============================================================================

if __name__ == "__main__":
    import uvicorn

    print("=" * 60)
    print("3D Model Feature Analyzer - FastAPI Backend")
    print("=" * 60)
    print("API Endpoints:")
    print("  POST /upload_and_render - Upload and render")
    print("  POST /extract_features  - Extract features")
    print("  POST /chat              - AI chat (streaming)")
    print("  POST /chat_end          - Save assistant response")
    print("  POST /generate_summary  - Generate feature summary")
    print("  POST /debug_proxy       - Raw probe of corporate LLM proxy")
    print("  GET  /health            - Health check")
    print("=" * 60)

    uvicorn.run(
        "cadagent.ui.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )