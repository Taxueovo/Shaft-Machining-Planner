"""Dedicated vector model client.

Wraps the OpenAI-compatible Embeddings API, independent of the main LLM configuration.
Lazy initialization, batch vectorization, automatic retries.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .config import EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

# ── Retry configuration ──

MAX_RETRIES: int = 2
RETRY_DELAY_SECONDS: float = 1.0

# ── Singleton client ──

_client: Any = None


def _get_client() -> Any:
    """Lazily initialize the OpenAI client (embedding only)."""
    global _client
    if _client is None:
        if not EMBEDDING_API_KEY:
            raise RuntimeError(
                "EMBEDDING_API_KEY is not configured. Set EMBEDDING_API_KEY=your-key in the .env file"
            )
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError(
                "The openai package is not installed. Run: pip install openai>=1.0"
            )
        _client = OpenAI(
            base_url=EMBEDDING_BASE_URL,
            api_key=EMBEDDING_API_KEY,
            timeout=60.0,
            max_retries=1,
        )
    return _client


def embedding_available() -> bool:
    """Check whether the embedding service is available."""
    if not EMBEDDING_API_KEY:
        return False
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


def embed(
    texts: list[str],
    *,
    model: Optional[str] = None,
) -> list[list[float]]:
    """Batch text vectorization.

    Parameters
    ----------
    texts : list of str
        The texts to embed.
    model : str, optional
        Embedding model name; defaults to EMBEDDING_MODEL.

    Returns
    -------
    list of list of float
        The embedding vector for each text; dimension depends on the model.
    """
    if not texts:
        return []

    client = _get_client()
    effective_model = model or EMBEDDING_MODEL

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            logger.info(
                "Embedding request: model=%s, texts=%d", effective_model, len(texts)
            )
            response = client.embeddings.create(
                model=effective_model,
                input=texts,
            )
            vectors = [item.embedding for item in response.data]
            logger.info("Embedding response: %d vectors", len(vectors))
            return vectors
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = RETRY_DELAY_SECONDS * (attempt + 1)
                logger.warning(
                    "Embedding attempt %d/%d failed: %s. Retrying in %.1fs...",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    exc,
                    wait,
                )
                time.sleep(wait)

    raise RuntimeError(
        f"Embedding request still failing after {MAX_RETRIES + 1} attempts: {last_error}"
    )


def embed_query(text: str, *, model: Optional[str] = None) -> list[float]:
    """Vectorize a single query text.

    Parameters
    ----------
    text : str
        The query text.
    model : str, optional
        Embedding model name.

    Returns
    -------
    list of float
        The query vector.
    """
    vectors = embed([text], model=model)
    return vectors[0]
