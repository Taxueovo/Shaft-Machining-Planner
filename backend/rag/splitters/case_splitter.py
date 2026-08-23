"""Case base splitter.

Reads structured case JSON and indexes each part case as one complete chunk.
Preserves structural integrity: part info + feature list + full process route
-> one textualized chunk.

Supported formats:
1. Standard cases.json format ({ "cases": [...] })
2. Single-case JSON file ({ "case_id": ..., ... })
3. Any JSON object containing a case_id
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..schemas import CaseChunk

logger = logging.getLogger(__name__)

# ── Case text template ──

_SEPARATOR = "─" * 24


def _feature_to_string(f: Any) -> str:
    """Normalize a single feature into a string (accepts dict or str); shared by textualization and CaseChunk."""
    if isinstance(f, str):
        return f
    if isinstance(f, dict):
        ftype = f.get("feature_type") or f.get("name") or "feature"
        parts = [ftype]
        fid = f.get("feature_id")
        if fid:
            parts.append(str(fid))
        pos = f.get("global_position_mm")
        if pos is not None:
            parts.append(f"@{pos}mm")
        return " ".join(parts)
    return str(f)


def _features_to_strings(case: dict[str, Any]) -> list[str]:
    """Normalize case features into a list of strings (dict or str both supported)."""
    features: list = case.get("main_features", []) or case.get("features", [])
    return [_feature_to_string(f) for f in features]


def _case_to_text(case: dict[str, Any]) -> str:
    """Textualize structured case data for embedding; empty fields are omitted."""
    lines = [
        f"Case: {case.get('part_name', case.get('case_id', 'Unknown'))} ({case.get('case_id', 'Unknown')})",
        "",
    ]

    def add(label: str, value: Any) -> None:
        if value not in (None, ""):
            lines.append(f"{label}: {value}")

    add("Material", case.get("material", ""))
    add("Category", case.get("taxonomy_id", ""))
    add("Industry", case.get("industry", ""))
    add("Application", case.get("application", ""))
    add("Heat Treatment", case.get("heat_treatment", ""))
    add("Tolerance", case.get("tolerance", ""))
    add("Surface Roughness", case.get("surface_roughness", ""))
    if case.get("length_mm") or case.get("diameter_mm"):
        add(
            "Dimensions", f"length {case.get('length_mm')}mm, diameter φ{case.get('diameter_mm')}mm"
        )
    features_text = ", ".join(_features_to_strings(case))
    add("Features", features_text if features_text else None)
    add("Description", case.get("description", ""))

    lines.extend(["", _SEPARATOR, "Process Route:", _SEPARATOR])

    process_plan = case.get("process_plan", [])
    if process_plan:
        for step in process_plan:
            if isinstance(step, dict):
                no = step.get("step_no", "")
                name = step.get("name", "")
                stage = step.get("stage", "")
                desc = step.get("description", "")
                machine = step.get("machine", "")
                tool = step.get("tool", "")
                line = f"Op {no}. {name} [{stage}] — {desc}"
                if machine:
                    line += f" | Machine: {machine}"
                if tool:
                    line += f" | Tool: {tool}"
                lines.append(line)
            else:
                lines.append(str(step))
    else:
        lines.append("(no process plan data)")

    return "\n".join(lines)


def _extract_cases_from_data(data: Any) -> list[dict[str, Any]]:
    """Extract the list of cases from various JSON formats."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Standard format {"cases": [...]}
        if "cases" in data and isinstance(data["cases"], list):
            return data["cases"]
        # Single-case format
        if "case_id" in data:
            return [data]
        # Fallback: find the first list value
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def split_case(source_path: str, content: str) -> list[CaseChunk]:
    """Main entry point for chunking case library JSON.

    Parameters
    ----------
    source_path : str
        Path of the source JSON file.
    content : str
        Raw text content of the JSON file.

    Returns
    -------
    list of CaseChunk
    """
    source_file = Path(source_path).name
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse case JSON: %s — %s", source_file, exc)
        return []

    cases = _extract_cases_from_data(data)
    if not cases:
        logger.warning("No cases found in: %s", source_file)
        return []

    chunks = []
    for case in cases:
        case_id = case.get("case_id", f"unknown-{len(chunks)}")
        text = _case_to_text(case)

        chunk = CaseChunk(
            case_id=case_id,
            source_file=source_file,
            part_name=case.get("part_name", case_id),
            material=case.get("material", "Unknown"),
            taxonomy_id=case.get("taxonomy_id"),
            industry=case.get("industry"),
            features=_features_to_strings(case),
            content=text,
        )
        chunks.append(chunk)

    logger.info("Case chunking: %s → %d chunks", source_file, len(chunks))
    return chunks
