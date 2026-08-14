"""
Verifies that cad_mapper mapping results are consistent with the peagent
input models.

How to run (py310 conda environment):
    cd ShaftPlanner/cad_agent
    ~/.conda/envs/py310/python.exe -m pytest tests/test_cad_mapper.py -v

Notes:
- The peagent backend directory is injected via sys.path to load the real
  PlanningRequest.
- The fixture structure strictly mirrors the output of cad_agent
  ``Scripts/main_extractor.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# ---- Add cad_agent to path (so it can be imported as a package) ----
CAD_AGENT_DIR = Path(__file__).resolve().parents[1]
if str(CAD_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(CAD_AGENT_DIR))

# ---- Add the peagent backend to path (loads the real PlanningRequest) ----
PEAGENT_BACKEND_DIR = CAD_AGENT_DIR.parent / "backend"
if str(PEAGENT_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(PEAGENT_BACKEND_DIR))

from services.cad_mapper import map_features_to_planning_request  # noqa: E402
from models.workflow import PlanningRequest  # noqa: E402
from models.input import FeatureInput  # noqa: E402


# ==============================================================================
# Fixtures
# ==============================================================================

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def shaft_features() -> dict:
    return _load("shaft_features.json")


# ==============================================================================
# Basic mapping validation
# ==============================================================================

class TestBasicMapping:
    def test_segments_mapped_in_order(self, shaft_features):
        result = map_features_to_planning_request(shaft_features, material="45")
        req = result["planning_request"]
        assert [s["segment_id"] for s in req["segments"]] == ["S01", "S02", "S03"]
        assert [s["diameter_mm"] for s in req["segments"]] == [60.0, 50.0, 45.0]
        assert [s["length_mm"] for s in req["segments"]] == [80.0, 70.0, 30.0]

    def test_keyway_and_hole_mapped(self, shaft_features):
        result = map_features_to_planning_request(shaft_features, material="45")
        req = result["planning_request"]
        types = {f["feature_type"]: f for f in req["features"]}
        # The bore feature is also generated alongside the others
        assert {"keyway", "hole", "bore"} <= set(types)
        assert types["keyway"]["keyway_width_mm"] == 8.0
        assert types["keyway"]["keyway_depth_mm"] == 4.0
        assert types["keyway"]["global_position_mm"] == 130.0
        assert types["hole"]["hole_diameter_mm"] == 6.0
        assert types["hole"]["hole_type"] == "through"
        assert types["hole"]["hole_direction"] == "radial"
        assert types["hole"]["global_position_mm"] == 45.0

    def test_hollow_blank_from_inner_bore(self, shaft_features):
        result = map_features_to_planning_request(shaft_features, material="45")
        req = result["planning_request"]
        assert req["blank_type"] == "hollow"
        assert req["blank_inner_diameter_mm"] == 20.0
        assert req["blank_diameter_mm"] >= max(s["diameter_mm"] for s in req["segments"])

    def test_geometry_confidence_high(self, shaft_features):
        result = map_features_to_planning_request(shaft_features)
        assert result["confidence"]["geometry"] == "high"
        # Marked as required when no material is given
        assert result["confidence"]["material"] == "required"
        assert result["planning_request"]["material"] == ""

    def test_material_injected(self, shaft_features):
        result = map_features_to_planning_request(shaft_features, material="40Cr")
        assert result["planning_request"]["material"] == "40Cr"
        assert result["confidence"]["material"] == "suggested"

    def test_passes_peagent_validation(self, shaft_features):
        result = map_features_to_planning_request(shaft_features, material="45")
        model = PlanningRequest.model_validate(result["planning_request"])
        assert len(model.segments) == 3
        assert len(model.features) == 3  # keyway + hole + bore

    def test_peagent_features_all_valid(self, shaft_features):
        result = map_features_to_planning_request(shaft_features, material="45")
        for f in result["planning_request"]["features"]:
            # FeatureInput validation (includes per-type required-field checks)
            FeatureInput.model_validate(f)


# ==============================================================================
# Gear + spline mapping
# ==============================================================================

class TestGearAndSplineMapping:
    def _features_json_with_gear_and_spline(self) -> dict:
        base = _load("shaft_features.json")
        feats = base["features"]
        feats["spline_zone"] = {
            "detected": True,
            "z_ranges": [{"z_start": 160.0, "z_end": 180.0}],
            "approx_outer_radius": 23.0,
            "outer_cylinder_count": 20,
            "parameters": {
                "spline_type": "involute",
                "tooth_count": 24,
                "major_diameter": 46.0,
                "minor_diameter": 40.0,
                "module": 1.5,
                "pressure_angle": 30.0,
                "key_width_B": 2.36,
            },
        }
        feats["gear_features"] = {
            "detected": True,
            "gear_count": 1,
            "gear_zones": [{"position_start": 80.0, "position_end": 120.0, "mid_position": 100.0}],
            "parameters": [{
                "tooth_count": 36,
                "module": 2.5,
                "addendum_radius": 47.5,
                "dedendum_radius": 43.0,
                "tooth_height": 4.5,
                "pressure_angle": 20.0,
                "gear_type": "spur",
                "helix_angle": 0.0,
            }],
        }
        return base

    def test_gear_mapped(self):
        features_json = self._features_json_with_gear_and_spline()
        result = map_features_to_planning_request(features_json, material="45")
        gear = next(f for f in result["planning_request"]["features"] if f["feature_type"] == "gear_teeth")
        assert gear["gear_teeth"] == 36
        assert gear["gear_module"] == 2.5
        assert gear["gear_pressure_angle"] == 20.0
        assert gear["gear_face_width_mm"] == 40.0  # 120 - 80
        assert gear["global_position_mm"] == 80.0

    def test_spline_mapped(self):
        features_json = self._features_json_with_gear_and_spline()
        result = map_features_to_planning_request(features_json, material="45")
        spline = next(f for f in result["planning_request"]["features"] if f["feature_type"] == "spline")
        assert spline["spline_type"] == "involute"
        assert spline["spline_teeth"] == 24
        assert spline["spline_module"] == 1.5
        assert spline["feature_length_mm"] == 20.0  # 180 - 160
        assert spline["global_position_mm"] == 160.0

    def test_gear_and_spline_pass_validation(self):
        features_json = self._features_json_with_gear_and_spline()
        result = map_features_to_planning_request(features_json, material="45")
        model = PlanningRequest.model_validate(result["planning_request"])
        types = {f.feature_type for f in model.features}
        assert {"gear_teeth", "spline", "keyway", "hole"} <= types

    def test_unknown_spline_type_falls_back(self):
        features_json = self._features_json_with_gear_and_spline()
        features_json["features"]["spline_zone"]["parameters"]["spline_type"] = "unknown"
        result = map_features_to_planning_request(features_json, material="45")
        spline = next(f for f in result["planning_request"]["features"] if f["feature_type"] == "spline")
        assert spline["spline_type"] == "involute"  # fallback
        assert any("Spline" in w for w in result["warnings"])


# ==============================================================================
# Model coordinate offset normalization
# ==============================================================================

class TestCoordinateNormalization:
    def _features_with_offset(self, offset: float = 100.0) -> dict:
        """Model with all axial coordinates shifted by +offset."""
        base = _load("shaft_features.json")
        feats = base["features"]
        for cyl in feats["outer_cylinders"]:
            cyl["position_x"] += offset
        for kw in feats["keyways"]["keyways"]:
            kw["position_axial"] += offset
        for i, z in enumerate(feats["radial_oil_holes"]["axial_positions"]):
            feats["radial_oil_holes"]["axial_positions"][i] = z + offset
            feats["radial_oil_holes"]["holes_per_position"] = {
                str(float(z) + offset): v for z, v in feats["radial_oil_holes"]["holes_per_position"].items()
            }
        for bore in feats.get("inner_bore", []):
            bore["position_x"] += offset
        return base

    def test_segments_start_at_zero(self):
        features_json = self._features_with_offset(100.0)
        result = map_features_to_planning_request(features_json, material="45")
        req = result["planning_request"]
        # Segment lengths are unaffected by the offset
        assert [s["length_mm"] for s in req["segments"]] == [80.0, 70.0, 30.0]
        # Feature positions are normalized to shaft-end coordinates
        keyway = next(f for f in req["features"] if f["feature_type"] == "keyway")
        assert keyway["global_position_mm"] == 130.0  # 130+100 - 100(offset)
        hole = next(f for f in req["features"] if f["feature_type"] == "hole")
        assert hole["global_position_mm"] == 45.0

    def test_offset_passes_validation(self):
        features_json = self._features_with_offset(100.0)
        result = map_features_to_planning_request(features_json, material="45")
        PlanningRequest.model_validate(result["planning_request"])


# ==============================================================================
# Edge cases
# ==============================================================================

class TestEdgeCases:
    def test_empty_features_json(self):
        result = map_features_to_planning_request({}, material="45")
        req = result["planning_request"]
        assert req["segments"] == []
        assert req["features"] == []
        assert any("outer_cylinders" in w for w in result["warnings"])
        # Without segments the peagent validation necessarily fails -- verify
        # that the warnings flag it
        assert result["confidence"]["geometry"] == "high"

    def test_no_inner_bore_is_solid(self):
        features_json = _load("shaft_features.json")
        features_json["features"]["inner_bore"] = []
        result = map_features_to_planning_request(features_json, material="45")
        assert result["planning_request"]["blank_type"] == "solid"
        assert result["planning_request"]["blank_inner_diameter_mm"] is None

    def test_adjacent_same_diameter_merged(self):
        features_json = _load("shaft_features.json")
        # Build two adjacent segments with the same diameter of 60
        features_json["features"]["outer_cylinders"] = [
            {"radius": 30.0, "position_x": 20.0, "length": 40.0, "area": 1.0},
            {"radius": 30.0, "position_x": 60.0, "length": 40.0, "area": 1.0},
            {"radius": 22.5, "position_x": 150.0, "length": 30.0, "area": 1.0},
        ]
        result = map_features_to_planning_request(features_json, material="45")
        req = result["planning_request"]
        assert len(req["segments"]) == 2
        assert req["segments"][0]["diameter_mm"] == 60.0
        assert req["segments"][0]["length_mm"] == 80.0  # 40+40

    def test_duplicate_feature_ids_never_happen(self):
        """Two keyways should produce distinct feature IDs."""
        features_json = _load("shaft_features.json")
        features_json["features"]["keyways"]["keyways"].append({
            "type": "rectangular",
            "position_axial": 150.0,
            "width": 10.0,
            "depth": 5.0,
            "length": 20.0,
            "area": 200.0,
            "radial_distance": 25.0,
        })
        features_json["features"]["keyways"]["count"] = 2
        result = map_features_to_planning_request(features_json, material="45")
        ids = [f["feature_id"] for f in result["planning_request"]["features"] if f["feature_type"] == "keyway"]
        assert ids == ["F01", "F02"]

    def test_validate_with_peagent_returns_model(self, shaft_features):
        result = map_features_to_planning_request(shaft_features, material="45")
        model, errors = __import__("services.cad_mapper", fromlist=["validate_with_peagent"]).validate_with_peagent(
            result["planning_request"]
        )
        assert model is not None
        assert errors == []


# ==============================================================================
# Tail coverage: extend the last segment when the total segment length is
# shorter than the part length
# ==============================================================================

class TestSegmentTailCoverage:
    def _shaft_with_short_segments(self) -> dict:
        """Outer cylinders cover only 147.3mm while the part is 180mm long (the
        tail is a chamfer/transition); the features fall in the tail region."""
        features_json = _load("shaft_features.json")
        feats = features_json["features"]
        feats["outer_cylinders"] = [
            {"radius": 30.0, "position_x": 40.0, "length": 80.0, "area": 15079.64},
            {"radius": 25.0, "position_x": 115.0, "length": 67.3, "area": 10550.0},
        ]
        # The oil hole is located at the tail (z=150 > total segment length
        # 147.3, but still within the 180mm part)
        feats["radial_oil_holes"] = {
            "count": 1, "radius": 3.0, "axial_positions": [150.0],
            "holes_per_position": {"150.0": 1}, "angular_positions": [0.0],
            "holes_per_angle": {"0.0": 1}, "radial_positions": [],
        }
        features_json["overall_dimensions"]["length"] = 180.0
        return features_json

    def test_last_segment_extended_to_overall_length(self):
        features_json = self._shaft_with_short_segments()
        result = map_features_to_planning_request(features_json, material="45")
        req = result["planning_request"]
        seg_total = round(sum(s["length_mm"] for s in req["segments"]), 3)
        # Total segment length equals the part length, so tail features no longer overflow
        assert seg_total == 180.0
        assert req["segments"][-1]["length_mm"] == 100.0  # 67.3 + 32.7
        assert any("extended" in w for w in result["warnings"])
        # The tail oil-hole position lies within the total segment length
        hole = next(f for f in req["features"] if f["feature_type"] == "hole")
        assert hole["global_position_mm"] == 150.0
        assert hole["global_position_mm"] <= seg_total

    def test_no_extension_when_segments_cover_part(self, shaft_features):
        # Original fixture: total segment length = 180 = overall length, so no extension
        result = map_features_to_planning_request(shaft_features, material="45")
        req = result["planning_request"]
        assert [s["length_mm"] for s in req["segments"]] == [80.0, 70.0, 30.0]
        assert not any("extended" in w for w in result["warnings"])

    def test_gear_at_part_end_fits_after_extension(self):
        """When a helical gear sits at the tail and the total segment length is
        short, after extension both the gear position and face width fit within
        the part."""
        features_json = self._shaft_with_short_segments()
        feats = features_json["features"]
        feats["inner_bore"] = []
        feats["gear_features"] = {
            "detected": True, "gear_count": 1,
            "gear_zones": [{"position_start": 168.0, "position_end": 180.0, "mid_position": 174.0}],
            "parameters": [{
                "tooth_count": 36, "module": 2.5, "addendum_radius": 47.5,
                "dedendum_radius": 43.0, "tooth_height": 4.5, "pressure_angle": 20.0,
                "gear_type": "helical", "helix_angle": 15.25,
            }],
        }
        result = map_features_to_planning_request(features_json, material="45")
        req = result["planning_request"]
        seg_total = round(sum(s["length_mm"] for s in req["segments"]), 3)
        gear = next(f for f in req["features"] if f["feature_type"] == "gear_teeth")
        # Position + face width must fit within the total segment length
        # (hard validation in the backend feature_analysis)
        assert gear["global_position_mm"] + gear["gear_face_width_mm"] <= seg_total + 1e-9
        # Helix angle keeps the decimal precision from the CAD parse; it is
        # not rounded to one decimal
        assert gear["helix_angle_deg"] == 15.25
