"""
================================================

Core Module - ShaftPlanner

Contains:
- LLM client wrapper
- Tool definitions
- Memory management

Uses lazy imports to handle missing optional dependencies
================================================
"""


def __getattr__(name):
    """
    Lazy import - imports the submodule only when the attribute is accessed.
    """
    if name in ("LLMWrapper", "MultimodalMessage", "Message", "MessageRole"):
        from cadagent.core.llm import LLMWrapper, MultimodalMessage, Message, MessageRole
        return locals()[name]
    
    if name == "PluginManager":
        from cadagent.core.plugin_manager import PluginManager, get_plugin_manager
        return locals().get("PluginManager") or PluginManager
    
    if name in ("BaseMCPServer", "LocalMCPWrapper", "MCPTool", "MCPToolSchema",
                "MCPToolParameter", "MCPResponse", "create_mcp_tool_from_function",
                "convert_tools_to_openai_format"):
        from cadagent.core.mcp_client import (
            BaseMCPServer,
            LocalMCPWrapper,
            MCPTool,
            MCPToolSchema,
            MCPToolParameter,
            MCPResponse,
            create_mcp_tool_from_function,
            convert_tools_to_openai_format,
        )
        return locals()[name]

    raise AttributeError(f"module 'core' has no attribute '{name}'")


__all__ = [
    "LLMWrapper",
    "MultimodalMessage",
    "Message",
    "MessageRole",
    "PluginManager",
    "get_plugin_manager",
    "BaseMCPServer",
    "LocalMCPWrapper",
    "MCPTool",
    "MCPToolSchema",
    "MCPToolParameter",
    "MCPResponse",
    "create_mcp_tool_from_function",
    "convert_tools_to_openai_format",
]
