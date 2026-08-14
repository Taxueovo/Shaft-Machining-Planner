"""Hybrid retriever - BM25 + Vector -> RRF fusion -> Cross-Encoder rerank.

Three-stage retrieval pipeline:
  1. Recall:  BM25 (keyword) + Vector (semantic) recall in parallel
  2. Fusion:  RRF (Reciprocal Rank Fusion) merge and deduplicate
  3. Rerank:  Cross-Encoder reranks the candidate set

Graceful degradation:
  - BM25 unavailable -> vector-only retrieval
  - Reranker unavailable -> skip reranking, sort by RRF score
"""

from __future__ import annotations

import logging
from typing import Optional

from .config import HYBRID_TOP_K_RECALL, HYBRID_TOP_K_FINAL, RRF_K
from .schemas import SearchResult, RetrievalResponse, Channel
from .vector_store import VectorStoreManager

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# RRF fusion
# ═══════════════════════════════════════════════════════════════

def _rrf_fusion(
    *result_lists: list[SearchResult],
    k: int = RRF_K,
) -> list[SearchResult]:
    """Reciprocal Rank Fusion - merge results from multiple retrieval paths.

    Formula: RRF_score(d) = Σ 1 / (k + rank_i(d))
    Scores for the same chunk_id are accumulated; hits from both BM25 and
    Vector rank higher.

    Parameters
    ----------
    *result_lists : list of SearchResult
        Results from each retrieval path (already sorted).
    k : int
        Smoothing parameter, default 60.

    Returns
    -------
    list of SearchResult
        Merged results sorted by RRF score in descending order.
    """
    rrf_scores: dict[str, float] = {}
    recall_sources: dict[str, set[str]] = {}
    doc_map: dict[str, SearchResult] = {}

    for results in result_lists:
        for rank, r in enumerate(results):
            cid = r.chunk_id
            # Accumulate RRF
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            # Record the source
            if cid not in recall_sources:
                recall_sources[cid] = set()
            recall_sources[cid].add(r.recall_source)
            # Keep the content (from the first occurrence)
            if cid not in doc_map:
                doc_map[cid] = r

    # Sort
    sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

    merged = []
    for cid, rrf_score in sorted_ids:
        r = doc_map[cid]
        # Mark multi-path hit sources
        sources = recall_sources.get(cid, {"vector"})
        r.recall_source = "both" if len(sources) > 1 else next(iter(sources))
        r.score = round(rrf_score, 6)  # override with the RRF score
        merged.append(r)

    logger.debug("RRF fusion: %d lists → %d unique results",
                 sum(len(l) for l in result_lists), len(merged))
    return merged


# ═══════════════════════════════════════════════════════════════
# Hybrid retriever
# ═══════════════════════════════════════════════════════════════

class HybridRetriever:
    """Hybrid retriever - BM25 + Vector -> RRF -> Cross-Encoder.

    Replaces the original RAGRetriever with a fully compatible interface.
    """

    def __init__(self, store: Optional[VectorStoreManager] = None):
        self.store = store or VectorStoreManager()
        self._bm25 = None       # lazy loading
        self._reranker = None   # lazy loading
        self._bm25_available: Optional[bool] = None
        self._reranker_available: Optional[bool] = None

    @property
    def bm25_available(self) -> bool:
        if self._bm25_available is None:
            try:
                from .bm25_index import BM25IndexManager
                _ = BM25IndexManager()
                self._bm25_available = True
            except Exception:
                self._bm25_available = False
        return self._bm25_available

    @property
    def reranker_available(self) -> bool:
        if self._reranker_available is None:
            try:
                from .reranker import reranker_available
                self._reranker_available = reranker_available()
            except Exception:
                self._reranker_available = False
        return self._reranker_available

    def _ensure_bm25(self):
        if self._bm25 is None and self.bm25_available:
            from .bm25_index import BM25IndexManager
            self._bm25 = BM25IndexManager()
            self._bm25.sync_from_vector_store(self.store)
            logger.info("BM25 index synced for HybridRetriever")

    # ── Main retrieval interface ──

    def retrieve(
        self,
        query: str,
        top_k_per_channel: int = HYBRID_TOP_K_RECALL,
        top_k_final: int = HYBRID_TOP_K_FINAL,
        use_bm25: bool = True,
        use_rerank: bool = True,
    ) -> RetrievalResponse:
        """Three-stage hybrid retrieval.

        Parameters
        ----------
        query : str
            The query text.
        top_k_per_channel : int
            Candidate count recalled per path (this many from BM25 and Vector each).
        top_k_final : int
            Number of final results returned.
        use_bm25 : bool
            Whether to enable BM25 keyword recall.
        use_rerank : bool
            Whether to enable Cross-Encoder reranking.

        Returns
        -------
        RetrievalResponse
        """
        # ── Phase 1: Recall ──
        spec_results: list[SearchResult] = []
        case_results: list[SearchResult] = []

        # Vector recall
        vec_specs, vec_cases = self.store.search_all(query, top_k_per_channel)

        # BM25 recall
        bm25_specs: list[SearchResult] = []
        bm25_cases: list[SearchResult] = []
        if use_bm25 and self.bm25_available:
            self._ensure_bm25()
            if self._bm25 and self._bm25.ready:
                bm25_specs, bm25_cases = self._bm25.search_all(query, top_k_per_channel)

        # ── Phase 2: RRF Fusion ──
        spec_merged = _rrf_fusion(vec_specs, bm25_specs)
        case_merged = _rrf_fusion(vec_cases, bm25_cases)

        # ── Phase 3: Rerank ──
        candidates = spec_merged + case_merged
        if use_rerank and self.reranker_available and candidates:
            from .reranker import rerank
            candidates = rerank(query, candidates, top_k=top_k_final)
        else:
            # No reranker - sort by RRF score and take top_k
            candidates.sort(key=lambda r: r.score, reverse=True)
            candidates = candidates[:top_k_final]

        # Stats
        spec_count = sum(1 for r in candidates if r.channel == Channel.SPECS)
        case_count = sum(1 for r in candidates if r.channel == Channel.CASES)

        logger.info(
            "Hybrid retrieve: query='%s' → %d results (specs=%d, cases=%d) "
            "[bm25=%s, rerank=%s]",
            query[:60], len(candidates), spec_count, case_count,
            use_bm25 and self.bm25_available,
            use_rerank and self.reranker_available,
        )

        return RetrievalResponse(
            query=query,
            results=candidates,
            spec_count=spec_count,
            case_count=case_count,
            total=len(candidates),
        )

    def retrieve_for_llm_context(
        self,
        query: str,
        top_k_per_channel: int = 10,
        top_k_final: int = 3,
        max_total_chars: int = 3000,
        use_bm25: bool = True,
        use_rerank: bool = True,
    ) -> str:
        """Hybrid retrieval formatted as an LLM context string.

        Parameters
        ----------
        query : str
            The query text.
        top_k_per_channel : int
            Candidate count recalled per path.
        top_k_final : int
            Number of final results.
        max_total_chars : int
            Maximum context length in characters.
        use_bm25 : bool
            Whether to enable BM25.
        use_rerank : bool
            Whether to enable Cross-Encoder reranking.

        Returns
        -------
        str
            Formatted context ready to inject into an LLM prompt.
        """
        response = self.retrieve(
            query,
            top_k_per_channel=top_k_per_channel,
            top_k_final=top_k_final,
            use_bm25=use_bm25,
            use_rerank=use_rerank,
        )

        if not response.results:
            return ""

        parts: list[str] = []
        char_count = 0

        for result in response.results:
            meta = result.metadata
            header: str

            if result.channel == Channel.SPECS:
                hierarchy = meta.get("hierarchy_path", "")
                source_tag = f" [recall: {result.recall_source}]" if result.recall_source else ""
                header = (
                    f"\n--- 📖 {hierarchy} "
                    f"(score: {result.score:.4f}{source_tag}) ---\n"
                )
            else:
                case_label = f"{meta.get('part_name', '')} ({meta.get('case_id', '')})"
                source_tag = f" [recall: {result.recall_source}]" if result.recall_source else ""
                header = (
                    f"\n--- 📋 {case_label} "
                    f"(score: {result.score:.4f}{source_tag}) ---\n"
                )

            addition = len(header) + len(result.content)
            if char_count + addition > max_total_chars and parts:
                break

            parts.append(header + result.content)
            char_count += addition

        logger.info("LLM context built: %d results, %d chars", len(parts), char_count)
        return "\n".join(parts)


# ── Module-level convenience functions ──

_retriever: Optional[HybridRetriever] = None


def _get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever


def retrieve(query: str, top_k: int = 5) -> RetrievalResponse:
    """Convenience function for hybrid retrieval."""
    return _get_retriever().retrieve(
        query, top_k_per_channel=HYBRID_TOP_K_RECALL, top_k_final=top_k,
    )


def retrieve_for_llm(query: str, top_k: int = 3, max_chars: int = 3000) -> str:
    """Retrieve and format as LLM context."""
    return _get_retriever().retrieve_for_llm_context(
        query, top_k_per_channel=HYBRID_TOP_K_RECALL,
        top_k_final=top_k, max_total_chars=max_chars,
    )
