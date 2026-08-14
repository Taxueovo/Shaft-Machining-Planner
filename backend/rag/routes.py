"""RAG management API - FastAPI Router.

Provides all REST endpoints needed by the frontend RAG management panel.
Designed as a standalone Router; failures to load do not affect the main app.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

# ── Router ──

rag_router = APIRouter(tags=["RAG"])

# ── Singleton services (lazy initialization) ──

_store: Any = None
_builder: Any = None
_retriever: Any = None
_rag_available: Optional[bool] = None


def _init() -> bool:
    """Lazily initialize the RAG services. Returns True on success."""
    global _store, _builder, _retriever, _rag_available
    if _rag_available is not None:
        return _rag_available

    try:
        from .vector_store import VectorStoreManager
        from .indexer import IndexBuilder
        from .retriever import HybridRetriever
        from .config import embedding_available, SPECS_DIR, CASES_DIR, SPEC_EXTENSIONS, CASE_EXTENSIONS

        _store = VectorStoreManager()
        _builder = IndexBuilder()
        _retriever = HybridRetriever(store=_store)
        _rag_available = True
        logger.info("RAG services initialized successfully")
    except Exception as exc:
        _rag_available = False
        logger.warning("RAG services unavailable: %s", exc)

    return _rag_available


def _check_available():
    if not _init():
        raise HTTPException(status_code=503, detail="RAG service unavailable; check chromadb installation and embedding configuration.")


def _scan_spec_files() -> list[dict]:
    from .config import SPECS_DIR, SPEC_EXTENSIONS
    if not SPECS_DIR.exists():
        return []
    return [
        {"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1)}
        for f in sorted(SPECS_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in SPEC_EXTENSIONS
    ]


def _scan_case_files() -> list[dict]:
    from .config import CASES_DIR, CASE_EXTENSIONS
    if not CASES_DIR.exists():
        return []
    return [
        {"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1)}
        for f in sorted(CASES_DIR.iterdir())
        if f.is_file() and f.suffix.lower() in CASE_EXTENSIONS and not f.name.startswith(".")
    ]


# ═══════════════════════════════════════════════════════════════
# Status
# ═══════════════════════════════════════════════════════════════

@rag_router.get("/status")
def rag_status() -> dict[str, Any]:
    """Full RAG status - dashboard data."""
    if not _init():
        return {
            "available": False,
            "message": "RAG service unavailable. Install chromadb and configure EMBEDDING_API_KEY.",
        }

    from .config import EMBEDDING_MODEL, CHROMA_DIR, embedding_available

    status = _builder.get_status()
    spec_files = _scan_spec_files()
    case_files = _scan_case_files()

    return {
        "available": True,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_available": embedding_available(),
        "chroma_dir": str(CHROMA_DIR),
        "specs": {
            "collection_name": status.specs.name,
            "document_count": status.specs.document_count,
            "source_files": len(spec_files),
            "files": spec_files,
        },
        "cases": {
            "collection_name": status.cases.name,
            "document_count": status.cases.document_count,
            "source_files": len(case_files),
            "files": case_files,
        },
        "total_source_files": len(spec_files) + len(case_files),
        "total_documents": status.specs.document_count + status.cases.document_count,
    }


# ═══════════════════════════════════════════════════════════════
# Index management
# ═══════════════════════════════════════════════════════════════

@rag_router.post("/build")
def build_index(
    channel: Optional[str] = Query(default="all", description="all | specs | cases"),
) -> dict[str, Any]:
    """Build the index."""
    _check_available()

    import time
    t0 = time.time()

    if channel == "specs":
        count = _builder.build_spec_index()
        return {"channel": "specs", "chunks": count, "elapsed_s": round(time.time() - t0, 1)}
    elif channel == "cases":
        count = _builder.build_case_index()
        return {"channel": "cases", "chunks": count, "elapsed_s": round(time.time() - t0, 1)}
    else:
        total = _builder.build_all()
        return {"channel": "all", "chunks": total, "elapsed_s": round(time.time() - t0, 1)}


@rag_router.delete("/clear")
def clear_index(
    channel: Optional[str] = Query(default="all", description="all | specs | cases"),
) -> dict[str, Any]:
    """Clear the index."""
    _check_available()

    from .config import COLLECTION_SPECS, COLLECTION_CASES

    if channel == "specs":
        _store.clear(COLLECTION_SPECS)
    elif channel == "cases":
        _store.clear(COLLECTION_CASES)
    else:
        _store.clear()

    return {"message": f"Cleared {channel} index", "channel": channel}


# ═══════════════════════════════════════════════════════════════
# Retrieval
# ═══════════════════════════════════════════════════════════════

@rag_router.get("/search")
def search(
    q: str = Query(description="Query text"),
    top_k: int = Query(default=5, ge=1, le=20, description="Number of final results"),
    use_bm25: bool = Query(default=True, description="Enable BM25 keyword recall"),
    use_rerank: bool = Query(default=True, description="Enable Cross-Encoder reranking"),
) -> dict[str, Any]:
    """Hybrid retrieval (BM25 + Vector -> RRF -> Cross-Encoder)."""
    _check_available()

    response = _retriever.retrieve(
        q,
        top_k_per_channel=10,
        top_k_final=top_k,
        use_bm25=use_bm25,
        use_rerank=use_rerank,
    )
    return {
        "query": q,
        "results": [
            {
                "chunk_id": r.chunk_id,
                "content_preview": r.content[:300] + ("..." if len(r.content) > 300 else ""),
                "score": r.score,
                "channel": r.channel.value,
                "metadata": r.metadata,
            }
            for r in response.results
        ],
        "spec_count": response.spec_count,
        "case_count": response.case_count,
        "total": response.total,
    }


# ═══════════════════════════════════════════════════════════════
# Chunk details
# ═══════════════════════════════════════════════════════════════

@rag_router.get("/chunks")
def list_chunks(
    channel: Optional[str] = Query(default="all", description="all | specs | cases"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """List samples of indexed chunks."""
    _check_available()

    from .config import COLLECTION_SPECS, COLLECTION_CASES

    result: dict[str, Any] = {"channels": {}}

    for col_name, label in [
        (COLLECTION_SPECS, "specs"),
        (COLLECTION_CASES, "cases"),
    ]:
        if channel != "all" and label != channel:
            continue

        info = _store.get_collection_status(col_name)
        if info["document_count"] == 0:
            result["channels"][label] = {"count": 0, "items": []}
            continue

        col = _store.specs_collection if col_name == COLLECTION_SPECS else _store.cases_collection
        data = col.get(limit=min(limit, info["document_count"]),
                       include=["documents", "metadatas"])

        items = []
        for cid, doc, meta in zip(data.get("ids", []),
                                   data.get("documents", []),
                                   data.get("metadatas", [])):
            item = {
                "chunk_id": cid,
                # Send full content; the frontend collapses the display
                "content": doc or "",
            }
            if meta:
                if label == "specs":
                    item["hierarchy_path"] = meta.get("hierarchy_path", "")
                else:
                    item["case_id"] = meta.get("case_id", "")
                    item["part_name"] = meta.get("part_name", "")
                    item["material"] = meta.get("material", "")
            items.append(item)

        result["channels"][label] = {"count": len(items), "items": items}

    return result
