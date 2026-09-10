# 定义扁平存储的分类节点与树查询方法，支持路径及后代遍历。
"""Taxonomy tree data model."""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


# 表示分类标识、名称及父节点等单节点属性。
class TaxonomyNode(BaseModel):
    """Taxonomy tree node."""

    id: str = Field(description="Unique node identifier")
    name: str = Field(description="Display name")
    parent_id: Optional[str] = Field(default=None, description="Parent node ID")
    description: Optional[str] = Field(default=None, description="Node description")
    icon: Optional[str] = Field(default=None, description="Icon name (optional)")


# 保存分类节点集合，并提供父子、后代、路径和叶节点查询。
class TaxonomyTree(BaseModel):
    """Taxonomy tree."""

    nodes: list[TaxonomyNode] = Field(default_factory=list)

    # 按唯一标识查找分类节点，未找到时返回空值。
    def get_node(self, node_id: str) -> Optional[TaxonomyNode]:
        """Get node by ID."""
        return next((n for n in self.nodes if n.id == node_id), None)

    # 筛选父节点标识匹配的直接子节点。
    def get_children(self, parent_id: Optional[str]) -> list[TaxonomyNode]:
        """Get direct children of a node."""
        return [n for n in self.nodes if n.parent_id == parent_id]

    # 递归收集指定节点下的全部后代节点。
    def get_all_descendants(self, node_id: str) -> list[TaxonomyNode]:
        """Get all descendants of a node (recursive)."""
        descendants = []
        children = self.get_children(node_id)
        for child in children:
            descendants.append(child)
            descendants.extend(self.get_all_descendants(child.id))
        return descendants

    # 沿父节点链回溯，再反转为从根到目标节点的路径。
    def get_path(self, node_id: str) -> list[TaxonomyNode]:
        """Get path from root to specified node."""
        path = []
        current = self.get_node(node_id)
        # 自目标节点沿 parent 链向根回溯并逐次前插，最终得到根 → 目标节点的有序路径
        while current:
            path.insert(0, current)
            current = self.get_node(current.parent_id) if current.parent_id else None
        return path

    # 取得没有子节点的分类，用于最终案例归类。
    def get_leaves(self) -> list[TaxonomyNode]:
        """Get all leaf nodes (nodes without children)."""
        parent_ids = {n.parent_id for n in self.nodes if n.parent_id}
        return [n for n in self.nodes if n.id not in parent_ids]
