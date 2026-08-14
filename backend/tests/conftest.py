"""pytest configuration - automatically adds the backend directory to the Python path."""
import sys
from pathlib import Path

import pytest

# Add the backend directory to the Python path to avoid manual sys.path.insert in each test file
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Machine/tool capability libraries (data/machines.xlsx, data/tools.xlsx) and
# the case library (data/cases.json) are private business data, intentionally
# not shipped with the repository. Tests that exercise those resources are
# skipped automatically when the files are absent (e.g. CI) instead of failing.
_REQUIRE_DATA_FILES = {
    "test_preview_resources",
    "test_heat_treatment_provider",
    "test_case_library_and_repair",
}


def pytest_collection_modifyitems(config, items):
    repo_root = Path(__file__).resolve().parent.parent.parent
    data_dir = repo_root / "data"
    has_data = (data_dir / "machines.xlsx").is_file() and (data_dir / "tools.xlsx").is_file()
    if has_data:
        return
    skip = pytest.mark.skip(
        reason="capability libraries (data/*.xlsx, data/cases.json) are not included in this repository"
    )
    for item in items:
        module_name = item.module.__name__.rsplit(".", 1)[-1]
        if module_name in _REQUIRE_DATA_FILES:
            item.add_marker(skip)
