"""RAG module data models.

Defines the core data structures such as Chunk and SearchResult.
Follows the Pydantic BaseModel pattern used in backend/models/.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ──

class ChunkType(str, enum.Enum):
    SPEC = "spec"
    CASE = "case"


class Channel(str, enum.Enum):
    SPECS = "specs"
    CASES = "cases"


# ── Specs chunk ──

class SpecChunk(BaseModel):
    """Process handbook text block - semantically split by chapter -> section -> process step description."""

    chunk_id: str = Field(
        default_factory=lambda: f"spec-{uuid.uuid4().hex[:8]}",
        description="Unique chunk identifier",
    )
    source_file: str = Field(description="Source file name")
    chapter: Optional[str] = Field(default=None, description="Chapter title (H1)")
    section: Optional[str] = Field(default=None, description="Section title (H2)")
    subsection: Optional[str] = Field(default=None, description="Subsection title (H3)")
    content: str = Field(description="Text content")
    hierarchy_path: str = Field(
        default="", description="Hierarchy path, e.g. 'Chapter 3 Rough Turning / Cutting Parameters / Cutting Tool Selection'"
    )
    metadata: dict[str, Any] = Field(
        default_factory=lambda: {"source_type": ChunkType.SPEC.value},
        description="Additional metadata",
    )

    def model_post_init(self, __context) -> None:
        """Automatically build hierarchy_path."""
        if not self.hierarchy_path:
            parts = [p for p in (self.chapter, self.section, self.subsection) if p]
            self.hierarchy_path = " / ".join(parts) if parts else self.source_file


# ── Cases chunk ──

class CaseChunk(BaseModel):
    """Case base text block - one part case kept structurally complete."""

    chunk_id: str = Field(
        default_factory=lambda: f"case-{uuid.uuid4().hex[:8]}",
        description="Unique chunk identifier",
    )
    case_id: str = Field(description="Case ID (e.g. MS-001)")
    part_name: str = Field(description="Part name")
    material: str = Field(description="Material grade")
    taxonomy_id: Optional[str] = Field(default=None, description="Taxonomy node ID")
    industry: Optional[str] = Field(default=None, description="Industry tag")
    features: list[str] = Field(default_factory=list, description="Feature list")
    content: str = Field(description="Textualized content (part info + features + full process route)")
    metadata: dict[str, Any] = Field(
        default_factory=lambda: {"source_type": ChunkType.CASE.value},
        description="Additional metadata",
    )


# ── Search results ──

class SearchResult(BaseModel):
    """A single search result."""

    chunk_id: str = Field(description="Matched chunk ID")
    content: str = Field(description="Chunk text content")
    score: float = Field(description="Similarity score (0-1, higher is more relevant)")
    channel: Channel = Field(description="Source channel: specs or cases")
    recall_source: str = Field(
        default="vector",
        description="Recall source: 'bm25' | 'vector' | 'both'",
    )
    rerank_score: Optional[float] = Field(
        default=None,
        description="Cross-Encoder rerank score (only filled after reranking)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Metadata carried by the chunk"
    )


class RetrievalResponse(BaseModel):
    """Full dual-channel retrieval response."""

    query: str = Field(description="Original query text")
    results: list[SearchResult] = Field(
        default_factory=list, description="Merged search results (sorted by similarity descending)"
    )
    spec_count: int = Field(default=0, description="Number of results from the specs channel")
    case_count: int = Field(default=0, description="Number of results from the cases channel")
    total: int = Field(default=0, description="Total number of results")


# ── Index status ──

class CollectionStatus(BaseModel):
    """Status of a single collection."""

    name: str = Field(description="Collection name")
    document_count: int = Field(default=0, description="Number of indexed documents")
    exists: bool = Field(default=False, description="Whether the collection exists")


class IndexStatus(BaseModel):
    """Dual-channel index status."""

    specs: CollectionStatus = Field(default_factory=lambda: CollectionStatus(name="specs"))
    cases: CollectionStatus = Field(default_factory=lambda: CollectionStatus(name="cases"))
    embedding_available: bool = Field(default=False)
