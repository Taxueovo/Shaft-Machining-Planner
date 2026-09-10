# 集中校验状态、几何和工艺路线结构，阻止无效输入进入后续执行。
"""Guardrails: input/output validation and constraint layer."""

from __future__ import annotations

from typing import Any, Callable, Optional


# 保存统一校验规则，分别检查状态、路线结构和几何必需字段。
class Guardrails:
    """Unified input/output validation and constraint layer."""

    # 初始化有序规则列表，后续注册的规则按加入顺序执行。
    def __init__(self) -> None:
        self._rules: list[Callable[[dict[str, Any]], Optional[str]]] = []

    def add_rule(self, rule: Callable[[dict[str, Any]], Optional[str]]) -> None:
        """注册一条校验规则；规则返回 None 表示通过，否则返回错误描述。"""
        self._rules.append(rule)

    def validate_output(
        self, output: dict[str, Any], expected_keys: list[str], context: str = ""
    ) -> list[str]:
        """检查输出字典是否包含全部期望键，返回缺失键对应的错误列表。"""
        errors = []
        for key in expected_keys:
            if key not in output:
                errors.append(f"[{context}] Output missing expected key: {key}")
        return errors

    def check_all(self, state: dict[str, Any]) -> list[str]:
        """依序执行全部已注册规则，汇总返回所有校验错误。"""
        errors = []
        for rule in self._rules:
            error = rule(state)
            if error:
                errors.append(error)
        return errors

    @staticmethod
    def validate_route(route: list[dict[str, Any]]) -> list[str]:
        """校验工艺路线：非空、每条工序字段齐全且工序号（operation_no）唯一。"""
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
        # 去重后数量减少即说明存在重复工序号（工艺顺序编号不允许重复）
        if len(nos) != len(set(nos)):
            errors.append("Duplicate operation_no detected.")
        return errors

    @staticmethod
    def validate_geometry(geometry: dict[str, Any]) -> list[str]:
        """校验几何模型：必需字段齐全，且各轴段的直径与长度均为正值。"""
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
