# 持久化案例分类树，维护父子层级及分类节点的增删改查。
"""Taxonomy tree database."""

from __future__ import annotations

import json
import logging
import sys
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models.taxonomy import TaxonomyNode, TaxonomyTree

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TAXONOMY_FILE = DATA_DIR / "taxonomy.json"


# 管理分类树的 JSON 持久化及节点增删改查。
class TaxonomyDB:
    """Taxonomy tree database - JSON file storage."""

    def __init__(self, file_path: Optional[Path] = None):
        """初始化分类树数据库；未指定 file_path 时使用项目默认的 data/taxonomy.json。"""
        self._file_path = file_path or TAXONOMY_FILE
        self._tree: Optional[TaxonomyTree] = None
        self._lock = threading.RLock()

    # 从 JSON 文件恢复内存数据，处理文件缺失或内容无效的情况。
    def _load(self) -> TaxonomyTree:
        """Load taxonomy from JSON file."""
        if self._tree is not None:
            return self._tree

        if not self._file_path.exists():
            logger.warning("Taxonomy file not found: %s", self._file_path)
            self._tree = TaxonomyTree(nodes=[])
            return self._tree

        with open(self._file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._tree = TaxonomyTree(**data)
        logger.info("Loaded %d taxonomy nodes", len(self._tree.nodes))
        return self._tree

    # 将当前内存数据序列化并保存到对应 JSON 文件。
    def _save(self) -> None:
        """Save taxonomy to JSON file."""
        if self._tree is None:
            return

        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        # 先写同目录临时文件再原子替换，避免写入中途失败留下残缺的 JSON
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self._file_path.parent,
            prefix=f".{self._file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(self._tree.model_dump(), temp_file, indent=2, ensure_ascii=False)
            temp_path = Path(temp_file.name)
        os.replace(temp_path, self._file_path)

        logger.info("Saved %d taxonomy nodes", len(self._tree.nodes))

    # 返回当前完整分类树模型。
    def get_tree(self) -> TaxonomyTree:
        """Get complete taxonomy tree."""
        return self._load()

    # 按唯一标识查找分类节点，未找到时返回空值。
    def get_node(self, node_id: str) -> Optional[TaxonomyNode]:
        """Get node by ID."""
        tree = self._load()
        return tree.get_node(node_id)

    # 筛选父节点标识匹配的直接子节点。
    def get_children(self, parent_id: Optional[str]) -> list[TaxonomyNode]:
        """Get direct children of a node."""
        tree = self._load()
        return tree.get_children(parent_id)

    # 递归收集指定节点下的全部后代节点。
    def get_all_descendants(self, node_id: str) -> list[TaxonomyNode]:
        """Get all descendants of a node."""
        tree = self._load()
        return tree.get_all_descendants(node_id)

    # 沿父节点链回溯，再反转为从根到目标节点的路径。
    def get_path(self, node_id: str) -> list[TaxonomyNode]:
        """Get path from root to specified node."""
        tree = self._load()
        return tree.get_path(node_id)

    # 取得没有子节点的分类，用于最终案例归类。
    def get_leaves(self) -> list[TaxonomyNode]:
        """Get all leaf nodes."""
        tree = self._load()
        return tree.get_leaves()

    # 校验父节点并添加分类节点，随后持久化分类树。
    def add_node(self, node: TaxonomyNode) -> None:
        """Add a new node."""
        with self._lock:
            self._add_node_locked(node)

    def _add_node_locked(self, node: TaxonomyNode) -> None:
        """锁内执行新增：校验 ID 与父节点均存在后追加并落盘。"""
        tree = self._load()

        # Check if ID already exists
        if tree.get_node(node.id):
            raise ValueError(f"Node ID already exists: {node.id}")

        # Check if parent exists (unless root)
        if node.parent_id and not tree.get_node(node.parent_id):
            raise ValueError(f"Parent node not found: {node.parent_id}")

        tree.nodes.append(node)
        self._tree = tree
        self._save()

    # 在锁内更新节点模型上存在的字段，并持久化分类树。
    def update_node(self, node_id: str, updates: dict) -> None:
        """Update an existing node."""
        with self._lock:
            self._update_node_locked(node_id, updates)

    def _update_node_locked(self, node_id: str, updates: dict) -> None:
        """锁内执行更新：仅应用节点上真实存在的字段并落盘。"""
        tree = self._load()
        node = tree.get_node(node_id)

        if not node:
            raise ValueError(f"Node not found: {node_id}")

        # Apply updates
        for key, value in updates.items():
            if hasattr(node, key):
                setattr(node, key, value)

        self._tree = tree
        self._save()

    # 删除分类节点；按实现约束处理其后代，避免留下无效层级。
    def delete_node(self, node_id: str, recursive: bool = False) -> None:
        """Delete a node."""
        with self._lock:
            self._delete_node_locked(node_id, recursive)

    def _delete_node_locked(self, node_id: str, recursive: bool = False) -> None:
        """锁内执行删除；默认禁止删除仍有子节点的节点，recursive=True 时连后代一并删除。"""
        tree = self._load()
        node = tree.get_node(node_id)

        if not node:
            raise ValueError(f"Node not found: {node_id}")

        # Check if node has children
        children = tree.get_children(node_id)
        if children and not recursive:
            raise ValueError("Node has children, use recursive=True to delete")

        # Remove node and all descendants
        to_remove = {node_id}
        if recursive:
            descendants = tree.get_all_descendants(node_id)
            to_remove.update(d.id for d in descendants)

        tree.nodes = [n for n in tree.nodes if n.id not in to_remove]
        self._tree = tree
        self._save()
