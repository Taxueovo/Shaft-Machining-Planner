"""
================================================

Agent Base Class

================================================
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Generator
from enum import Enum

from core.llm import Message, MessageRole, LLMWrapper


logger = logging.getLogger(__name__)


# ==============================================================================
# Agent Types
# ==============================================================================

class AgentType(str, Enum):
    """Agent type enum"""
    CAE_EXPERT = "cae_expert"
    FEATURE_ANALYZER = "feature_analyzer"
    DESIGN_REVIEWER = "design_reviewer"
    COORDINATOR = "coordinator"


# ==============================================================================
# Agent Context & Response
# ==============================================================================

@dataclass
class AgentContext:
    """
    Agent context

    Contains all the information needed to execute an agent
    """
    user_message: str
    session_id: Optional[str] = None
    images: Dict[str, bytes] = field(default_factory=dict)
    features_json: Optional[Dict[str, Any]] = None
    conversation_history: List[Message] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResponse:
    """
    Agent response

    Contains the agent execution result
    """
    content: str
    success: bool = True
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ==============================================================================
# Base Agent
# ==============================================================================

class Agent(ABC):
    """
    Agent base class

    All concrete agents should inherit from this class and implement the process method
    """

    def __init__(
        self,
        name: str,
        agent_type: AgentType,
        system_prompt: str,
        llm_wrapper: Optional[LLMWrapper] = None,
    ):
        """
        Initialize the agent

        Args:
            name: agent name
            agent_type: agent type
            system_prompt: system prompt
            llm_wrapper: LLM wrapper instance
        """
        self.name = name
        self.agent_type = agent_type
        self.system_prompt = system_prompt
        self.llm = llm_wrapper or LLMWrapper()

        logger.info(f"Agent initialized: {name} ({agent_type.value})")

    @abstractmethod
    def process(self, context: AgentContext) -> AgentResponse:
        """
        Process a request

        Args:
            context: agent context

        Returns:
            AgentResponse: agent response
        """
        pass

    def _build_messages(
        self,
        context: AgentContext,
        prepend_history: bool = True,
    ) -> List[Message]:
        """
        Build the message list

        Args:
            context: agent context
            prepend_history: whether to include the conversation history

        Returns:
            message list
        """
        messages = []

        # Add the conversation history
        if prepend_history and context.conversation_history:
            messages.extend(context.conversation_history)

        # Build the current message
        if context.images:
            # Multimodal message
            content_parts = [{"type": "text", "text": context.user_message}]
            for name, image_bytes in context.images.items():
                import base64
                base64_image = base64.b64encode(image_bytes).decode("utf-8")
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                })
            messages.append(Message(role=MessageRole.USER, content=content_parts))
        else:
            messages.append(Message(role=MessageRole.USER, content=context.user_message))

        return messages

    def chat(
        self,
        context: AgentContext,
        stream: bool = False,
    ) -> AgentResponse:
        """
        Chat with the LLM

        Args:
            context: agent context
            stream: whether to stream the output

        Returns:
            AgentResponse: agent response
        """
        try:
            messages = self._build_messages(context)

            response = self.llm.chat(
                messages=messages,
                system_prompt=self.system_prompt,
                stream=stream,
            )

            return AgentResponse(
                content=response if isinstance(response, str) else "",
                success=True,
            )
        except Exception as e:
            logger.error(f"LLM chat error: {e}")
            return AgentResponse(
                content="",
                success=False,
                error=str(e),
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name}, type={self.agent_type.value})"
