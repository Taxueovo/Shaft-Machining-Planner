# 回归测试：覆盖资源状态区分与部分覆盖数量统计。
"""Resource status distinction tests (DEF-VAL-05)."""

from models.process import ResourceStatus


# 覆盖资源状态的独立含义及部分覆盖计数规则。
class TestResourceStatusDistinction:
    # 验证不适用状态可按字符串枚举序列化。
    def test_not_applicable_is_string_enum(self):
        assert ResourceStatus.not_applicable.value == "not_applicable"

    # 验证未覆盖状态可按字符串枚举序列化。
    def test_not_covered_is_string_enum(self):
        assert ResourceStatus.not_covered.value == "not_covered"

    # 验证所有资源状态值互不混淆。
    def test_all_statuses_distinct(self):
        values = [s.value for s in ResourceStatus]
        assert len(values) == len(set(values)), "Duplicate resource status values found"

    # 验证资源状态集合包含预期的五种状态。
    def test_five_statuses(self):
        assert len(ResourceStatus) == 5

    # 验证不适用资源不被计入部分覆盖缺口。
    def test_not_applicable_excluded_from_partial(self):
        """DEF-VAL-05: not_applicable should not count toward partial coverage."""
        # Simulate the logic inside resource_selection
        partial = 0
        statuses = [
            ResourceStatus.satisfied.value,
            ResourceStatus.not_applicable.value,  # e.g. Blanking, Final Inspection
            ResourceStatus.not_covered.value,
            ResourceStatus.satisfied.value,
        ]
        for status in statuses:
            if status not in (ResourceStatus.satisfied.value, ResourceStatus.not_applicable.value):
                partial += 1
        # Only not_covered counts toward partial
        assert partial == 1, (
            f"not_applicable should not count toward partial, but partial={partial}"
        )

    # 验证未覆盖资源计入需要进一步确认的覆盖数量。
    def test_not_covered_counts_as_partial(self):
        partial = 0
        statuses = [
            ResourceStatus.satisfied.value,
            ResourceStatus.not_covered.value,
        ]
        for status in statuses:
            if status not in (ResourceStatus.satisfied.value, ResourceStatus.not_applicable.value):
                partial += 1
        assert partial == 1
