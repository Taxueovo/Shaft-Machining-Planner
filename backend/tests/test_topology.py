# 回归测试：覆盖工序阶段先后关系和特征定位基准顺序。
"""Topology verification tests (DEF-VAL-01)."""

from workflow.graph import Workflow


# 直接调用路线拓扑检查，隔离完整规划流程。
def _topo(route):
    return Workflow._topological_verify(route)


# 覆盖合法路线和空路线的拓扑检查边界。
class TestTopologyBasic:
    # 验证阶段顺序正确的路线通过拓扑检查。
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

    # 验证拓扑检查对空列表的约定；路线非空要求由其他护栏负责。
    def test_empty_route_passes(self):
        result = _topo([])
        assert result["passed"] is True


# 覆盖典型工序阶段倒置的识别。
class TestTopologyStageInversion:
    # 验证先精车后粗车被识别为阶段倒置。
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

    # 验证最终检验早于精加工被识别。
    def test_inspection_before_finish_detected(self):
        route = [
            {"operation_no": 10, "name": "Blanking", "stage": "blank"},
            {"operation_no": 20, "name": "Final Inspection", "stage": "inspection"},
            {"operation_no": 30, "name": "Finish Turning", "stage": "finish"},
        ]
        result = _topo(route)
        assert result["passed"] is False

    # 验证热处理早于半精加工被识别。
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


# 覆盖未知阶段的拒绝行为。
class TestTopologyInvalidStage:
    # 验证拓扑检查拒绝未定义的工序阶段。
    def test_invalid_stage_detected(self):
        route = [
            {"operation_no": 10, "name": "Test", "stage": "invalid_stage"},
        ]
        result = _topo(route)
        assert result["passed"] is False
        assert "valid enum" in result["message"] or "stage" in result["message"].lower()


# 覆盖特征工序和定位基准形成顺序。
class TestTopologyWithFeatures:
    # 验证热前特征加工位于半精加工之后、热处理之前。
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

    # 验证外圆精磨先于依赖该成品基准的最终特征加工。
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
