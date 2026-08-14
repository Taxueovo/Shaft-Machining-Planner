"""FastAPI application: route definitions and middleware configuration."""

from __future__ import annotations

import logging
import os
import signal
import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Unified environment configuration lives in the project root .env (one level above backend)
_load_dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
load_dotenv(_load_dotenv_path)
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

from models.workflow import PlanningRequest
from models.input import ChoicesRequest
from models.case import CaseSearchRequest
from models.process import RouteCustomizeRequest
from repositories import MACHINE_FILE, TOOL_FILE
from llm_client import llm_available
from service import PlanningService
from database.taxonomy_db import TaxonomyDB
from database.case_db import CaseDB

# ---- Service instances ----

service = PlanningService()
taxonomy_db = TaxonomyDB()
case_db = CaseDB()

# ---- FastAPI application ----

app = FastAPI(title="ShaftPlanner Backend", version="1.0.0", description="Motor shaft structured process planning backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False, allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ============================================================
# Health & Heartbeat
# ============================================================

@app.get("/health")
async def health() -> dict[str, Any]:
    checks = {"machine_db": MACHINE_FILE.is_file(), "tool_db": TOOL_FILE.is_file(), "llm_available": llm_available()}
    active_jobs = len([j for j in service.store.jobs.values() if j.get("status") in ("running", "queued", "waiting_user_choice")])
    return {"status": "ok" if all(checks.values()) else "degraded", "checks": checks, "machine_db_file": str(MACHINE_FILE), "tool_db_file": str(TOOL_FILE), "active_jobs": active_jobs, "total_jobs": len(service.store.jobs)}


@app.post("/api/v1/heartbeat")
async def heartbeat() -> dict[str, str]:
    """Browser heartbeat — resets watchdog timer."""
    service.touch_heartbeat()
    return {"status": "ok"}


@app.post("/api/v1/shutdown")
async def shutdown() -> dict[str, str]:
    """Graceful shutdown signal from frontend."""
    logger.info("Shutdown signal received from frontend.")
    os.kill(os.getpid(), signal.SIGTERM)
    return {"status": "shutting_down"}


# ============================================================
# Job API
# ============================================================

@app.post("/api/v1/jobs", status_code=202)
def create_job(request: PlanningRequest) -> dict[str, Any]:
    return {"job_id": service.create(request), "status": "queued", "message": "Task submitted."}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    try:
        job = service.store.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Task not found.") from error
    return {"job_id": job_id, "status": job["status"], "progress": job["progress"], "current_step": job["current_step"], "message": job["message"], "pending_choices": job["pending_choices"], "error": job["error"], "result_ready": job["result"] is not None}


@app.post("/api/v1/jobs/{job_id}/choices")
def submit_choices(job_id: str, request: ChoicesRequest) -> dict[str, Any]:
    try:
        service.resume(job_id, request)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Task not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"job_id": job_id, "status": "running", "message": "Choices submitted."}


@app.get("/api/v1/jobs/{job_id}/result")
def get_result(job_id: str) -> dict[str, Any]:
    try:
        return service.result(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Task not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/v1/jobs/{job_id}/process-card/export")
def export_process_card(job_id: str) -> dict[str, Any]:
    """Generate the process card Excel file and save it to the project output/ directory."""
    try:
        file_path = service.export_process_card_excel(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Task not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"file_path": str(file_path), "message": "Process card exported successfully."}


@app.post("/api/v1/jobs/{job_id}/process-route/customize")
def customize_process_route(job_id: str, request: RouteCustomizeRequest) -> dict[str, Any]:
    """Save the user-customized process route (route adjusted by the user before the process card is generated)."""
    try:
        operations = service.customize_route(job_id, request.operations)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Task not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"job_id": job_id, "operations": operations, "message": "Process route customized."}


@app.delete("/api/v1/jobs/{job_id}/process-route/customization")
def reset_process_route(job_id: str) -> dict[str, Any]:
    """Clear the user-customized route and restore the original route generated by the workflow."""
    try:
        service.reset_custom_route(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Task not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"job_id": job_id, "status": "ok", "message": "Custom route reset."}


@app.post("/api/v1/preview-route")
def preview_route(request_data: dict[str, Any]) -> dict[str, Any]:
    """Preview the process route in real time and validate machining capability against the local machine/tool libraries.

    This endpoint stays lightweight: it does not create jobs, call the LLM, or enter human-in-the-loop choice or auto-repair.
    """
    from rules import get_material_properties, is_feature_high_precision, is_high_precision
    from rules.engine import build_route
    from models.process import ResourceStatus
    from providers import HeatTreatmentProvider

    segments_raw = request_data.get("segments", [])
    features_raw = request_data.get("features", [])
    material = request_data.get("material", "45")
    blank_dia = request_data.get("blank_diameter_mm", 50)

    if not segments_raw:
        return {"route": [], "warnings": ["Please add at least one segment."]}

    # Compute segment global coordinates (reusing feature_analysis logic)
    segments = []
    cursor = 0.0
    for source in segments_raw:
        item = dict(source)
        item["global_start_mm"] = round(cursor, 3)
        cursor += float(item.get("length_mm", 0))
        item["global_end_mm"] = round(cursor, 3)
        item["high_precision"] = is_high_precision(
            item.get("diameter_upper_deviation_mm"),
            item.get("diameter_lower_deviation_mm"),
            item.get("roughness_ra"),
        )
        segments.append(item)

    total = round(cursor, 3)

    # Compute feature global coordinates
    features = []
    warnings = []
    for source in features_raw:
        item = dict(source)
        if item.get("positioning_mode") == "segment_relative":
            index = int(item.get("segment_index", 1))
            if index > len(segments) or index < 1:
                warnings.append(f"{item.get('feature_id', '?')} references non-existent segment {index}.")
                continue
            segment = segments[index - 1]
            offset = float(item.get("segment_offset_mm", 0))
            if offset > float(segment["length_mm"]):
                warnings.append(f"{item.get('feature_id', '?')} offset exceeds segment {index} length.")
                continue
            position = float(segment["global_start_mm"]) + offset
            item["resolved_segment_id"] = segment["segment_id"]
        else:
            position = float(item.get("global_position_mm", 0))
            segment = next(
                (s for s in segments if s["global_start_mm"] <= position <= s["global_end_mm"]), None
            )
            item["resolved_segment_id"] = segment["segment_id"] if segment else None

        item["global_position_mm"] = round(position, 3)
        item["high_precision"] = is_feature_high_precision(item)
        features.append(item)

    # Build the request and geometry dicts required by build_route
    global_req = request_data.get("global_requirements", {})
    heat_treatment = global_req.get("heat_treatment", "none")
    # Consistent with the formal request model: when heat treatment is unspecified for high-precision features, use the material recommendation.
    if heat_treatment == "none" and any(feature["high_precision"] for feature in features):
        heat_treatment = get_material_properties(material).get("recommended_heat_treatment", "none")
    req = {
        "material": material,
        "blank_diameter_mm": blank_dia,
        "blank_type": request_data.get("blank_type", "solid"),
        "blank_inner_diameter_mm": request_data.get("blank_inner_diameter_mm"),
        "segments": segments_raw,
        "features": features_raw,
        "global_requirements": {
            "heat_treatment": heat_treatment,
            "heat_treatment_note": global_req.get("heat_treatment_note"),
            "target_hardness_hrc": global_req.get("target_hardness_hrc"),
            "case_depth_mm": global_req.get("case_depth_mm"),
            "blank_condition": global_req.get("blank_condition", "unknown"),
            "pre_heat_treatment": global_req.get("pre_heat_treatment", "auto"),
            "surface_treatment": global_req.get("surface_treatment", "none"),
            "batch_quantity": global_req.get("batch_quantity", 1),
        },
    }
    geo = {
        "total_length_mm": total,
        "max_finished_diameter_mm": max((float(s["diameter_mm"]) for s in segments), default=0),
        "blank_diameter_mm": blank_dia,
        "segments": segments,
        "features": features,
        "warnings": warnings,
    }
    heat_treatment_decision = HeatTreatmentProvider().recommend(req, geo)
    req["heat_treatment_plan"] = heat_treatment_decision
    warnings.extend(heat_treatment_decision["trace"]["warnings"])

    try:
        route = build_route(req, geo, {})
    except Exception as e:
        return {"route": [], "warnings": [f"Route generation error: {e}"]}

    # Uses the same local resource-matching rules as the formal workflow, but does not trigger LLM ranking or job state changes.
    machine = service.workflow.machine_repo.search_turning(total, float(blank_dia))
    process_categories = sorted({
        operation["process_category"]
        for operation in route
        if operation.get("process_category")
    })
    tool_checks: dict[str, dict[str, Any]] = {}
    machine_checks: dict[str, dict[str, Any]] = {}
    resource_notes: list[str] = []
    for process in process_categories:
        if process == "Heat Treatment":
            continue
        machine_checks[process] = service.workflow.machine_repo.search_process(
            process, total, float(blank_dia)
        )
        try:
            tool_checks[process] = service.workflow.tool_repo.search(material, process)
        except ValueError as error:
            tool_checks[process] = {
                "conclusion": ResourceStatus.unknown.value,
                "message": str(error),
                "recommendations": [],
            }
            resource_notes.append(f"{process}: {error}")

    if any(feature.get("feature_type") == "knurl" for feature in features):
        resource_notes.append("Knurl tool specs are not covered in the current tool material table; engineer confirmation is required.")

    turning_tools = tool_checks.get("ISO Turning", {})
    critical_ok = (
        machine["conclusion"] == ResourceStatus.satisfied.value
        and turning_tools.get("conclusion") == ResourceStatus.satisfied.value
    )
    capability = {
        "critical_ok": critical_ok,
        "overall": ResourceStatus.satisfied.value if critical_ok else ResourceStatus.not_satisfied.value,
        "machine": machine,
        "machine_checks": machine_checks,
        "tool_checks": tool_checks,
        "notes": resource_notes,
    }

    operation_resources: list[dict[str, Any]] = []
    partial_verification_count = 0
    for operation in route:
        process = operation.get("process_category")
        if process is None:
            status, recommendations, machine_recommendations, note = (
                ResourceStatus.not_applicable.value,
                [],
                [],
                "No tool or equipment verification is needed for this operation.",
            )
        elif process == "Heat Treatment":
            status, recommendations, machine_recommendations, note = (
                ResourceStatus.not_applicable.value,
                [],
                [],
                "Heat-treatment resource matching is intentionally out of scope for this route.",
            )
        elif process in tool_checks:
            tool_check = tool_checks[process]
            recommendations = tool_check.get("recommendations", [])
            machine_check = machine_checks[process]
            machine_recommendations = machine_check.get("active_matches", [])
            status = (
                tool_check["conclusion"]
                if machine_check["conclusion"] == ResourceStatus.satisfied.value
                else ResourceStatus.not_satisfied.value
            )
            note = f"Machine: {machine_check['message']} Tool: {tool_check.get('message', '')}"
        else:
            status, recommendations, machine_recommendations, note = (
                ResourceStatus.not_covered.value,
                [],
                [],
                "The current local tool library does not cover this process.",
            )
        if status not in (ResourceStatus.satisfied.value, ResourceStatus.not_applicable.value):
            partial_verification_count += 1
        operation_resources.append({
            "operation_no": operation["operation_no"],
            "operation_name": operation["name"],
            "process_category": process,
            "verification_status": status,
            "tool_recommendations": recommendations,
            "machine_recommendations": machine_recommendations,
            "note": note,
        })

    if not critical_ok:
        resource_notes.append("Critical turning capability is not satisfied by the active local machine and tool libraries.")

    return {
        "route": route,
        "total_length_mm": total,
        "heat_treatment_decision": heat_treatment_decision,
        "warnings": warnings + resource_notes,
        "capability": capability,
        "resource_selection": {
            "turning_machine_candidates": machine.get("active_matches", []),
            "operation_resources": operation_resources,
            "partial_verification_count": partial_verification_count,
            "scope_note": "Preview checks local machine capacity for machining operations plus material/process tool-grade coverage. Heat-treatment resource matching is intentionally skipped; the route still preserves its process constraints and engineering warnings.",
        },
    }


# ============================================================
# Materials & Tools API
# ============================================================

@app.get("/api/v1/materials")
def list_materials() -> dict[str, Any]:
    materials = [
        {"value": "45", "label": "45 Steel", "category": "P", "description": "Quality carbon structural steel, most common motor shaft material"},
        {"value": "40Cr", "label": "40Cr", "category": "P", "description": "Alloy structural steel, good comprehensive properties after quench and temper"},
        {"value": "42CrMo", "label": "42CrMo", "category": "P", "description": "High-strength alloy steel, first choice for heavy-duty shafts"},
        {"value": "35CrMo", "label": "35CrMo", "category": "P", "description": "Medium-carbon alloy steel, high fatigue strength"},
        {"value": "20Cr", "label": "20Cr", "category": "P", "description": "Carburizing steel, used for camshafts and wear-resistant parts"},
        {"value": "20CrMnTi", "label": "20CrMnTi", "category": "P", "description": "Carburizing steel, hard surface tough core"},
        {"value": "Q235", "label": "Q235", "category": "P", "description": "Plain carbon steel, low-cost light-duty shaft"},
        {"value": "45Mn2", "label": "45Mn2", "category": "P", "description": "Quenched and tempered steel, high-strength medium-duty shaft"},
        {"value": "303", "label": "303 Stainless Steel", "category": "M", "description": "Free-machining austenitic stainless steel, excellent machinability"},
        {"value": "304", "label": "304 Stainless Steel", "category": "M", "description": "Austenitic stainless steel, corrosion-resistant"},
        {"value": "316", "label": "316 Stainless Steel", "category": "M", "description": "Acid-alkali resistant, chemical equipment shaft"},
        {"value": "2Cr13", "label": "2Cr13", "category": "M", "description": "Martensitic stainless steel, can be strengthened by heat treatment"},
        {"value": "1Cr17Ni2", "label": "1Cr17Ni2", "category": "M", "description": "High-strength stainless steel"},
        {"value": "GCr15", "label": "GCr15", "category": "H", "description": "High-carbon chromium bearing steel, high hardness high wear resistance"},
        {"value": "GCr15SiMn", "label": "GCr15SiMn", "category": "H", "description": "Large section bearing steel"},
        {"value": "6061", "label": "6061 Aluminum", "category": "N", "description": "Lightweight shaft, easy to cut"},
        {"value": "7075", "label": "7075 Aluminum", "category": "N", "description": "High-strength aluminum alloy, aerospace shaft"},
        {"value": "H62", "label": "H62 Brass", "category": "N", "description": "Easy to cut, corrosion resistant"},
    ]
    return {"materials": materials}


@app.get("/api/v1/materials/price")
def get_material_price(
    material: str, blank_type: str = "solid",
    outer_dia: float = 50, inner_dia: float = 0, length: float = 100,
) -> dict[str, Any]:
    """Estimate material cost."""
    from rules.pricing import estimate_material_cost
    return estimate_material_cost(
        material, blank_type, outer_dia,
        inner_dia if inner_dia > 0 else None, length,
    )


@app.get("/api/v1/tools")
def list_tools() -> dict[str, Any]:
    return {"tools": service.workflow.tool_registry.list_tools()}


@app.post("/api/v1/tools/{tool_name}")
def call_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"tool": tool_name, "result": service.workflow.tool_registry.call(tool_name, **params)}
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {type(error).__name__}: {error}") from error


@app.get("/api/v1/agents")
def list_agents() -> dict[str, Any]:
    return {"agents": service.workflow.agent_registry.list_agents()}


@app.get("/api/v1/orchestrator/status")
def orchestrator_status() -> dict[str, Any]:
    wf = service.workflow
    return {"orchestrator": wf.orchestrator.get_status(), "tools": wf.tool_registry.list_tools(), "guardrails_rules": len(wf.guardrails._rules)}


@app.get("/api/v1/prompts")
def list_prompts() -> dict[str, Any]:
    return {"templates": service.workflow.prompt_manager.list_templates()}


# ============================================================
# Taxonomy API
# ============================================================

@app.get("/api/v1/taxonomy")
def get_taxonomy() -> dict[str, Any]:
    return {"nodes": [n.model_dump() for n in taxonomy_db.get_tree().nodes]}


@app.get("/api/v1/taxonomy/{node_id}")
def get_taxonomy_node(node_id: str) -> dict[str, Any]:
    node = taxonomy_db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Taxonomy node not found.")
    return {"node": node.model_dump(), "children": [n.model_dump() for n in taxonomy_db.get_children(node_id)], "path": [n.model_dump() for n in taxonomy_db.get_path(node_id)]}


@app.get("/api/v1/taxonomy/{node_id}/cases")
def get_taxonomy_cases(node_id: str, industry: Optional[str] = None, material: Optional[str] = None) -> dict[str, Any]:
    node = taxonomy_db.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Taxonomy node not found.")
    descendants = taxonomy_db.get_all_descendants(node_id)
    cases = case_db.get_by_taxonomy_recursive([node_id] + [d.id for d in descendants])
    if industry:
        cases = [case for case in cases if case.industry.casefold() == industry.casefold()]
    if material:
        cases = [case for case in cases if case.material.casefold() == material.casefold()]
    return {"cases": [c.model_dump() for c in cases], "total": len(cases)}


# ============================================================
# Case Library API
# ============================================================

@app.get("/api/v1/cases")
def list_cases(taxonomy_id: Optional[str] = None, industry: Optional[str] = None, material: Optional[str] = None, keyword: Optional[str] = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    search_request = CaseSearchRequest(keyword=keyword, taxonomy_id=taxonomy_id, industry=industry, material=material, limit=limit, offset=offset)
    cases = case_db.search(search_request)
    return {"cases": [c.model_dump() for c in cases], "total": case_db.count(search_request)}


@app.get("/api/v1/cases/filters")
def get_case_filters() -> dict[str, Any]:
    return {"industries": case_db.get_industries(), "materials": case_db.get_materials()}


@app.get("/api/v1/cases/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    case = case_db.get_by_id(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return {"case": case.model_dump()}


@app.post("/api/v1/cases/save-from-form", status_code=201)
async def save_case_from_form(request: Request) -> dict[str, Any]:
    from models.case import Case as CaseModel
    try:
        body = await request.json()
        case_id = f"USER-{uuid.uuid4().hex[:6].upper()}"
        case_data = {
            "case_id": case_id,
            "part_name": body.get("part_name", "Custom Shaft"),
            "taxonomy_id": body.get("taxonomy_id", "other"),
            "industry": body.get("industry", "Custom"),
            "material": body.get("material", "45"),
            "tolerance": body.get("tolerance"),
            "description": body.get("description"),
            "main_features": body.get("main_features", []),
            "process_plan": body.get("process_plan", []),
            "segments": body.get("segments", []),
            "features": body.get("features", []),
        }
        case = CaseModel(**case_data)
        created = case_db.create(case)
        return {"case": created.model_dump(), "message": "Case saved successfully."}
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


# ============================================================
# RAG management API (optional module - load failure does not affect the main app)
# ============================================================

_rag_logger = logging.getLogger(__name__)
_RAG_AVAILABLE = False

try:
    from rag.routes import rag_router
    app.include_router(rag_router, prefix="/api/v1/rag")
    _RAG_AVAILABLE = True
    _rag_logger.info("RAG management API mounted at /api/v1/rag")
except ImportError as e:
    _rag_logger.info("RAG module not available (missing dependency): %s", e)
except Exception as e:
    _rag_logger.warning("RAG management API failed to load: %s", e)


@app.get("/api/v1/rag-health")
def rag_health() -> dict:
    """Lets the frontend check whether the RAG module is available."""
    return {"available": _RAG_AVAILABLE}
