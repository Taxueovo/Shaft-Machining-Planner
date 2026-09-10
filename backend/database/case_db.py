# 以 JSON 文件保存案例，提供组合筛选、分页和增删改操作。
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


# 将案例对象存入本地 JSON 文件，并提供筛选和分页查询。
class CaseDB:
    """Case database - JSON file storage."""

    def __init__(self, file_path: Optional[Path] = None):
        """初始化案例库；未指定 file_path 时使用项目默认的 data/cases.json。"""
        self._file_path = file_path or CASES_FILE
        self._cases: Optional[list[Case]] = None
        self._lock = threading.RLock()

    # 从 JSON 文件恢复内存数据，处理文件缺失或内容无效的情况。
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

    # 将当前内存数据序列化并保存到对应 JSON 文件。
    def _save(self) -> None:
        """Save cases to JSON file."""
        if self._cases is None:
            return

        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"cases": [case.model_dump(mode="json") for case in self._cases]}

        # 先写同目录临时文件再原子替换，避免写入中途失败留下残缺的 JSON
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self._file_path.parent,
            prefix=f".{self._file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(data, temp_file, indent=2, ensure_ascii=False, default=str)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, self._file_path)

        logger.info("Saved %d cases", len(self._cases))

    # 返回列表展示所需的全部案例摘要，不携带完整路线。
    def get_all(self) -> list[CaseMetadata]:
        """Get all cases (metadata only)."""
        cases = self._load()
        return [case.to_metadata() for case in cases]

    # 按案例唯一标识获取记录，未找到时返回空值。
    def get_by_id(self, case_id: str) -> Optional[Case]:
        """Get case by ID."""
        cases = self._load()
        return next((c for c in cases if c.case_id == case_id), None)

    # 筛选属于指定分类节点的案例。
    def get_by_taxonomy(self, taxonomy_id: str) -> list[CaseMetadata]:
        """Get cases by taxonomy node ID."""
        cases = self._load()
        return [c.to_metadata() for c in cases if c.taxonomy_id == taxonomy_id]

    # 使用已展开的分类标识集合筛选案例，包含调用方指定的后代分类。
    def get_by_taxonomy_recursive(self, taxonomy_ids: list[str]) -> list[CaseMetadata]:
        """Get cases by multiple taxonomy node IDs (including descendants)."""
        cases = self._load()
        return [c.to_metadata() for c in cases if c.taxonomy_id in taxonomy_ids]

    # 组合筛选条件后再分页，将案例转换成列表摘要返回。
    def search(self, request: CaseSearchRequest) -> list[CaseMetadata]:
        """Search cases with filters."""
        results = self._filtered(request)

        # Apply pagination
        results = results[request.offset : request.offset + request.limit]

        return [c.to_metadata() for c in results]

    # 在分页前组合各项筛选条件，供列表查询与总数统计共用。
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
            results = [
                c
                for c in results
                if c.tolerance and c.tolerance.lower() == request.tolerance.lower()
            ]

        # Filter by keyword
        if request.keyword:
            keyword = request.keyword.lower()
            results = [
                c
                for c in results
                if keyword in c.part_name.lower()
                or keyword in c.case_id.lower()
                or (c.description and keyword in c.description.lower())
                or any(keyword in f.lower() for f in c.main_features)
            ]

        return results

    # 在锁内校验案例标识唯一性，设置时间戳并保存完整记录。
    def create(self, case: Case) -> Case:
        """Create a new case."""
        with self._lock:
            return self._create_locked(case)

    def _create_locked(self, case: Case) -> Case:
        """锁内执行新增：校验 ID 唯一、写入时间戳并持久化。"""
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

    # 在锁内更新模型上存在的字段，并刷新时间戳后持久化。
    def update(self, case_id: str, updates: dict) -> Case:
        """Update an existing case."""
        with self._lock:
            return self._update_locked(case_id, updates)

    def _update_locked(self, case_id: str, updates: dict) -> Case:
        """锁内执行更新：仅应用模型上真实存在的字段，并刷新 updated_at。"""
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

    # 按标识删除案例并保存，未找到时抛出异常。
    def delete(self, case_id: str) -> None:
        """Delete a case."""
        with self._lock:
            self._delete_locked(case_id)

    def _delete_locked(self, case_id: str) -> None:
        """锁内执行删除；case_id 不存在时抛 ValueError，避免静默失败。"""
        cases = self._load()
        original_count = len(cases)
        cases = [c for c in cases if c.case_id != case_id]

        if len(cases) == original_count:
            raise ValueError(f"Case not found: {case_id}")

        self._cases = cases
        self._save()

        logger.info("Deleted case: %s", case_id)

    # 从案例中提取去重后的行业筛选项。
    def get_industries(self) -> list[str]:
        """Get all unique industries."""
        cases = self._load()
        return sorted(set(c.industry for c in cases))

    # 从案例中提取去重后的材料筛选项。
    def get_materials(self) -> list[str]:
        """Get all unique materials."""
        cases = self._load()
        return sorted(set(c.material for c in cases))

    # 统计全部或筛选后的案例总数，不受当前页大小影响。
    def count(self, request: Optional[CaseSearchRequest] = None) -> int:
        """Return total case count, or the filtered count before pagination."""
        return len(self._filtered(request)) if request else len(self._load())
