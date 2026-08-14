"""ShaftPlanner RAG module - dual-channel differentiated chunk retrieval-augmented generation.

Module structure:
- Specs (process handbook): long-form process handbook text, semantically split by
  chapter -> section -> process step description
- Cases (case base): structured part cases, each case = one complete chunk

Quick start:
    from rag import build_index, retrieve, get_index_status

    # Check index status
    status = get_index_status()
    print(status)

    # Build the index (place source files in data/specs/ and data/cases/ first)
    build_index()

    # Retrieve
    response = retrieve("motor shaft rough turning 40Cr")
    for r in response.results:
        print(f"[{r.channel}] {r.score}: {r.content[:100]}...")

    # Build LLM context
    context = retrieve_for_llm("stepped shaft keyway high precision")
    messages = [
        {"role": "system", "content": f"Reference the following cases:\n{context}"},
        {"role": "user", "content": "Please generate the process route..."},
    ]

Workflow integration (enabled):
    rag.workflow_integration.build_rag_context(request, geometry, user_choices, heat_decision)
    Injects "Reference knowledge" into the LLM prompt of process_planning / repair nodes.
    Returns an empty string when RAG is unavailable or retrieval returns nothing,
    degrading gracefully without affecting the main flow.
"""

from .schemas import (
    ChunkType,
    Channel,
    SpecChunk,
    CaseChunk,
    SearchResult,
    RetrievalResponse,
    CollectionStatus,
    IndexStatus,
)
from .config import embedding_available
from .indexer import build_index, get_index_status
from .retriever import retrieve, retrieve_for_llm, HybridRetriever
from .workflow_integration import build_rag_context, build_rag_query

__all__ = [
    # Core API
    "build_index",
    "get_index_status",
    "retrieve",
    "retrieve_for_llm",
    "embedding_available",
    "HybridRetriever",
    # Workflow integration
    "build_rag_context",
    "build_rag_query",
    # Data models
    "ChunkType",
    "Channel",
    "SpecChunk",
    "CaseChunk",
    "SearchResult",
    "RetrievalResponse",
    "CollectionStatus",
    "IndexStatus",
]
