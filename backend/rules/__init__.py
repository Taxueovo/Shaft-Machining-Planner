# 包入口：组织当前模块命名空间；已有导出用于保持上层导入方式稳定。
"""Shaft Machining Planner rule engine."""

from .constants import (
    FEATURE_NAME,
    FEATURE_PROCESS,
    HEAT_NAME,
    SURFACE_NAME,
    MATERIAL_PROPERTIES,
    FEATURE_SUPPORTS_SPLIT,
    FEATURE_REQUIRED_PROCESS,
    get_material_properties,
    is_high_precision,
    is_feature_high_precision,
    requires_grinding,
)
from .engine import add_operation, build_route

__all__ = [
    "FEATURE_NAME",
    "FEATURE_PROCESS",
    "HEAT_NAME",
    "SURFACE_NAME",
    "MATERIAL_PROPERTIES",
    "FEATURE_SUPPORTS_SPLIT",
    "FEATURE_REQUIRED_PROCESS",
    "get_material_properties",
    "is_high_precision",
    "is_feature_high_precision",
    "requires_grinding",
    "add_operation",
    "build_route",
]
