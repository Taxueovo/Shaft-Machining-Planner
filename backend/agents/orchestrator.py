"""Orchestrator: enhanced scheduler with dynamic routing and error recovery."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .base import AgentResult
from .registry import AgentRegistry
from .guardrails import Guardrails
from .prompts import PromptManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """Enhanced scheduler - supports dynamic routing, error recovery and agent orchestration."""

    def __init__(self, registry: AgentRegistry, guardrails: Guardrails, prompt_manager: PromptManager) -> None:
        self.registry = registry
        self.guardrails = guardrails
        self.prompt_manager = prompt_manager
        self._error_handlers: dict[str, Callable[[Exception, dict[str, Any]], AgentResult]] = {}
        self._fallback_chain: dict[str, list[str]] = {}

    def register_error_handler(self, agent_name: str, handler: Callable[[Exception, dict[str, Any]], AgentResult]) -> None:
        self._error_handlers[agent_name] = handler

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

    def dynamic_dispatch(self, state: dict[str, Any], completed_agents: set[str] | None = None) -> Optional[str]:
        completed = completed_agents or set()
        available = self.registry.find_for_state(state)
        candidates = [a for a in available if a.name not in completed]
        if not candidates:
            return None
        high_priority = [a for a in candidates if "priority:high" in a.capabilities().tags]
        if high_priority:
            return high_priority[0].name
        return candidates[0].name

    def get_status(self) -> dict[str, Any]:
        return {
            "registered_agents": len(self.registry),
            "agents": self.registry.list_agents(),
            "prompt_templates": self.prompt_manager.list_templates(),
            "fallback_chains": dict(self._fallback_chain),
        }
