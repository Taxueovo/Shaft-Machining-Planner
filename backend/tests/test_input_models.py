# 回归测试：覆盖输入模型、必填参数、公差和枚举约束。
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


# 集中验证轴段标识、尺寸和有效值约束。
class TestShaftSegment:
    # 验证合法轴段能够创建并保留输入尺寸。
    def test_valid_segment(self):
        seg = ShaftSegment(segment_id="S1", diameter_mm=30, length_mm=100)
        assert seg.segment_id == "S1"

    # 验证零直径不被接受。
    def test_zero_diameter_rejected(self):
        with pytest.raises(ValidationError):
            ShaftSegment(segment_id="S1", diameter_mm=0, length_mm=100)

    # 验证负长度不被接受。
    def test_negative_length_rejected(self):
        with pytest.raises(ValidationError):
            ShaftSegment(segment_id="S1", diameter_mm=30, length_mm=-1)

    # 验证轴段标识不能为空。
    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            ShaftSegment(segment_id="", diameter_mm=30, length_mm=100)


# ============================================================
# FeatureInput
# ============================================================


# 集中验证特征类型对应的必填字段与定位条件。
class TestFeatureInput:
    # 验证键槽必须提供宽度。
    def test_keyway_requires_width(self):
        with pytest.raises(ValidationError):
            FeatureInput(
                feature_id="F1",
                feature_type="keyway",
                positioning_mode="global_absolute",
                global_position_mm=50,
                # missing keyway_width_mm
            )

    # 验证盲孔必须提供深度。
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

    # 验证相对轴段定位必须指定轴段索引。
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

    # 验证完整合法的键槽参数能够通过校验。
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


# 覆盖凸轮、蜗杆和曲柄销等扩展特征模型。
class TestNewFeatureTypes:
    # 构造扩展特征测试共用的基础定位字段。
    def _base(self, feature_type, **extra):
        return {
            "feature_id": "F1",
            "feature_type": feature_type,
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            **extra,
        }

    # 验证凸轮特征必须提供轴向长度。
    def test_cam_requires_feature_length(self):
        with pytest.raises(ValidationError):
            FeatureInput(**self._base("cam"))
        f = FeatureInput(**self._base("cam", feature_length_mm=30.0))
        assert f.feature_type == "cam"

    # 验证凸轮可选参数能正确保留。
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

    # 验证蜗杆特征必须提供轴向长度。
    def test_worm_requires_feature_length(self):
        with pytest.raises(ValidationError):
            FeatureInput(**self._base("worm"))
        f = FeatureInput(**self._base("worm", feature_length_mm=40.0))
        assert f.feature_type == "worm"

    # 验证蜗杆可选参数能正确保留。
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

    # 验证曲柄销特征必须提供长度。
    def test_crank_pin_requires_feature_length(self):
        with pytest.raises(ValidationError):
            FeatureInput(**self._base("crank_pin"))
        f = FeatureInput(**self._base("crank_pin", feature_length_mm=25.0))
        assert f.feature_type == "crank_pin"

    # 验证曲柄销的可选偏心等参数能够保留。
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


# 验证热处理枚举值的接受与拒绝行为。
class TestHeatTreatmentValues:
    # 验证氮化是可接受的热处理值。
    def test_nitriding_valid(self):
        g = GlobalRequirements(heat_treatment="nitriding")
        assert g.heat_treatment == "nitriding"

    # 验证感应淬火是可接受的热处理值。
    def test_induction_hardening_valid(self):
        g = GlobalRequirements(heat_treatment="induction_hardening")
        assert g.heat_treatment == "induction_hardening"

    # 验证未定义热处理名称被拒绝。
    def test_invalid_heat_treatment_rejected(self):
        with pytest.raises(ValidationError):
            GlobalRequirements(heat_treatment="supercritical_quench")


# ============================================================
# PlanningRequest
# ============================================================


# 验证完整规划请求中跨轴段、毛坯和特征的一致性。
class TestPlanningRequest:
    # 构造路线测试的基础请求，允许覆盖材料、特征及全局要求。
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

    # 验证完整且一致的规划请求可通过模型校验。
    def test_valid_request(self):
        req = self._make_request()
        assert req.material == "45"

    # 验证轴段标识重复时拒绝请求。
    def test_duplicate_segment_ids_rejected(self):
        with pytest.raises(ValidationError, match="Segment IDs must be unique"):
            self._make_request(
                segments=[
                    {"segment_id": "S1", "diameter_mm": 30, "length_mm": 50},
                    {"segment_id": "S1", "diameter_mm": 25, "length_mm": 50},
                ]
            )

    # 验证毛坯外径不能小于最大成品外径。
    def test_blank_smaller_than_max_diameter_rejected(self):
        with pytest.raises(ValidationError, match="Blank diameter"):
            self._make_request(
                blank_diameter_mm=20,
                segments=[{"segment_id": "S1", "diameter_mm": 30, "length_mm": 100}],
            )

    # 验证特征标识重复时拒绝请求。
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


# 验证工序编号、阶段和字段模型约束。
class TestProcessOperation:
    # 验证合法工序的字段能够通过模型校验。
    def test_valid_operation(self):
        op = ProcessOperation(
            operation_no=10,
            name="Blanking",
            stage=ProcessStage.blank,
        )
        assert op.stage == ProcessStage.blank

    # 验证不在统一枚举中的工序阶段被拒绝。
    def test_invalid_stage_rejected(self):
        with pytest.raises(ValidationError):
            ProcessOperation(
                operation_no=10,
                name="Test",
                stage="invalid_stage",
            )

    # 验证工序编号必须为正值。
    def test_operation_no_must_be_positive(self):
        with pytest.raises(ValidationError):
            ProcessOperation(
                operation_no=0,
                name="Test",
                stage=ProcessStage.blank,
            )


# 验证资源状态枚举表达不适用等独立含义。
class TestResourceStatus:
    # 验证不适用状态不会与未覆盖等状态混淆。
    def test_not_applicable_is_distinct(self):
        assert ResourceStatus.not_applicable != ResourceStatus.not_covered
        assert ResourceStatus.not_applicable != ResourceStatus.satisfied


# 验证结构化校验问题的字段构造。
class TestValidationIssue:
    # 验证校验问题可按预期字段构造。
    def test_create_issue(self):
        issue = ValidationIssue(
            error_code="TEST",
            message="Test error",
        )
        assert issue.severity == "error"


# 验证加工时机策略的枚举覆盖。
class TestFeatureProcessStrategy:
    # 验证热前、热后和拆分三种加工策略均存在。
    def test_three_strategies_exist(self):
        assert len(FeatureProcessStrategy) == 3


# 验证模型路线输出的数据合同。
class TestLLMRouteOutput:
    # 验证合法路线能通过模型输出合同。
    def test_valid_route(self):
        output = LLMRouteOutput(
            process_route=[
                ProcessOperation(operation_no=10, name="Blanking", stage=ProcessStage.blank),
            ]
        )
        assert len(output.process_route) == 1
