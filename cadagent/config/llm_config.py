"""
================================================

LLM Configuration Module

================================================
"""

import os
import logging
import threading
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI
from openai import AsyncOpenAI
import httpx

# Loads the project-root .env (OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL);
# the backend peagent (backend/llm_client.py) uses the same credentials and endpoint.
load_dotenv()


# ==============================================================================
# Logging
# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ==============================================================================
# Global Client Singletons (prevents httpx cancel scope errors)
# ==============================================================================
# httpx 0.28+ emits "Attempted to exit cancel scope in a different task" warnings
# when an AsyncClient is created and destroyed per-request. The fix: keep SINGLE
# client instances alive for the process lifetime and reuse them.

_sync_client: Optional[OpenAI] = None
_async_client: Optional[AsyncOpenAI] = None
_client_lock = threading.Lock()


# ==============================================================================
# LLM Configuration Data Class
# ==============================================================================

@dataclass
class LLMConfig:
    """LLM configuration data class."""
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-5-nano"
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    timeout: float = 120.0

    def __post_init__(self):
        """Validate the configuration."""
        if not self.api_key_env:
            raise ValueError("API key environment variable name is required")


def get_llm_config() -> LLMConfig:
    """
    Get an LLMConfig instance (prefers OPENAI_BASE_URL / OPENAI_MODEL from the
    project-root .env).

    Returns:
        LLMConfig: the LLM configuration object
    """
    return LLMConfig(
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.getenv("OPENAI_MODEL", "gpt-5-nano"),
    )


def get_api_key() -> str:
    """
    Get the API key.

    Returns:
        str: the API key, or an empty string when not set
    """
    config = get_llm_config()
    api_key = os.getenv(config.api_key_env, "")
    logger.info(f"API Key length: {len(api_key) if api_key else 0}")
    return api_key


def _resolve_http_client(async_client: bool = False):
    """
    Build an httpx client with proxy configuration (sync or async).
    
    Args:
        async_client: If True, return an ``httpx.AsyncClient``; otherwise a
            synchronous ``httpx.Client``.
    
    Returns:
        The configured httpx client, or ``None`` when no proxy is set.
    """
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if not proxy:
        return None
    
    logger.info(f"Using proxy: {proxy[:30]}...")
    if async_client:
        return httpx.AsyncClient(proxy=proxy, timeout=120.0)
    return httpx.Client(proxy=proxy)


def create_openai_client() -> OpenAI:
    """
    Get the synchronous OpenAI client singleton.

    Uses a singleton so we do not create a new client per request (wasteful and
    error-prone) and avoid httpx 0.28+'s cross-task cancel scope issue.

    Returns:
        OpenAI: the synchronous OpenAI client singleton
    """
    global _sync_client
    if _sync_client is None:
        with _client_lock:
            if _sync_client is None:
                config = get_llm_config()
                api_key = get_api_key()
                
                logger.info(f"Creating OpenAI (sync) client singleton for: {config.base_url}")
                logger.info(f"Model: {config.model}")
                
                http_client = _resolve_http_client(async_client=False)
                _sync_client = OpenAI(
                    api_key=api_key,
                    base_url=config.base_url,
                    timeout=config.timeout,
                    http_client=http_client,
                )
    return _sync_client


def create_async_openai_client() -> AsyncOpenAI:
    """
    Get the asynchronous OpenAI client singleton.

    The async client does not block the asyncio event loop when calling
    ``chat.completions.create(stream=True)``, so FastAPI / Starlette streaming
    responses flush to the frontend promptly and the "GeneratorExit / cancel
    scope in different task" errors are avoided.

    Uses a singleton to avoid httpx 0.28+'s cross-task cancel scope issue.

    Returns:
        AsyncOpenAI: the asynchronous OpenAI client singleton
    """
    global _async_client
    if _async_client is None:
        with _client_lock:
            if _async_client is None:
                config = get_llm_config()
                api_key = get_api_key()
                
                logger.info(f"Creating OpenAI (async) client singleton for: {config.base_url}")
                logger.info(f"Model: {config.model}")
                
                http_client = _resolve_http_client(async_client=True)
                _async_client = AsyncOpenAI(
                    api_key=api_key,
                    base_url=config.base_url,
                    timeout=config.timeout,
                    http_client=http_client,
                )
    return _async_client


async def close_llm_clients():
    """
    Close all LLM clients (called on application shutdown).

    Ensures all connection-pool resources are released properly to avoid
    warnings and resource leaks.
    """
    global _sync_client, _async_client
    
    if _sync_client is not None:
        try:
            if hasattr(_sync_client, 'close'):
                _sync_client.close()
            logger.info("Sync LLM client closed")
        except Exception as e:
            logger.warning(f"Error closing sync LLM client: {e}")
        _sync_client = None
    
    if _async_client is not None:
        try:
            if hasattr(_async_client, 'close'):
                await _async_client.close()
            logger.info("Async LLM client closed")
        except Exception as e:
            logger.warning(f"Error closing async LLM client: {e}")
        _async_client = None


# ==============================================================================
# Module-level convenience functions
# ==============================================================================

# For backward compatibility (note: LLM_API_KEY holds the *environment variable
# name*, not the key itself)
LLM_API_KEY = "OPENAI_API_KEY"
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
LLM_TEMPERATURE = 0.7