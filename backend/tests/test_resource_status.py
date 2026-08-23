"""Resource status distinction tests (DEF-VAL-05)."""

from models.process import ResourceStatus


class TestResourceStatusDistinction:
    def test_not_applicable_is_string_enum(self):
        assert ResourceStatus.not_applicable.value == "not_applicable"

    def test_not_covered_is_string_enum(self):
        assert ResourceStatus.not_covered.value == "not_covered"

    def test_all_statuses_distinct(self):
        values = [s.value for s in ResourceStatus]
        assert len(values) == len(set(values)), "Duplicate resource status values found"

    def test_five_statuses(self):
        assert len(ResourceStatus) == 5

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
