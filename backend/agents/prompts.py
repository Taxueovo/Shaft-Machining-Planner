"""Prompt template manager."""

from __future__ import annotations

from typing import Any


class PromptManager:
    """Externalized management of prompt templates."""

    def __init__(self) -> None:
        self._templates: dict[str, dict[str, str]] = {}

    def register(self, name: str, system: str = "", user: str = "", version: str = "1.0") -> None:
        """按名称登记一条提示模板，system 与 user 文案可分别指定。"""
        self._templates[name] = {"system": system, "user": user, "version": version}

    def get(self, name: str, variables: dict[str, Any] | None = None) -> dict[str, str]:
        """取模板并将 {占位符} 替换为变量值；模板未登记时抛出 KeyError。"""
        if name not in self._templates:
            raise KeyError(f"Unknown prompt template: {name}")
        template = self._templates[name]
        vars_ = variables or {}
        result = {}
        for key in ("system", "user"):
            text = template[key]
            for var_name, var_value in vars_.items():
                # 模板占位符采用花括号 {变量名} 写法，逐项做纯文本替换
                text = text.replace(f"{{{var_name}}}", str(var_value))
            result[key] = text
        return result

    def list_templates(self) -> list[dict[str, str]]:
        """返回全部已登记模板的名称与版本清单。"""
        return [{"name": name, "version": tpl["version"]} for name, tpl in self._templates.items()]

    def render_messages(
        self, name: str, variables: dict[str, Any] | None = None
    ) -> list[dict[str, str]]:
        """渲染模板并组装为对话消息列表；空 system/user 段落不产生对应消息。"""
        rendered = self.get(name, variables)
        messages = []
        if rendered["system"]:
            messages.append({"role": "system", "content": rendered["system"]})
        if rendered["user"]:
            messages.append({"role": "user", "content": rendered["user"]})
        return messages
