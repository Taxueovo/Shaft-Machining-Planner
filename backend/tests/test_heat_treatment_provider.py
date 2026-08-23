"""Tests for heat-treatment knowledge retrieval and route constraints."""

from providers import HeatTreatmentProvider
from rules import build_route


def _request(**global_requirement_overrides):
    requirements = {
        "heat_treatment": "quench_temper",
        "surface_treatment": "none",
        "batch_quantity": 1,
        **global_requirement_overrides,
    }
    return {
        "material": "45",
        "blank_diameter_mm": 50,
        "segments": [{"segment_id": "S01", "diameter_mm": 30, "length_mm": 100}],
        "features": [],
        "global_requirements": requirements,
    }


def _geometry():
    return {
        "total_length_mm": 100,
        "max_finished_diameter_mm": 30,
        "blank_diameter_mm": 50,
        "segments": [{"segment_id": "S01", "diameter_mm": 30, "length_mm": 100}],
        "features": [],
        "warnings": [],
    }


def test_quench_temper_has_no_implicit_normalizing():
    decision = HeatTreatmentProvider().recommend(_request(), _geometry())

    assert decision["process_name"] == "Quench and Temper"
    assert decision["pre_treatment"] is None
    assert decision["requires_datum_recovery"] is True
    assert "Target hardness is not specified" in decision["trace"]["warnings"][0]


def test_forged_blank_auto_adds_normalizing_and_route_uses_it():
    request = _request(blank_condition="forged")
    geometry = _geometry()
    decision = HeatTreatmentProvider().recommend(request, geometry)
    route = build_route({**request, "heat_treatment_plan": decision}, geometry, {})

    assert decision["pre_treatment"]["type"] == "normalizing"
    assert "Normalizing Pre-treatment" in [operation["name"] for operation in route]


def test_carburize_requires_case_depth_and_hardness_confirmation():
    request = _request(heat_treatment="carburize_quench")
    decision = HeatTreatmentProvider().recommend(request, _geometry())

    assert decision["requires_hard_finish"] is False
    assert any("Surface hardness" in warning for warning in decision["trace"]["warnings"])
    assert any("case depth" in warning.lower() for warning in decision["trace"]["warnings"])


def test_nitriding_profile_and_warning():
    decision = HeatTreatmentProvider().recommend(_request(heat_treatment="nitriding"), _geometry())

    assert decision["process_name"] == "Nitriding"
    assert decision["requires_datum_recovery"] is True
    assert any(
        "Nitriding target surface hardness" in warning for warning in decision["trace"]["warnings"]
    )


def test_induction_hardening_profile_and_warning():
    decision = HeatTreatmentProvider().recommend(
        _request(heat_treatment="induction_hardening"), _geometry()
    )

    assert decision["process_name"] == "Induction Hardening"
    assert decision["requires_datum_recovery"] is True
    assert any(
        "Induction hardening target hardness" in warning
        for warning in decision["trace"]["warnings"]
    )


def test_nitriding_route_uses_grind_only_finish():
    request = _request(heat_treatment="nitriding", target_hardness_hrc=58)
    geometry = _geometry()
    decision = HeatTreatmentProvider().recommend(request, geometry)
    route = build_route({**request, "heat_treatment_plan": decision}, geometry, {})
    names = [operation["name"] for operation in route]

    assert "Heat Treatment" in names
    assert "Finish Grind OD" in names
    assert decision["process_name"] == "Nitriding"
