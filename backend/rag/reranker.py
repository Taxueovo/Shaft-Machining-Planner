"""Cross-Encoder reranker.

After BM25 + Vector hybrid recall, reranks candidate documents with a
Cross-Encoder, which jointly encodes and scores (query, document) pairs and is
more accurate than dual-tower models.

Model: BAAI/bge-reranker-v2-m3 (optimized for Chinese, ~2.2GB, downloads on first load)
Degradation: model load failure -> skip rerank and sort by RRF score
"""

from __future__ import annotations

import logging
from typing import Optional

from .config import RERANKER_ENABLED, RERANKER_MODEL
from .schemas import SearchResult

logger = logging.getLogger(__name__)

# ── Global model cache (loaded once) ──

_reranker_model: Optional[object] = None
_reranker_available: Optional[bool] = None


def reranker_available() -> bool:
    """Check whether Cross-Encoder reranking is available."""
    global _reranker_available
    if _reranker_available is not None:
        return _reranker_available

    if not RERANKER_ENABLED:
        _reranker_available = False
        return False

    try:
        from sentence_transformers import CrossEncoder  # noqa: F401  # availability probe
        _reranker_available = True
    except ImportError:
        logger.warning("sentence-transformers not installed, reranker unavailable")
        _reranker_available = False
    return _reranker_available


def _get_model() -> Optional[object]:
    """Lazily load the Cross-Encoder model."""
    global _reranker_model, _reranker_available

    if not reranker_available():
        return None

    if _reranker_model is not None:
        return _reranker_model

    try:
        from sentence_transformers import CrossEncoder
        logger.info("Loading Cross-Encoder model: %s ...", RERANKER_MODEL)
        _reranker_model = CrossEncoder(RERANKER_MODEL)
        logger.info("Cross-Encoder model loaded: %s", RERANKER_MODEL)
        return _reranker_model
    except Exception as exc:
        logger.warning("Failed to load reranker model '%s': %s. "
                       "Reranking disabled.", RERANKER_MODEL, exc)
        _reranker_available = False
        return None


def rerank(query: str, candidates: list[SearchResult],
           top_k: Optional[int] = None) -> list[SearchResult]:
    """Rerank candidate documents with the Cross-Encoder.

    Parameters
    ----------
    query : str
        The query text.
    candidates : list of SearchResult
        Candidate documents (recalled from BM25 + Vector).
    top_k : int, optional
        Return the top_k results. Defaults to all.

    Returns
    -------
    list of SearchResult
        Results sorted by cross-encoder score in descending order.
        If the reranker is unavailable, returns the original order (degraded).
    """
    if not candidates:
        return []

    model = _get_model()
    if model is None:
        logger.debug("Reranker unavailable, returning candidates as-is")
        if top_k:
            return candidates[:top_k]
        return candidates

    # Build (query, document) pairs
    # bge-reranker-v2-m3 requires a "query: " prefix on the query and a
    # "document: " prefix on the document (required by the BAAI model card);
    # otherwise scoring quality drops noticeably.
    pairs = [(f"query: {query}", f"document: {c.content}") for c in candidates]
    logger.debug("Reranking %d candidates...", len(pairs))

    try:
        scores = model.predict(pairs)
    except Exception as exc:
        logger.warning("Reranker prediction failed: %s. Returning as-is.", exc)
        if top_k:
            return candidates[:top_k]
        return candidates

    # Write the score back to each result
    for i, score in enumerate(scores):
        candidates[i].rerank_score = round(float(score), 4)
        # Override the original score with rerank_score (used for sorting)
        candidates[i].score = round(float(score), 4)

    # Sort by cross-encoder score in descending order
    candidates.sort(key=lambda r: r.score, reverse=True)

    logger.debug("Rerank complete: %d → %d results",
                 len(candidates), len(candidates[:top_k]) if top_k else len(candidates))

    if top_k:
        return candidates[:top_k]
    return candidates
