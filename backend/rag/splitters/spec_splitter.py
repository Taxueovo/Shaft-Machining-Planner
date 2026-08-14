"""Process handbook splitter.

Semantically splits Markdown by heading level (H1 chapter -> H2 section -> H3
process step description). Each chunk carries its full hierarchy path,
preserving contextual relationships.

Supported:
- # Chapter 1 ... (chapter)
- ## 1.1 ... (section)
- ### 1.1.1 ... (subsection / process step description)

Plain text without headings is split by paragraphs and character count.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from ..config import SPEC_CHUNK_SIZE, SPEC_CHUNK_OVERLAP, SPEC_MIN_CHUNK_SIZE
from ..schemas import SpecChunk

logger = logging.getLogger(__name__)

# ── Heading matching regex ──

HEADING_PATTERN = re.compile(
    r"^(#{1,3})\s+(.+?)(?:\s*\{[^}]*\})?\s*$", re.MULTILINE
)


def _parse_hierarchy(markdown_text: str) -> list[dict[str, Any]]:
    """Parse the heading hierarchy of Markdown text, returning section structure.

    Each item in the returned list is:
        {
            "level": 1|2|3,
            "heading": "heading text",
            "content": "raw text under this heading (including sub-headings)",
            "start_line": int,
        }
    """
    lines = markdown_text.split("\n")
    sections: list[dict[str, Any]] = []
    current_section: Optional[dict[str, Any]] = None

    for i, line in enumerate(lines):
        match = HEADING_PATTERN.match(line)
        if not match:
            if current_section is not None:
                current_section["content_lines"].append(line)
            continue

        level = len(match.group(1))
        heading = match.group(2).strip()

        if current_section is not None:
            current_section["content"] = "\n".join(
                current_section.pop("content_lines")
            )

        current_section = {
            "level": level,
            "heading": heading,
            "content_lines": [],
            "content": "",
            "start_line": i + 1,
        }
        sections.append(current_section)

    # Last section
    if current_section is not None:
        current_section["content"] = "\n".join(current_section.pop("content_lines"))

    return sections


def _build_chunks_from_hierarchy(
    sections: list[dict[str, Any]], source_file: str
) -> list[SpecChunk]:
    """Build the SpecChunk list from hierarchical sections.

    Rules:
    - H1 -> chapter
    - H2 -> section
    - H3 -> subsection
    - H3 is the smallest semantic unit; one H3 section = one chunk
    - Without H3, an H2 section becomes a chunk
    - With only H1, H1 sections are split by character count
    - Each chunk records the current chapter / section / subsection context
    """
    chunks: list[SpecChunk] = []
    current_chapter: Optional[str] = None
    current_section: Optional[str] = None

    i = 0
    while i < len(sections):
        sec = sections[i]

        if sec["level"] == 1:
            current_chapter = sec["heading"]
            current_section = None
            # The H1 itself is also a chunk (chapter intro)
            if sec["content"].strip():
                chunks.append(
                    SpecChunk(
                        source_file=source_file,
                        chapter=current_chapter,
                        content=sec["content"].strip(),
                    )
                )
            i += 1

        elif sec["level"] == 2:
            current_section = sec["heading"]
            # The H2 itself is a chunk (section intro)
            if sec["content"].strip():
                chunks.append(
                    SpecChunk(
                        source_file=source_file,
                        chapter=current_chapter,
                        section=current_section,
                        content=sec["content"].strip(),
                    )
                )
            i += 1

        elif sec["level"] == 3:
            # H3 = smallest semantic unit, one chunk
            text = sec["content"].strip()
            if not text:
                i += 1
                continue
            chunks.append(
                SpecChunk(
                    source_file=source_file,
                    chapter=current_chapter,
                    section=current_section,
                    subsection=sec["heading"],
                    content=text,
                )
            )
            i += 1

        else:
            i += 1

    return chunks


def _fallback_chunk_by_size(
    text: str,
    source_file: str,
    chunk_size: int = SPEC_CHUNK_SIZE,
    overlap: int = SPEC_CHUNK_OVERLAP,
    min_size: int = SPEC_MIN_CHUNK_SIZE,
) -> list[SpecChunk]:
    """Fallback strategy: split heading-less text with a character-count sliding window."""
    if not text.strip():
        return []

    paragraphs = text.split("\n\n")
    chunks: list[SpecChunk] = []
    buffer = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(buffer) + len(para) + 2 <= chunk_size:
            buffer = (buffer + "\n\n" + para).strip()
        else:
            if len(buffer) >= min_size:
                chunks.append(
                    SpecChunk(source_file=source_file, content=buffer)
                )
                # Keep the overlap portion
                overlap_text = buffer[-overlap:] if len(buffer) > overlap else ""
                buffer = (overlap_text + "\n\n" + para).strip()
            else:
                buffer = (buffer + "\n\n" + para).strip()

    if len(buffer) >= min_size:
        chunks.append(SpecChunk(source_file=source_file, content=buffer))

    return chunks


def split_spec(source_path: str, content: str) -> list[SpecChunk]:
    """Main entry point for chunking process handbook documents.

    Parameters
    ----------
    source_path : str
        Source file path.
    content : str
        Document content (Markdown or plain text).

    Returns
    -------
    list of SpecChunk
    """
    source_file = Path(source_path).name

    # Detect whether the document has Markdown headings
    has_headings = bool(HEADING_PATTERN.search(content))

    if has_headings:
        sections = _parse_hierarchy(content)
        chunks = _build_chunks_from_hierarchy(sections, source_file)
        logger.info(
            "Spec chunking (hierarchy): %s → %d chunks", source_file, len(chunks)
        )
    else:
        chunks = _fallback_chunk_by_size(content, source_file)
        logger.info(
            "Spec chunking (fallback): %s → %d chunks", source_file, len(chunks)
        )

    return chunks
