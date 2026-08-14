"""
================================================

Agent module - ShaftPlanner

Contains:
- Agent base class
- CAE expert agent

Uses lazy imports to handle missing dependencies
================================================
"""


def __getattr__(name):
    """
    Lazy import - only imports the submodule when the attribute is accessed
    """
    if name in ("Agent", "AgentContext", "AgentResponse", "AgentType"):
        from cadagent.agents.base import Agent, AgentContext, AgentResponse, AgentType
        return locals()[name]

    if name == "CAEExpertAgent":
        from cadagent.agents.cae_expert import CAEExpertAgent
        return CAEExpertAgent

    raise AttributeError(f"module 'agents' has no attribute '{name}'")


__all__ = [
    "Agent",
    "AgentContext",
    "AgentResponse",
    "AgentType",
    "CAEExpertAgent",
]
