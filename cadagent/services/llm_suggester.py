"""
================================================

LLM Engineering-Intent Suggester

Uses the CAE expert LLM to infer engineering intent missing from the pure
geometry of the CAD model:
- Material suggestion (chosen from the peagent material candidate list)
- Tolerance / roughness suggestion per shaft segment
- Heat treatment / hardness suggestion

Design principles:
- Output is always a "suggested value"; the user finally confirms it in the form;
- Structured JSON output + strict validation (material must be in the
  candidate list, heat treatment must be in the enum);
- Any failure degrades gracefully (returns None) without affecting the
  mapping main flow.

================================================
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Legal values aligned with peagent GlobalRequirements.heat_treatment
VALID_HEAT_TREATMENT = {
    "none", "normalizing", "quench_temper", "carburize_quench", "quench_and_temper",
    "nitriding", "induction_hardening",
}

SUGGEST_SYSTEM_PROMPT = """You are a senior manufacturing process engineer for motor shafts.

You are given the geometric feature data extracted from a 3D CAD model (a stepped shaft).
The CAD geometry does NOT carry engineering intent. Based on the geometry and your
engineering judgment, you must propose reasonable values for:

1. material_suggestion  - pick from the provided candidate list (a steel/alloy grade).
2. segment_specs        - per segment: diameter tolerance (upper/lower deviation in mm)
                          and surface roughness Ra (micrometers). Bearings seats and
                          mating surfaces -> tight tolerance (IT6/IT7) and Ra <= 1.6;
                          non-critical bodies -> looser (IT10+) and Ra 3.2.
3. heat_treatment_suggestion - whether heat treatment is needed. High-precision or
                          heavily loaded shafts -> quench_and_temper / quench_temper;
                          carburizing steels -> carburize_quench; light duty -> none.
                          IMPORTANT: if the geometry has gear_teeth features (a gear
                          shaft), it is typically carburized -> suggest a carburizing
                          steel (e.g. 20CrMnTi / 20Cr) and heat_treatment_suggestion
                          = carburize_quench. Precision spindles / nitriding steels
                          (e.g. 38CrMoAlA) -> nitriding; shafts needing localized
                          journal hardening -> induction_hardening.
4. target_hardness_hrc  - suggested hardness if heat treatment is applied (else null).
                          Carburized gear shafts -> 58-62 HRC surface hardness.
5. notes               - short notes explaining key decisions.

IMPORTANT: Always respond in English. All notes, labels, and any other free-text
output must be written in English — never in Chinese or any other language.

Respond with STRICT JSON ONLY, no markdown, no code fences, no commentary. Example:

{
  "material_suggestion": "45",
  "segment_specs": {
    "S01": {"diameter_upper_deviation_mm": 0.02, "diameter_lower_deviation_mm": -0.02, "roughness_ra": 1.6}
  },
  "heat_treatment_suggestion": "quench_temper",
  "target_hardness_hrc": 28,
  "notes": ["S01 bearing seat: IT6, Ra 1.6"]
}
"""


# ----------------------------------------------------------------------------
# Pure functions: JSON parsing and validation (unit-testable)
# ----------------------------------------------------------------------------

def parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    """Robustly extract a JSON object from the LLM reply (tolerates markdown code fences / surrounding text)."""
    if not text:
        return None
    cleaned = text.strip()
    # Strip ```json ... ``` fences
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Retry with the substring between the first { and the last }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def validate_suggestions(
    raw: Dict[str, Any],
    material_candidates: Optional[List[str]] = None,
    segment_ids: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Validate and clean the LLM suggestions. Invalid/missing fields are dropped (None means overall failure)."""
    if not isinstance(raw, dict):
        return None

    candidates = {m.strip().upper() for m in (material_candidates or [])}
    out: Dict[str, Any] = {"segment_specs": {}, "notes": []}

    # ---- Material ----
    material = raw.get("material_suggestion")
    if material and str(material).strip():
        m = str(material).strip()
        if candidates and m.upper() not in candidates:
            logger.info("LLM material suggestion %r is not in the candidate list; ignored.", m)
        else:
            out["material_suggestion"] = m

    # ---- Segment tolerance/roughness ----
    segment_specs = raw.get("segment_specs")
    if isinstance(segment_specs, dict):
        for seg_id, spec in segment_specs.items():
            if segment_ids and seg_id not in segment_ids:
                continue
            if not isinstance(spec, dict):
                continue
            clean_spec: Dict[str, Any] = {}
            upper = spec.get("diameter_upper_deviation_mm")
            lower = spec.get("diameter_lower_deviation_mm")
            ra = spec.get("roughness_ra")
            if upper is not None:
                try:
                    clean_spec["diameter_upper_deviation_mm"] = round(float(upper), 4)
                except (TypeError, ValueError):
                    pass
            if lower is not None:
                try:
                    clean_spec["diameter_lower_deviation_mm"] = round(float(lower), 4)
                except (TypeError, ValueError):
                    pass
            if ra is not None:
                try:
                    clean_spec["roughness_ra"] = round(float(ra), 4)
                except (TypeError, ValueError):
                    pass
            if clean_spec:
                out["segment_specs"][seg_id] = clean_spec

    # ---- Heat treatment ----
    heat = raw.get("heat_treatment_suggestion")
    if heat and str(heat).strip() in VALID_HEAT_TREATMENT:
        out["heat_treatment_suggestion"] = str(heat).strip()

    # ---- Hardness ----
    hrc = raw.get("target_hardness_hrc")
    if hrc is not None:
        try:
            val = float(hrc)
            if 0 < val <= 75:
                out["target_hardness_hrc"] = round(val, 1)
        except (TypeError, ValueError):
            pass

    # ---- Notes ----
    notes = raw.get("notes")
    if isinstance(notes, list):
        out["notes"] = [str(n) for n in notes if str(n).strip()]

    # Only return if at least one meaningful field is present
    if not out.get("material_suggestion") and not out["segment_specs"] \
            and not out.get("heat_treatment_suggestion") and not out.get("target_hardness_hrc"):
        return None
    return out


# ----------------------------------------------------------------------------
# LLM invocation
# ----------------------------------------------------------------------------

async def suggest_engineering_fields(
    features_json: Dict[str, Any],
    material_candidates: Optional[List[str]] = None,
    isometric_image_base64: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Invoke the CAE expert LLM to infer engineering intent. Any failure returns None (graceful degradation).

    Args:
        features_json: cad_agent feature extraction output.
        material_candidates: grade list supported by peagent (used to validate the material suggestion).
        isometric_image_base64: optional isometric image base64 (data URL prefix optional).

    Returns:
        Cleaned suggestion dict, or None.
    """
    try:
        from cadagent.config.llm_config import (
            create_async_openai_client, LLM_MODEL, LLM_TEMPERATURE, LLM_API_KEY,
        )
    except ImportError:  # pragma: no cover
        from config.llm_config import (
            create_async_openai_client, LLM_MODEL, LLM_TEMPERATURE, LLM_API_KEY,
        )

    # Degrade directly when no API key is present (LLM_API_KEY is an env var name, e.g. OPENAI_API_KEY)
    import os
    if not os.getenv(LLM_API_KEY):
        logger.warning(f"{LLM_API_KEY} is not set; skipping engineering-intent completion.")
        return None

    # Build the part summary (limit tokens)
    dims = (features_json or {}).get("overall_dimensions", {}) or {}
    feats = (features_json or {}).get("features", {}) or {}
    summary_lines = [
        f"Overall: length={dims.get('length')}mm, max_diameter={dims.get('max_diameter')}mm",
        f"Segments: {[(c.get('radius', 0) * 2, c.get('length', 0)) for c in feats.get('outer_cylinders', [])]}",
    ]
    if feats.get("keyways", {}).get("count"):
        summary_lines.append(f"Keyways: {feats['keyways']['keyways']}")
    if feats.get("radial_oil_holes", {}).get("count"):
        summary_lines.append(f"Radial holes: {feats['radial_oil_holes']}")
    if (feats.get("spline_zone") or {}).get("detected"):
        summary_lines.append(f"Spline: {(feats['spline_zone'] or {}).get('parameters')}")
    if (feats.get("gear_features") or {}).get("detected"):
        summary_lines.append(f"Gears: {(feats['gear_features'] or {}).get('parameters')}")

    user_content: List[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Given the shaft feature data below, propose engineering intent.\n\n"
                "```\n" + "\n".join(summary_lines) + "\n```\n\n"
                + f"Material candidates: {material_candidates or []}\n"
                + "Respond with strict JSON per the schema in your instructions."
            ),
        }
    ]
    if isometric_image_base64:
        img_url = isometric_image_base64
        if not img_url.startswith("data:"):
            img_url = f"data:image/jpeg;base64,{img_url}"
        user_content.append({"type": "image_url", "image_url": {"url": img_url}})

    try:
        client = create_async_openai_client()
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SUGGEST_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            stream=False,
            temperature=min(LLM_TEMPERATURE, 0.4),
        )
        content = response.choices[0].message.content if response.choices else None
        if not content:
            logger.warning("LLM completion returned empty content.")
            return None
        raw = parse_llm_json(content)
        if raw is None:
            logger.warning("LLM completion returned unparseable JSON; first 200 chars: %r", content[:200])
            return None
        segment_ids = [
            c.get("segment_id") for c in
            (features_json or {}).get("segments", [])
        ] or None
        return validate_suggestions(raw, material_candidates, segment_ids)
    except Exception as exc:  # pragma: no cover - network/API errors
        logger.warning("LLM completion failed, degrading gracefully: %s", exc)
        return None
