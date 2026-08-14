"""Agent registry: dynamic registration and discovery."""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Dynamic agent registry - supports registration, discovery and capability-based dispatch."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._tags: dict[str, list[str]] = {}

    def register(self, agent: BaseAgent) -> None:
        name = agent.name
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered.")
        self._agents[name] = agent
        cap = agent.capabilities()
        for tag in cap.tags:
            self._tags.setdefault(tag, []).append(name)
        logger.info("Registered agent: %s (tags: %s)", name, cap.tags)

    def get(self, name: str) -> BaseAgent:
        if name not in self._agents:
            raise KeyError(f"Unknown agent: {name}")
        return self._agents[name]

    def list_agents(self) -> list[dict[str, Any]]:
        return [
            {"name": agent.name, **agent.capabilities().model_dump(), "execution_count": agent._execution_count}
            for agent in self._agents.values()
        ]

    def find_by_tag(self, tag: str) -> list[BaseAgent]:
        names = self._tags.get(tag, [])
        return [self._agents[n] for n in names if n in self._agents]

    def find_for_state(self, state: dict[str, Any]) -> list[BaseAgent]:
        available = []
        for agent in self._agents.values():
            cap = agent.capabilities()
            if all(k in state for k in cap.required_state_keys):
                available.append(agent)
        return available

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: str) -> bool:
        return name in self._agents
