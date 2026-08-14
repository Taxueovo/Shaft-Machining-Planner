"""Taxonomy tree database."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models.taxonomy import TaxonomyNode, TaxonomyTree

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TAXONOMY_FILE = DATA_DIR / "taxonomy.json"


class TaxonomyDB:
    """Taxonomy tree database - JSON file storage."""

    def __init__(self, file_path: Optional[Path] = None):
        self._file_path = file_path or TAXONOMY_FILE
        self._tree: Optional[TaxonomyTree] = None

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

    def _save(self) -> None:
        """Save taxonomy to JSON file."""
        if self._tree is None:
            return

        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file_path, "w", encoding="utf-8") as f:
            json.dump(self._tree.model_dump(), f, indent=2, ensure_ascii=False)

        logger.info("Saved %d taxonomy nodes", len(self._tree.nodes))

    def get_tree(self) -> TaxonomyTree:
        """Get complete taxonomy tree."""
        return self._load()

    def get_node(self, node_id: str) -> Optional[TaxonomyNode]:
        """Get node by ID."""
        tree = self._load()
        return tree.get_node(node_id)

    def get_children(self, parent_id: Optional[str]) -> list[TaxonomyNode]:
        """Get direct children of a node."""
        tree = self._load()
        return tree.get_children(parent_id)

    def get_all_descendants(self, node_id: str) -> list[TaxonomyNode]:
        """Get all descendants of a node."""
        tree = self._load()
        return tree.get_all_descendants(node_id)

    def get_path(self, node_id: str) -> list[TaxonomyNode]:
        """Get path from root to specified node."""
        tree = self._load()
        return tree.get_path(node_id)

    def get_leaves(self) -> list[TaxonomyNode]:
        """Get all leaf nodes."""
        tree = self._load()
        return tree.get_leaves()

    def add_node(self, node: TaxonomyNode) -> None:
        """Add a new node."""
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

    def update_node(self, node_id: str, updates: dict) -> None:
        """Update an existing node."""
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

    def delete_node(self, node_id: str, recursive: bool = False) -> None:
        """Delete a node."""
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
