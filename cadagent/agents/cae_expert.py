"""
================================================

CAE expert agent - LangGraph + OpenAI implementation

Keeps the LangChain framework (StateGraph, MemorySaver, BaseMessage)
and connects to the LLM via the native OpenAI API

================================================
"""

import json
import logging
import base64
from typing import List, Dict, Any, Generator, Optional, TypedDict, Annotated
from typing_extensions import TypedDict

# ==============================================================================
# LangChain Framework Imports
# ==============================================================================
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

# ==============================================================================
# OpenAI Direct API Import
# ==============================================================================
from openai import OpenAI

# ==============================================================================
# Configuration
# ==============================================================================
from cadagent.config import (
    setup_logger,
    CAE_SYSTEM_PROMPT,
    get_llm_config,
)

logger = setup_logger(__name__)


# ==============================================================================
# LangGraph State Definition
# ==============================================================================

class CAEChatState(TypedDict):
    """
    LangGraph state definition

    Uses the MessagesState pattern to manage the conversation history
    """
    messages: Annotated[List[BaseMessage], add_messages]
    session_id: Optional[str]
    images: Optional[Dict[str, bytes]]
    features_json: Optional[Dict[str, Any]]


# ==============================================================================
# OpenAI Client Factory
# ==============================================================================

def _get_openai_client() -> OpenAI:
    """
    Get the native OpenAI client

    Returns:
        OpenAI: OpenAI client instance
    """
    config = get_llm_config()
    import os
    api_key = os.getenv(config.api_key_env, "")

    return OpenAI(
        api_key=api_key,
        base_url=config.base_url,
    )


# ==============================================================================
# LangGraph Nodes - using the native OpenAI API
# ==============================================================================

def call_model_node(state: CAEChatState) -> Dict[str, Any]:
    """
    LangGraph model call node

    Calls the LLM with the native OpenAI API and
    returns a LangChain AIMessage for compatibility with LangGraph state management.

    Args:
        state: current LangGraph state

    Returns:
        the updated state dictionary
    """
    config = get_llm_config()
    client = _get_openai_client()

    # Convert the LangChain message objects into the dictionary format OpenAI requires
    openai_messages = []

    # Add the system prompt
    openai_messages.append({
        "role": "system",
        "content": CAE_SYSTEM_PROMPT
    })

    # Process the conversation history
    for msg in state["messages"]:
        if isinstance(msg, SystemMessage):
            openai_messages.append({"role": "system", "content": msg.content})
        elif isinstance(msg, HumanMessage):
            # HumanMessage content may be a string or multimodal content
            if isinstance(msg.content, str):
                openai_messages.append({"role": "user", "content": msg.content})
            else:
                openai_messages.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            openai_messages.append({"role": "assistant", "content": msg.content})

    # Handle multimodal content (images)
    if state.get("images"):
        # If there are images, add multimodal content
        last_user_msg = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, HumanMessage):
                last_user_msg = msg
                break

        if last_user_msg:
            content_parts = []
            # Keep the original text
            if isinstance(last_user_msg.content, str):
                content_parts.append({"type": "text", "text": last_user_msg.content})
            else:
                content_parts.extend(last_user_msg.content)

            # Add the images
            for view_name, image_bytes in state["images"].items():
                if image_bytes:
                    base64_image = base64.b64encode(image_bytes).decode("utf-8")
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                    })

            # Replace the last user message with the multimodal content
            openai_messages[-1] = {"role": "user", "content": content_parts}

    # Call the native OpenAI API
    logger.info(f"Calling OpenAI API with {len(openai_messages)} messages")

    response = client.chat.completions.create(
        model=config.model,
        messages=openai_messages,
        temperature=config.temperature,
        stream=False
    )

    # Wrap as a LangChain AIMessage for LangGraph compatibility
    ai_content = response.choices[0].message.content
    logger.info(f"OpenAI response: {len(ai_content)} chars")

    return {"messages": [AIMessage(content=ai_content)]}


def should_continue(state: CAEChatState) -> str:
    """
    Decide whether to continue the conversation

    Simple decision logic: always end (single-turn conversation)
    Can be extended to multi-turn tool calls
    """
    return END


# ==============================================================================
# LangGraph Agent Builder
# ==============================================================================

def create_cae_expert_agent():
    """
    Create the CAE expert agent (LangGraph implementation)

    Connects to the LLM with the native OpenAI API while keeping the
    LangGraph framework's state management and conversation memory.

    Returns:
        the compiled LangGraph application
    """
    # Define the workflow
    workflow = StateGraph(CAEChatState)

    # Add the nodes
    workflow.add_node("model", call_model_node)

    # Define the edges
    workflow.add_edge(START, "model")
    workflow.add_edge("model", END)

    # Use MemorySaver for conversation memory
    checkpointer = MemorySaver()

    # Compile
    app = workflow.compile(checkpointer=checkpointer)

    logger.info("CAE Expert Agent compiled successfully (OpenAI + LangGraph)")

    return app


# ==============================================================================
# Multi-modal Message Creation
# ==============================================================================

def create_initial_message(
    images: Dict[str, bytes],
    features_json: Optional[Dict[str, Any]] = None
) -> HumanMessage:
    """
    Create the initial multimodal message

    Args:
        images: image dictionary {view_name: bytes}
        features_json: feature JSON data

    Returns:
        HumanMessage: multimodal human message
    """
    # Build the text content
    if features_json:
        features_text = f"""
## Part Feature Data

```json
{json.dumps(features_json, ensure_ascii=False, indent=2)}
```

Please analyze this mechanical part using the above feature data and images.
"""
    else:
        features_text = "Please analyze the following multi-view images of the 3D part."

    # Build the multimodal content
    content = [{"type": "text", "text": features_text}]

    # Add the images
    for view_name, image_bytes in images.items():
        if image_bytes:
            base64_image = base64.b64encode(image_bytes).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            })

    return HumanMessage(content=content)


# ==============================================================================
# Streaming Response (using the native OpenAI stream)
# ==============================================================================

def stream_response(
    app,
    messages: List[BaseMessage],
    config: Dict[str, Any],
    images: Optional[Dict[str, bytes]] = None,
    features_json: Optional[Dict[str, Any]] = None,
) -> Generator[str, None, None]:
    """
    Streaming response generator

    Uses LangGraph state management + the native OpenAI streaming API

    Args:
        app: compiled LangGraph application
        messages: conversation message list
        config: Checkpoint configuration
        images: optional multimodal images
        features_json: optional feature data

    Yields:
        text fragments
    """
    try:
        # Build the input state
        input_state = {
            "messages": messages,
            "session_id": config.get("configurable", {}).get("thread_id"),
            "images": images or {},
            "features_json": features_json,
        }

        # Stream the execution
        for event in app.stream(input_state, config, stream_mode="values"):
            if "messages" in event:
                last_msg = event["messages"][-1]
                if isinstance(last_msg, AIMessage) and last_msg.content:
                    yield last_msg.content

    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield f"Sorry, an error occurred: {str(e)}"


# ==============================================================================
# Simple Non-Streaming Interface
# ==============================================================================

def chat(
    messages: List[BaseMessage],
    images: Optional[Dict[str, bytes]] = None,
    features_json: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Simple chat interface (non-streaming)

    Args:
        messages: conversation message list
        images: optional multimodal images
        features_json: optional feature data

    Returns:
        the AI reply content
    """
    config = get_llm_config()
    client = _get_openai_client()

    # Build the messages
    openai_messages = [{"role": "system", "content": CAE_SYSTEM_PROMPT}]

    for msg in messages:
        if isinstance(msg, SystemMessage):
            openai_messages.append({"role": "system", "content": msg.content})
        elif isinstance(msg, HumanMessage):
            if isinstance(msg.content, str):
                openai_messages.append({"role": "user", "content": msg.content})
            else:
                openai_messages.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            openai_messages.append({"role": "assistant", "content": msg.content})

    # Add the images
    if images and openai_messages:
        last_msg = openai_messages[-1]
        if last_msg["role"] == "user":
            content_parts = []
            if isinstance(last_msg["content"], str):
                content_parts.append({"type": "text", "text": last_msg["content"]})
            else:
                content_parts.extend(last_msg["content"])

            for view_name, image_bytes in images.items():
                if image_bytes:
                    base64_image = base64.b64encode(image_bytes).decode("utf-8")
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                    })

            last_msg["content"] = content_parts

    # Call the OpenAI API
    response = client.chat.completions.create(
        model=config.model,
        messages=openai_messages,
        temperature=config.temperature,
        stream=False
    )

    return response.choices[0].message.content


# ==============================================================================
# Module Exports
# ==============================================================================

__all__ = [
    "CAEChatState",
    "create_cae_expert_agent",
    "create_initial_message",
    "stream_response",
    "chat",
    "call_model_node",
    "CAE_SYSTEM_PROMPT",
]
