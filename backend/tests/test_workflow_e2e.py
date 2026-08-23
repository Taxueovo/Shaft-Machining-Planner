"""End-to-end workflow test: POST-style job lifecycle through the LangGraph pipeline.

Runs in deterministic rules mode (conftest sets LLM_PROVIDER=rules), so no real model
API is called. Exercises the path that unit tests normally bypass:
  PlanningService.create -> executor -> workflow graph -> result() -> process card export.
"""

from __future__ import annotations

import time

import pytest

from models.process import ProcessStage
from models.workflow import PlanningRequest


@pytest.fixture()
def service():
    from service import PlanningService

    svc = PlanningService()
    svc.touch_heartbeat()
    try:
        yield svc
    finally:
        svc._watchdog_active = False
        svc.executor.shutdown(wait=False)


def _simple_request() -> PlanningRequest:
    return PlanningRequest(
        material="45",
        blank_type="solid",
        blank_diameter_mm=50,
        segments=[
            {"segment_id": "S1", "diameter_mm": 30, "length_mm": 100},
        ],
        features=[],
        global_requirements={
            "heat_treatment": "quench_temper",
            "surface_treatment": "none",
            "batch_quantity": 1,
        },
    )


def _wait_terminal(service, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = service.store.get(job_id)
        if job["status"] in {"completed", "resource_mismatch", "failed"}:
            return job
        service.touch_heartbeat()
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not reach a terminal state within {timeout}s")


def test_workflow_rules_mode_end_to_end(service):
    request = _simple_request()
    job_id = service.create(request)

    job = _wait_terminal(service, job_id)
    assert job["status"] == "completed", job.get("message") or job.get("error")

    result = job["result"]
    route = result.get("process_route", [])
    assert route, "workflow produced an empty route"
    names = [op["name"] for op in route]
    stages = {op["stage"] for op in route}
    for mandatory in (
        "Blanking",
        "Face Turning",
        "Rough Turning",
        "Finish Turning",
        "Final Inspection",
    ):
        assert mandatory in names, f"missing mandatory operation {mandatory}"
    assert ProcessStage.heat_treatment.value in stages
    assert ProcessStage.inspection.value in stages

    # Operation numbers are continuous and unique.
    nos = [op["operation_no"] for op in route]
    assert nos == list(range(1, len(route) + 1))

    # The result is downloadable as a process card (export + download both work).
    card_path = service.export_process_card_excel(job_id)
    assert card_path.exists()
    assert card_path.suffix == ".xlsx"
    assert card_path.stat().st_size > 0


def test_workflow_hitl_choice_and_resume(service):
    # A high-precision keyway triggers the precision_choice HITL interrupt.
    request = PlanningRequest(
        material="45",
        blank_type="solid",
        blank_diameter_mm=50,
        segments=[{"segment_id": "S1", "diameter_mm": 30, "length_mm": 100}],
        features=[
            {
                "feature_id": "F1",
                "feature_type": "keyway",
                "positioning_mode": "global_absolute",
                "global_position_mm": 50,
                "tolerance_upper_mm": 0.01,
                "tolerance_lower_mm": -0.01,
                "roughness_ra": 0.4,
                "processing_timing": "undecided",
                "keyway_width_mm": 10,
                "keyway_depth_mm": 5,
                "feature_length_mm": 50,
                "high_precision": True,
            }
        ],
        global_requirements={
            "heat_treatment": "none",
            "surface_treatment": "none",
            "batch_quantity": 1,
        },
    )
    job_id = service.create(request)

    deadline = time.time() + 20
    while time.time() < deadline:
        job = service.store.get(job_id)
        if job["status"] == "waiting_user_choice":
            break
        service.touch_heartbeat()
        time.sleep(0.1)
    assert job["status"] == "waiting_user_choice", f"expected HITL wait, got {job['status']}"

    pending = job["pending_choices"]
    assert pending, "expected at least one pending choice"

    from models.input import ChoicesRequest

    service.resume(
        job_id,
        ChoicesRequest(
            choices=[
                {"feature_id": c["feature_id"], "processing_timing": "before_heat_treatment"}
                for c in pending
            ]
        ),
    )

    job = _wait_terminal(service, job_id)
    assert job["status"] == "completed", job.get("message") or job.get("error")
    assert job["result"]["process_route"]
