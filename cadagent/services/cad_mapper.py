"""
================================================

CAD Feature -> PE Process Planning Input Mapper

Maps cad_agent's ``features_json`` (B-Rep feature extraction result) to a
peagent ``PlanningRequest`` draft.

Design principles:
- Geometric features (shaft segments / keyways / radial holes / splines / gears)
  are mapped with deterministic rules that can be verified;
- Engineering intent (material / tolerance / roughness / heat treatment) is not
  generated in this module; it is marked as ``required`` / ``suggested`` and is
  inferred by the LLM plus confirmed by the user in the form;
- Output carries ``confidence`` annotations and ``warnings`` for the frontend
  to highlight and hint at;
- Optionally calls :func:`validate_with_peagent` to strictly validate against
  peagent's real Pydantic models (requires the peagent backend on sys.path).

Coordinate normalization: the axial coordinates output by cad_agent are in CAD
model coordinates (the origin is not necessarily at the shaft end); when
mapping, the minimum axial coordinate is subtracted so the shaft end aligns to 0.

================================================
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

#: Diameter tolerance (mm) for merging adjacent segments with the same diameter
MERGE_DIAMETER_TOL_MM = 0.5
#: Axial gap tolerance (mm) for merging adjacent segments with the same diameter
MERGE_GAP_TOL_MM = 3.0
#: Machining allowance (mm) of the blank diameter relative to the max finished diameter
BLANK_ALLOWANCE_MM = 2.0
#: spline_type supported by peagent FeatureInput
VALID_SPLINE_TYPES = {"involute", "straight"}

# Segment / feature ID prefixes
SEGMENT_ID_PREFIX = "S"
FEATURE_ID_PREFIX = "F"


# ----------------------------------------------------------------------------
# Internal utilities
# ----------------------------------------------------------------------------

def _get_features(features_json: Dict[str, Any]) -> Dict[str, Any]:
    """Safely fetch the features sub-dictionary."""
    if not features_json or not isinstance(features_json, dict):
        return {}
    feats = features_json.get("features")
    return feats if isinstance(feats, dict) else {}


def _axis_position_components(features_json: Dict[str, Any]) -> List[Tuple[str, float]]:
    """Collect the raw axial coordinates of all features, used to compute the normalization offset."""
    out: List[Tuple[str, float]] = []
    feats = _get_features(features_json)
    for cyl in feats.get("outer_cylinders", []):
        if cyl.get("position_x") is not None:
            out.append(("cylinder", float(cyl["position_x"])))
    for kw in feats.get("keyways", {}).get("keyways", []):
        if kw.get("position_axial") is not None:
            out.append(("keyway", float(kw["position_axial"])))
    for z in feats.get("radial_oil_holes", {}).get("axial_positions", []):
        out.append(("hole", float(z)))
    spline = feats.get("spline_zone", {}) or {}
    for zr in spline.get("z_ranges", []) or []:
        if zr.get("z_start") is not None:
            out.append(("spline", float(zr["z_start"])))
    gear = feats.get("gear_features", {}) or {}
    for zone in gear.get("gear_zones", []) or []:
        if zone.get("position_start") is not None:
            out.append(("gear", float(zone["position_start"])))
    # Bore coordinates are also included in the normalization basis, to avoid
    # negative positions when through/deep bores lie outside the other features
    for bore in feats.get("inner_bore", []) or []:
        if bore.get("position_x") is not None:
            out.append(("bore", float(bore["position_x"])))
    return out


def _compute_offset(features_json: Dict[str, Any]) -> float:
    """Compute the axial normalization offset (shaft-end datum).

    The shaft end is taken as the minimum of "outermost cylinder segment start"
    = position_x - length/2; if there are no cylinder segments, fall back to the
    minimum of all feature coordinates.
    This way the axial coordinates of keyway/hole/spline/gear are normalized to
    shaft-end positions starting at 0.
    """
    feats = _get_features(features_json)
    candidates: List[float] = []
    for cyl in feats.get("outer_cylinders", []):
        if cyl.get("position_x") is not None:
            length = float(cyl.get("length", 0.0))
            candidates.append(float(cyl["position_x"]) - length / 2.0)
    # Non-cylinder feature coordinates are also included in the minimum
    # (handles degenerate cases where features start before the first segment)
    for kind, coord in _axis_position_components(features_json):
        if kind != "cylinder":
            candidates.append(coord)
    return min(candidates) if candidates else 0.0


# ----------------------------------------------------------------------------
# 1. Segment mapping (outer_cylinders -> segments)
# ----------------------------------------------------------------------------

def _segment_extents(cyl: Dict[str, Any]) -> Tuple[float, float]:
    """Estimate the axial extent (start, end) of a cylinder segment. position_x is treated as the segment center."""
    pos = float(cyl.get("position_x", 0.0))
    length = float(cyl.get("length", 0.0))
    return pos - length / 2.0, pos + length / 2.0


def _build_segments(features_json: Dict[str, Any], offset: float) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Outer cylinders -> stepped shaft segments (sorted by axial position, adjacent segments with the same diameter merged)."""
    warnings: List[str] = []
    cyls = _get_features(features_json).get("outer_cylinders", [])
    if not cyls:
        return [], ["No outer cylindrical segments detected (outer_cylinders is empty); cannot build shaft segments."]

    # Sort by axial center
    cyls = sorted(cyls, key=lambda c: c.get("position_x", 0.0))

    raw: List[Dict[str, Any]] = []
    for i, cyl in enumerate(cyls, 1):
        radius = cyl.get("radius", 0.0)
        length = cyl.get("length", 0.0)
        if not radius or radius <= 0 or length <= 0:
            warnings.append(
                f"Cylinder segment #{i} has invalid diameter/length (radius={radius}, length={length}); skipped."
            )
            continue
        start, end = _segment_extents(cyl)
        raw.append({
            "segment_id": f"{SEGMENT_ID_PREFIX}{i:02d}",
            "diameter_mm": round(2.0 * float(radius), 3),
            "length_mm": round(float(length), 3),
            "_start": start - offset,
            "_end": end - offset,
            "_area": float(cyl.get("area", 0.0) or 0.0),
            "_type": cyl.get("type"),
        })

    # Merge adjacent segments with the same diameter (surface areas are accumulated)
    merged: List[Dict[str, Any]] = []
    for seg in raw:
        if merged and abs(merged[-1]["diameter_mm"] - seg["diameter_mm"]) <= MERGE_DIAMETER_TOL_MM:
            prev = merged[-1]
            if seg["_start"] - prev["_end"] <= MERGE_GAP_TOL_MM:
                prev["length_mm"] = round(prev["length_mm"] + seg["length_mm"], 3)
                prev["_end"] = max(prev["_end"], seg["_end"])
                prev["_area"] = round(prev["_area"] + seg["_area"], 3)
                continue
        merged.append(seg)

    # Renumber (carry the supplementary info from CAD extraction: surface area / segment type)
    segments: List[Dict[str, Any]] = []
    for idx, seg in enumerate(merged, 1):
        segment = {
            "segment_id": f"{SEGMENT_ID_PREFIX}{idx:02d}",
            "diameter_mm": seg["diameter_mm"],
            "length_mm": seg["length_mm"],
        }
        if seg.get("_area"):
            segment["surface_area_mm2"] = round(seg["_area"], 3)
        if seg.get("_type"):
            segment["segment_type"] = seg["_type"]
        segments.append(segment)

    # Compare against the overall length: the gear enveloping surface is excluded
    # from the cylinder segments, so the gear face width is added to the covered
    # length. When the segments(+gear) total is still clearly smaller than the part
    # length, the tail contains chamfers/tapers/transitions/gear envelopes not
    # covered by cylinder segments - merge that length into the last segment so
    # segment total = part length; otherwise tail features (keyways/oil holes/
    # gears) would be misjudged by the backend feature_analysis as
    # "global position exceeds total part length".
    dims = features_json.get("overall_dimensions", {}) or {}
    overall_length = dims.get("length")
    if overall_length:
        seg_total = round(sum(s["length_mm"] for s in segments), 3)
        gear = features_json.get("features", {}).get("gear_features", {}) or {}
        gear_width = 0.0
        for zone in gear.get("gear_zones", []) or []:
            if zone.get("position_start") is not None and zone.get("position_end") is not None:
                gear_width += max(float(zone["position_end"]) - float(zone["position_start"]), 0.0)
        accounted = round(seg_total + gear_width, 3)
        if segments and float(overall_length) - accounted > 3.0:
            gap = round(float(overall_length) - seg_total, 3)
            segments[-1]["length_mm"] = round(segments[-1]["length_mm"] + gap, 3)
            warnings.append(
                f"Outer cylinder segments only cover {seg_total}mm of the {overall_length}mm part; "
                f"the last segment was extended by {gap}mm to cover the remaining "
                "chamfers/shoulders/transitions, please verify."
            )
        elif abs(accounted - float(overall_length)) > 3.0:
            warnings.append(
                f"Total segment length {seg_total}mm + gear face width {gear_width:.1f}mm = {accounted}mm "
                f"does not match overall length {overall_length}mm; the rest may be "
                "chamfers/shoulders/transitions, please verify the segment data."
            )
    return segments, warnings


# ----------------------------------------------------------------------------
# 2. Feature mapping (keyways / radial holes / spline / gear -> features)
# ----------------------------------------------------------------------------

def _next_feature_id(counter: List[int]) -> str:
    counter[0] += 1
    return f"{FEATURE_ID_PREFIX}{counter[0]:02d}"


def _build_keyway_features(feats: Dict[str, Any], offset: float,
                           counter: List[int], warnings: List[str]) -> List[Dict[str, Any]]:
    """Keyway -> feature_type=keyway."""
    out: List[Dict[str, Any]] = []
    keyways = feats.get("keyways", {}) or {}
    for kw in keyways.get("keyways", []) or []:
        width, depth, length = kw.get("width"), kw.get("depth"), kw.get("length")
        if not width or width <= 0:
            warnings.append(f"Keyway at z={kw.get('position_axial')} has invalid width; skipped.")
            continue
        feature = {
            "feature_id": _next_feature_id(counter),
            "feature_type": "keyway",
            "positioning_mode": "global_absolute",
            "global_position_mm": round(float(kw["position_axial"]) - offset, 3),
            "processing_timing": "undecided",
            "keyway_width_mm": round(float(width), 3),
            "keyway_depth_mm": round(float(depth), 3) if depth is not None else None,
            "feature_length_mm": round(float(length), 3) if length else None,
            # Keyway type (cad_agent: profile_key / wedge_key / flat_key)
            "keyway_type": kw.get("type"),
        }
        if not feature["keyway_depth_mm"]:
            warnings.append(f"Keyway {feature['feature_id']} depth is missing; please confirm.")
        if not feature["feature_length_mm"]:
            warnings.append(f"Keyway {feature['feature_id']} length is missing (estimated from area); please confirm.")
        out.append(feature)
    return out


def _build_hole_features(feats: Dict[str, Any], offset: float,
                         counter: List[int], warnings: List[str]) -> List[Dict[str, Any]]:
    """Radial oil holes -> feature_type=hole (one feature per axial position)."""
    out: List[Dict[str, Any]] = []
    holes = feats.get("radial_oil_holes", {}) or {}
    radius = holes.get("radius", 0.0) or 0.0
    if not radius or radius <= 0:
        if holes.get("count"):
            warnings.append("Radial holes detected but the radius is unknown; no hole feature generated, please add manually.")
        return out

    diameter = round(2.0 * float(radius), 3)
    holes_per_position = holes.get("holes_per_position", {}) or {}
    angles_per_position = holes.get("angles_per_position", {}) or {}
    for z in holes.get("axial_positions", []) or []:
        count = int(holes_per_position.get(str(z), 0) or 0)
        angles = angles_per_position.get(str(z)) or []
        feature = {
            "feature_id": _next_feature_id(counter),
            "feature_type": "hole",
            "positioning_mode": "global_absolute",
            "global_position_mm": round(float(z) - offset, 3),
            "processing_timing": "undecided",
            "hole_diameter_mm": diameter,
            "hole_type": "through",
            "hole_direction": "radial",
            # Hole count at the same axial position (e.g. 2/4 evenly spaced holes)
            "hole_count": count if count > 1 else None,
            # The minimum hole angle at this position serves as the angular start
            # (distribution reference for the evenly spaced holes)
            "hole_angle_deg": round(float(min(angles)), 1) if angles else None,
        }
        out.append(feature)
    return out


def _build_spline_feature(feats: Dict[str, Any], offset: float,
                          counter: List[int], warnings: List[str]) -> List[Dict[str, Any]]:
    """Spline -> feature_type=spline."""
    spline = feats.get("spline_zone", {}) or {}
    params = spline.get("parameters")
    if not spline.get("detected") or not params:
        return []
    teeth = params.get("tooth_count", 0) or 0
    if teeth <= 0:
        warnings.append("Spline zone detected but the tooth count is unknown; no spline feature generated, please add manually.")
        return []

    z_ranges = spline.get("z_ranges", []) or []
    z_start = None
    feature_length = None
    if z_ranges:
        z_start = float(z_ranges[0].get("z_start", 0.0))
        z_end = float(z_ranges[0].get("z_end", 0.0))
        feature_length = round(max(z_end - z_start, 0.0), 3) or None

    spline_type = params.get("spline_type")
    if spline_type not in VALID_SPLINE_TYPES:
        warnings.append(
            f"Spline type {spline_type!r} is not supported by peagent (involute/straight); "
            "reverted to involute, please confirm."
        )
        spline_type = "involute"

    # Spline centering and hobbing parameters: the major/minor diameter, pressure
    # angle and key width extracted by cad_agent are all preserved
    def _pos_float(value):
        try:
            val = float(value)
            return round(val, 3) if val > 0 else None
        except (TypeError, ValueError):
            return None

    feature = {
        "feature_id": _next_feature_id(counter),
        "feature_type": "spline",
        "positioning_mode": "global_absolute",
        "global_position_mm": round(z_start - offset, 3) if z_start is not None else None,
        "processing_timing": "undecided",
        "spline_type": spline_type,
        "spline_teeth": int(teeth),
        "spline_module": round(float(params.get("module", 0.0)), 3) if params.get("module") else None,
        "feature_length_mm": feature_length,
        "spline_major_diameter_mm": _pos_float(params.get("major_diameter")),
        "spline_minor_diameter_mm": _pos_float(params.get("minor_diameter")),
        "spline_pressure_angle_deg": _pos_float(params.get("pressure_angle")),
        "spline_key_width_mm": _pos_float(params.get("key_width_B")),
    }
    if not feature["global_position_mm"] or not feature["feature_length_mm"]:
        warnings.append(f"Spline {feature['feature_id']} position/length is missing; please confirm.")
    return [feature]


def _build_gear_features(feats: Dict[str, Any], offset: float,
                         counter: List[int], warnings: List[str]) -> List[Dict[str, Any]]:
    """Gear -> feature_type=gear_teeth (gear_zones and parameters correspond in parallel)."""
    out: List[Dict[str, Any]] = []
    gear = feats.get("gear_features", {}) or {}
    zones = gear.get("gear_zones", []) or []
    params_list = gear.get("parameters", []) or []

    for i, (zone, params) in enumerate(zip(zones, params_list), 1):
        teeth = params.get("tooth_count", 0) or 0
        module = params.get("module", 0.0) or 0.0
        if teeth <= 0 or module <= 0:
            warnings.append(f"Gear #{i} has incomplete parameters (teeth={teeth}, module={module}); skipped.")
            continue
        position_start = zone.get("position_start")
        position_end = zone.get("position_end")
        face_width = None
        if position_start is not None and position_end is not None:
            face_width = round(float(position_end) - float(position_start), 3) or None
        helix_angle = params.get("helix_angle", 0.0) or 0.0
        gear_type = params.get("gear_type", "spur") or "spur"
        feature = {
            "feature_id": _next_feature_id(counter),
            "feature_type": "gear_teeth",
            "positioning_mode": "global_absolute",
            "global_position_mm": round(float(position_start) - offset, 3),
            "processing_timing": "undecided",
            "gear_teeth": int(teeth),
            "gear_module": round(float(module), 3),
            "gear_pressure_angle": round(float(params.get("pressure_angle", 20.0)), 3),
            "gear_face_width_mm": face_width,
            # Gear extras: spur/helical + helix angle + full tooth height +
            # addendum/root circle diameters (all extracted by cad_agent)
            # The helix angle sign only indicates left/right-hand direction;
            # peagent only needs the magnitude (ge=0), so take the absolute value
            "gear_type": gear_type,
            "helix_angle_deg": round(abs(float(helix_angle)), 3),
            "gear_tooth_height_mm": round(float(params.get("tooth_height", 0.0)), 3)
            if params.get("tooth_height") else None,
            "gear_outer_diameter_mm": round(2.0 * float(params["addendum_radius"]), 3)
            if params.get("addendum_radius") else None,
            "gear_root_diameter_mm": round(2.0 * float(params["dedendum_radius"]), 3)
            if params.get("dedendum_radius") else None,
            # Default post-heat-treatment finishing for gears: helical gears
            # (transmission teeth) need it by default, spur gears do not; the
            # form allows changing this
            "gear_finish_required": gear_type == "helical",
        }
        if not face_width:
            warnings.append(f"Gear #{i} face width is missing; not generated / please confirm.")
            continue
        out.append(feature)
    return out


def _build_bore_features(feats: Dict[str, Any], offset: float,
                         counter: List[int], warnings: List[str]) -> List[Dict[str, Any]]:
    """Bore -> features.

    - Concentric bores along the main axis (pitch_radius ~ 0) -> feature_type=bore
      (stepped bores, one feature per segment);
    - Eccentric/patterned axial holes (pitch_radius above threshold, e.g. gear
      web lightening holes) -> feature_type=hole, hole_direction=axial, merged
      into one feature per (axial position, diameter) with the hole count in
      hole_count, so they are not mislabelled as bore while nothing is lost.
    """
    ECCENTRIC_PITCH_TOL = 0.5  # pitch_radius(mm) above this is treated as eccentric/patterned axial hole
    out: List[Dict[str, Any]] = []
    bores = feats.get("inner_bore", []) or []

    eccentric: List[Dict[str, Any]] = []
    for i, bore in enumerate(bores, 1):
        radius = bore.get("radius", 0.0) or 0.0
        pitch = bore.get("pitch_radius", 0.0) or 0.0
        if radius <= 0:
            warnings.append(f"Bore #{i} has invalid radius (radius={radius}); skipped.")
            continue
        if pitch > ECCENTRIC_PITCH_TOL:
            eccentric.append(bore)
            continue
        length = bore.get("length", 0.0) or 0.0
        if length <= 0:
            warnings.append(f"Bore #{i} has invalid length (length={length}); skipped.")
            continue
        # Note: the position_x from the extraction layer is the projection of the
        # cylinder face geometry origin (cylinder.Location()) onto the main axis,
        # not necessarily the face center. Use this axis origin directly as the
        # axial reference position; the hole depth/through length is expressed by
        # bore_length_mm, which avoids "position - length/2" pushing the position
        # outside the part when the axis origin lies at the face end.
        pos = round(float(bore.get("position_x", 0.0)) - offset, 3)
        out.append({
            "feature_id": _next_feature_id(counter),
            "feature_type": "bore",
            "positioning_mode": "global_absolute",
            "global_position_mm": pos,
            "processing_timing": "undecided",
            "bore_diameter_mm": round(2.0 * radius, 3),
            "bore_length_mm": round(length, 3),
            "bore_through": True,  # judged as a through hole by feature extraction (default)
        })

    # Eccentric/patterned axial holes: merge by (axial position, diameter) into
    # hole features, with the hole count in hole_count
    groups: Dict[Tuple[float, float], List[Dict[str, Any]]] = {}
    for bore in eccentric:
        key = (round(float(bore.get("position_x", 0.0)), 1),
               round(2.0 * float(bore.get("radius", 0.0)), 1))
        groups.setdefault(key, []).append(bore)
    for (z_pos, dia), items in sorted(groups.items()):
        feature = {
            "feature_id": _next_feature_id(counter),
            "feature_type": "hole",
            "positioning_mode": "global_absolute",
            "global_position_mm": round(z_pos - offset, 3),
            "processing_timing": "undecided",
            "hole_diameter_mm": dia,
            "hole_type": "through",
            "hole_direction": "axial",
            "hole_count": len(items) if len(items) > 1 else None,
        }
        out.append(feature)
        warnings.append(
            f"Eccentric/patterned axial hole Ø{dia}mm x {len(items)} @ z={round(z_pos - offset, 3)}mm"
            " (extracted from eccentric bore faces); please confirm hole type/depth."
        )
    return out


# ----------------------------------------------------------------------------
# 3. Blank and global requirements
# ----------------------------------------------------------------------------

def _build_blank(features_json: Dict[str, Any], segments: List[Dict[str, Any]],
                 warnings: List[str]) -> Tuple[str, float, Optional[float]]:
    """Bore -> hollow blank; the blank diameter defaults to max finished diameter + allowance."""
    dims = features_json.get("overall_dimensions", {}) or {}
    max_dia = dims.get("max_diameter", 0.0) or 0.0
    seg_max = max((s["diameter_mm"] for s in segments), default=0.0)
    blank_dia = round(max(max_dia, seg_max) + BLANK_ALLOWANCE_MM, 1)

    # The blank inner diameter only considers concentric bores along the main
    # axis; eccentric/patterned holes (gear web holes) do not contribute
    bores = [
        b for b in (_get_features(features_json).get("inner_bore", []) or [])
        if (b.get("pitch_radius") or 0.0) <= 0.5
    ]
    if bores:
        # Blank inner diameter takes the main bore (segment with max radius);
        # the stepped bore shape is fully expressed by the bore features.
        main_bore = max(bores, key=lambda b: b.get("radius", 0.0))
        inner_dia = round(2.0 * float(main_bore["radius"]), 3)
        if inner_dia >= blank_dia:
            warnings.append(
                f"Bore diameter {inner_dia}mm is not smaller than blank outer diameter {blank_dia}mm; "
                "please confirm the blank parameters manually."
            )
        return "hollow", blank_dia, inner_dia

    return "solid", blank_dia, None


def _build_global_requirements() -> Dict[str, Any]:
    """Global requirement defaults (engineering-intent fields are left for the LLM/user to confirm)."""
    return {
        "heat_treatment": "none",
        "heat_treatment_note": None,
        "target_hardness_hrc": None,
        "case_depth_mm": None,
        "blank_condition": "unknown",
        "pre_heat_treatment": "auto",
        "surface_treatment": "none",
        "batch_quantity": 1,
    }


# ----------------------------------------------------------------------------
# 4. Main mapping entry
# ----------------------------------------------------------------------------

def map_features_to_planning_request(
    features_json: Dict[str, Any],
    material: Optional[str] = None,
) -> Dict[str, Any]:
    """Map features_json to a peagent PlanningRequest draft.

    Args:
        features_json: cad_agent ``/extract_features`` output.
        material: optional material suggestion; None means the user must choose.

    Returns:
        {
            "planning_request": {...},   # peagent PlanningRequest-compatible structure
            "confidence": {...},         # confidence annotations per field
            "warnings": [...],           # mapping hints / items to confirm
        }
    """
    warnings: List[str] = []
    feats = _get_features(features_json)
    offset = _compute_offset(features_json)

    segments, seg_warnings = _build_segments(features_json, offset)
    warnings.extend(seg_warnings)

    counter = [0]
    features: List[Dict[str, Any]] = []
    features.extend(_build_keyway_features(feats, offset, counter, warnings))
    features.extend(_build_hole_features(feats, offset, counter, warnings))
    features.extend(_build_spline_feature(feats, offset, counter, warnings))
    features.extend(_build_gear_features(feats, offset, counter, warnings))
    features.extend(_build_bore_features(feats, offset, counter, warnings))

    # Fallback: global absolute positioning requires position >= 0; any residual
    # negative position is reset to 0 and flagged for verification
    for f in features:
        gp = f.get("global_position_mm")
        if gp is not None and gp < 0:
            warnings.append(
                f"{f.get('feature_id')} axial position {gp}mm is abnormal (extraction reference offset); "
                "reset to 0, please verify."
            )
            f["global_position_mm"] = 0.0

    blank_type, blank_diameter_mm, blank_inner_diameter_mm = _build_blank(features_json, segments, warnings)

    planning_request: Dict[str, Any] = {
        "material": material if material else "",
        "blank_type": blank_type,
        "blank_diameter_mm": blank_diameter_mm,
        "blank_inner_diameter_mm": blank_inner_diameter_mm,
        "segments": segments,
        "features": features,
        "global_requirements": _build_global_requirements(),
        # Supplementary info from CAD extraction (round-trips with the request, never lost)
        "main_axis": features_json.get("main_axis"),
        "cad_statistics": features_json.get("statistics"),
    }

    confidence = {
        "geometry": "high",
        "blank": "suggested",
        "material": "suggested" if material else "required",
        "tolerances": "suggested",
        "roughness": "suggested",
        "heat_treatment": "suggested",
    }

    return {
        "planning_request": planning_request,
        "confidence": confidence,
        "warnings": warnings,
    }


# ----------------------------------------------------------------------------
# 5. Strict validation against the real peagent models (optional)
# ----------------------------------------------------------------------------

def validate_with_peagent(
    planning_request: Dict[str, Any],
    peagent_backend_dir: Optional[str] = None,
    fallback_material: str = "45",
) -> Tuple[Optional[Any], List[str]]:
    """Validate the mapping result against peagent's ``PlanningRequest`` Pydantic model.

    Imports the real models after injecting the peagent backend directory into
    sys.path. If the import fails, returns (None, [error messages]) without
    raising.

    Returns:
        (validated_model or None, list of validation errors/warnings)
    """
    import sys

    if peagent_backend_dir is None:
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]  # ShaftPlanner/
        peagent_backend_dir = str(root / "backend")

    errors: List[str] = []
    try:
        if peagent_backend_dir not in sys.path:
            sys.path.insert(0, peagent_backend_dir)
        from models.workflow import PlanningRequest
    except Exception as exc:  # pragma: no cover - environment-independent failure
        errors.append(f"Failed to import peagent PlanningRequest: {exc}")
        return None, errors

    candidate = dict(planning_request)
    if not candidate.get("material"):
        candidate["material"] = fallback_material  # only for structural validation, not a final value

    try:
        model = PlanningRequest.model_validate(candidate)
        return model, []
    except Exception as exc:
        errors.append(str(exc))
        return None, errors


# ----------------------------------------------------------------------------
# 6. CLI entry point (quick verification)
# ----------------------------------------------------------------------------

def main() -> None:  # pragma: no cover - debug entry point
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Verify features_json -> PlanningRequest mapping")
    parser.add_argument("features_json", help="cad_agent feature extraction JSON file path")
    parser.add_argument("--material", default=None, help="material suggestion value")
    args = parser.parse_args()

    with open(args.features_json, "r", encoding="utf-8") as f:
        features_json = json.load(f)

    result = map_features_to_planning_request(features_json, material=args.material)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    model, errors = validate_with_peagent(result["planning_request"])
    if model is not None:
        print("\n[OK] Mapping result passed peagent PlanningRequest validation")
    else:
        print("\n[FAIL] Validation failed:")
        for err in errors:
            print("  -", err)


if __name__ == "__main__":
    main()
