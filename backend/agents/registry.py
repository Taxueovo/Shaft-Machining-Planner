# 登记智能体实例及能力，供编排器查询、发现与调度。
"""Agent registry: dynamic registration and discovery."""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseAgent

logger = logging.getLogger(__name__)


# 按唯一名称维护智能体实例，支持能力发现及运行状态查询。
class AgentRegistry:
    """Dynamic agent registry - supports registration, discovery and capability-based dispatch."""

    def __init__(self) -> None:
        """初始化代理表及“标签 → 代理名”索引，供按能力检索使用。"""
        self._agents: dict[str, BaseAgent] = {}
        self._tags: dict[str, list[str]] = {}

    def register(self, agent: BaseAgent) -> None:
        """注册代理并按其能力标签建立索引；同名代理不允许重复注册。"""
        name = agent.name
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered.")
        self._agents[name] = agent
        cap = agent.capabilities()
        # 维护倒排索引：同一标签下可挂多个代理，便于后续按标签/能力调度
        for tag in cap.tags:
            self._tags.setdefault(tag, []).append(name)
        logger.info("Registered agent: %s (tags: %s)", name, cap.tags)

    def get(self, name: str) -> BaseAgent:
        """按名称获取代理；未注册时抛出 KeyError。"""
        if name not in self._agents:
            raise KeyError(f"Unknown agent: {name}")
        return self._agents[name]

    def list_agents(self) -> list[dict[str, Any]]:
        """返回各代理的注册信息摘要（名称、能力描述与累计执行次数）。"""
        return [
            {
                "name": agent.name,
                **agent.capabilities().model_dump(),
                "execution_count": agent._execution_count,
            }
            for agent in self._agents.values()
        ]

    def find_for_state(self, state: dict[str, Any]) -> list[BaseAgent]:
        """返回当前状态下可用（前置依赖键均已就绪）的代理列表。"""
        available = []
        for agent in self._agents.values():
            cap = agent.capabilities()
            if all(k in state for k in cap.required_state_keys):
                available.append(agent)
        return available

    def __len__(self) -> int:
        """已注册代理的总数。"""
        return len(self._agents)

    def __contains__(self, name: str) -> bool:
        """判断指定名称的代理是否已注册。"""
        return name in self._agents
