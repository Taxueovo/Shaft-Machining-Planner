# 定义智能体统一接口、能力声明和执行结果，集中处理输入输出校验与异常。
"""Agent base class and standard data structures."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# 声明智能体名称、输入输出字段和标签，供调度与校验读取。
class AgentCapability(BaseModel):
    """Agent capability description."""

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_state_keys: list[str] = Field(default_factory=list)
    produces_state_keys: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


# 统一封装执行成功标志、状态增量、错误和工具调用记录。
class AgentResult(BaseModel):
    """Standard format for agent execution results."""

    success: bool
    state_updates: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    error: Optional[str] = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# 定义智能体共同接口，并集中维护执行统计与安全调用流程。
class BaseAgent(ABC):
    """Agent abstract base class - defines a unified Agent interface.

    All agents must implement:
    - capabilities(): returns the capability description
    - execute(): executes the core logic
    """

    def __init__(self, name: str) -> None:
        """初始化代理：记录唯一名称，并预置执行统计字段供运行审计使用。"""
        self.name = name
        self._execution_count = 0
        self._last_execution: Optional[dict[str, Any]] = None

    # 声明该智能体读取和产出的状态键，供执行前后检查。
    @abstractmethod
    def capabilities(self) -> AgentCapability:
        """Return this agent's capability description."""
        ...

    # 实现当前智能体的执行入口，按能力声明返回结构化结果。
    @abstractmethod
    def execute(self, state: dict[str, Any]) -> AgentResult:
        """Execute the agent's core logic."""
        ...

    # 检查能力声明要求的输入键，缺失时返回错误而不进入核心逻辑。
    def validate_input(self, state: dict[str, Any]) -> Optional[str]:
        """Validate that the input state satisfies preconditions. Returns None if passed, otherwise an error message."""
        cap = self.capabilities()
        missing = [k for k in cap.required_state_keys if k not in state]
        if missing:
            return f"{self.name} missing required state keys: {', '.join(missing)}"
        return None

    # 检查结果中必需的输出键，防止空结果被当作执行成功。
    def validate_output(self, result: AgentResult) -> Optional[str]:
        """Validate output against schema constraints. Returns None if passed."""
        cap = self.capabilities()
        missing = [key for key in cap.produces_state_keys if key not in result.state_updates]
        if missing:
            return f"{self.name} missing required outputs: {', '.join(missing)}"
        return None

    # 执行前后校验并记录耗时；普通异常转为失败结果，人工中断信号继续上抛。
    def safe_execute(self, state: dict[str, Any]) -> AgentResult:
        """Safe execution entry with Guardrails."""
        input_error = self.validate_input(state)
        if input_error:
            logger.error("Input validation failed for %s: %s", self.name, input_error)
            return AgentResult(
                success=False, error=input_error, message=f"Input validation failed: {input_error}"
            )

        t0 = time.monotonic()
        try:
            result = self.execute(state)
        except Exception as exc:
            from langgraph.errors import GraphInterrupt

            # GraphInterrupt 是 LangGraph 的中断控制信号（如等待人工介入），须原样向上抛出而非按失败处理
            if isinstance(exc, GraphInterrupt):
                raise
            duration = (time.monotonic() - t0) * 1000
            logger.exception("Agent %s execution failed after %.0fms", self.name, duration)
            return AgentResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                message=f"{self.name} execution failed: {exc}",
                metadata={"duration_ms": round(duration)},
            )

        duration = (time.monotonic() - t0) * 1000
        result.metadata["duration_ms"] = round(duration)

        output_error = self.validate_output(result)
        if output_error:
            logger.warning("Output validation warning for %s: %s", self.name, output_error)
            result.success = False
            result.error = output_error

        self._execution_count += 1
        self._last_execution = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration),
            "success": result.success,
        }
        return result
