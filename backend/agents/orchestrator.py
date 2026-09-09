"""Orchestrator: enhanced scheduler with dynamic routing and error recovery."""

from __future__ import annotations

import logging
from typing import Any

from .base import AgentResult
from .registry import AgentRegistry
from .guardrails import Guardrails
from .prompts import PromptManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """Enhanced scheduler - supports dynamic routing, error recovery and agent orchestration."""

    def __init__(
        self, registry: AgentRegistry, guardrails: Guardrails, prompt_manager: PromptManager
    ) -> None:
        """组合注册表、护栏与提示管理器，并维护各代理的备选回退链。"""
        self.registry = registry
        self.guardrails = guardrails
        self.prompt_manager = prompt_manager
        self._fallback_chain: dict[str, list[str]] = {}

    def register_fallback(self, agent_name: str, fallback_chain: list[str]) -> None:
        """为指定代理登记一条备选代理链，供其失败时按顺序接管。"""
        self._fallback_chain[agent_name] = fallback_chain

    def execute_with_recovery(self, agent_name: str, state: dict[str, Any]) -> AgentResult:
        """执行指定代理；主代理失败时沿回退链依次尝试，返回首个成功结果。"""
        agent = self.registry.get(agent_name)
        result = agent.safe_execute(state)
        if result.success:
            return result

        fallbacks = self._fallback_chain.get(agent_name, [])
        # 按登记顺序逐级降级：任一回退代理成功即返回；全部失败则保留最后一次失败结果
        for fallback_name in fallbacks:
            logger.warning("Agent %s failed, trying fallback %s", agent_name, fallback_name)
            try:
                fallback = self.registry.get(fallback_name)
                result = fallback.safe_execute(state)
                if result.success:
                    # 记录原始失败代理名，便于上层追溯实际执行路径
                    result.metadata["fallback_from"] = agent_name
                    return result
            except Exception:
                continue
        return result

    def get_status(self) -> dict[str, Any]:
        """返回编排器运行概况：已注册代理、提示模板与回退链配置。"""
        return {
            "registered_agents": len(self.registry),
            "agents": self.registry.list_agents(),
            "prompt_templates": self.prompt_manager.list_templates(),
            "fallback_chains": dict(self._fallback_chain),
        }
