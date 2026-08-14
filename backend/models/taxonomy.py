"""Taxonomy tree data model."""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


class TaxonomyNode(BaseModel):
    """Taxonomy tree node."""

    id: str = Field(description="Unique node identifier")
    name: str = Field(description="Display name")
    parent_id: Optional[str] = Field(default=None, description="Parent node ID")
    description: Optional[str] = Field(default=None, description="Node description")
    icon: Optional[str] = Field(default=None, description="Icon name (optional)")


class TaxonomyTree(BaseModel):
    """Taxonomy tree."""

    nodes: list[TaxonomyNode] = Field(default_factory=list)

    def get_node(self, node_id: str) -> Optional[TaxonomyNode]:
        """Get node by ID."""
        return next((n for n in self.nodes if n.id == node_id), None)

    def get_children(self, parent_id: Optional[str]) -> list[TaxonomyNode]:
        """Get direct children of a node."""
        return [n for n in self.nodes if n.parent_id == parent_id]

    def get_all_descendants(self, node_id: str) -> list[TaxonomyNode]:
        """Get all descendants of a node (recursive)."""
        descendants = []
        children = self.get_children(node_id)
        for child in children:
            descendants.append(child)
            descendants.extend(self.get_all_descendants(child.id))
        return descendants

    def get_path(self, node_id: str) -> list[TaxonomyNode]:
        """Get path from root to specified node."""
        path = []
        current = self.get_node(node_id)
        while current:
            path.insert(0, current)
            current = self.get_node(current.parent_id) if current.parent_id else None
        return path

    def get_leaves(self) -> list[TaxonomyNode]:
        """Get all leaf nodes (nodes without children)."""
        parent_ids = {n.parent_id for n in self.nodes if n.parent_id}
        return [n for n in self.nodes if n.id not in parent_ids]
