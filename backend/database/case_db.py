"""Case database."""

from __future__ import annotations

import json
import logging
import sys
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models.case import Case, CaseMetadata, CaseSearchRequest

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CASES_FILE = DATA_DIR / "cases.json"


class CaseDB:
    """Case database - JSON file storage."""

    def __init__(self, file_path: Optional[Path] = None):
        self._file_path = file_path or CASES_FILE
        self._cases: Optional[list[Case]] = None
        self._lock = threading.RLock()

    def _load(self) -> list[Case]:
        """Load cases from JSON file."""
        if self._cases is not None:
            return self._cases

        if not self._file_path.exists():
            logger.warning("Cases file not found: %s", self._file_path)
            self._cases = []
            return self._cases

        with open(self._file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._cases = [Case(**case) for case in data.get("cases", [])]
        logger.info("Loaded %d cases", len(self._cases))
        return self._cases

    def _save(self) -> None:
        """Save cases to JSON file."""
        if self._cases is None:
            return

        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"cases": [case.model_dump(mode="json") for case in self._cases]}

        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self._file_path.parent,
            prefix=f".{self._file_path.name}.", suffix=".tmp", delete=False,
        ) as temp_file:
            json.dump(data, temp_file, indent=2, ensure_ascii=False, default=str)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, self._file_path)

        logger.info("Saved %d cases", len(self._cases))

    def get_all(self) -> list[CaseMetadata]:
        """Get all cases (metadata only)."""
        cases = self._load()
        return [case.to_metadata() for case in cases]

    def get_by_id(self, case_id: str) -> Optional[Case]:
        """Get case by ID."""
        cases = self._load()
        return next((c for c in cases if c.case_id == case_id), None)

    def get_by_taxonomy(self, taxonomy_id: str) -> list[CaseMetadata]:
        """Get cases by taxonomy node ID."""
        cases = self._load()
        return [c.to_metadata() for c in cases if c.taxonomy_id == taxonomy_id]

    def get_by_taxonomy_recursive(self, taxonomy_ids: list[str]) -> list[CaseMetadata]:
        """Get cases by multiple taxonomy node IDs (including descendants)."""
        cases = self._load()
        return [c.to_metadata() for c in cases if c.taxonomy_id in taxonomy_ids]

    def search(self, request: CaseSearchRequest) -> list[CaseMetadata]:
        """Search cases with filters."""
        results = self._filtered(request)

        # Apply pagination
        results = results[request.offset:request.offset + request.limit]

        return [c.to_metadata() for c in results]

    def _filtered(self, request: CaseSearchRequest) -> list[Case]:
        """Return cases matching the search filters, without pagination."""
        results = self._load()

        # Filter by taxonomy
        if request.taxonomy_id:
            results = [c for c in results if c.taxonomy_id == request.taxonomy_id]

        # Filter by industry
        if request.industry:
            results = [c for c in results if c.industry.lower() == request.industry.lower()]

        # Filter by material
        if request.material:
            results = [c for c in results if c.material.lower() == request.material.lower()]

        # Filter by tolerance
        if request.tolerance:
            results = [c for c in results if c.tolerance and c.tolerance.lower() == request.tolerance.lower()]

        # Filter by keyword
        if request.keyword:
            keyword = request.keyword.lower()
            results = [
                c for c in results
                if keyword in c.part_name.lower()
                or keyword in c.case_id.lower()
                or (c.description and keyword in c.description.lower())
                or any(keyword in f.lower() for f in c.main_features)
            ]

        return results

    def create(self, case: Case) -> Case:
        """Create a new case."""
        with self._lock:
            return self._create_locked(case)

    def _create_locked(self, case: Case) -> Case:
        cases = self._load()

        # Check if ID already exists
        if any(c.case_id == case.case_id for c in cases):
            raise ValueError(f"Case ID already exists: {case.case_id}")

        # Set timestamps
        now = datetime.now()
        case.created_at = now
        case.updated_at = now

        cases.append(case)
        self._cases = cases
        self._save()

        logger.info("Created case: %s", case.case_id)
        return case

    def update(self, case_id: str, updates: dict) -> Case:
        """Update an existing case."""
        with self._lock:
            return self._update_locked(case_id, updates)

    def _update_locked(self, case_id: str, updates: dict) -> Case:
        cases = self._load()
        case = next((c for c in cases if c.case_id == case_id), None)

        if not case:
            raise ValueError(f"Case not found: {case_id}")

        # Apply updates
        for key, value in updates.items():
            if hasattr(case, key):
                setattr(case, key, value)

        case.updated_at = datetime.now()
        self._cases = cases
        self._save()

        logger.info("Updated case: %s", case_id)
        return case

    def delete(self, case_id: str) -> None:
        """Delete a case."""
        with self._lock:
            self._delete_locked(case_id)

    def _delete_locked(self, case_id: str) -> None:
        cases = self._load()
        original_count = len(cases)
        cases = [c for c in cases if c.case_id != case_id]

        if len(cases) == original_count:
            raise ValueError(f"Case not found: {case_id}")

        self._cases = cases
        self._save()

        logger.info("Deleted case: %s", case_id)

    def get_industries(self) -> list[str]:
        """Get all unique industries."""
        cases = self._load()
        return sorted(set(c.industry for c in cases))

    def get_materials(self) -> list[str]:
        """Get all unique materials."""
        cases = self._load()
        return sorted(set(c.material for c in cases))

    def count(self, request: Optional[CaseSearchRequest] = None) -> int:
        """Return total case count, or the filtered count before pagination."""
        return len(self._filtered(request)) if request else len(self._load())
