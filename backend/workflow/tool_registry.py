"""Tool registry: manages registration and invocation of callable tools."""

from __future__ import annotations

from typing import Any, Optional

from repositories import MachineRepository, ToolRepository
from rules import FEATURE_PROCESS, build_route, is_high_precision


class ToolRegistry:
    """Tool registry managing machine query, tool query, process route generation, and other tools."""

    def __init__(self, machine_repo: MachineRepository, tool_repo: ToolRepository) -> None:
        self.machine_repo = machine_repo
        self.tool_repo = tool_repo
        self._tools: dict[str, dict[str, Any]] = {}
        self._register_defaults()

    def register(
        self, name: str, description: str, parameters: dict[str, Any], handler: Any
    ) -> None:
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }

    def list_tools(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in tool.items() if k != "handler"} for tool in self._tools.values()]

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[name]["handler"](**kwargs)

    def _register_defaults(self) -> None:
        self.register(
            name="query_turning_machines",
            description="Query turning machines by length and diameter.",
            parameters={
                "type": "object",
                "properties": {
                    "required_length_mm": {
                        "type": "number",
                        "description": "Part total length (mm)",
                    },
                    "required_diameter_mm": {"type": "number", "description": "Bar diameter (mm)"},
                    "top_n": {"type": "integer", "description": "Max candidates", "default": 5},
                },
                "required": ["required_length_mm", "required_diameter_mm"],
            },
            handler=self._query_turning_machines,
        )
        self.register(
            name="query_cutting_tools",
            description="Query cutting tools by material and process.",
            parameters={
                "type": "object",
                "properties": {
                    "material": {"type": "string", "description": "Material grade"},
                    "process": {"type": "string", "description": "Machining process"},
                    "top_n": {"type": "integer", "description": "Max candidates", "default": 5},
                },
                "required": ["material", "process"],
            },
            handler=self._query_cutting_tools,
        )
        self.register(
            name="build_process_route",
            description="Generate process route from request, geometry and choices.",
            parameters={
                "type": "object",
                "properties": {
                    "request": {"type": "object"},
                    "geometry": {"type": "object"},
                    "choices": {"type": "object"},
                },
                "required": ["request", "geometry"],
            },
            handler=self._build_process_route,
        )
        self.register(
            name="check_precision",
            description="Check if tolerance/roughness is high precision.",
            parameters={
                "type": "object",
                "properties": {
                    "tolerance_upper_mm": {"type": ["number", "null"]},
                    "tolerance_lower_mm": {"type": ["number", "null"]},
                    "roughness_ra": {"type": ["number", "null"]},
                },
            },
            handler=self._check_precision,
        )
        self.register(
            name="get_feature_processes",
            description="Get feature type to process mapping.",
            parameters={"type": "object", "properties": {}},
            handler=self._get_feature_processes,
        )

    def _query_turning_machines(
        self, required_length_mm: float, required_diameter_mm: float, top_n: int = 5
    ) -> dict[str, Any]:
        return self.machine_repo.search_turning(required_length_mm, required_diameter_mm, top_n)

    def _query_cutting_tools(self, material: str, process: str, top_n: int = 5) -> dict[str, Any]:
        return self.tool_repo.search(material, process, top_n)

    @staticmethod
    def _build_process_route(
        request: dict[str, Any], geometry: dict[str, Any], choices: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        return build_route(request, geometry, choices or {})

    @staticmethod
    def _check_precision(
        tolerance_upper_mm: Optional[float] = None,
        tolerance_lower_mm: Optional[float] = None,
        roughness_ra: Optional[float] = None,
    ) -> dict[str, Any]:
        return {
            "high_precision": is_high_precision(
                tolerance_upper_mm, tolerance_lower_mm, roughness_ra
            ),
            "criteria": "Tolerance absolute value <= 0.02 mm or roughness Ra <= 0.8",
        }

    @staticmethod
    def _get_feature_processes() -> dict[str, Any]:
        return FEATURE_PROCESS
