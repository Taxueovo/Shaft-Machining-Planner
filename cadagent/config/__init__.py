"""
================================================

Configuration Module - ShaftPlanner

Contains:
- Application base settings
- LLM configuration
- Prompt templates

Uses lazy imports to handle missing optional dependencies
================================================
"""


def __getattr__(name):
    """
    Lazy import - imports the submodule only when the attribute is accessed.
    """
    if name in ("APP_NAME", "APP_VERSION", "API_HOST", "API_PORT",
                "ALLOWED_EXTENSIONS", "apply_proxy_settings", "setup_logger"):
        from cadagent.config.settings import (
            APP_NAME, APP_VERSION, API_HOST, API_PORT,
            ALLOWED_EXTENSIONS, apply_proxy_settings, setup_logger
        )
        return locals()[name]

    if name in ("LLMConfig", "get_llm_config", "create_openai_client",
                "create_async_openai_client", "close_llm_clients",
                "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TEMPERATURE"):
        from cadagent.config.llm_config import (
            LLMConfig, get_llm_config, create_openai_client,
            create_async_openai_client, close_llm_clients,
            LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE,
        )
        return locals()[name]

    if name in ("CAE_SYSTEM_PROMPT", "FEATURE_ANALYSIS_PROMPT", "DESIGN_REVIEW_PROMPT"):
        from cadagent.config.prompts import (
            CAE_SYSTEM_PROMPT, FEATURE_ANALYSIS_PROMPT, DESIGN_REVIEW_PROMPT
        )
        return locals()[name]

    raise AttributeError(f"module 'config' has no attribute '{name}'")


__all__ = [
    # Settings
    "APP_NAME", "APP_VERSION", "API_HOST", "API_PORT",
    "ALLOWED_EXTENSIONS", "apply_proxy_settings", "setup_logger",
    # NOTE: PROXY_CONFIG was removed for security (it previously contained
    # hardcoded credentials). Use apply_proxy_settings() instead, which
    # reads HTTP_PROXY_USER / HTTP_PROXY_PASSWORD from the environment.
    # LLM
    "LLMConfig", "get_llm_config", "create_openai_client",
    "create_async_openai_client", "close_llm_clients",
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TEMPERATURE",
    # Prompts
    "CAE_SYSTEM_PROMPT", "FEATURE_ANALYSIS_PROMPT", "DESIGN_REVIEW_PROMPT",
]
