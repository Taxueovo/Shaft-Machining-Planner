from copy import deepcopy

from rules.process_state import verify_process_states
from tests.test_production_reviews import request, workflow_state


def operation(number, name, stage, dimensions=None):
    return {"operation_no": number, "name": name, "stage": stage, "dimensions": dimensions or []}


def codes(result):
    return {i["error_code"] for i in result["issues"]}


def test_heat_invalidates_datum_and_explicit_recovery_restores_it():
    req = request(global_requirements={"heat_treatment": "induction_hardening"}).model_dump()
    route = [
        operation(1, "Heat Treatment", "heat_treatment"),
        operation(2, "Finish Grind OD", "precision_finish"),
    ]
    assert "FINISH_WITHOUT_DATUM_RECOVERY" in codes(verify_process_states(req, route))
    route.insert(1, operation(3, "Recover Datum", "datum_recovery"))
    assert verify_process_states(req, route)["passed"]
    assert verify_process_states(req, [route[0], route[2]], {"requires_datum_recovery": False})[
        "passed"
    ]


def test_final_inspection_cannot_be_used_to_hide_later_cutting():
    route = [
        operation(1, "Final Inspection", "inspection"),
        operation(2, "Finish Turning", "finish"),
    ]
    assert "MODIFICATION_AFTER_FINAL_INSPECTION" in codes(
        verify_process_states(request().model_dump(), route)
    )


def test_packaging_requires_final_inspection_and_remains_terminal():
    route = [operation(1, "Package", "packaging"), operation(2, "Final Inspection", "inspection")]
    result = verify_process_states(request().model_dump(), route)
    assert {"PACKAGING_WITHOUT_FINAL_INSPECTION", "OPERATION_AFTER_PACKAGING"} <= codes(result)


def test_dimensions_are_anchored_to_stock_and_known_drawing_objects():
    dim = {"object_id": "S1", "surface": "external", "before_mm": 55, "after_mm": 40}
    route = [operation(1, "Turn", "rough", [dim])]
    assert "DIMENSION_OUTSIDE_STOCK" in codes(verify_process_states(request().model_dump(), route))
    dim["object_id"] = "invented"
    assert "UNKNOWN_DIMENSION_OBJECT" in codes(verify_process_states(request().model_dump(), route))


def test_existing_bore_cannot_shrink_and_unknown_incoming_size_is_preserved():
    req = request(
        blank_type="hollow",
        blank_inner_diameter_mm=20,
        features=[
            {
                "feature_id": "B",
                "feature_type": "bore",
                "bore_diameter_mm": 22,
                "bore_length_mm": 50,
                "positioning_mode": "global_absolute",
                "global_position_mm": 0,
            }
        ],
    ).model_dump()
    dim = {"object_id": "B", "surface": "internal", "before_mm": 18, "after_mm": 19}
    route = [operation(1, "Bore", "rough", [dim])]
    assert "BORE_SMALLER_THAN_STOCK" in codes(verify_process_states(req, route))
    dim.update(before_mm=None, after_mm=22)
    result = verify_process_states(req, route)
    assert result["passed"] and any("unknown" in warning for warning in result["warnings"])


def test_verification_gate_and_rule_repair_use_the_state_checker():
    flow, _, state = workflow_state()
    changed = deepcopy(state)
    changed["process_route"].append(operation(999, "Unexpected cutting", "rough"))
    result = flow.verification(changed)
    assert not result["verification"]["process_state"]["passed"]
    assert result["verification"]["conclusion"] == "failed"
    repaired = flow._rule_based_repair(
        changed["process_route"], state["geometry"], state["request"], {}, result["verification"]
    )
    assert verify_process_states(state["request"], repaired)["passed"]


def test_drawing_overcut_has_operation_bound_counterexample():
    req = request(
        segments=[
            dict(
                segment_id="S1",
                diameter_mm=30,
                length_mm=100,
                diameter_upper_deviation_mm=0.02,
                diameter_lower_deviation_mm=-0.01,
            )
        ]
    ).model_dump()
    route = [
        operation(
            1,
            "Turn",
            "rough",
            [dict(object_id="S1", surface="external", before_mm=50, after_mm=29.98)],
        )
    ]
    result = verify_process_states(req, route)
    counterexample = next(
        c for c in result["counterexamples"] if c["constraint_id"] == "DRAWING_MATERIAL_OVERCUT"
    )
    assert counterexample["expected"]["lower_mm"] == 29.99
    assert counterexample["actual"]["after_mm"] == 29.98
    assert counterexample["operation_no"] == 1


def test_final_inspection_rejects_explicit_unfinished_diameter():
    req = request(
        segments=[
            dict(
                segment_id="S1",
                diameter_mm=30,
                length_mm=100,
                diameter_upper_deviation_mm=0.02,
                diameter_lower_deviation_mm=-0.01,
            )
        ]
    ).model_dump()
    route = [
        operation(
            1, "Finish Turning", "finish", [dict(object_id="S1", surface="external", after_mm=31)]
        ),
        operation(2, "Final Inspection", "inspection"),
    ]
    assert "FINAL_DIAMETER_OUTSIDE_DRAWING" in codes(verify_process_states(req, route))
    route[0]["dimensions"][0]["after_mm"] = 30.01
    assert verify_process_states(req, route)["passed"]


def test_unknown_drawing_limits_remain_unknown():
    route = [
        operation(1, "Turn", "finish", [dict(object_id="S1", surface="external", after_mm=29.9)]),
        operation(2, "Final Inspection", "inspection"),
    ]
    result = verify_process_states(request().model_dump(), route)
    assert result["passed"]
    assert any("limits incomplete" in warning for warning in result["warnings"])


def test_final_inspection_checks_dimensions_declared_in_its_own_record():
    req = request(
        segments=[
            dict(
                segment_id="S1",
                diameter_mm=30,
                length_mm=100,
                diameter_upper_deviation_mm=0.02,
                diameter_lower_deviation_mm=-0.01,
            )
        ]
    ).model_dump()
    route = [
        operation(
            1,
            "Final Inspection",
            "inspection",
            [dict(object_id="S1", surface="external", after_mm=31)],
        )
    ]
    assert "FINAL_DIAMETER_OUTSIDE_DRAWING" in codes(verify_process_states(req, route))


def test_input_segment_cannot_be_used_as_an_internal_surface():
    route = [operation(1, "Turn", "rough", [dict(object_id="S1", surface="internal", after_mm=20)])]
    assert "DRAWING_SURFACE_MISMATCH" in codes(verify_process_states(request().model_dump(), route))
