"""Input-only manufacturing geometry checks, shared by API and workflow."""

from __future__ import annotations

import math
from typing import Any


def validate_manufacturing_geometry(request: dict[str, Any]) -> None:
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("All dimensions must be finite.")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)

    finite(request)
    segments = request["segments"]
    spans = []
    cursor = 0.0
    blank_od = float(request["blank_diameter_mm"])
    stock_id = (
        float(request.get("blank_inner_diameter_mm") or 0)
        if request.get("blank_type") == "hollow"
        else 0
    )
    for s in segments:
        diameter = float(s["diameter_mm"])
        if stock_id >= diameter:
            raise ValueError(f"{s['segment_id']}: stock bore must be smaller than finished OD.")
        upper, lower = s.get("diameter_upper_deviation_mm"), s.get("diameter_lower_deviation_mm")
        if upper is not None and lower is not None and upper < lower:
            raise ValueError(f"{s['segment_id']}: inverted diameter tolerance.")
        if diameter + max(0, upper or 0) > blank_od:
            raise ValueError(f"{s['segment_id']}: finished OD envelope exceeds stock.")
        spans.append((cursor, cursor + s["length_mm"], diameter))
        cursor += s["length_mm"]
    length_fields = {
        "bore": "bore_length_mm",
        "flange": "flange_thickness_mm",
        "gear_teeth": "gear_face_width_mm",
        "taper": "taper_length_mm",
        "groove": "groove_width_mm",
    }
    for f in request.get("features", []):
        fid = f["feature_id"]
        if f["positioning_mode"] == "segment_relative":
            index = f.get("segment_index", 0)
            if not index or index > len(spans):
                raise ValueError(f"{fid}: invalid segment reference.")
            start, end, _ = spans[index - 1]
            pos = start + float(f["segment_offset_mm"])
        else:
            pos, end = float(f["global_position_mm"]), cursor
        length = float(f.get(length_fields.get(f["feature_type"], "feature_length_mm")) or 0)
        if pos < 0 or pos > end or pos + length > end + 1e-9:
            raise ValueError(f"{fid}: feature extent exceeds valid axial range.")
        for key in (
            "flange_diameter_mm",
            "gear_outer_diameter_mm",
            "worm_outer_diameter_mm",
            "taper_large_diameter_mm",
            "bearing_seat_diameter_mm",
            "seal_diameter_mm",
        ):
            if f.get(key) and f[key] > blank_od:
                raise ValueError(f"{fid}: {key} exceeds stock diameter.")
        if f["feature_type"] == "bore":
            bore = f["bore_diameter_mm"]
            if bore < stock_id:
                raise ValueError(f"{fid}: finished bore cannot be smaller than stock bore.")
            affected = [d for a, b, d in spans if a < pos + length and b > pos]
            if affected and bore >= min(affected):
                raise ValueError(f"{fid}: bore leaves no wall in an intersected segment.")


def dimension_route_errors(route: list[dict]) -> list[str]:
    """Validate explicit transitions and continuity independently of model reviews."""
    from models.process import ProcessOperation

    errors, last = [], {}
    for operation in route:
        try:
            op = ProcessOperation.model_validate(operation)
        except ValueError as exc:
            errors.append(f"Operation {operation.get('operation_no')}: {exc}")
            continue
        for dim in op.dimensions:
            key = (dim.object_id, dim.surface)
            previous = last.get(key)
            if previous is not None:
                if dim.before_mm is not None and abs(dim.before_mm - previous) > 1e-9:
                    errors.append(f"{dim.object_id}: discontinuous operation dimensions.")
                if (dim.surface == "internal" and dim.after_mm < previous) or (
                    dim.surface == "external" and dim.after_mm > previous
                ):
                    errors.append(f"{dim.object_id}: material removal direction is reversed.")
            last[key] = dim.after_mm
    return errors
