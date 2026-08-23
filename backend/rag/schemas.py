"""RAG module data models.

Defines the core data structures such as Chunk and SearchResult.
Follows the Pydantic BaseModel pattern used in backend/models/.
"""

from __future__ import annotations

import enum
import hashlib
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


def make_chunk_id(kind: str, source_file: str, content: str) -> str:
    """Deterministic content-hash chunk id.

    The hash covers the source basename and the chunk text, so rebuilds produce
    identical ids (idempotent upserts) and an edited chunk gets a new id that
    naturally replaces the old one. Returns e.g. ``spec-1f2e3d4a5b6c7d8e``.
    """
    digest = hashlib.sha1(f"{Path(source_file).name}\x00{content}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{digest}"


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

    chunk_id: Optional[str] = Field(
        default=None,
        description="Deterministic content-hash chunk identifier (auto-filled in model_post_init)",
    )
    source_file: str = Field(description="Source file name")
    chapter: Optional[str] = Field(default=None, description="Chapter title (H1)")
    section: Optional[str] = Field(default=None, description="Section title (H2)")
    subsection: Optional[str] = Field(default=None, description="Subsection title (H3)")
    content: str = Field(description="Text content")
    part: Optional[int] = Field(
        default=None,
        description="Part number when a long H3 subsection is split into multiple chunks",
    )
    total_parts: Optional[int] = Field(
        default=None,
        description="Total parts when a long H3 subsection is split into multiple chunks",
    )
    hierarchy_path: str = Field(
        default="",
        description="Hierarchy path, e.g. 'Chapter 3 Rough Turning / Cutting Parameters / Cutting Tool Selection'",
    )
    metadata: dict[str, Any] = Field(
        default_factory=lambda: {"source_type": ChunkType.SPEC.value},
        description="Additional metadata",
    )

    def model_post_init(self, __context) -> None:
        """Automatically build hierarchy_path and a deterministic chunk_id."""
        if not self.hierarchy_path:
            parts = [p for p in (self.chapter, self.section, self.subsection) if p]
            self.hierarchy_path = " / ".join(parts) if parts else self.source_file
        if not self.chunk_id:
            self.chunk_id = make_chunk_id("spec", self.source_file, self.content)


# ── Cases chunk ──


class CaseChunk(BaseModel):
    """Case base text block - one part case kept structurally complete."""

    chunk_id: Optional[str] = Field(
        default=None,
        description="Deterministic content-hash chunk identifier (auto-filled in model_post_init)",
    )
    case_id: str = Field(description="Case ID (e.g. MS-001)")
    source_file: str = Field(default="", description="Source JSON file name")
    part_name: str = Field(description="Part name")
    material: str = Field(description="Material grade")
    taxonomy_id: Optional[str] = Field(default=None, description="Taxonomy node ID")
    industry: Optional[str] = Field(default=None, description="Industry tag")
    features: list[str] = Field(default_factory=list, description="Feature list")
    content: str = Field(
        description="Textualized content (part info + features + full process route)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=lambda: {"source_type": ChunkType.CASE.value},
        description="Additional metadata",
    )

    def model_post_init(self, __context) -> None:
        """Automatically build a deterministic chunk_id from case id + content."""
        if not self.chunk_id:
            digest = hashlib.sha1(f"{self.case_id}\x00{self.content}".encode("utf-8")).hexdigest()[
                :16
            ]
            self.chunk_id = f"case-{digest}"


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
