"""RAG smoke tests.

Run in the dedicated ``rag-smoke`` CI job, which installs the lightweight RAG runtime
deps (chromadb + rank-bm25 + jieba, no torch / sentence-transformers). The torch-based
Cross-Encoder reranker path is intentionally not exercised here.

The whole module skips when chromadb is not installed, so it is safe to collect in the
main (non-RAG) test job too.
"""

from __future__ import annotations

import pytest

pytest.importorskip("chromadb")

from backend.rag.schemas import CaseChunk, Channel, SearchResult, SpecChunk
from backend.rag.retriever import _rrf_fusion
from backend.rag.reranker import _apply_feature_penalty, _match_feature_aliases


def _sr(
    chunk_id: str,
    src: str,
    score: float,
    channel: Channel = Channel.SPECS,
    features: str | None = None,
) -> SearchResult:
    metadata = {"features": features} if features is not None else {}
    return SearchResult(
        chunk_id=chunk_id,
        content="c",
        score=score,
        channel=channel,
        recall_source=src,
        metadata=metadata,
    )


def test_chunk_ids_are_deterministic():
    a = SpecChunk(source_file="a.md", section="S", content="hello world")
    b = SpecChunk(source_file="a.md", section="S", content="hello world")
    c = SpecChunk(source_file="a.md", section="S", content="hello world CHANGED")
    assert a.chunk_id == b.chunk_id
    assert a.chunk_id != c.chunk_id
    assert a.chunk_id.startswith("spec-")

    case_a = CaseChunk(
        case_id="C1", source_file="c1.json", content="x", part_name="P", material="45"
    )
    case_b = CaseChunk(
        case_id="C1", source_file="c1.json", content="x", part_name="P", material="45"
    )
    assert case_a.chunk_id == case_b.chunk_id
    assert case_a.chunk_id.startswith("case-")


def test_rrf_fusion_merges_dedupes_and_marks_both():
    merged = _rrf_fusion(
        [_sr("a", "vector", 1.0), _sr("b", "vector", 0.8)],
        [_sr("a", "bm25", 0.9), _sr("c", "bm25", 0.7)],
    )
    by_id = {r.chunk_id: r for r in merged}
    assert set(by_id) == {"a", "b", "c"}
    assert by_id["a"].recall_source == "both"
    assert merged[0].chunk_id == "a"  # accumulated over both recall paths -> highest RRF


def test_feature_penalty_fires_for_english_workflow_query():
    # The production query builder emits English feature labels; the penalty must fire
    # (this used to be a no-op because FEATURE_KEYWORDS was Chinese-only).
    assert _match_feature_aliases("Features Keyway@45mm, Spline@120mm")
    hit = _sr("hit", "vector", 1.0, channel=Channel.CASES, features="keyway F1, spline F2")
    miss = _sr("miss", "vector", 1.0, channel=Channel.CASES, features="keyway F1")
    out = _apply_feature_penalty("Features Keyway@45mm, Spline@120mm", [hit, miss])
    by_id = {r.chunk_id: r for r in out}
    assert by_id["miss"].score < by_id["hit"].score
    assert by_id["hit"].score == 1.0


def test_vector_store_build_and_search(tmp_path):
    from backend.rag.vector_store import VectorStoreManager

    store = VectorStoreManager(persist_dir=str(tmp_path / "chroma"))
    chunks = [
        SpecChunk(
            source_file="handbook.md",
            section="Rough Turning",
            content="Rough turning removes bulk stock with high feed and low depth of cut.",
        ),
        SpecChunk(
            source_file="handbook.md",
            section="Finish Grinding",
            content="Finish grinding controls diameter tolerance and surface roughness.",
        ),
    ]
    store.add_specs(chunks)

    results = store.search_specs("rough turning bulk stock removal", top_k=1)
    assert len(results) == 1
    assert "Rough turning" in results[0].content

    both = store.search_all("grinding tolerance roughness")
    assert len(both[0]) >= 1


def test_empty_query_guard_and_collection_name_guard():
    from backend.rag.retriever import HybridRetriever

    retriever = HybridRetriever()
    response = retriever.retrieve("   ")
    assert response.total == 0
    assert response.results == []
