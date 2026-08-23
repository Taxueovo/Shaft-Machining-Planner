"""Guardrails: input/output validation and constraint layer."""

from __future__ import annotations

from typing import Any, Callable, Optional


class Guardrails:
    """Unified input/output validation and constraint layer."""

    def __init__(self) -> None:
        self._rules: list[Callable[[dict[str, Any]], Optional[str]]] = []

    def add_rule(self, rule: Callable[[dict[str, Any]], Optional[str]]) -> None:
        self._rules.append(rule)

    def validate_output(
        self, output: dict[str, Any], expected_keys: list[str], context: str = ""
    ) -> list[str]:
        errors = []
        for key in expected_keys:
            if key not in output:
                errors.append(f"[{context}] Output missing expected key: {key}")
        return errors

    def check_all(self, state: dict[str, Any]) -> list[str]:
        errors = []
        for rule in self._rules:
            error = rule(state)
            if error:
                errors.append(error)
        return errors

    @staticmethod
    def validate_route(route: list[dict[str, Any]]) -> list[str]:
        errors = []
        if not route:
            errors.append("Process route is empty.")
            return errors
        required_fields = {"operation_no", "name", "stage", "description"}
        for i, op in enumerate(route):
            missing = required_fields - set(op.keys())
            if missing:
                errors.append(f"Operation {i}: missing fields {missing}")
        nos = [op.get("operation_no") for op in route]
        if len(nos) != len(set(nos)):
            errors.append("Duplicate operation_no detected.")
        return errors

    @staticmethod
    def validate_geometry(geometry: dict[str, Any]) -> list[str]:
        errors = []
        required = {"total_length_mm", "blank_diameter_mm", "segments", "features"}
        missing = required - set(geometry.keys())
        if missing:
            errors.append(f"Geometry model missing fields: {missing}")
        for seg in geometry.get("segments", []):
            if seg.get("diameter_mm", 0) <= 0:
                errors.append(f"Segment {seg.get('segment_id')}: diameter must be positive.")
            if seg.get("length_mm", 0) <= 0:
                errors.append(f"Segment {seg.get('segment_id')}: length must be positive.")
        return errors
