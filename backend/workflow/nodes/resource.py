"""Resource matching nodes: machine check, tool check."""

from __future__ import annotations

from typing import Any

from models.workflow import WorkflowState, traced


class ResourceNodesMixin:
    """Mixin for resource matching nodes."""

    @traced("machine_check", ["request", "geometry"])
    def machine_check(self, state: WorkflowState) -> dict[str, Any]:
        request, geometry = state["request"], state["geometry"]
        candidates = self.machine_repo.query(
            max_length_mm=geometry["total_length_mm"],
            max_diameter_mm=request["blank_diameter_mm"],
        )
        return {
            "machine_check": {
                "candidates": [m.model_dump() for m in candidates],
                "count": len(candidates),
            }
        }

    @traced("tool_check", ["request", "geometry"])
    def tool_check(self, state: WorkflowState) -> dict[str, Any]:
        request = state["request"]
        material = request.get("material", "45")
        tools = self.tool_repo.query(material=material)
        return {
            "tool_check": {
                "tools": [t.model_dump() for t in tools],
                "count": len(tools),
            }
        }
