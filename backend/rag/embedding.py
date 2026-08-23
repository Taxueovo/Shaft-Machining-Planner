"""专用向量模型客户端。

封装 OpenAI-compatible Embeddings API，独立于主 LLM 配置。
延迟初始化，批量向量化，自动重试，自动分批（适配不同 provider 的 batch 上限）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from .config import EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_DIMENSIONS, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

# ── 重试配置 ──

MAX_RETRIES: int = 2
RETRY_DELAY_SECONDS: float = 1.0
# 单次请求最大文本数：不同 provider 上限不同（DashScope qwen3.7=20、text-embedding-v4=10），
# 取较小值 10 兼容两者，自动分批规避。
EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "10"))

# ── 单例客户端 ──

_client: Any = None


def _get_client() -> Any:
    """延迟初始化 OpenAI 客户端（embedding 专用）。"""
    global _client
    if _client is None:
        if not EMBEDDING_API_KEY:
            raise RuntimeError(
                "EMBEDDING_API_KEY 未配置。请在 .env 文件中设置 EMBEDDING_API_KEY=your-key"
            )
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai 包未安装。请运行: pip install openai>=1.0")
        _client = OpenAI(
            base_url=EMBEDDING_BASE_URL,
            api_key=EMBEDDING_API_KEY,
            timeout=60.0,
            max_retries=1,
        )
    return _client


def embedding_available() -> bool:
    """检查 embedding 服务是否可用。"""
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
    """批量文本向量化（自动分批，适配 provider 的 batch 上限）。

    Parameters
    ----------
    texts : list of str
        待向量化的文本列表。
    model : str, optional
        Embedding 模型名称，默认使用 EMBEDDING_MODEL。

    Returns
    -------
    list of list of float
        每条文本对应的向量列表，维度取决于模型。
    """
    if not texts:
        return []

    # 分批调用，避免超出 provider 的单次 batch 上限（如 DashScope=20）
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + EMBEDDING_BATCH_SIZE]
        vectors.extend(_embed_batch(batch, model=model))
    return vectors


def _embed_batch(
    texts: list[str],
    *,
    model: Optional[str] = None,
) -> list[list[float]]:
    """单批文本向量化（含自动重试）。"""
    client = _get_client()
    effective_model = model or EMBEDDING_MODEL

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            logger.info("Embedding request: model=%s, texts=%d", effective_model, len(texts))
            request_kwargs: dict[str, Any] = {
                "model": effective_model,
                "input": texts,
            }
            if EMBEDDING_DIMENSIONS:
                request_kwargs["dimensions"] = EMBEDDING_DIMENSIONS
            response = client.embeddings.create(**request_kwargs)
            vectors = [item.embedding for item in response.data]
            logger.info("Embedding response: %d vectors", len(vectors))
            return vectors
        except Exception as exc:
            last_error = exc
            # 鉴权/凭据错误不会因重试而恢复，快速失败交给上层降级
            if _is_credential_error(exc):
                raise
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

    raise RuntimeError(f"Embedding 请求在 {MAX_RETRIES + 1} 次尝试后仍失败: {last_error}")


def _is_credential_error(exc: Exception) -> bool:
    """判断是否为鉴权/凭据类错误（不可重试）。"""
    text = str(exc).lower()
    return any(
        keyword in text
        for keyword in ("authentication", "401", "invalid api key", "apikey", "unauthorized")
    )
