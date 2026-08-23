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
        self.registry = registry
        self.guardrails = guardrails
        self.prompt_manager = prompt_manager
        self._fallback_chain: dict[str, list[str]] = {}

    def register_fallback(self, agent_name: str, fallback_chain: list[str]) -> None:
        self._fallback_chain[agent_name] = fallback_chain

    def execute_with_recovery(self, agent_name: str, state: dict[str, Any]) -> AgentResult:
        agent = self.registry.get(agent_name)
        result = agent.safe_execute(state)
        if result.success:
            return result

        fallbacks = self._fallback_chain.get(agent_name, [])
        for fallback_name in fallbacks:
            logger.warning("Agent %s failed, trying fallback %s", agent_name, fallback_name)
            try:
                fallback = self.registry.get(fallback_name)
                result = fallback.safe_execute(state)
                if result.success:
                    result.metadata["fallback_from"] = agent_name
                    return result
            except Exception:
                continue
        return result

    def get_status(self) -> dict[str, Any]:
        return {
            "registered_agents": len(self.registry),
            "agents": self.registry.list_agents(),
            "prompt_templates": self.prompt_manager.list_templates(),
            "fallback_chains": dict(self._fallback_chain),
        }
