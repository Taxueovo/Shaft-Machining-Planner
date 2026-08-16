"""规范库分块器。

按 Markdown 标题层级（H1 章 → H2 节 → H3 工序说明）进行语义切分。
每个 Chunk 携带完整的层级路径，保留上下文关系。

支持：
- # 第一章 …（章）
- ## 1.1 …（节）
- ### 1.1.1 …（子节/工序说明）

无标题的纯文本按段落 + 字符数切分。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from ..config import SPEC_CHUNK_SIZE, SPEC_CHUNK_OVERLAP, SPEC_MIN_CHUNK_SIZE
from ..schemas import SpecChunk

logger = logging.getLogger(__name__)

# ── 标题匹配正则 ──

HEADING_PATTERN = re.compile(
    r"^(#{1,3})\s+(.+?)(?:\s*\{[^}]*\})?\s*$", re.MULTILINE
)


def _parse_hierarchy(markdown_text: str) -> list[dict[str, Any]]:
    """解析 Markdown 文本的标题层级，返回段落结构。

    返回列表中每一项为：
        {
            "level": 1|2|3,
            "heading": "标题文本",
            "content": "该标题下的原始文本（含子标题）",
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

    # 最后一段
    if current_section is not None:
        current_section["content"] = "\n".join(current_section.pop("content_lines"))

    return sections


def _split_long_subsection(
    heading: str,
    text: str,
    chapter: Optional[str],
    section: Optional[str],
    source_file: str,
) -> list[SpecChunk]:
    """超长 H3 按字符数二次切分，保留层级路径，段落级不跨段。

    一个超长 H3 被拆为多个 chunk，subsection 标注 (1/n) 便于追溯；
    切分点在段落边界，避免割裂一个完整句段。
    """
    paragraphs = text.split("\n\n")
    sub_chunks: list[SpecChunk] = []
    buffer = ""
    overlap = ""
    total_len = len(text)
    max_len = max(SPEC_CHUNK_SIZE, SPEC_MIN_CHUNK_SIZE)

    def flush() -> None:
        nonlocal buffer, overlap
        if len(buffer.strip()) < SPEC_MIN_CHUNK_SIZE:
            return
        sub_chunks.append(
            SpecChunk(
                source_file=source_file,
                chapter=chapter,
                section=section,
                subsection=heading,
                part=len(sub_chunks) + 1,
                total_parts=max(1, (total_len + max_len - 1) // max_len),
                content=buffer.strip(),
            )
        )
        overlap = buffer[-SPEC_CHUNK_OVERLAP:] if len(buffer) > SPEC_CHUNK_OVERLAP else buffer
        buffer = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(buffer) + len(para) + 2 <= SPEC_CHUNK_SIZE:
            buffer = (buffer + "\n\n" + para).strip()
        else:
            flush()
            buffer = ((overlap + "\n\n" + para) if overlap else para).strip()
            overlap = ""
    flush()
    return sub_chunks


def _build_chunks_from_hierarchy(
    sections: list[dict[str, Any]], source_file: str
) -> list[SpecChunk]:
    """从层级段落构建 SpecChunk 列表。

    规则：
    - H1 → chapter
    - H2 → section
    - H3 → subsection
    - H3 作为最小语义单元，一个 H3 段落 = 一个 chunk
    - 无 H3 时，H2 段落作为 chunk
    - 仅 H1 时，H1 段落按字符数切分
    - 每个 chunk 记录当前 chapter / section / subsection 上下文
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
            # H1 自身也作为一个 chunk（章简介）
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
            # H2 自身作为 chunk（节简介）
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
            # H3 = 最小语义单元；超长 H3 按字符二次切分，保留层级路径
            text = sec["content"].strip()
            if not text:
                i += 1
                continue
            if len(text) <= SPEC_CHUNK_SIZE:
                chunks.append(
                    SpecChunk(
                        source_file=source_file,
                        chapter=current_chapter,
                        section=current_section,
                        subsection=sec["heading"],
                        content=text,
                    )
                )
            else:
                chunks.extend(
                    _split_long_subsection(
                        sec["heading"], text, current_chapter,
                        current_section, source_file,
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
    """兜底策略：无标题文本按字符数滑动窗口切分。"""
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
                # 保留 overlap 部分
                overlap_text = buffer[-overlap:] if len(buffer) > overlap else ""
                buffer = (overlap_text + "\n\n" + para).strip()
            else:
                buffer = (buffer + "\n\n" + para).strip()

    if len(buffer) >= min_size:
        chunks.append(SpecChunk(source_file=source_file, content=buffer))

    return chunks


def split_spec(source_path: str, content: str) -> list[SpecChunk]:
    """规范库文档分块主入口。

    Parameters
    ----------
    source_path : str
        源文件路径。
    content : str
        文档内容（Markdown 或纯文本）。

    Returns
    -------
    list of SpecChunk
    """
    source_file = Path(source_path).name

    # 检测是否有 Markdown 标题
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
