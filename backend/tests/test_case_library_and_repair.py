"""Regression tests for case-library APIs and route repair scheduling."""

import json

from database.case_db import CaseDB
from models.case import Case, CaseSearchRequest
from rules import build_route
from workflow.nodes.verification import VerificationNodesMixin


def _request_with_feature(feature: dict) -> dict:
    return {
        "material": "45",
        "blank_diameter_mm": 50,
        "segments": [{"segment_id": "S1", "diameter_mm": 30, "length_mm": 100}],
        "features": [feature],
        "global_requirements": {
            "heat_treatment": "quench_temper",
            "surface_treatment": "none",
            "batch_quantity": 1,
        },
    }


def _geometry_with_feature(feature: dict) -> dict:
    return {
        "total_length_mm": 100,
        "max_finished_diameter_mm": 30,
        "blank_diameter_mm": 50,
        "segments": [{
            "segment_id": "S1", "diameter_mm": 30, "length_mm": 100,
            "global_start_mm": 0, "global_end_mm": 100, "high_precision": False,
        }],
        "features": [feature],
        "warnings": [],
    }


def test_feature_repair_reuses_current_route_engine() -> None:
    """A feature repair must use the same split/precision rules as initial planning."""
    feature = {
        "feature_id": "F1", "feature_type": "bearing_seat", "global_position_mm": 30,
        "high_precision": True, "processing_timing": "undecided", "bearing_seat_tolerance": "IT6",
    }
    request = _request_with_feature(feature)
    geometry = _geometry_with_feature(feature)
    expected = build_route(request, geometry, {})

    repaired = VerificationNodesMixin._rule_based_repair(
        None, [], geometry, request, {},
        {"validation_issues": [{"error_code": "FEATURE_NOT_COVERED", "object_id": "F1"}]},
    )

    assert repaired == expected
    assert [op["name"] for op in repaired if op.get("feature_id") == "F1"] == [
        "Rough turn bearing seat", "Precision grind bearing seat",
    ]


def test_filtered_count_is_not_limited_by_page_size(tmp_path) -> None:
    """The list API's total must reflect filtering before pagination."""
    file_path = tmp_path / "cases.json"
    cases = [
        Case(case_id=f"C{i}", part_name=f"Part {i}", taxonomy_id="motor", industry="Auto", material="45")
        for i in range(3)
    ]
    file_path.write_text(json.dumps({"cases": [case.model_dump(mode="json") for case in cases]}), encoding="utf-8")
    db = CaseDB(file_path)
    request = CaseSearchRequest(industry="auto", limit=1, offset=1)

    assert len(db.search(request)) == 1
    assert db.count(request) == 3


def test_case_search_applies_keyword_industry_and_material_filters(tmp_path) -> None:
    """Library filters must narrow the result set together."""
    file_path = tmp_path / "cases.json"
    cases = [
        Case(case_id="A", part_name="Servo Motor Shaft", taxonomy_id="motor", industry="Industrial", material="45"),
        Case(case_id="B", part_name="ABS Motor Shaft", taxonomy_id="motor", industry="Automotive", material="40Cr"),
    ]
    file_path.write_text(json.dumps({"cases": [case.model_dump(mode="json") for case in cases]}), encoding="utf-8")
    db = CaseDB(file_path)

    results = db.search(CaseSearchRequest(keyword="servo", industry="industrial", material="45"))

    assert [case.case_id for case in results] == ["A"]
