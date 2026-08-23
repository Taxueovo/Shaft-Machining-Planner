"""Input model validation tests (subset of DEF-TEST-01)."""

import pytest
from pydantic import ValidationError

from models.workflow import PlanningRequest
from models.input import ShaftSegment, FeatureInput, GlobalRequirements
from models.process import (
    ProcessOperation,
    ProcessStage,
    ResourceStatus,
    ValidationIssue,
    FeatureProcessStrategy,
    LLMRouteOutput,
)


# ============================================================
# ShaftSegment
# ============================================================


class TestShaftSegment:
    def test_valid_segment(self):
        seg = ShaftSegment(segment_id="S1", diameter_mm=30, length_mm=100)
        assert seg.segment_id == "S1"

    def test_zero_diameter_rejected(self):
        with pytest.raises(ValidationError):
            ShaftSegment(segment_id="S1", diameter_mm=0, length_mm=100)

    def test_negative_length_rejected(self):
        with pytest.raises(ValidationError):
            ShaftSegment(segment_id="S1", diameter_mm=30, length_mm=-1)

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            ShaftSegment(segment_id="", diameter_mm=30, length_mm=100)


# ============================================================
# FeatureInput
# ============================================================


class TestFeatureInput:
    def test_keyway_requires_width(self):
        with pytest.raises(ValidationError):
            FeatureInput(
                feature_id="F1",
                feature_type="keyway",
                positioning_mode="global_absolute",
                global_position_mm=50,
                # missing keyway_width_mm
            )

    def test_blind_hole_requires_depth(self):
        with pytest.raises(ValidationError):
            FeatureInput(
                feature_id="F1",
                feature_type="hole",
                positioning_mode="global_absolute",
                global_position_mm=50,
                hole_diameter_mm=5,
                hole_type="blind",
                hole_direction="radial",
                # missing hole_depth_mm
            )

    def test_segment_relative_requires_index(self):
        with pytest.raises(ValidationError):
            FeatureInput(
                feature_id="F1",
                feature_type="keyway",
                positioning_mode="segment_relative",
                keyway_width_mm=10,
                keyway_depth_mm=5,
                feature_length_mm=50,
                # missing segment_index and segment_offset_mm
            )

    def test_valid_keyway(self):
        f = FeatureInput(
            feature_id="F1",
            feature_type="keyway",
            positioning_mode="global_absolute",
            global_position_mm=50,
            keyway_width_mm=10,
            keyway_depth_mm=5,
            feature_length_mm=50,
        )
        assert f.feature_type == "keyway"


# ============================================================
# New shaft features (cam / worm / crank_pin) and nitriding / induction hardening heat treatments
# ============================================================


class TestNewFeatureTypes:
    def _base(self, feature_type, **extra):
        return {
            "feature_id": "F1",
            "feature_type": feature_type,
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            **extra,
        }

    def test_cam_requires_feature_length(self):
        with pytest.raises(ValidationError):
            FeatureInput(**self._base("cam"))
        f = FeatureInput(**self._base("cam", feature_length_mm=30.0))
        assert f.feature_type == "cam"

    def test_cam_optional_fields(self):
        f = FeatureInput(
            **self._base(
                "cam",
                feature_length_mm=30.0,
                cam_type="grinding",
                cam_lobe_count=4,
                cam_base_circle_diameter_mm=40.0,
                cam_lobe_lift_mm=8.0,
            )
        )
        assert f.cam_lobe_count == 4
        assert f.cam_lobe_lift_mm == 8.0

    def test_worm_requires_feature_length(self):
        with pytest.raises(ValidationError):
            FeatureInput(**self._base("worm"))
        f = FeatureInput(**self._base("worm", feature_length_mm=40.0))
        assert f.feature_type == "worm"

    def test_worm_optional_fields(self):
        f = FeatureInput(
            **self._base(
                "worm",
                feature_length_mm=40.0,
                worm_module=2.0,
                worm_starts=1,
                worm_pressure_angle_deg=20.0,
                worm_outer_diameter_mm=35.0,
            )
        )
        assert f.worm_module == 2.0
        assert f.worm_starts == 1

    def test_crank_pin_requires_feature_length(self):
        with pytest.raises(ValidationError):
            FeatureInput(**self._base("crank_pin"))
        f = FeatureInput(**self._base("crank_pin", feature_length_mm=25.0))
        assert f.feature_type == "crank_pin"

    def test_crank_pin_optional_fields(self):
        f = FeatureInput(
            **self._base(
                "crank_pin",
                feature_length_mm=25.0,
                crank_pin_diameter_mm=30.0,
                crank_pin_width_mm=25.0,
                crank_offset_mm=10.0,
            )
        )
        assert f.crank_pin_diameter_mm == 30.0
        assert f.crank_offset_mm == 10.0


class TestHeatTreatmentValues:
    def test_nitriding_valid(self):
        g = GlobalRequirements(heat_treatment="nitriding")
        assert g.heat_treatment == "nitriding"

    def test_induction_hardening_valid(self):
        g = GlobalRequirements(heat_treatment="induction_hardening")
        assert g.heat_treatment == "induction_hardening"

    def test_invalid_heat_treatment_rejected(self):
        with pytest.raises(ValidationError):
            GlobalRequirements(heat_treatment="supercritical_quench")


# ============================================================
# PlanningRequest
# ============================================================


class TestPlanningRequest:
    def _make_request(self, **kwargs):
        defaults = {
            "material": "45",
            "blank_diameter_mm": 50,
            "segments": [
                {"segment_id": "S1", "diameter_mm": 30, "length_mm": 100},
            ],
        }
        defaults.update(kwargs)
        return PlanningRequest(**defaults)

    def test_valid_request(self):
        req = self._make_request()
        assert req.material == "45"

    def test_duplicate_segment_ids_rejected(self):
        with pytest.raises(ValidationError, match="Segment IDs must be unique"):
            self._make_request(
                segments=[
                    {"segment_id": "S1", "diameter_mm": 30, "length_mm": 50},
                    {"segment_id": "S1", "diameter_mm": 25, "length_mm": 50},
                ]
            )

    def test_blank_smaller_than_max_diameter_rejected(self):
        with pytest.raises(ValidationError, match="Blank diameter"):
            self._make_request(
                blank_diameter_mm=20,
                segments=[{"segment_id": "S1", "diameter_mm": 30, "length_mm": 100}],
            )

    def test_duplicate_feature_ids_rejected(self):
        with pytest.raises(ValidationError, match="Feature IDs must be unique"):
            self._make_request(
                features=[
                    FeatureInput(
                        feature_id="F1",
                        feature_type="keyway",
                        positioning_mode="global_absolute",
                        global_position_mm=50,
                        keyway_width_mm=10,
                        keyway_depth_mm=5,
                        feature_length_mm=50,
                    ),
                    FeatureInput(
                        feature_id="F1",
                        feature_type="hole",
                        positioning_mode="global_absolute",
                        global_position_mm=60,
                        hole_diameter_mm=5,
                        hole_type="through",
                        hole_direction="radial",
                    ),
                ]
            )


# ============================================================
# ProcessOperation & Enums
# ============================================================


class TestProcessOperation:
    def test_valid_operation(self):
        op = ProcessOperation(
            operation_no=10,
            name="Blanking",
            stage=ProcessStage.blank,
        )
        assert op.stage == ProcessStage.blank

    def test_invalid_stage_rejected(self):
        with pytest.raises(ValidationError):
            ProcessOperation(
                operation_no=10,
                name="Test",
                stage="invalid_stage",
            )

    def test_operation_no_must_be_positive(self):
        with pytest.raises(ValidationError):
            ProcessOperation(
                operation_no=0,
                name="Test",
                stage=ProcessStage.blank,
            )


class TestResourceStatus:
    def test_not_applicable_is_distinct(self):
        assert ResourceStatus.not_applicable != ResourceStatus.not_covered
        assert ResourceStatus.not_applicable != ResourceStatus.satisfied


class TestValidationIssue:
    def test_create_issue(self):
        issue = ValidationIssue(
            error_code="TEST",
            message="Test error",
        )
        assert issue.severity == "error"


class TestFeatureProcessStrategy:
    def test_three_strategies_exist(self):
        assert len(FeatureProcessStrategy) == 3


class TestLLMRouteOutput:
    def test_valid_route(self):
        output = LLMRouteOutput(
            process_route=[
                ProcessOperation(operation_no=10, name="Blanking", stage=ProcessStage.blank),
            ]
        )
        assert len(output.process_route) == 1
