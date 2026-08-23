"""Topology verification tests (DEF-VAL-01)."""

from workflow.graph import Workflow


def _topo(route):
    return Workflow._topological_verify(route)


class TestTopologyBasic:
    def test_valid_route_passes(self):
        route = [
            {"operation_no": 10, "name": "Blanking", "stage": "blank"},
            {"operation_no": 20, "name": "Face Turning", "stage": "datum"},
            {"operation_no": 30, "name": "Rough Turning", "stage": "rough"},
            {"operation_no": 40, "name": "Semi-finish Turning", "stage": "semi_finish"},
            {"operation_no": 50, "name": "Finish Turning", "stage": "finish"},
            {"operation_no": 60, "name": "Final Inspection", "stage": "inspection"},
        ]
        result = _topo(route)
        assert result["passed"] is True

    def test_empty_route_passes(self):
        result = _topo([])
        assert result["passed"] is True


class TestTopologyStageInversion:
    def test_finish_before_rough_detected(self):
        """Finish turning before rough turning should be detected as a stage inversion (DEF-VAL-01)."""
        route = [
            {"operation_no": 10, "name": "Blanking", "stage": "blank"},
            {"operation_no": 20, "name": "Finish Turning", "stage": "finish"},
            {"operation_no": 30, "name": "Rough Turning", "stage": "rough"},
            {"operation_no": 40, "name": "Final Inspection", "stage": "inspection"},
        ]
        result = _topo(route)
        assert result["passed"] is False, "finish turning before rough turning should be detected"
        assert "Stage inversion" in result["message"] or "inversion" in result["message"]

    def test_inspection_before_finish_detected(self):
        route = [
            {"operation_no": 10, "name": "Blanking", "stage": "blank"},
            {"operation_no": 20, "name": "Final Inspection", "stage": "inspection"},
            {"operation_no": 30, "name": "Finish Turning", "stage": "finish"},
        ]
        result = _topo(route)
        assert result["passed"] is False

    def test_heat_treatment_before_semi_finish_detected(self):
        route = [
            {"operation_no": 10, "name": "Blanking", "stage": "blank"},
            {"operation_no": 20, "name": "Heat Treatment", "stage": "heat_treatment"},
            {"operation_no": 30, "name": "Semi-finish Turning", "stage": "semi_finish"},
            {"operation_no": 40, "name": "Finish Turning", "stage": "finish"},
            {"operation_no": 50, "name": "Final Inspection", "stage": "inspection"},
        ]
        result = _topo(route)
        assert result["passed"] is False


class TestTopologyInvalidStage:
    def test_invalid_stage_detected(self):
        route = [
            {"operation_no": 10, "name": "Test", "stage": "invalid_stage"},
        ]
        result = _topo(route)
        assert result["passed"] is False
        assert "valid enum" in result["message"] or "stage" in result["message"].lower()


class TestTopologyWithFeatures:
    def test_feature_before_heat_order(self):
        """Feature operations in the feature_before_heat stage should come after semi-finish turning and before Heat Treatment."""
        route = [
            {"operation_no": 10, "name": "Blanking", "stage": "blank"},
            {"operation_no": 20, "name": "Face Turning", "stage": "datum"},
            {"operation_no": 30, "name": "Rough Turning", "stage": "rough"},
            {"operation_no": 40, "name": "Semi-finish Turning", "stage": "semi_finish"},
            {
                "operation_no": 50,
                "name": "Mill keyway",
                "stage": "feature_before_heat",
                "feature_id": "F1",
            },
            {"operation_no": 60, "name": "Heat Treatment", "stage": "heat_treatment"},
            {"operation_no": 70, "name": "Repair Center Holes", "stage": "datum_recovery"},
            {"operation_no": 80, "name": "Finish Turning", "stage": "finish"},
            {"operation_no": 90, "name": "Final Inspection", "stage": "inspection"},
        ]
        result = _topo(route)
        assert result["passed"] is True

    def test_od_grinding_precedes_final_feature_machining(self):
        """Finish-grind the finished OD first; only then machine final features such as the keyway that use this OD as their locating datum."""
        route = [
            {"operation_no": 10, "name": "Blanking", "stage": "blank"},
            {"operation_no": 20, "name": "Face Turning", "stage": "datum"},
            {"operation_no": 30, "name": "Rough Turning", "stage": "rough"},
            {"operation_no": 40, "name": "Semi-finish Turning", "stage": "semi_finish"},
            {"operation_no": 50, "name": "Finish Turning", "stage": "finish"},
            {"operation_no": 60, "name": "Finish Grind OD", "stage": "precision_finish"},
            {
                "operation_no": 70,
                "name": "Mill keyway",
                "stage": "feature_before_inspection",
                "feature_id": "F1",
            },
            {"operation_no": 80, "name": "Final Inspection", "stage": "inspection"},
        ]
        assert _topo(route)["passed"] is True
