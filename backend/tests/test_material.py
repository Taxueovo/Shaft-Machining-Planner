"""Material resolution tests (DEF-RES-04, subset of DEF-TEST-01)."""

import pytest

from repositories import ToolRepository


class TestMaterialResolution:
    def test_common_steel_45(self):
        result = ToolRepository.resolve_material("45")
        assert result["mode"] == "iso"
        assert result["value"] == "P"

    def test_stainless_304(self):
        result = ToolRepository.resolve_material("304")
        assert result["value"] == "M"

    def test_aluminum_6061(self):
        result = ToolRepository.resolve_material("6061")
        assert result["value"] == "N"

    def test_iso_category_p(self):
        result = ToolRepository.resolve_material("P")
        assert result["mode"] == "iso"
        assert result["value"] == "P"

    def test_iso_category_m(self):
        result = ToolRepository.resolve_material("M")
        assert result["value"] == "M"

    def test_material_group_14(self):
        """DEF-RES-04: a purely numeric material group should be resolvable."""
        result = ToolRepository.resolve_material("group 14")
        assert result["mode"] == "group"
        assert result["value"] == "14"

    def test_material_group_range(self):
        """DEF-RES-04: a numeric range material group should be resolvable."""
        result = ToolRepository.resolve_material("group 14-16")
        assert result["mode"] == "group"
        assert result["value"] == "14-16"

    def test_iscar_group_prefix(self):
        result = ToolRepository.resolve_material("ISCAR GROUP 23")
        assert result["mode"] == "group"
        assert result["value"] == "23"

    def test_chinese_group_prefix(self):
        result = ToolRepository.resolve_material("material group: 14")
        assert result["mode"] == "group"
        assert result["value"] == "14"

    def test_unknown_material_raises(self):
        with pytest.raises(ValueError, match="Unrecognized material"):
            ToolRepository.resolve_material("XYZUNKNOWN999")

    def test_empty_material_raises(self):
        with pytest.raises(ValueError, match="Material cannot be empty"):
            ToolRepository.resolve_material("")

    def test_case_insensitive(self):
        result = ToolRepository.resolve_material("steel")
        assert result["value"] == "P"

    def test_common_nicknames(self):
        assert ToolRepository.resolve_material("42CrMo")["value"] == "P"
        assert ToolRepository.resolve_material("AISI 4140")["value"] == "P"
        assert ToolRepository.resolve_material("SUS304")["value"] == "M"


class TestGroupMatches:
    def test_single_number_in_range(self):
        assert ToolRepository.group_matches("12-18", "14") is True

    def test_single_number_exact(self):
        assert ToolRepository.group_matches("14", "14") is True

    def test_single_number_not_in_range(self):
        assert ToolRepository.group_matches("12-18", "20") is False

    def test_range_match(self):
        assert (
            ToolRepository.group_matches("12-18", "14-16") is False
        )  # range vs range does not match
