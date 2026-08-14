"""ChromaDB vector store manager.

Manages two collections - shaftplanner_specs (process handbook) and shaftplanner_cases (case base).
Each collection uses a dedicated embedding model (configured via EMBEDDING_API_KEY),
instead of ChromaDB's built-in default model.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from chromadb.api.types import EmbeddingFunction
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False

from .config import (
    COLLECTION_SPECS,
    COLLECTION_CASES,
    CHROMA_DIR,
    EMBEDDING_MODEL,
    embedding_available as _embedding_available,
)
from .schemas import SpecChunk, CaseChunk, SearchResult, Channel

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Custom Embedding Function - calls the dedicated vector model API
# ═══════════════════════════════════════════════════════════════

# Defined only when chromadb is available: if the import above failed
# (HAS_CHROMADB=False), referencing EmbeddingFunction directly would raise a
# NameError and mask the real cause (see the check in __init__).
if HAS_CHROMADB:

    class ShaftMachiningPlannerEmbeddingFunction(EmbeddingFunction):
        """Wrap the OpenAI-compatible embedding API as a ChromaDB embedding function.

        Does not use ChromaDB's built-in all-MiniLM-L6-v2; instead calls the
        dedicated model configured via EMBEDDING_BASE_URL / EMBEDDING_API_KEY /
        EMBEDDING_MODEL in .env.
        """

        def __call__(self, input_texts: list[str]) -> list[list[float]]:
            from .embedding import embed
            return embed(input_texts)


def _get_or_recreate_collection(
    client: Any,
    name: str,
    embedding_fn: Any,
    description: str,
) -> Any:
    """Get a collection, recreating it automatically if the embedding function does not match.

    ChromaDB does not allow changing an existing collection's embedding function,
    so switching vector models requires deleting the old collection and recreating it.
    """
    # When embedding_fn is None (EMBEDDING_API_KEY not configured), omit the
    # parameter so ChromaDB uses its built-in default embedding
    # (all-MiniLM-L6-v2, runs offline via onnxruntime).
    # Passing None explicitly would make ChromaDB persist None and fail on
    # later add/query with "You must provide an embedding function to compute embeddings".
    collection_kwargs: dict[str, Any] = {
        "name": name,
        "metadata": {"description": description},
    }
    if embedding_fn is not None:
        collection_kwargs["embedding_function"] = embedding_fn

    try:
        return client.get_or_create_collection(**collection_kwargs)
    except ValueError as exc:
        if "embedding function" in str(exc).lower():
            logger.warning(
                "Embedding function mismatch for '%s', recreating collection. "
                "All existing data will be lost. Error: %s",
                name, exc,
            )
            try:
                client.delete_collection(name)
            except Exception:
                pass
            return client.create_collection(**collection_kwargs)
        raise


# ═══════════════════════════════════════════════════════════════
# VectorStoreManager
# ═══════════════════════════════════════════════════════════════

class VectorStoreManager:
    """ChromaDB dual-collection manager.

    Uses a dedicated embedding model (not the ChromaDB default).
    Falls back to the built-in ChromaDB model when EMBEDDING_API_KEY is not configured.

    Usage:
        store = VectorStoreManager()
        store.add_specs(spec_chunks)   # write to the specs collection
        store.add_cases(case_chunks)   # write to the cases collection
        results = store.search_all("motor shaft rough turning")  # dual-channel search
    """

    def __init__(self, persist_dir: Optional[str] = None):
        if not HAS_CHROMADB:
            raise RuntimeError(
                "chromadb is not installed. Run: pip install chromadb>=0.5"
            )

        self._persist_dir = str(persist_dir or CHROMA_DIR.resolve())
        self._client = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._specs: Any = None
        self._cases: Any = None

        # Use the dedicated embedding model; ChromaDB falls back to the built-in model if unavailable
        self._embedding_fn = ShaftMachiningPlannerEmbeddingFunction() if _embedding_available() else None
        ef_label = f"custom:{EMBEDDING_MODEL}" if self._embedding_fn else "chromadb:default"
        logger.info("VectorStore initialized at %s (embedding=%s)", self._persist_dir, ef_label)

    # ── Collection lazy loading ──

    @property
    def specs_collection(self) -> Any:
        """Specs collection (lazy-loaded, created if missing)."""
        if self._specs is None:
            self._specs = _get_or_recreate_collection(
                self._client, COLLECTION_SPECS, self._embedding_fn,
                "Shaft Machining Planner process handbook - semantic chunks of chapters/sections/process step descriptions",
            )
        return self._specs

    @property
    def cases_collection(self) -> Any:
        """Cases collection (lazy-loaded, created if missing)."""
        if self._cases is None:
            self._cases = _get_or_recreate_collection(
                self._client, COLLECTION_CASES, self._embedding_fn,
                "Shaft Machining Planner part case base - full case process routes",
            )
        return self._cases

    # ── Write ──

    def add_specs(self, chunks: list[SpecChunk]) -> int:
        """Batch-write spec chunks.

        ChromaDB calls the embedding function automatically; we pass the text
        directly and let ChromaDB's embedding_function (or a later manual embed) handle it.
        """
        if not chunks:
            return 0

        ids = [c.chunk_id for c in chunks]
        documents = [c.content for c in chunks]
        metadatas = [
            {
                "source_file": c.source_file,
                "chapter": c.chapter or "",
                "section": c.section or "",
                "subsection": c.subsection or "",
                "hierarchy_path": c.hierarchy_path,
                "source_type": "spec",
                **c.metadata,
            }
            for c in chunks
        ]

        self.specs_collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info("Added %d spec chunks to ChromaDB", len(chunks))
        return len(chunks)

    def add_cases(self, chunks: list[CaseChunk]) -> int:
        """Batch-write case chunks."""
        if not chunks:
            return 0

        ids = [c.chunk_id for c in chunks]
        documents = [c.content for c in chunks]
        metadatas = [
            {
                "case_id": c.case_id,
                "part_name": c.part_name,
                "material": c.material,
                "taxonomy_id": c.taxonomy_id or "",
                "industry": c.industry or "",
                "features": ", ".join(c.features),
                "source_type": "case",
                **c.metadata,
            }
            for c in chunks
        ]

        self.cases_collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info("Added %d case chunks to ChromaDB", len(chunks))
        return len(chunks)

    # ── Retrieval ──

    def search_specs(
        self, query_text: str, top_k: int = 5
    ) -> list[SearchResult]:
        """Search the specs collection."""
        if self.specs_collection.count() == 0:
            return []

        results = self.specs_collection.query(
            query_texts=[query_text],
            n_results=min(top_k, self.specs_collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        return _to_search_results(
            results, Channel.SPECS, query_text
        )

    def search_cases(
        self, query_text: str, top_k: int = 5
    ) -> list[SearchResult]:
        """Search the cases collection."""
        if self.cases_collection.count() == 0:
            return []

        results = self.cases_collection.query(
            query_texts=[query_text],
            n_results=min(top_k, self.cases_collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        return _to_search_results(
            results, Channel.CASES, query_text
        )

    def search_all(
        self, query_text: str, top_k_per_channel: int = 5
    ) -> tuple[list[SearchResult], list[SearchResult]]:
        """Dual-channel search: queries both the specs and cases collections.

        Returns
        -------
        tuple[list[SearchResult], list[SearchResult]]
            (specs_results, cases_results)
        """
        spec_results = self.search_specs(query_text, top_k_per_channel)
        case_results = self.search_cases(query_text, top_k_per_channel)
        return spec_results, case_results

    # ── Status ──

    def get_collection_status(self, collection_name: str) -> dict[str, Any]:
        """Get the status of a single collection."""
        col = (
            self.specs_collection
            if collection_name == COLLECTION_SPECS
            else self.cases_collection
        )
        try:
            count = col.count()
        except Exception:
            count = 0
        return {
            "name": collection_name,
            "document_count": count,
            "exists": count > 0 or True,  # get_or_create guarantees existence
        }

    def get_all_spec_chunks(self) -> list[dict[str, Any]]:
        """Return all documents from the specs collection (for BM25 index sync)."""
        if self.specs_collection.count() == 0:
            return []
        data = self.specs_collection.get(
            include=["documents", "metadatas"],
        )
        return [
            {"id": cid, "content": doc, "metadata": meta}
            for cid, doc, meta in zip(
                data.get("ids", []),
                data.get("documents", []),
                data.get("metadatas", []),
            )
        ]

    def get_all_case_chunks(self) -> list[dict[str, Any]]:
        """Return all documents from the cases collection (for BM25 index sync)."""
        if self.cases_collection.count() == 0:
            return []
        data = self.cases_collection.get(
            include=["documents", "metadatas"],
        )
        return [
            {"id": cid, "content": doc, "metadata": meta}
            for cid, doc, meta in zip(
                data.get("ids", []),
                data.get("documents", []),
                data.get("metadatas", []),
            )
        ]

    def clear(self, collection_name: Optional[str] = None) -> None:
        """Clear a collection.

        Parameters
        ----------
        collection_name : str, optional
            The collection name. If not specified, clears all collections.
        """
        if collection_name is None:
            for col in [self.specs_collection, self.cases_collection]:
                ids = col.get()["ids"]
                if ids:
                    col.delete(ids=ids)
            logger.info("All RAG collections cleared")
        else:
            col = (
                self.specs_collection
                if collection_name == COLLECTION_SPECS
                else self.cases_collection
            )
            ids = col.get()["ids"]
            if ids:
                col.delete(ids=ids)
            logger.info("Cleared collection: %s", collection_name)


# ── Helper functions ──

def _to_search_results(
    chroma_result: dict[str, Any],
    channel: Channel,
    query_text: str,
) -> list[SearchResult]:
    """Convert a ChromaDB query result into a list of SearchResult."""
    results: list[SearchResult] = []

    ids_list = chroma_result.get("ids", [[]])[0]
    docs_list = chroma_result.get("documents", [[]])[0]
    metas_list = chroma_result.get("metadatas", [[]])[0]
    distances_list = chroma_result.get("distances", [[]])[0]

    for i in range(len(ids_list)):
        # ChromaDB distance: smaller means more similar. Convert to a 0-1 similarity score.
        distance = distances_list[i] if i < len(distances_list) else 1.0
        score = _distance_to_score(distance)

        results.append(
            SearchResult(
                chunk_id=ids_list[i] if i < len(ids_list) else f"unknown-{i}",
                content=docs_list[i] if i < len(docs_list) else "",
                score=round(score, 4),
                channel=channel,
                metadata=metas_list[i] if i < len(metas_list) else {},
            )
        )

    # Sort by similarity in descending order
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def _distance_to_score(distance: float) -> float:
    """Convert a ChromaDB distance into a 0-1 similarity score.

    ChromaDB uses L2 distance by default: smaller means more similar.
    A smooth mapping of 1 / (1 + distance) is applied.
    """
    if distance < 0:
        return 1.0
    return round(1.0 / (1.0 + distance), 4)
