"""
================================================

LLM Client Wrapper

Provides a unified LLM call interface supporting streaming output and
multimodal content.

================================================
"""

import logging
import base64
from typing import List, Dict, Any, Optional, Generator, Union
from dataclasses import dataclass
from enum import Enum

from cadagent.config.llm_config import get_llm_config, create_openai_client


logger = logging.getLogger(__name__)


# ==============================================================================
# Message Types
# ==============================================================================

class MessageRole(str, Enum):
    """Message role enum."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class MultimodalMessage:
    """A multimodal message content item."""
    type: str  # "text" or "image_url"
    text: Optional[str] = None
    image_url: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        if self.type == "text":
            return {"type": "text", "text": self.text or ""}
        elif self.type == "image_url":
            return {"type": "image_url", "image_url": self.image_url or {}}
        return {}


@dataclass
class Message:
    """A conversation message."""
    role: Union[str, MessageRole]
    content: Union[str, List[Dict[str, Any]]]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to API format."""
        return {
            "role": self.role.value if isinstance(self.role, MessageRole) else self.role,
            "content": self.content
        }


# ==============================================================================
# LLM Wrapper
# ==============================================================================

class LLMWrapper:
    """
    LLM wrapper class.

    Provides a unified LLM call interface supporting:
    - Text chat
    - Multimodal chat (text + images)
    - Streaming output
    - Conversation history management
    """

    def __init__(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """
        Initialize the LLM wrapper.

        Args:
            model: Model name; defaults to the configured model
            temperature: Temperature parameter
            max_tokens: Maximum token count
        """
        self.config = get_llm_config()
        self.model = model or self.config.model
        self.temperature = temperature if temperature is not None else self.config.temperature
        self.max_tokens = max_tokens or self.config.max_tokens
        self.client = None
        
        logger.info(f"LLMWrapper initialized with model: {self.model}")
    
    def _get_client(self):
        """Get or create the OpenAI client."""
        if self.client is None:
            self.client = create_openai_client()
        return self.client
    
    def _build_messages(
        self,
        system_prompt: Optional[str],
        messages: List[Message],
    ) -> List[Dict[str, Any]]:
        """Build the message list."""
        result = []
        
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        
        for msg in messages:
            result.append(msg.to_dict())
        
        return result
    
    def chat(
        self,
        messages: List[Message],
        system_prompt: Optional[str] = None,
        stream: bool = False,
    ) -> Union[str, Generator[str, None, None]]:
        """
        Chat interface.

        Args:
            messages: List of messages
            system_prompt: System prompt
            stream: Whether to stream the output

        Returns:
            A generator when stream=True, otherwise the full response string
        """
        client = self._get_client()
        api_messages = self._build_messages(system_prompt, messages)
        
        logger.info(f"Sending request with {len(api_messages)} messages, stream={stream}")
        
        response = client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=stream,
        )
        
        if stream:
            def generate():
                full_response = ""
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_response += content
                        yield content
                logger.info(f"Streaming complete: {len(full_response)} chars")
            return generate()
        else:
            return response.choices[0].message.content
    
    def chat_with_images(
        self,
        text: str,
        images: Dict[str, bytes],
        system_prompt: Optional[str] = None,
        stream: bool = False,
    ) -> Union[str, Generator[str, None, None]]:
        """
        Chat interface with images.

        Args:
            text: Text content
            images: Image dictionary {name: bytes}
            system_prompt: System prompt
            stream: Whether to stream the output

        Returns:
            A generator when stream=True, otherwise the full response string
        """
        # Build multimodal content
        content_parts = [{"type": "text", "text": text}]
        
        for name, image_bytes in images.items():
            base64_image = base64.b64encode(image_bytes).decode("utf-8")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            })
        
        messages = [Message(role=MessageRole.USER, content=content_parts)]
        return self.chat(messages, system_prompt, stream)
    
    def close(self):
        """Close the client."""
        if self.client:
            self.client = None