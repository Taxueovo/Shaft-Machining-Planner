"""
OpenAI client - OpenAI Compatible API

Environment variables:

OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL

LANGSMITH:
LANGSMITH_TRACING
LANGSMITH_ENDPOINT
LANGSMITH_API_KEY
LANGSMITH_PROJECT
"""

from __future__ import annotations

import json
import ipaddress
import logging
import os
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv


load_dotenv()


logger = logging.getLogger(__name__)


# =====================================================
# Config
# =====================================================

# OPENAI_* is the canonical scheme; the legacy LLM_* names are honoured as a
# fallback so older .env files (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / ...) still work.
OPENAI_BASE_URL = (
    os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")

OPENAI_MODEL = os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-5-nano"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "remote").strip().lower()
LOCAL_MODEL_BASE_URL = os.getenv("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
LOCAL_MODEL_NAME = os.getenv("LOCAL_MODEL_NAME", "qwen3:8b")


# GPT-5: forcing temperature is not recommended
OPENAI_TEMPERATURE = os.getenv("OPENAI_TEMPERATURE") or os.getenv("LLM_TEMPERATURE")

# 0 / empty means "not set": the API default applies. A literal max_tokens=0 is
# rejected by OpenAI-compatible providers and would make every call fail.
OPENAI_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS") or os.getenv("LLM_MAX_TOKENS") or "0")


_client = None


def _is_loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _runtime_config() -> tuple[str, str, str]:
    if LLM_PROVIDER == "local":
        if not _is_loopback_url(LOCAL_MODEL_BASE_URL):
            raise RuntimeError("LOCAL_MODEL_BASE_URL must use a loopback address")
        return (
            LOCAL_MODEL_BASE_URL.rstrip("/"),
            os.getenv("LOCAL_MODEL_API_KEY", "local-only"),
            LOCAL_MODEL_NAME,
        )
    if LLM_PROVIDER == "rules":
        raise RuntimeError("LLM provider is disabled; deterministic rules remain available")
    if LLM_PROVIDER != "remote":
        raise RuntimeError("LLM_PROVIDER must be remote, local, or rules")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    return OPENAI_BASE_URL.rstrip("/"), OPENAI_API_KEY, OPENAI_MODEL


# =====================================================
# Client
# =====================================================


def _get_client():

    global _client

    if _client is None:
        base_url, api_key, _ = _runtime_config()

        try:
            from openai import OpenAI

        except ImportError:
            raise RuntimeError("Install openai: pip install openai>=1.0")

        _client = OpenAI(base_url=base_url, api_key=api_key, timeout=120, max_retries=2)

    return _client


# =====================================================
# Availability
# =====================================================


def llm_available() -> bool:
    """
    Determine whether the LLM client is available
    """

    if LLM_PROVIDER == "rules":
        return False
    if LLM_PROVIDER == "remote" and not OPENAI_API_KEY:
        return False
    if LLM_PROVIDER == "local" and not _is_loopback_url(LOCAL_MODEL_BASE_URL):
        return False

    try:
        import openai  # noqa: F401  # availability probe

        return True

    except ImportError:
        return False


def warn_if_llm_misconfigured() -> None:
    """Log a clear warning when the configured provider will silently fall back to rules.

    Without this, an empty/misnamed credential (e.g. a legacy LLM_* key while the app
    reads OPENAI_*) makes every LLM node silently degrade to rule mode with no signal.
    """
    if LLM_PROVIDER == "rules":
        logger.info("LLM_PROVIDER=rules: deterministic rule mode is active (no LLM).")
        return
    if LLM_PROVIDER == "remote" and not OPENAI_API_KEY:
        logger.warning(
            "LLM_PROVIDER=remote but no OPENAI_API_KEY (or legacy LLM_API_KEY) is configured. "
            "LLM nodes will silently fall back to the deterministic rule route. Set "
            "OPENAI_API_KEY in .env to enable the model, or set LLM_PROVIDER=rules explicitly."
        )
    elif LLM_PROVIDER == "local" and not _is_loopback_url(LOCAL_MODEL_BASE_URL):
        logger.warning(
            "LLM_PROVIDER=local but LOCAL_MODEL_BASE_URL is not a loopback address; "
            "LLM nodes will fall back to the rule route."
        )


# =====================================================
# Chat
# =====================================================


def chat(
    messages: list[dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_format=None,
):

    client = _get_client()

    _, _, configured_model = _runtime_config()
    kwargs = {
        "model": model or configured_model,
        "messages": messages,
    }

    # GPT-5: do not force sending temperature
    if temperature is not None:
        kwargs["temperature"] = temperature

    elif OPENAI_TEMPERATURE:
        kwargs["temperature"] = float(OPENAI_TEMPERATURE)

    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    elif OPENAI_MAX_TOKENS > 0:
        kwargs["max_tokens"] = OPENAI_MAX_TOKENS

    if response_format:
        kwargs["response_format"] = response_format

    logger.info("LLM request model=%s", kwargs["model"])

    response = client.chat.completions.create(**kwargs)

    content = response.choices[0].message.content or ""

    return content


# =====================================================
# JSON Chat
# =====================================================


def chat_json(messages, *, model=None, temperature=None, max_tokens=None):

    try:
        raw = chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

    except Exception as e:
        msg = str(e).lower()

        if "response_format" in msg or "unsupported" in msg or "not supported" in msg:
            raw = chat(messages, model=model, temperature=temperature, max_tokens=max_tokens)

        else:
            raise

    return _extract_json(raw)


# =====================================================
# JSON Extract
# =====================================================


def _extract_json(text: str):

    text = text.strip()

    try:
        return json.loads(text)

    except Exception:
        pass

    if "```json" in text:
        start = text.index("```json") + 7

        end = text.index("```", start)

        return json.loads(text[start:end].strip())

    for a, b in [("{", "}"), ("[", "]")]:
        if a in text:
            start = text.index(a)

            depth = 0

            for i, ch in enumerate(text[start:], start):
                if ch == a:
                    depth += 1

                elif ch == b:
                    depth -= 1

                    if depth == 0:
                        return json.loads(text[start : i + 1])

    raise ValueError("Unable to parse the LLM JSON response")
