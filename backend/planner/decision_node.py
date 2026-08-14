"""Planner decision node base class for reusable decision nodes."""

from __future__ import annotations

from typing import Any, Dict, Optional


class DecisionNode:
    """A lightweight base class representing a single decision node in the planner.

    Each concrete node should override evaluate() to apply its specific logic
    using inputs, knowledge, and constraints.
    """

    def __init__(
        self,
        name: str,
        inputs: Optional[Dict[str, Any]] = None,
        knowledge: Optional[Dict[str, Any]] = None,
        constraints: Optional[list] = None,
    ) -> None:
        self.name = name
        self.inputs = inputs or {}
        self.knowledge = knowledge or {}
        self.constraints = constraints or []
        self.output: Dict[str, Any] = {}

    def evaluate(self) -> Dict[str, Any]:  # pragma: no cover
        """Execute the decision node logic.

        This is a placeholder to be overridden by concrete decision nodes.
        """
        raise NotImplementedError("DecisionNode.evaluate must be implemented by subclasses")
