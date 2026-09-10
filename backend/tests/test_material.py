# 回归测试：覆盖材料别名、ISO 分类与刀具材料组匹配。
"""Material resolution tests (DEF-RES-04, subset of DEF-TEST-01)."""

import pytest

from repositories import ToolRepository


# 集中验证材料牌号、别名和材料组的归一解析。
class TestMaterialResolution:
    # 验证常用钢牌号映射到正确刀具材料分类。
    def test_common_steel_45(self):
        result = ToolRepository.resolve_material("45")
        assert result["mode"] == "iso"
        assert result["value"] == "P"

    # 验证不锈钢牌号的材料分类解析。
    def test_stainless_304(self):
        result = ToolRepository.resolve_material("304")
        assert result["value"] == "M"

    # 验证铝合金牌号的材料分类解析。
    def test_aluminum_6061(self):
        result = ToolRepository.resolve_material("6061")
        assert result["value"] == "N"

    # 验证可直接使用 ISO P 类材料条件。
    def test_iso_category_p(self):
        result = ToolRepository.resolve_material("P")
        assert result["mode"] == "iso"
        assert result["value"] == "P"

    # 验证可直接使用 ISO M 类材料条件。
    def test_iso_category_m(self):
        result = ToolRepository.resolve_material("M")
        assert result["value"] == "M"

    # 验证纯数字材料组能够解析，覆盖此前数字组识别缺陷。
    def test_material_group_14(self):
        """DEF-RES-04: a purely numeric material group should be resolvable."""
        result = ToolRepository.resolve_material("group 14")
        assert result["mode"] == "group"
        assert result["value"] == "14"

    # 验证区间形式的材料组能够解析。
    def test_material_group_range(self):
        """DEF-RES-04: a numeric range material group should be resolvable."""
        result = ToolRepository.resolve_material("group 14-16")
        assert result["mode"] == "group"
        assert result["value"] == "14-16"

    # 验证带刀具厂商组别前缀的输入能够解析。
    def test_iscar_group_prefix(self):
        result = ToolRepository.resolve_material("ISCAR GROUP 23")
        assert result["mode"] == "group"
        assert result["value"] == "23"

    # 验证中文材料组前缀能够解析。
    def test_chinese_group_prefix(self):
        result = ToolRepository.resolve_material("material group: 14")
        assert result["mode"] == "group"
        assert result["value"] == "14"

    # 验证未知材料产生明确错误，而不静默套用错误组别。
    def test_unknown_material_raises(self):
        with pytest.raises(ValueError, match="Unrecognized material"):
            ToolRepository.resolve_material("XYZUNKNOWN999")

    # 验证空材料名称被拒绝。
    def test_empty_material_raises(self):
        with pytest.raises(ValueError, match="Material cannot be empty"):
            ToolRepository.resolve_material("")

    # 验证材料别名解析不受大小写影响。
    def test_case_insensitive(self):
        result = ToolRepository.resolve_material("steel")
        assert result["value"] == "P"

    # 验证常见材料俗称能够归一到正式分类。
    def test_common_nicknames(self):
        assert ToolRepository.resolve_material("42CrMo")["value"] == "P"
        assert ToolRepository.resolve_material("AISI 4140")["value"] == "P"
        assert ToolRepository.resolve_material("SUS304")["value"] == "M"


# 集中验证材料组的单值与区间匹配规则。
class TestGroupMatches:
    # 验证单个材料组落在记录区间内时匹配。
    def test_single_number_in_range(self):
        assert ToolRepository.group_matches("12-18", "14") is True

    # 验证相同材料组编号精确匹配。
    def test_single_number_exact(self):
        assert ToolRepository.group_matches("14", "14") is True

    # 验证区间外材料组不匹配。
    def test_single_number_not_in_range(self):
        assert ToolRepository.group_matches("12-18", "20") is False

    # 验证材料组区间输入与库记录的匹配行为。
    def test_range_match(self):
        assert (
            ToolRepository.group_matches("12-18", "14-16") is False
        )  # range vs range does not match
