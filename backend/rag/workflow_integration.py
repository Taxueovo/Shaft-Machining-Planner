"""RAG x Workflow integration layer.

Injects RAG (process handbook + case base) retrieval results into the LLM node
prompts of the workflow. Degrades gracefully: returns an empty string when RAG
is not configured, the index is empty, or retrieval errors out, without
affecting the main flow.

Usage:
    from rag.workflow_integration import build_rag_context

    context = build_rag_context(request, geometry, user_choices, heat_decision)
    if context:
        system_prompt += f"\n\nReference knowledge:\n{context}"
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _safe_retrieve_for_llm(query: str, top_k: int = 3, max_chars: int = 3000) -> str:
    """Safe retrieval - returns an empty string when RAG is unavailable or fails."""
    try:
        from .retriever import retrieve_for_llm

        return retrieve_for_llm(query, top_k=top_k, max_chars=max_chars)
    except Exception as exc:  # noqa: BLE001 - no RAG exception should block the main flow
        logger.warning("RAG retrieval failed, skipping context injection: %s", exc)
        return ""


def _feature_summary(features: list[dict[str, Any]]) -> str:
    """Compress a feature list into a retrieval-friendly text summary."""
    try:
        from rules import FEATURE_NAME
    except Exception:  # noqa: BLE001
        FEATURE_NAME = {}

    parts = []
    for f in features:
        label = FEATURE_NAME.get(f.get("feature_type"), f.get("feature_type", ""))
        position = f.get("global_position_mm", "")
        high_precision = " [high-precision]" if f.get("high_precision") else ""
        parts.append(f"{label}@{position}mm{high_precision}")
    return ", ".join(parts) or "None"


def build_rag_query(
    request: dict[str, Any],
    geometry: dict[str, Any],
    user_choices: Optional[dict[str, str]] = None,
    heat_decision: Optional[dict[str, Any]] = None,
) -> str:
    """Build a RAG retrieval query text from the current workflow state.

    Parameters
    ----------
    request : dict
        The process planning request (material, segments, global_requirements, etc.).
    geometry : dict
        Feature analysis output (segments, features, total_length_mm).
    user_choices : dict, optional
        High-precision feature processing sequence choices.
    heat_decision : dict, optional
        Heat treatment decision (process_name, etc.).

    Returns
    -------
    str
        A natural-language query for RAG retrieval.
    """
    from rules import HEAT_NAME, SURFACE_NAME

    global_req = request.get("global_requirements", {})

    parts = [
        f"Material {request.get('material', '')}",
        f"Blank diameter φ{request.get('blank_diameter_mm', '')}mm",
        f"Total length {geometry.get('total_length_mm', '')}mm",
    ]

    heat_type = global_req.get("heat_treatment")
    if heat_type and heat_type != "none":
        parts.append(f"Heat treatment {HEAT_NAME.get(heat_type, heat_type)}")

    surface_type = global_req.get("surface_treatment")
    if surface_type and surface_type != "none":
        parts.append(f"Surface treatment {SURFACE_NAME.get(surface_type, surface_type)}")

    features = geometry.get("features", [])
    if features:
        parts.append(f"Features {_feature_summary(features)}")

    if user_choices:
        parts.append(f"Precision choices {user_choices}")

    if heat_decision and heat_decision.get("process_name"):
        parts.append(f"Heat treatment process {heat_decision.get('process_name')}")

    return " ".join(parts)


def build_rag_context(
    request: dict[str, Any],
    geometry: dict[str, Any],
    user_choices: Optional[dict[str, str]] = None,
    heat_decision: Optional[dict[str, Any]] = None,
    top_k: int = 3,
    max_chars: int = 3000,
) -> str:
    """Build the RAG context to inject into the LLM prompt.

    Returns an empty string when RAG is unavailable or retrieval has no results;
    callers should skip injection in that case.

    Parameters
    ----------
    request : dict
        The process planning request.
    geometry : dict
        Feature analysis output.
    user_choices : dict, optional
        High-precision feature processing sequence choices.
    heat_decision : dict, optional
        Heat treatment decision.
    top_k : int
        Number of final retrieval results.
    max_chars : int
        Maximum context length in characters.

    Returns
    -------
    str
        Formatted RAG reference knowledge (with spec/case source annotations);
        an empty string means no injection is needed.
    """
    query = build_rag_query(request, geometry, user_choices, heat_decision)
    return _safe_retrieve_for_llm(query, top_k=top_k, max_chars=max_chars)
