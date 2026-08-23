"""BM25 keyword retrieval engine.

A BM25 dual-channel index kept in sync with the ChromaDB vector store.
Uses jieba tokenization for Chinese text and rank_bm25 for BM25 retrieval.

Sync:
    bm25 = BM25IndexManager()
    bm25.sync_from_vector_store(store)  # Pull all documents from ChromaDB

Usage:
    specs_results = bm25.search_specs("rough turning cutting tool", top_k=10)
    cases_results = bm25.search_cases("motor shaft 40Cr", top_k=10)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    import jieba
    from rank_bm25 import BM25Okapi

    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False

from .schemas import SearchResult, Channel

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """Chinese-aware tokenization - uses jieba for Chinese, keeps English/number tokens as-is."""
    if not HAS_BM25:
        return text.split()
    # jieba.cut tokenizes Chinese; English and numbers pass through unchanged
    return [t.strip() for t in jieba.cut(text) if t.strip()]


class BM25IndexManager:
    """BM25 dual-channel index manager.

    Maintains two independent BM25 indexes (specs and cases),
    synchronized with the documents in ChromaDB.
    """

    def __init__(self):
        if not HAS_BM25:
            raise RuntimeError("BM25 unavailable. Install it with: pip install rank-bm25 jieba")
        self._spec_index: Optional[BM25Okapi] = None
        self._case_index: Optional[BM25Okapi] = None
        self._spec_docs: list[dict[str, Any]] = []  # [{id, content, metadata}]
        self._case_docs: list[dict[str, Any]] = []
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    # ── Sync ──

    def sync_from_vector_store(self, store: Any) -> None:
        """Pull all documents from VectorStoreManager and rebuild the BM25 index.

        Call after build_index so BM25 stays in sync with ChromaDB.
        """
        logger.info("Syncing BM25 index from vector store...")
        self._spec_docs = store.get_all_spec_chunks()
        self._case_docs = store.get_all_case_chunks()

        # Build the BM25 index
        if self._spec_docs:
            tokenized = [_tokenize(d["content"]) for d in self._spec_docs]
            self._spec_index = BM25Okapi(tokenized)
            logger.info("BM25 specs index: %d documents", len(self._spec_docs))
        else:
            self._spec_index = None

        if self._case_docs:
            tokenized = [_tokenize(d["content"]) for d in self._case_docs]
            self._case_index = BM25Okapi(tokenized)
            logger.info("BM25 cases index: %d documents", len(self._case_docs))
        else:
            self._case_index = None

        self._ready = True
        logger.info(
            "BM25 sync complete (specs=%d, cases=%d)", len(self._spec_docs), len(self._case_docs)
        )

    # ── Retrieval ──

    def search_specs(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """BM25 retrieval over the specs channel."""
        if not self._spec_index or not self._spec_docs:
            return []
        return self._search(query, self._spec_index, self._spec_docs, Channel.SPECS, top_k)

    def search_cases(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """BM25 retrieval over the cases channel."""
        if not self._case_index or not self._case_docs:
            return []
        return self._search(query, self._case_index, self._case_docs, Channel.CASES, top_k)

    def search_all(
        self, query: str, top_k_per_channel: int = 10
    ) -> tuple[list[SearchResult], list[SearchResult]]:
        """Dual-channel BM25 retrieval."""
        return (
            self.search_specs(query, top_k_per_channel),
            self.search_cases(query, top_k_per_channel),
        )

    # ── Internals ──

    def _search(
        self, query: str, index: BM25Okapi, docs: list[dict], channel: Channel, top_k: int
    ) -> list[SearchResult]:
        tokens = _tokenize(query)
        scores = index.get_scores(tokens)
        # Take top_k
        indexed = [(i, s) for i, s in enumerate(scores) if s > 0]
        indexed.sort(key=lambda x: x[1], reverse=True)
        top = indexed[:top_k]

        # Normalize BM25 scores to 0-1
        max_score = top[0][1] if top else 1.0

        results = []
        for idx, raw_score in top:
            doc = docs[idx]
            norm_score = round(raw_score / max_score, 4) if max_score > 0 else 0.0
            results.append(
                SearchResult(
                    chunk_id=doc["id"],
                    content=doc["content"],
                    score=norm_score,
                    channel=channel,
                    recall_source="bm25",
                    metadata=doc.get("metadata", {}),
                )
            )
        return results

    def clear(self) -> None:
        """Clear the BM25 index."""
        self._spec_index = None
        self._case_index = None
        self._spec_docs = []
        self._case_docs = []
        self._ready = False
        logger.info("BM25 index cleared")
