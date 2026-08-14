"""Agent base class and standard data structures."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AgentCapability(BaseModel):
    """Agent capability description."""

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_state_keys: list[str] = Field(default_factory=list)
    produces_state_keys: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Standard format for agent execution results."""

    success: bool
    state_updates: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    error: Optional[str] = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseAgent(ABC):
    """Agent abstract base class - defines a unified Agent interface.

    All agents must implement:
    - capabilities(): returns the capability description
    - execute(): executes the core logic
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._execution_count = 0
        self._last_execution: Optional[dict[str, Any]] = None

    @abstractmethod
    def capabilities(self) -> AgentCapability:
        """Return this agent's capability description."""
        ...

    @abstractmethod
    def execute(self, state: dict[str, Any]) -> AgentResult:
        """Execute the agent's core logic."""
        ...

    def validate_input(self, state: dict[str, Any]) -> Optional[str]:
        """Validate that the input state satisfies preconditions. Returns None if passed, otherwise an error message."""
        cap = self.capabilities()
        missing = [k for k in cap.required_state_keys if k not in state]
        if missing:
            return f"{self.name} missing required state keys: {', '.join(missing)}"
        return None

    def validate_output(self, result: AgentResult) -> Optional[str]:
        """Validate output against schema constraints. Returns None if passed."""
        cap = self.capabilities()
        if cap.output_schema:
            try:
                class _Output(BaseModel):
                    __annotations__ = {k: Any for k in cap.produces_state_keys}
                _Output(**result.state_updates)
            except Exception as exc:
                return f"{self.name} output validation failed: {exc}"
        return None

    def safe_execute(self, state: dict[str, Any]) -> AgentResult:
        """Safe execution entry with Guardrails."""
        input_error = self.validate_input(state)
        if input_error:
            logger.error("Input validation failed for %s: %s", self.name, input_error)
            return AgentResult(success=False, error=input_error, message=f"Input validation failed: {input_error}")

        t0 = time.monotonic()
        try:
            result = self.execute(state)
        except Exception as exc:
            from langgraph.errors import GraphInterrupt
            if isinstance(exc, GraphInterrupt):
                raise
            duration = (time.monotonic() - t0) * 1000
            logger.exception("Agent %s execution failed after %.0fms", self.name, duration)
            return AgentResult(
                success=False, error=f"{type(exc).__name__}: {exc}",
                message=f"{self.name} execution failed: {exc}", metadata={"duration_ms": round(duration)},
            )

        duration = (time.monotonic() - t0) * 1000
        result.metadata["duration_ms"] = round(duration)

        output_error = self.validate_output(result)
        if output_error:
            logger.warning("Output validation warning for %s: %s", self.name, output_error)
            result.metadata["output_warning"] = output_error

        self._execution_count += 1
        self._last_execution = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration),
            "success": result.success,
        }
        return result
