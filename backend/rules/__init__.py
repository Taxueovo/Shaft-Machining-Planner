"""Shaft Machining Planner rule engine."""

from .constants import (
    FEATURE_NAME, FEATURE_PROCESS, HEAT_NAME, SURFACE_NAME,
    MATERIAL_PROPERTIES, FEATURE_INSERT_STAGE, FEATURE_SUPPORTS_SPLIT,
    FEATURE_REQUIRED_PROCESS,
    get_material_properties, is_high_precision, is_feature_high_precision, requires_grinding,
)
from .engine import add_operation, build_route

__all__ = [
    "FEATURE_NAME", "FEATURE_PROCESS", "HEAT_NAME", "SURFACE_NAME",
    "MATERIAL_PROPERTIES", "FEATURE_INSERT_STAGE", "FEATURE_SUPPORTS_SPLIT",
    "FEATURE_REQUIRED_PROCESS",
    "get_material_properties", "is_high_precision", "is_feature_high_precision", "requires_grinding",
    "add_operation", "build_route",
]
