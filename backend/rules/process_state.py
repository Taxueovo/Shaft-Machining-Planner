"""Bounded symbolic checks over declared operations, stock and drawing references.

Unknown dimensions stay unknown. This checks a planning model, not machining physics.
"""

from __future__ import annotations

from models.process import ProcessOperation

POLICY_VERSION = "process-state-v1"
POST_HEAT_FINISH = {
    "finish",
    "precision_finish",
    "precision_feature",
    "feature_after_heat",
    "feature_before_inspection",
}


def verify_process_states(
    request: dict, route: list[dict], heat_decision: dict | None = None
) -> dict:
    decision = heat_decision or {}
    stock_od = request["blank_diameter_mm"]
    stock_id = (
        request.get("blank_inner_diameter_mm") if request.get("blank_type") == "hollow" else None
    )
    segments = {s["segment_id"]: s for s in request.get("segments", [])}
    features = {f["feature_id"]: f for f in request.get("features", [])}
    known_ids = set(segments) | set(features)
    issues, steps, warnings = [], [], []
    dimensions = {}
    treated, datum_ready, inspected, packaged = False, True, False, False

    def issue(code, op, message, object_id=None):
        issues.append(
            {
                "error_code": code,
                "object_id": object_id or str(op.operation_no),
                "message": f"Operation {op.operation_no}: {message}",
                "severity": "error",
            }
        )

    for item in route:
        try:
            op = ProcessOperation.model_validate(item)
        except ValueError:
            issues.append(
                {
                    "error_code": "STATE_OPERATION_INVALID",
                    "severity": "error",
                    "object_id": str(item.get("operation_no")),
                    "message": "Operation does not satisfy the structured operation contract.",
                }
            )
            continue
        stage = op.stage.value
        if inspected and stage not in {"inspection", "packaging"}:
            issue(
                "MODIFICATION_AFTER_FINAL_INSPECTION",
                op,
                "Part changes after final inspection; move final inspection after all modifications.",
            )
        if packaged and stage != "packaging":
            issue("OPERATION_AFTER_PACKAGING", op, "An operation follows packaging.")
        if stage == "datum_recovery":
            if not treated:
                issue(
                    "DATUM_RECOVERY_WITHOUT_HEAT",
                    op,
                    "Post-treatment datum recovery has no preceding main heat treatment.",
                )
            else:
                datum_ready = True
        if stage in POST_HEAT_FINISH and treated and not datum_ready:
            issue(
                "FINISH_WITHOUT_DATUM_RECOVERY",
                op,
                "The treatment decision requires datum recovery before post-treatment finishing.",
            )
        if stage == "heat_treatment" and op.name == "Heat Treatment":
            treated = True
            datum_ready = not decision.get("requires_datum_recovery", True)
        if op.name == "Final Inspection":
            inspected = True
        if stage == "packaging":
            if not inspected:
                issue(
                    "PACKAGING_WITHOUT_FINAL_INSPECTION",
                    op,
                    "Final inspection is missing before packaging.",
                )
            packaged = True

        seen = set()
        for dim in op.dimensions:
            key = (dim.object_id, dim.surface)
            if dim.object_id not in known_ids:
                issue(
                    "UNKNOWN_DIMENSION_OBJECT",
                    op,
                    "Dimension refers to no input segment or feature.",
                    dim.object_id,
                )
                continue
            if key in seen:
                issue(
                    "DUPLICATE_SURFACE_TRANSITION",
                    op,
                    "A surface has multiple transitions in one operation.",
                    dim.object_id,
                )
            seen.add(key)
            previous = dimensions.get(key)
            if (
                previous is not None
                and dim.before_mm is not None
                and abs(dim.before_mm - previous) > 1e-9
            ):
                issue(
                    "DIMENSION_STATE_DISCONTINUITY",
                    op,
                    "Incoming size differs from the preceding explicit state.",
                    dim.object_id,
                )
            incoming = previous if previous is not None else dim.before_mm
            if incoming is not None and (
                (dim.surface == "external" and dim.after_mm > incoming + 1e-9)
                or (dim.surface == "internal" and dim.after_mm < incoming - 1e-9)
            ):
                issue(
                    "MATERIAL_REMOVAL_REVERSED",
                    op,
                    "The declared transition reverses material removal.",
                    dim.object_id,
                )
            if (
                dim.surface == "external"
                and max(dim.after_mm, dim.before_mm or 0) > stock_od + 1e-9
            ):
                issue(
                    "DIMENSION_OUTSIDE_STOCK",
                    op,
                    "External dimension exceeds the stock envelope.",
                    dim.object_id,
                )
            if (
                dim.surface == "internal"
                and stock_id is not None
                and (
                    dim.after_mm < stock_id - 1e-9
                    or (dim.before_mm is not None and dim.before_mm < stock_id - 1e-9)
                )
            ):
                issue(
                    "BORE_SMALLER_THAN_STOCK",
                    op,
                    "Cutting cannot shrink the existing stock bore.",
                    dim.object_id,
                )
            if incoming is None:
                warnings.append(
                    f"Operation {op.operation_no}: incoming size of {dim.object_id}/{dim.surface} is unknown."
                )
            dimensions[key] = dim.after_mm
        steps.append(
            {
                "operation_no": op.operation_no,
                "treated": treated,
                "datum_ready": datum_ready,
                "finally_inspected": inspected,
                "packaged": packaged,
            }
        )

    if not dimensions:
        warnings.append(
            "No explicit diameter transitions supplied; dimensional state coverage is incomplete."
        )
    return {
        "policy_version": POLICY_VERSION,
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "steps": steps,
        "explicit_surface_count": len(dimensions),
        "scope": "Declared process states only; fixtures, cutting physics and production release remain unverified.",
    }
