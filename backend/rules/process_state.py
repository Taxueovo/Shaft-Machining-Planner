"""Bounded symbolic checks over declared operations, stock and drawing references.

Unknown dimensions stay unknown. This checks a planning model, not machining physics.
"""

from __future__ import annotations

from models.process import ProcessOperation

POLICY_VERSION = "process-state-v2"
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
    issues, steps, warnings, counterexamples = [], [], [], []
    dimensions = {}
    targets = {}
    for sid, segment in segments.items():
        nominal = segment["diameter_mm"]
        targets[(sid, "external")] = (
            nominal + segment["diameter_lower_deviation_mm"]
            if segment.get("diameter_lower_deviation_mm") is not None
            else None,
            nominal + segment["diameter_upper_deviation_mm"]
            if segment.get("diameter_upper_deviation_mm") is not None
            else None,
        )
    for fid, feature in features.items():
        if feature.get("feature_type") == "bore" and feature.get("bore_diameter_mm"):
            # The input contract currently has a nominal bore diameter only.
            targets[(fid, "internal")] = (None, None)
    for key, bounds in targets.items():
        if None in bounds:
            warnings.append(
                f"Drawing limits incomplete for {key[0]}/{key[1]}; unknown limits are not enforced."
            )
    treated, datum_ready, inspected, packaged = False, True, False, False

    def issue(code, op, message, object_id=None, expected=None, actual=None):
        issues.append(
            {
                "error_code": code,
                "object_id": object_id or str(op.operation_no),
                "message": f"Operation {op.operation_no}: {message}",
                "severity": "error",
                "operation_no": op.operation_no,
                "expected": expected,
                "actual": actual,
            }
        )
        counterexamples.append(
            {
                "constraint_id": code,
                "operation_no": op.operation_no,
                "object_id": object_id,
                "expected": expected,
                "actual": actual,
                "message": message,
                "preceding_operation_nos": [step["operation_no"] for step in steps],
                "state_before": dict(state_before),
                "evidence_ids": ["input", "route", "heat_decision"],
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
        state_before = {
            "treated": treated,
            "datum_ready": datum_ready,
            "finally_inspected": inspected,
            "packaged": packaged,
        }
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
            if (dim.object_id in segments and dim.surface != "external") or (
                features.get(dim.object_id, {}).get("feature_type") == "bore"
                and dim.surface != "internal"
            ):
                issue(
                    "DRAWING_SURFACE_MISMATCH",
                    op,
                    "Declared surface does not match the input segment or bore.",
                    dim.object_id,
                    {"surface": "external" if dim.object_id in segments else "internal"},
                    {"surface": dim.surface},
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
            if key in targets:
                lower, upper = targets[key]
                overcut = (
                    dim.surface == "external" and lower is not None and dim.after_mm < lower - 1e-9
                ) or (
                    dim.surface == "internal" and upper is not None and dim.after_mm > upper + 1e-9
                )
                if overcut:
                    issue(
                        "DRAWING_MATERIAL_OVERCUT",
                        op,
                        "Declared removal passes the drawing limit; later cutting cannot restore this surface.",
                        dim.object_id,
                        {"surface": dim.surface, "lower_mm": lower, "upper_mm": upper},
                        {"after_mm": dim.after_mm},
                    )
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
        if op.name == "Final Inspection":
            for key, value in dimensions.items():
                if key in targets:
                    lower, upper = targets[key]
                    if (lower is not None and value < lower - 1e-9) or (
                        upper is not None and value > upper + 1e-9
                    ):
                        issue(
                            "FINAL_DIAMETER_OUTSIDE_DRAWING",
                            op,
                            "Declared diameter at final inspection is outside the input drawing limits.",
                            key[0],
                            {"surface": key[1], "lower_mm": lower, "upper_mm": upper},
                            {"diameter_mm": value},
                        )
            inspected = True
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
    missing_targets = sorted(
        object_id + "/" + surface
        for object_id, surface in targets
        if (object_id, surface) not in dimensions
    )
    if missing_targets:
        warnings.append(
            "Drawing surfaces without explicit transitions: " + ", ".join(missing_targets)
        )
    return {
        "policy_version": POLICY_VERSION,
        "passed": not issues,
        "issues": issues,
        "warnings": warnings,
        "steps": steps,
        "explicit_surface_count": len(dimensions),
        "counterexamples": counterexamples,
        "drawing_surface_count": len(targets),
        "missing_drawing_surfaces": missing_targets,
        "scope": "Declared process states only; fixtures, cutting physics and production release remain unverified.",
    }
