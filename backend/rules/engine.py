"""Rule engine: process route generation."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .constants import (
    FEATURE_NAME, FEATURE_PROCESS, FEATURE_SUPPORTS_SPLIT,
    HEAT_NAME, SURFACE_NAME, get_material_properties, requires_grinding,
)

logger = logging.getLogger(__name__)


def add_operation(
    operations: list[dict[str, Any]], name: str, stage: str, description: str,
    process_category: Optional[str], feature_id: Optional[str] = None, conditional: bool = False,
) -> None:
    operations.append({
        "operation_no": 0, "name": name, "stage": stage, "description": description,
        "process_category": process_category, "feature_id": feature_id, "conditional": conditional,
    })


# ============================================================
# Turning allowance lookup (turning_process.md external turning allowance table, length <= 200 mm band)
# ============================================================

_ALLOWANCE_ROUGH_MM = [1.5, 1.5, 2.0, 2.0, 2.3, 2.5, 2.5, 2.8]
_ALLOWANCE_FINISH_MM = [0.8, 1.0, 1.3, 1.4, 1.5, 1.5, 1.8, 2.0]
_ALLOWANCE_BOUNDS_MM = [10, 18, 30, 50, 80, 120, 180, 260]


def _allowance(diameter_mm: float, *, finish: bool) -> float:
    """Diameter-based rough/finish turning allowance (mm); values beyond the table use the top band."""
    table = _ALLOWANCE_FINISH_MM if finish else _ALLOWANCE_ROUGH_MM
    for idx, bound in enumerate(_ALLOWANCE_BOUNDS_MM):
        if diameter_mm <= bound:
            return table[idx]
    return table[-1]


def _allowance_text(diameter_mm: float, *, finish: bool) -> str:
    return f"{_allowance(diameter_mm, finish=finish):.1f} mm"


def _get_feature_operation(feature_type: str, is_split: bool) -> str:
    """Get the feature operation name before heat treatment."""
    return {
        "keyway": "Rough mill keyway" if is_split else "Mill keyway",
        "hole": "Pre-drill hole" if is_split else "Drill hole",
        "flat": "Rough mill flat" if is_split else "Mill flat",
        "thread": "Rough turn thread" if is_split else "Turn thread",
        "knurl": "Knurl",
        "bearing_seat": "Rough turn bearing seat" if is_split else "Turn bearing seat",
        "spline": "Rough hob spline" if is_split else "Hob spline",
        "taper": "Rough turn taper" if is_split else "Turn taper",
        "groove": "Turn groove",
        "seal_area": "Rough turn seal area" if is_split else "Turn seal area",
        "gear_teeth": "Rough hob gear" if is_split else "Hob gear",
        "flange": "Turn flange",
        "bore": "Rough bore" if is_split else "Bore",
        "cam": "Rough turn cam",
        "worm": "Rough turn worm spiral",
        "crank_pin": "Rough turn crank pin",
    }[feature_type]


def _get_finish_operation(feature_type: str) -> str:
    """Get the hard-finishing operation name after heat treatment."""
    return {
        "keyway": "Precision grind keyway", "hole": "Ream hole", "flat": "Grind flat",
        "thread": "Thread grinding", "bearing_seat": "Precision grind bearing seat",
        "spline": "Precision grind spline", "taper": "Precision grind taper",
        # 以车代磨: after quenching, seal areas are hard-turned with CBN instead of ground (cnc_machining.md);
        # bearing seats keep grinding.
        "seal_area": "Finish hard turn seal area", "gear_teeth": "Precision grind gear teeth",
        "bore": "Finish bore",
        "cam": "Precision grind cam",
        "worm": "Precision grind worm",
        "crank_pin": "Precision grind crank pin",
    }[feature_type]


def _get_post_finish_operation(feature_type: str, high_precision: bool) -> tuple[str, Optional[str]]:
    """Get the final-machining operation and its resource category after the finished-part datum is established."""
    standard = {
        "keyway": ("Mill keyway", "Indexable Milling"),
        "hole": ("Drill hole", "Drilling"),
        "flat": ("Mill flat", "Indexable Milling"),
        "thread": ("Turn thread", "Threading"),
        "bearing_seat": ("Finish turn bearing seat", "ISO Turning"),
        "taper": ("Finish turn taper", "Taper Turning"),
        "groove": ("Turn groove", "Grooving"),
        "seal_area": ("Finish turn seal area", "ISO Turning"),
        "flange": ("Finish turn flange", "ISO Turning"),
        "bore": ("Finish bore", "Boring"),
        "cam": ("Finish turn cam", "ISO Turning"),
        "worm": ("Finish turn worm spiral", "Threading"),
        "crank_pin": ("Finish turn crank pin", "ISO Turning"),
    }
    precision = {
        "keyway": ("Precision mill keyway", "Indexable Milling"),
        "hole": ("Ream hole", "Drilling"),
        "flat": ("Finish mill flat", "Indexable Milling"),
        "thread": ("Finish turn thread", "Threading"),
        "bearing_seat": ("Precision grind bearing seat", "Cylindrical Grinding"),
        "taper": ("Finish turn taper", "Taper Turning"),
        "seal_area": ("Precision grind seal area", "Cylindrical Grinding"),
        "flange": ("Finish turn flange", "ISO Turning"),
        "bore": ("Finish bore", "Boring"),
        "cam": ("Precision grind cam", "Cam Grinding"),
        "worm": ("Finish grind worm", "Worm Grinding"),
        "crank_pin": ("Precision grind crank pin", "Cylindrical Grinding"),
    }
    return (precision if high_precision else standard)[feature_type]


def _hard_finish_process(feature_type: str) -> Optional[str]:
    """Resource category for hard finishing after heat treatment; processes not in the
    library are explicitly kept as not_covered (more honest than silently skipping)."""
    return {
        "keyway": "Indexable Milling", "hole": "Drilling", "flat": "Indexable Milling",
        "thread": "Thread Grinding", "bearing_seat": "Cylindrical Grinding",
        "spline": "Gear Grinding", "taper": "Cylindrical Grinding",
        # 以车代磨: quenched seal areas are hard-turned (ISO Turning / CBN), not ground.
        "seal_area": "ISO Turning", "gear_teeth": "Gear Grinding",
        "bore": "Boring",
        "cam": "Cam Grinding", "worm": "Worm Grinding", "crank_pin": "Cylindrical Grinding",
    }.get(feature_type)


def _get_pre_heat_process(feature_type: str) -> Optional[str]:
    """Actual resource category for operations before heat treatment."""
    return {
        "bearing_seat": "ISO Turning",
        "seal_area": "ISO Turning",
        "flange": "ISO Turning",
    }.get(feature_type, FEATURE_PROCESS[feature_type])


def _feature_position_desc(feature: dict[str, Any]) -> str:
    """Feature position description: axial position + hole feature extras (hole count/direction)."""
    desc = f"global position {feature['global_position_mm']} mm"
    if feature.get("feature_type") == "hole":
        extras = []
        if feature.get("hole_count"):
            extras.append(f"{feature['hole_count']} holes")
        if feature.get("hole_direction"):
            extras.append(str(feature["hole_direction"]))
        if extras:
            desc += "; " + ", ".join(extras)
    return desc


def _is_main_bore_covered(feature: dict[str, Any], is_hollow: bool, inner_dia: Any) -> bool:
    """Whether the main bore of a hollow blank is already covered by Blank-stage rough/finish boring.

    For a hollow blank, Blank stage already schedules "Rough Boring / Finish Boring" (bored to inner_dia);
    scheduling feature-level boring for a bore feature of the same diameter would duplicate it, so it is skipped here.
    Steps of the stepped bore with a different (smaller) diameter are unaffected and are still scheduled normally.
    """
    if not is_hollow or feature.get("feature_type") != "bore":
        return False
    try:
        return abs(float(feature.get("bore_diameter_mm", 0.0)) - float(inner_dia)) < 0.5
    except (TypeError, ValueError):
        return False


def build_route(request: dict[str, Any], geometry: dict[str, Any], choices: dict[str, str]) -> list[dict[str, Any]]:
    """Generate process route - pure rule engine with a fixed if-else strategy."""
    heat = request["global_requirements"]["heat_treatment"]
    # Carburize-quench with gears -> use the real production route (turning first + carburize-quench chain + gear/OD grinding)
    if heat == "carburize_quench" and any(
        f.get("feature_type") == "gear_teeth" for f in geometry["features"]
    ):
        return _build_carburized_gear_shaft_route(request, geometry, choices)
    # Feature-driven dispatch: cam/crank pin/worm and nitriding/induction-hardening use their own production routes.
    # When heat == "none" it falls back to the generic route (new features are registered in the helper dicts below as a safe fallback).
    if heat != "none":
        if any(f.get("feature_type") == "cam" for f in geometry["features"]):
            return _build_camshaft_route(request, geometry, choices)
        if any(f.get("feature_type") == "crank_pin" for f in geometry["features"]):
            return _build_crankshaft_route(request, geometry, choices)
        if any(f.get("feature_type") == "worm" for f in geometry["features"]):
            return _build_worm_shaft_route(request, geometry, choices)
        if heat in ("nitriding", "induction_hardening"):
            return _build_surface_hardened_shaft_route(request, geometry, choices)

    operations: list[dict[str, Any]] = []
    heat = request["global_requirements"]["heat_treatment"]
    surface = request["global_requirements"]["surface_treatment"]
    has_heat = heat != "none"
    heat_plan = request.get("heat_treatment_plan", {})
    pre_treatment = heat_plan.get("pre_treatment")
    has_pre_treatment = bool(pre_treatment)
    material = request.get("material", "45")
    material_props = get_material_properties(material)

    # ---- 1. Basic operations (fixed order) ----
    blank_type = request.get("blank_type", "solid")
    inner_dia = request.get("blank_inner_diameter_mm")
    is_hollow = blank_type == "hollow" and inner_dia

    # The main bore of a hollow blank is covered by Blank-stage rough/finish boring: these two
    # operations carry that bore feature's feature_id, so the verification layer's Feature Coverage
    # can find the corresponding operation, and the process card clearly shows which operation machines the bore feature.
    main_bore_fid = None
    if is_hollow:
        for feature in geometry["features"]:
            if _is_main_bore_covered(feature, True, inner_dia):
                main_bore_fid = feature["feature_id"]
                break

    material_notes = ""
    if material_props["machinability"] == "difficult":
        material_notes = f" ({material} is difficult to machine, reduce cutting parameters)"
    elif material_props["machinability"] == "excellent":
        material_notes = f" ({material} is easy to cut, can increase cutting parameters)"

    max_finished_dia = float(geometry.get("max_finished_diameter_mm") or request["blank_diameter_mm"])
    rough_allowance = _allowance_text(max_finished_dia, finish=False)
    finish_allowance = _allowance_text(max_finished_dia, finish=True)
    # Slender shafts (L/D > 30) are deflection-prone and need straightening/stable cutting (cnc_machining.md / grinding_process.md).
    is_slender = geometry["total_length_mm"] / max(max_finished_dia, 1.0) > 30

    blank_desc = f"tube stock OD{request['blank_diameter_mm']}mm ID{inner_dia}mm" if is_hollow else f"bar stock, total length {geometry['total_length_mm']} mm"
    add_operation(operations, "Blanking", "blank",
                  f"Cut from {blank_desc}, reserve face allowance.", None)
    add_operation(operations, "Face Turning", "datum",
                  f"Turn both faces to establish axial datum.{material_notes}", "ISO Turning")
    add_operation(operations, "Center Drilling", "datum",
                  "Drill center holes at both ends for center clamping.", "Drilling")
    add_operation(operations, "Rough Turning", "rough",
                  f"Rough turn stepped profile with allowance (rough allowance ~{rough_allowance}).{material_notes}", "ISO Turning")

    # Hollow shaft: rough bore the inner diameter
    if is_hollow:
        add_operation(operations, "Rough Boring", "rough",
                      f"Rough bore inner diameter to {inner_dia + 1} mm with finishing allowance.",
                      "Boring", feature_id=main_bore_fid)

    add_operation(operations, "Semi-finish Turning", "semi_finish",
                  f"Semi-finish turn segments with finishing allowance (finish allowance ~{finish_allowance}).{material_notes}", "ISO Turning")

    # Stabilization/aging: relieve stress after semi-finish turning and before final heat treatment
    # (typical operation for motor shafts etc., scheduled per drawing requirements)
    if has_heat:
        add_operation(operations, "Stabilization / Aging", "semi_finish",
                      "Stabilization / aging heat treatment to relieve residual stress before finishing (per drawing).",
                      "Heat Treatment", conditional=True)

    # ---- 2. Pre heat treatment (only scheduled when the heat-treatment decision explicitly requires it) ----
    # Quench-temper no longer inserts normalizing by default; normalizing/annealing/stress relief
    # are decided by blank condition and drawing requirements.
    if has_pre_treatment:
        add_operation(
            operations,
            pre_treatment["name"],
            "pre_heat_treatment",
            pre_treatment["description"],
            "Heat Treatment",
            conditional=True,
        )

    # ---- 3. Rough machining / soft-state machining before heat treatment ----
    # Gear teeth and splines must be machined in the soft state; knurling must also
    # be avoided on hardened surfaces. The remaining features are located off the finished
    # OD/faces after finish turning and are moved to the final-machining stage.
    pre_heat_features = {"spline", "gear_teeth", "knurl"}
    split_features: set[str] = set()
    for feature in geometry["features"]:
        feature_type = feature["feature_type"]
        feature_id = feature["feature_id"]
        # The main bore of a hollow blank is already covered by Blank-stage rough/finish boring; skip feature-level boring to avoid duplication
        if _is_main_bore_covered(feature, is_hollow, inner_dia):
            continue
        high = feature["high_precision"]
        can_split = FEATURE_SUPPORTS_SPLIT.get(feature_type, False)
        timing = choices.get(feature_id, feature.get("processing_timing", "undecided"))

        # In live preview without manual choices, splittable high-precision features use the recommended
        # "rough machining before heat treatment + finishing after heat treatment" split.
        if high and has_heat and timing == "undecided":
            timing = "before_and_after_heat_treatment" if can_split else "before_heat_treatment"
        is_split = high and has_heat and can_split and timing == "before_and_after_heat_treatment"
        needs_pre_heat = is_split or (has_heat and feature_type in pre_heat_features) or (
            high and has_heat and timing == "before_heat_treatment"
        )
        if not needs_pre_heat:
            continue

        # When a pre heat treatment is explicitly scheduled, soft-state machining goes after it and before the final heat treatment.
        actual_stage = "pre_heat_treatment" if has_pre_treatment else "feature_before_heat"
        operation_name = _get_feature_operation(feature_type, is_split)
        add_operation(
            operations, operation_name, actual_stage,
            f"{feature_id} {FEATURE_NAME[feature_type]}, {_feature_position_desc(feature)}; "
            + ("Reserve finishing allowance." if is_split else "Process to input dimensions."),
            _get_pre_heat_process(feature_type), feature_id, True,
        )
        if is_split:
            split_features.add(feature_id)
        if feature_type == "flange" and int(feature.get("flange_holes") or 0) > 0:
            add_operation(
                operations, "Drill flange bolt holes", actual_stage,
                f"{feature_id} drill {int(feature['flange_holes'])} flange holes in the soft state; hole diameter and PCD require drawing confirmation.",
                "Drilling", feature_id, True,
            )

    # ---- 4. Final heat treatment and datum recovery ----
    if has_heat:
        note = request["global_requirements"].get("heat_treatment_note")
        description = heat_plan.get("description") or HEAT_NAME[heat]
        target_hardness = request["global_requirements"].get("target_hardness_hrc")
        case_depth = request["global_requirements"].get("case_depth_mm")
        requirements = []
        if target_hardness is not None:
            requirements.append(f"target {target_hardness:g} HRC")
        if case_depth is not None:
            requirements.append(f"effective case depth {case_depth:g} mm")
        if requirements:
            description += "; " + ", ".join(requirements)
        if note:
            description += f"; {note}"
        add_operation(operations, "Heat Treatment", "heat_treatment", description, "Heat Treatment")
        if heat_plan.get("requires_datum_recovery", True):
            add_operation(operations, "Repair Center Holes", "datum_recovery",
                          "Recover finishing datum after heat treatment.", None)

    # ---- 5. Finish turning (fixed) ----
    # Finish turning cuts the full stepped profile: shaft segments, transition fillets, end chamfers/lead-in tapers
    # are all turned in one pass (profile details without their own feature entries)
    add_operation(operations, "Finish Turning", "finish",
                  "Finish turn the full stepped profile to target dimensions, including fillets, chamfers and end tapers.", "ISO Turning")

    # Hollow shaft: finish bore the inner diameter
    if is_hollow:
        add_operation(operations, "Finish Boring", "finish",
                      f"Finish bore inner diameter to {inner_dia} mm.",
                      "Boring", feature_id=main_bore_fid)

    # ---- 6. Finish grinding (condition: tolerance <=0.01 or Ra <=0.4) ----
    grinding_segments = [
        item["segment_id"] for item in geometry["segments"]
        if requires_grinding(item.get("diameter_upper_deviation_mm"),
                             item.get("diameter_lower_deviation_mm"),
                             item.get("roughness_ra"))
    ]
    if grinding_segments:
        # Center holes are the grinding datum; lap them to >85% dead-center contact before finish grinding (grinding_process.md).
        add_operation(operations, "Center Hole Lapping", "precision_finish",
                      "Lap 60 degree center holes to reach >85% dead-center contact before finish grinding.",
                      None, conditional=True)
        add_operation(operations, "Finish Grind OD", "precision_finish",
                      "High-precision segment grinding: " + ", ".join(grinding_segments)
                      + " (finish grinding allowance 0.07-0.09 mm).",
                      "Cylindrical Grinding", conditional=True)

    # ---- 7. Hard finishing after heat treatment (split high-precision features + gears requiring post-heat finishing) ----
    post_heat_set = set(split_features)
    if has_heat:
        for feature in geometry["features"]:
            if feature["feature_type"] == "gear_teeth" and feature.get("gear_finish_required"):
                post_heat_set.add(feature["feature_id"])
    for feature in geometry["features"]:
        if feature["feature_id"] not in post_heat_set:
            continue
        feature_type = feature["feature_type"]
        feature_id = feature["feature_id"]
        operation_name = _get_finish_operation(feature_type)
        process = _hard_finish_process(feature_type)
        if feature_type == "gear_teeth" and feature.get("gear_finish_required"):
            description = f"{feature_id} {FEATURE_NAME[feature_type]}, post-heat gear finishing after hardening."
        else:
            description = f"{feature_id} {FEATURE_NAME[feature_type]}, ensure input tolerance and roughness."
        add_operation(
            operations, operation_name,
            "feature_after_heat",
            description,
            process, feature_id, True,
        )

    # ---- 8. Final machining after the finished-part datum is established ----
    # Keyways, holes, flats, bearing seats, tapers, grooves and seal areas have positional
    # relationships with the finished OD/faces, so they are machined after finish turning;
    # this way later OD finishing cannot destroy their size relationships.
    for feature in geometry["features"]:
        feature_type = feature["feature_type"]
        feature_id = feature["feature_id"]
        if feature_id in split_features:
            continue
        # The main bore of a hollow blank is already covered by Blank-stage finish boring; skip feature-level boring to avoid duplication
        if _is_main_bore_covered(feature, is_hollow, inner_dia):
            continue
        timing = choices.get(feature_id, feature.get("processing_timing", "undecided"))
        # Features explicitly chosen to be done before heat treatment, or gear teeth/splines/knurling that must
        # be done in the soft state, are not scheduled again here.
        if has_heat and (
            feature_type in pre_heat_features
            or (feature["high_precision"] and timing == "before_heat_treatment")
        ):
            continue
        if feature_type in pre_heat_features:
            # Without heat treatment, knurling goes after finish turning; gear teeth/splines get their final machining here.
            operation_name = _get_feature_operation(feature_type, False)
            process = FEATURE_PROCESS[feature_type]
        else:
            operation_name, process = _get_post_finish_operation(feature_type, feature["high_precision"])
        add_operation(
            operations, operation_name, "feature_before_inspection",
            f"{feature_id} {FEATURE_NAME[feature_type]}, {_feature_position_desc(feature)}; Process to final dimensions.",
            process, feature_id, True,
        )
        if feature_type == "flange" and int(feature.get("flange_holes") or 0) > 0:
            add_operation(
                operations, "Drill flange bolt holes", "feature_before_inspection",
                f"{feature_id} drill {int(feature['flange_holes'])} flange holes after the flange face and OD are finished; hole diameter and PCD require drawing confirmation.",
                "Drilling", feature_id, True,
            )

    # ---- 9. Finishing steps after the finished-part datum is established (straightening / dynamic balancing, per drawing, conditional) ----
    straighten_desc = (
        "Slender shaft (L/D>30): straighten and control bending within 0.15 mm per 1000 mm after machining/heat treatment."
        if is_slender else
        "Straighten if distortion exceeds tolerance after machining/heat treatment."
    )
    add_operation(operations, "Straighten", "feature_before_inspection",
                  straighten_desc,
                  None, conditional=True)
    add_operation(operations, "Dynamic Balancing", "feature_before_inspection",
                  "Dynamic balancing for high-speed / dynamically balanced shafts.",
                  None, conditional=True)

    # ---- 10. Chamfering/deburring (fixed, after all finishing, before surface treatment) ----
    add_operation(operations, "Chamfer & Deburr", "deburr",
                  "Chamfer sharp edges and deburr all machined features (fillets, end bevels, edges) per drawing.", None)

    # ---- 10. Surface treatment (conditional) ----
    if surface != "none":
        add_operation(operations, "Surface Treatment", "surface_treatment",
                      SURFACE_NAME[surface], None, conditional=True)

    # ---- 11. Final inspection (fixed) ----
    add_operation(operations, "Final Inspection", "inspection",
                  "Check segment dimensions, feature positions, tolerance, roughness and appearance.", None)

    # ---- 12. Cleaning and packaging (fixed) ----
    add_operation(operations, "Cleaning", "packaging",
                  "Clean part to remove cutting fluid, chips and contaminants.", None)
    add_operation(operations, "Packaging", "packaging",
                  "Apply rust preventive oil and package for shipment.", None)

    for index, operation in enumerate(operations, start=1):
        operation["operation_no"] = index
    return operations


def _build_carburized_gear_shaft_route(
    request: dict[str, Any], geometry: dict[str, Any], choices: dict[str, str],
) -> list[dict[str, Any]]:
    """Real production route for a carburized-quenched gear shaft (including hollow blanks).

    Based on the actual production route and process handbooks (typical carburized gear shaft chain):
    turning (soft state, completed before quenching, including finish turning) -> gear hobbing +
    gear chamfering (soft state) -> pre-clean -> carburize and quench -> clean quench oil ->
    temper -> shot blast -> heat-treatment inspection -> center-hole chamfer grinding ->
    external cylindrical grinding -> shot peening -> gear grinding -> laser marking ->
    magnetic particle inspection (MPI) -> appearance check/rust prevention/packaging.
    After quenching the surface is hardened, so the OD and tooth flanks can only be ground, not finish turned.
    """
    operations: list[dict[str, Any]] = []
    heat = request["global_requirements"]["heat_treatment"]
    surface = request["global_requirements"]["surface_treatment"]
    heat_plan = request.get("heat_treatment_plan", {})
    material = request.get("material", "45")
    material_props = get_material_properties(material)
    blank_type = request.get("blank_type", "solid")
    inner_dia = request.get("blank_inner_diameter_mm")
    is_hollow = blank_type == "hollow" and inner_dia

    # feature_id of the main bore of a hollow blank (carried by Blank-stage rough/finish boring, avoids false Feature Coverage reports)
    main_bore_fid = None
    for feature in geometry["features"]:
        if _is_main_bore_covered(feature, is_hollow, inner_dia):
            main_bore_fid = feature["feature_id"]
            break

    material_notes = ""
    if material_props["machinability"] == "difficult":
        material_notes = f" ({material} is difficult to machine, reduce cutting parameters)"

    # ---- 1. Turning (soft state, all completed before carburizing; after quenching the hardened surface can only be ground) ----
    blank_desc = (
        f"tube stock OD{request['blank_diameter_mm']}mm ID{inner_dia}mm"
        if is_hollow else f"bar stock, total length {geometry['total_length_mm']} mm"
    )
    add_operation(operations, "Blanking", "blank",
                  f"Cut from purchased {blank_desc}, reserve machining allowance.", None)
    add_operation(operations, "Face Turning", "datum",
                  f"Turn both faces to establish axial datum.{material_notes}", "ISO Turning")
    add_operation(operations, "Center Drilling", "datum",
                  "Drill center holes at both ends for center clamping.", "Drilling")
    add_operation(operations, "Rough Turning", "rough",
                  f"Rough turn stepped profile with allowance (soft state).{material_notes}", "ISO Turning")
    if is_hollow:
        add_operation(operations, "Rough Boring", "rough",
                      f"Rough bore inner diameter to {inner_dia + 1} mm with finishing allowance.",
                      "Boring", feature_id=main_bore_fid)
    add_operation(operations, "Semi-finish Turning", "semi_finish",
                  f"Semi-finish turn segments with allowance (soft state).{material_notes}", "ISO Turning")
    add_operation(operations, "Finish Turning", "finish_before_heat",
                  "Finish turn stepped profile to near-final size before carburizing (soft state).",
                  "ISO Turning")
    if is_hollow:
        add_operation(operations, "Finish Boring", "finish_before_heat",
                      f"Finish bore inner diameter to {inner_dia} mm before carburizing.",
                      "Boring", feature_id=main_bore_fid)

    # ---- 2. Soft-state features: gear hobbing + gear chamfering; splines/knurling also before heat treatment ----
    pre_heat_features = {"spline", "knurl"}
    for feature in geometry["features"]:
        ftype = feature["feature_type"]
        if _is_main_bore_covered(feature, is_hollow, inner_dia):
            continue
        if ftype == "gear_teeth":
            add_operation(operations, "Hob gear", "feature_before_heat",
                          f"{feature['feature_id']} {FEATURE_NAME[ftype]}, {_feature_position_desc(feature)}; "
                          "soft hobbing, leave grinding allowance for post-heat gear grinding.",
                          FEATURE_PROCESS["gear_teeth"], feature["feature_id"], True)
            add_operation(operations, "Gear Chamfer", "feature_before_heat",
                          f"{feature['feature_id']} chamfer gear tooth edges before carburizing.",
                          None, feature["feature_id"], True)
        elif ftype == "worm":
            add_operation(operations, "Rough turn worm spiral", "feature_before_heat",
                          f"{feature['feature_id']} {FEATURE_NAME[ftype]}, {_feature_position_desc(feature)}; "
                          "rough machine the worm spiral profile in the soft state before carburizing.",
                          FEATURE_PROCESS.get(ftype), feature["feature_id"], True)
        elif ftype in pre_heat_features:
            op_name = _get_feature_operation(ftype, False)
            add_operation(operations, op_name, "feature_before_heat",
                          f"{feature['feature_id']} {FEATURE_NAME[ftype]}, {_feature_position_desc(feature)}; "
                          "process before heat treatment.",
                          FEATURE_PROCESS.get(ftype), feature["feature_id"], True)

    # ---- 3. Pre heat treatment (if any) -> pre-clean + carburize-quench chain (clean quench oil -> temper -> shot blast -> heat-treatment inspection) ----
    if heat_plan.get("pre_treatment"):
        pre_treatment = heat_plan["pre_treatment"]
        add_operation(operations, pre_treatment["name"], "pre_heat_treatment",
                      pre_treatment["description"], "Heat Treatment", conditional=True)
    add_operation(operations, "Pre-Clean", "heat_treatment",
                  "Pre-clean part to remove cutting fluid and chips before carburizing.",
                  None, conditional=True)
    description = heat_plan.get("description") or HEAT_NAME[heat]
    target_hardness = request["global_requirements"].get("target_hardness_hrc")
    case_depth = request["global_requirements"].get("case_depth_mm")
    requirements = []
    if target_hardness is not None:
        requirements.append(f"target {target_hardness:g} HRC")
    if case_depth is not None:
        requirements.append(f"effective case depth {case_depth:g} mm")
    if requirements:
        description += "; " + ", ".join(requirements)
    add_operation(operations, "Heat Treatment", "heat_treatment", description, "Heat Treatment")
    add_operation(operations, "Clean Quench Oil", "heat_treatment",
                  "Clean quench oil residue from the part after quenching.", None, conditional=True)
    add_operation(operations, "Temper", "heat_treatment",
                  "Low-temperature temper to relieve stress and stabilize structure.", "Heat Treatment", conditional=True)
    add_operation(operations, "Shot Blast", "heat_treatment",
                  "Shot blast to remove oxide scale and induce surface compressive stress.", None, conditional=True)
    add_operation(operations, "Heat-treatment Inspection", "heat_treatment",
                  "Inspect hardness, effective case depth and distortion after heat treatment.", None, conditional=True)

    # ---- 4. After heat treatment: center-hole grinding (datum recovery), OD grinding, shot peening, gear grinding ----
    add_operation(operations, "Center-hole Chamfer Grinding", "datum_recovery",
                  "Grind center-hole chamfers to recover the finishing datum after heat treatment.",
                  "Cylindrical Grinding", conditional=True)
    add_operation(operations, "External Cylindrical Grinding", "precision_finish",
                  "Grind external cylindrical surfaces to final size after carburizing (hardened surface).",
                  "Cylindrical Grinding", conditional=True)
    add_operation(operations, "Shot Peening", "precision_finish",
                  "Shot peen cylindrical surfaces to induce compressive residual stress.", None, conditional=True)
    for feature in geometry["features"]:
        if feature["feature_type"] == "gear_teeth":
            add_operation(operations, "Precision grind gear teeth", "feature_after_heat",
                          f"{feature['feature_id']} {FEATURE_NAME['gear_teeth']}, grind teeth to final "
                          "accuracy after hardening.",
                          "Gear Grinding", feature["feature_id"], True)
        elif feature["feature_type"] in ("cam", "crank_pin"):
            ftype = feature["feature_type"]
            add_operation(operations, _get_finish_operation(ftype), "feature_after_heat",
                          f"{feature['feature_id']} {FEATURE_NAME[ftype]}, hard-finish to final accuracy after hardening.",
                          _hard_finish_process(ftype), feature["feature_id"], True)

    # ---- 5. Final-machining features (keyways/holes/bearing seats etc., machined after finish grinding) ----
    for feature in geometry["features"]:
        ftype = feature["feature_type"]
        if _is_main_bore_covered(feature, is_hollow, inner_dia):
            continue
        if ftype in ("gear_teeth", "spline", "knurl", "cam", "crank_pin"):
            continue
        op_name, process = _get_post_finish_operation(ftype, feature["high_precision"])
        add_operation(operations, op_name, "feature_before_inspection",
                      f"{feature['feature_id']} {FEATURE_NAME[ftype]}, {_feature_position_desc(feature)}; "
                      "process to final dimensions.",
                      process, feature["feature_id"], True)

    # ---- 6. Finishing steps: laser marking -> MPI -> appearance check/rust prevention/packaging ----
    add_operation(operations, "Laser Marking", "feature_before_inspection",
                  "Laser-mark part number, drawing and batch identifiers.", None, conditional=True)
    add_operation(operations, "Magnetic Particle Inspection", "inspection",
                  "MPI inspection of ground surfaces for grinding cracks and defects.", None, conditional=True)
    add_operation(operations, "Final Inspection", "inspection",
                  "Check segment dimensions, feature positions, tolerance, roughness and appearance.", None)
    add_operation(operations, "Cleaning & Rust Prevention", "packaging",
                  "Clean part and apply rust preventive oil.", None)
    add_operation(operations, "Packaging", "packaging",
                  "Package for shipment.", None)

    # Surface treatment (if specified)
    if surface != "none":
        add_operation(operations, "Surface Treatment", "surface_treatment",
                      SURFACE_NAME[surface], None, conditional=True)

    for index, operation in enumerate(operations, start=1):
        operation["operation_no"] = index
    return operations


# ============================================================
# Shaft-specific routes: camshaft / crankshaft / worm shaft / surface-hardened shaft (nitriding, induction hardening)
# ============================================================


def _main_bore_feature_id(
    geometry: dict[str, Any], is_hollow: bool, inner_dia: Any,
) -> Optional[str]:
    """ID of the bore feature matching the main bore of a hollow blank (carried by Blank-stage rough/finish boring)."""
    for feature in geometry["features"]:
        if _is_main_bore_covered(feature, is_hollow, inner_dia):
            return feature["feature_id"]
    return None


def _first_feature_id(geometry: dict[str, Any], feature_type: str) -> Optional[str]:
    """ID of the first feature of the given type (None if absent)."""
    for feature in geometry["features"]:
        if feature.get("feature_type") == feature_type:
            return feature["feature_id"]
    return None


def _append_soft_features(
    operations: list[dict[str, Any]], geometry: dict[str, Any],
    choices: dict[str, str], inner_dia: Any, is_hollow: bool, has_heat: bool,
    own_primary: Optional[set[str]] = None,
) -> set[str]:
    """Soft-state feature block (feature_before_heat): gear teeth/splines/knurling must be machined
    in the soft state; splittable high-precision features get "rough machining before heat
    treatment + finishing after heat treatment". Returns the set of split feature_ids.

    Consistent with the generic route logic, except that it always uses the feature_before_heat stage
    (the dedicated builders schedule their pre heat treatment separately at the pre_heat_treatment stage).
    """
    soft_set = {"spline", "gear_teeth", "knurl"}
    split_features: set[str] = set()
    for feature in geometry["features"]:
        feature_type = feature["feature_type"]
        if own_primary and feature_type in own_primary:
            continue
        if _is_main_bore_covered(feature, is_hollow, inner_dia):
            continue
        feature_id = feature["feature_id"]
        high = feature["high_precision"]
        can_split = FEATURE_SUPPORTS_SPLIT.get(feature_type, False)
        timing = choices.get(feature_id, feature.get("processing_timing", "undecided"))
        if high and has_heat and timing == "undecided":
            timing = "before_and_after_heat_treatment" if can_split else "before_heat_treatment"
        is_split = high and has_heat and can_split and timing == "before_and_after_heat_treatment"
        needs_pre_heat = is_split or (has_heat and feature_type in soft_set) or (
            high and has_heat and timing == "before_heat_treatment"
        )
        if not needs_pre_heat:
            continue
        add_operation(
            operations, _get_feature_operation(feature_type, is_split), "feature_before_heat",
            f"{feature_id} {FEATURE_NAME[feature_type]}, {_feature_position_desc(feature)}; "
            + ("Reserve finishing allowance." if is_split else "Process to input dimensions."),
            _get_pre_heat_process(feature_type), feature_id, True,
        )
        if is_split:
            split_features.add(feature_id)
        if feature_type == "gear_teeth":
            add_operation(operations, "Gear Chamfer", "feature_before_heat",
                          f"{feature_id} chamfer gear tooth edges before heat treatment.",
                          None, feature_id, True)
        if feature_type == "flange" and int(feature.get("flange_holes") or 0) > 0:
            add_operation(operations, "Drill flange bolt holes", "feature_before_heat",
                          f"{feature_id} drill {int(feature['flange_holes'])} flange holes in the soft state; hole diameter and PCD require drawing confirmation.",
                          "Drilling", feature_id, True)
    return split_features


def _append_hard_finish_features(
    operations: list[dict[str, Any]], geometry: dict[str, Any],
    split_features: set[str],
) -> None:
    """Post-heat hard-finishing block (feature_after_heat): split features + gears requiring post-heat finishing."""
    post_heat_set = set(split_features)
    for feature in geometry["features"]:
        if feature["feature_type"] == "gear_teeth" and feature.get("gear_finish_required"):
            post_heat_set.add(feature["feature_id"])
    for feature in geometry["features"]:
        if feature["feature_id"] not in post_heat_set:
            continue
        feature_type = feature["feature_type"]
        feature_id = feature["feature_id"]
        operation_name = _get_finish_operation(feature_type)
        process = _hard_finish_process(feature_type)
        if feature_type == "gear_teeth" and feature.get("gear_finish_required"):
            description = f"{feature_id} {FEATURE_NAME[feature_type]}, post-heat gear finishing after hardening."
        else:
            description = f"{feature_id} {FEATURE_NAME[feature_type]}, ensure input tolerance and roughness."
        add_operation(operations, operation_name, "feature_after_heat", description, process, feature_id, True)


def _append_post_finish_features(
    operations: list[dict[str, Any]], geometry: dict[str, Any],
    choices: dict[str, str], inner_dia: Any, is_hollow: bool, has_heat: bool,
    split_features: set[str], own_primary: Optional[set[str]] = None,
) -> None:
    """Final-machining block after the finished-part datum is established (feature_before_inspection)."""
    pre_heat_features = {"spline", "gear_teeth", "knurl"}
    for feature in geometry["features"]:
        feature_type = feature["feature_type"]
        feature_id = feature["feature_id"]
        if feature_id in split_features:
            continue
        if _is_main_bore_covered(feature, is_hollow, inner_dia):
            continue
        if own_primary and feature_type in own_primary:
            continue
        timing = choices.get(feature_id, feature.get("processing_timing", "undecided"))
        if has_heat and (
            feature_type in pre_heat_features
            or (feature["high_precision"] and timing == "before_heat_treatment")
        ):
            continue
        if feature_type in pre_heat_features:
            operation_name = _get_feature_operation(feature_type, False)
            process = FEATURE_PROCESS[feature_type]
        else:
            operation_name, process = _get_post_finish_operation(feature_type, feature["high_precision"])
        add_operation(
            operations, operation_name, "feature_before_inspection",
            f"{feature_id} {FEATURE_NAME[feature_type]}, {_feature_position_desc(feature)}; Process to final dimensions.",
            process, feature_id, True,
        )
        if feature_type == "flange" and int(feature.get("flange_holes") or 0) > 0:
            add_operation(
                operations, "Drill flange bolt holes", "feature_before_inspection",
                f"{feature_id} drill {int(feature['flange_holes'])} flange holes after the flange face and OD are finished; hole diameter and PCD require drawing confirmation.",
                "Drilling", feature_id, True,
            )


def _build_camshaft_route(
    request: dict[str, Any], geometry: dict[str, Any], choices: dict[str, str],
) -> list[dict[str, Any]]:
    """Real production route for a camshaft.

    Rough turn journals/cams -> quench and temper (or whole-shaft induction hardening) ->
    semi-finish turning -> finish turning (before hardening) -> rough grind OD/cams ->
    local induction hardening of cam lobes -> CBN finish grind journals/cams -> polish -> straighten -> inspection.
    """
    operations: list[dict[str, Any]] = []
    heat = request["global_requirements"]["heat_treatment"]
    surface = request["global_requirements"]["surface_treatment"]
    heat_plan = request.get("heat_treatment_plan", {})
    pre_treatment = heat_plan.get("pre_treatment")
    material = request.get("material", "45")
    material_props = get_material_properties(material)
    blank_type = request.get("blank_type", "solid")
    inner_dia = request.get("blank_inner_diameter_mm")
    is_hollow = blank_type == "hollow" and inner_dia
    main_bore_fid = _main_bore_feature_id(geometry, is_hollow, inner_dia)
    cam_fid = _first_feature_id(geometry, "cam")

    material_notes = ""
    if material_props["machinability"] == "difficult":
        material_notes = f" ({material} is difficult to machine, reduce cutting parameters)"

    blank_desc = (
        f"tube stock OD{request['blank_diameter_mm']}mm ID{inner_dia}mm"
        if is_hollow else f"bar stock, total length {geometry['total_length_mm']} mm"
    )
    add_operation(operations, "Blanking", "blank",
                  f"Cut from {blank_desc}, reserve machining allowance.", None)
    add_operation(operations, "Face Turning", "datum",
                  f"Turn both faces to establish axial datum.{material_notes}", "ISO Turning")
    add_operation(operations, "Center Drilling", "datum",
                  "Drill center holes at both ends for center clamping.", "Drilling")
    add_operation(operations, "Rough Turning", "rough",
                  f"Rough turn journals and cam profile with allowance.{material_notes}", "ISO Turning")
    add_operation(operations, "Straighten", "rough",
                  "Straighten after rough machining to limit distortion before finishing.",
                  None, conditional=True)
    if is_hollow:
        add_operation(operations, "Rough Boring", "rough",
                      f"Rough bore inner diameter to {inner_dia + 1} mm with finishing allowance.",
                      "Boring", feature_id=main_bore_fid)
    add_operation(operations, "Semi-finish Turning", "semi_finish",
                  f"Semi-finish turn journals and cam profile with allowance.{material_notes}", "ISO Turning")
    # Finish turning before hardening: after induction hardening/quench-temper, cams and journals can only be ground
    add_operation(operations, "Finish Turning", "finish_before_heat",
                  "Finish turn journals to near-final size before hardening (hardened cam surface cannot be turned).",
                  "ISO Turning")
    if is_hollow:
        add_operation(operations, "Finish Boring", "finish_before_heat",
                      f"Finish bore inner diameter to {inner_dia} mm before hardening.",
                      "Boring", feature_id=main_bore_fid)

    split_features = _append_soft_features(
        operations, geometry, choices, inner_dia, is_hollow, True, own_primary={"cam"})

    if pre_treatment:
        add_operation(operations, pre_treatment["name"], "pre_heat_treatment",
                      pre_treatment["description"], "Heat Treatment", conditional=True)

    description = heat_plan.get("description") or HEAT_NAME[heat]
    target_hardness = request["global_requirements"].get("target_hardness_hrc")
    requirements = []
    if target_hardness is not None:
        requirements.append(f"target {target_hardness:g} HRC")
    if requirements:
        description += "; " + ", ".join(requirements)
    add_operation(operations, "Heat Treatment", "heat_treatment", description, "Heat Treatment")
    if heat_plan.get("requires_datum_recovery", True):
        add_operation(operations, "Repair Center Holes", "datum_recovery",
                      "Recover finishing datum after heat treatment.", None)

    # Rough grind OD/cams (before finish grinding and local hardening)
    add_operation(operations, "Rough Grind OD", "precision_finish",
                  "Rough grind journal surfaces before cam hardening and finish grinding.",
                  "Cylindrical Grinding", conditional=True)
    if cam_fid:
        add_operation(operations, "Rough Grind Cam Lobe", "precision_finish",
                      f"{cam_fid} Cam, rough grind cam lobes to leave finish allowance.",
                      "Cam Grinding", cam_fid, True)

    # Local induction hardening of cam lobes (skipped when the whole shaft is induction-hardened)
    if heat != "induction_hardening" and cam_fid:
        add_operation(operations, "Induction Harden Cam Lobe", "feature_after_heat",
                      f"{cam_fid} induction harden cam lobes (surface hardening).",
                      None, cam_fid, True)
    add_operation(operations, "Straighten", "feature_after_heat",
                  "Straighten after heat treatment, fully cooled, to correct distortion.",
                  None, conditional=True)
    add_operation(operations, "CBN Finish Grind Journals", "feature_after_heat",
                  "CBN finish grind journal surfaces to final size and finish.",
                  "Cylindrical Grinding", conditional=True)
    if cam_fid:
        add_operation(operations, "CBN Finish Grind Cam Lobe", "feature_after_heat",
                      f"{cam_fid} Cam, CBN finish grind cam lobes to final profile and finish.",
                      "Cam Grinding", cam_fid, True)

    _append_hard_finish_features(operations, geometry, split_features)

    add_operation(operations, "Polish", "feature_after_heat",
                  "Polish journal and cam surfaces to required finish.", None, conditional=True)

    _append_post_finish_features(
        operations, geometry, choices, inner_dia, is_hollow, True, split_features,
        own_primary={"cam"})

    add_operation(operations, "Chamfer & Deburr", "deburr",
                  "Chamfer sharp edges and deburr all machined features per drawing.", None)
    if surface != "none":
        add_operation(operations, "Surface Treatment", "surface_treatment",
                      SURFACE_NAME[surface], None, conditional=True)
    add_operation(operations, "Magnetic Particle Inspection", "inspection",
                  "Magnetic particle inspection of ground surfaces for grinding cracks and defects.",
                  None, conditional=True)
    add_operation(operations, "Final Inspection", "inspection",
                  "Check segment dimensions, feature positions, tolerance, roughness and appearance.", None)
    add_operation(operations, "Cleaning", "packaging",
                  "Clean part to remove cutting fluid, chips and contaminants.", None)
    add_operation(operations, "Packaging", "packaging",
                  "Apply rust preventive oil and package for shipment.", None)

    for index, operation in enumerate(operations, start=1):
        operation["operation_no"] = index
    return operations


def _build_crankshaft_route(
    request: dict[str, Any], geometry: dict[str, Any], choices: dict[str, str],
) -> list[dict[str, Any]]:
    """Real production route for a crankshaft.

    Mill end faces and drill center holes -> rough machining (main journals/crank pins,
    turn-turn-broach / CNC external mill) -> semi-finish machining -> quench and temper ->
    finish grinding (CBN main journals/crank pins) -> oil holes/flange holes -> fillet rolling
    for strengthening -> dynamic balancing -> cleaning and inspection.
    Rolling must follow quenching (order cannot be swapped); dynamic balancing is mandatory.
    """
    operations: list[dict[str, Any]] = []
    heat = request["global_requirements"]["heat_treatment"]
    surface = request["global_requirements"]["surface_treatment"]
    heat_plan = request.get("heat_treatment_plan", {})
    pre_treatment = heat_plan.get("pre_treatment")
    material = request.get("material", "45")
    material_props = get_material_properties(material)
    blank_type = request.get("blank_type", "solid")
    inner_dia = request.get("blank_inner_diameter_mm")
    is_hollow = blank_type == "hollow" and inner_dia
    main_bore_fid = _main_bore_feature_id(geometry, is_hollow, inner_dia)
    crank_fid = _first_feature_id(geometry, "crank_pin")

    material_notes = ""
    if material_props["machinability"] == "difficult":
        material_notes = f" ({material} is difficult to machine, reduce cutting parameters)"

    blank_desc = (
        f"tube stock OD{request['blank_diameter_mm']}mm ID{inner_dia}mm"
        if is_hollow else f"bar stock, total length {geometry['total_length_mm']} mm"
    )
    add_operation(operations, "Blanking", "blank",
                  f"Cut from {blank_desc}, reserve machining allowance.", None)
    add_operation(operations, "Face Turning", "datum",
                  f"Mill/turn both end faces to establish axial datum.{material_notes}", "ISO Turning")
    add_operation(operations, "Center Drilling", "datum",
                  "Drill center holes at both ends for center clamping.", "Drilling")
    add_operation(operations, "Rough Turning", "rough",
                  f"Rough turn main journals (turn-turn-broach / CNC external mill).{material_notes}", "ISO Turning")
    if crank_fid:
        add_operation(operations, "Rough Turn Crank Pins", "rough",
                      f"{crank_fid} Crank Pin, rough machine eccentric crank pin journals with allowance.",
                      "ISO Turning", crank_fid, True)
    if is_hollow:
        add_operation(operations, "Rough Boring", "rough",
                      f"Rough bore inner diameter to {inner_dia + 1} mm with finishing allowance.",
                      "Boring", feature_id=main_bore_fid)
    add_operation(operations, "Semi-finish Turning", "semi_finish",
                  f"Semi-finish turn journals and crank pins with finishing allowance.{material_notes}", "ISO Turning")

    # After quenching the hardened surface can only be ground: finish turning/machining of crank pins goes before heat treatment
    add_operation(operations, "Finish Turning", "finish_before_heat",
                  "Finish turn main journals and thrust faces to near-final size before hardening, leave grinding allowance.",
                  "ISO Turning")
    if crank_fid:
        add_operation(operations, "Finish Turn Crank Pins", "finish_before_heat",
                      f"{crank_fid} finish turn crank pin journals to near-final size before hardening.",
                      "ISO Turning", crank_fid, True)
    if is_hollow:
        add_operation(operations, "Finish Boring", "finish_before_heat",
                      f"Finish bore inner diameter to {inner_dia} mm before hardening.",
                      "Boring", feature_id=main_bore_fid)

    split_features = _append_soft_features(
        operations, geometry, choices, inner_dia, is_hollow, True, own_primary={"crank_pin"})

    if pre_treatment:
        add_operation(operations, pre_treatment["name"], "pre_heat_treatment",
                      pre_treatment["description"], "Heat Treatment", conditional=True)

    description = heat_plan.get("description") or HEAT_NAME[heat]
    target_hardness = request["global_requirements"].get("target_hardness_hrc")
    requirements = []
    if target_hardness is not None:
        requirements.append(f"target {target_hardness:g} HRC")
    if requirements:
        description += "; " + ", ".join(requirements)
    add_operation(operations, "Heat Treatment", "heat_treatment", description, "Heat Treatment")
    if heat_plan.get("requires_datum_recovery", True):
        add_operation(operations, "Repair Center Holes", "datum_recovery",
                      "Recover finishing datum after heat treatment.", None)

    # After quenching: oil holes -> CBN finish grinding (main journals including thrust faces; crank pins after main journals)
    add_operation(operations, "Oil Hole Drilling", "precision_finish",
                  "Drill oil holes through main journal and crank pin passages.",
                  "Drilling", conditional=True)
    add_operation(operations, "CBN Grind Main Journals", "precision_finish",
                  "CBN finish grind main journals and thrust faces to final size after hardening.",
                  "Cylindrical Grinding", conditional=True)
    if crank_fid:
        add_operation(operations, "CBN Grind Crank Pins", "precision_finish",
                      f"{crank_fid} CBN finish grind crank pin journals to final size after hardening.",
                      "Cylindrical Grinding", crank_fid, True)

    # Fillet rolling for strengthening (after quench and temper; order cannot be swapped)
    if crank_fid:
        add_operation(operations, "Fillet Rolling", "feature_after_heat",
                      f"{crank_fid} fillet roll journal and crank pin fillets to induce compressive residual stress.",
                      "Fillet Rolling", crank_fid, True)
    add_operation(operations, "Straighten", "feature_after_heat",
                  "Straighten after heat treatment and grinding to correct distortion.",
                  None, conditional=True)

    _append_post_finish_features(
        operations, geometry, choices, inner_dia, is_hollow, True, split_features,
        own_primary={"crank_pin"})

    # Dynamic balancing: mandatory for crankshafts, scheduled unconditionally
    add_operation(operations, "Dynamic Balancing", "feature_before_inspection",
                  "Dynamic balance the crankshaft to the required balance grade.", None)

    add_operation(operations, "Chamfer & Deburr", "deburr",
                  "Chamfer sharp edges and deburr all machined features per drawing.", None)
    if surface != "none":
        add_operation(operations, "Surface Treatment", "surface_treatment",
                      SURFACE_NAME[surface], None, conditional=True)
    add_operation(operations, "Magnetic Particle Inspection", "inspection",
                  "Magnetic particle inspection of ground surfaces for grinding cracks and defects.",
                  None, conditional=True)
    add_operation(operations, "Final Inspection", "inspection",
                  "Check segment dimensions, feature positions, tolerance, roughness and appearance.", None)
    add_operation(operations, "Cleaning", "packaging",
                  "Clean part to remove cutting fluid, chips and contaminants.", None)
    add_operation(operations, "Packaging", "packaging",
                  "Apply rust preventive oil and package for shipment.", None)

    for index, operation in enumerate(operations, start=1):
        operation["operation_no"] = index
    return operations


def _build_worm_shaft_route(
    request: dict[str, Any], geometry: dict[str, Any], choices: dict[str, str],
) -> list[dict[str, Any]]:
    """Real production route for a worm shaft.

    Carburized worm: rough turning -> semi-finish turn OD and thread profile -> finish turn regions
    not requiring carburizing -> carburize and quench -> lap center holes -> rough grind OD ->
    rough grind thread profile -> low-temperature aging -> lap center holes -> semi-finish grind OD ->
    semi-finish grind thread profile -> finish grind OD and end faces -> finish grind thread profile.
    Nitrided worm: finish turning (before nitriding) -> rough grinding before nitriding to control
    case depth -> nitriding -> finish grind OD/thread profile.
    """
    operations: list[dict[str, Any]] = []
    heat = request["global_requirements"]["heat_treatment"]
    surface = request["global_requirements"]["surface_treatment"]
    heat_plan = request.get("heat_treatment_plan", {})
    pre_treatment = heat_plan.get("pre_treatment")
    material = request.get("material", "45")
    material_props = get_material_properties(material)
    blank_type = request.get("blank_type", "solid")
    inner_dia = request.get("blank_inner_diameter_mm")
    is_hollow = blank_type == "hollow" and inner_dia
    main_bore_fid = _main_bore_feature_id(geometry, is_hollow, inner_dia)
    worm_fid = _first_feature_id(geometry, "worm")

    material_notes = ""
    if material_props["machinability"] == "difficult":
        material_notes = f" ({material} is difficult to machine, reduce cutting parameters)"

    blank_desc = (
        f"tube stock OD{request['blank_diameter_mm']}mm ID{inner_dia}mm"
        if is_hollow else f"bar stock, total length {geometry['total_length_mm']} mm"
    )
    add_operation(operations, "Blanking", "blank",
                  f"Cut from {blank_desc}, reserve machining allowance.", None)
    add_operation(operations, "Face Turning", "datum",
                  f"Turn both faces to establish axial datum.{material_notes}", "ISO Turning")
    add_operation(operations, "Center Drilling", "datum",
                  "Drill center holes at both ends for center clamping.", "Drilling")
    add_operation(operations, "Rough Turning", "rough",
                  f"Rough turn external profile with allowance.{material_notes}", "ISO Turning")
    if is_hollow:
        add_operation(operations, "Rough Boring", "rough",
                      f"Rough bore inner diameter to {inner_dia + 1} mm with finishing allowance.",
                      "Boring", feature_id=main_bore_fid)
    add_operation(operations, "Semi-finish Turning", "semi_finish",
                  f"Semi-finish turn external profile with allowance.{material_notes}", "ISO Turning")
    if worm_fid:
        add_operation(operations, "Semi-finish Turn Spiral", "semi_finish",
                      f"{worm_fid} Worm, semi-finish turn the worm spiral profile in the soft state.",
                      "ISO Turning", worm_fid, True)

    split_features: set[str] = set()

    if heat == "carburize_quench":
        # Finish turn regions not requiring carburizing -> carburize-quench chain -> center-hole lapping -> rough grinding -> aging -> finish grinding
        add_operation(operations, "Finish Turning", "finish_before_heat",
                      "Finish turn regions not requiring carburizing before the carburizing chain (soft state).",
                      "ISO Turning")
        if is_hollow:
            add_operation(operations, "Finish Boring", "finish_before_heat",
                          f"Finish bore inner diameter to {inner_dia} mm before carburizing.",
                          "Boring", feature_id=main_bore_fid)
        split_features = _append_soft_features(
            operations, geometry, choices, inner_dia, is_hollow, True, own_primary={"worm"})
        if pre_treatment:
            add_operation(operations, pre_treatment["name"], "pre_heat_treatment",
                          pre_treatment["description"], "Heat Treatment", conditional=True)

        add_operation(operations, "Pre-Clean", "heat_treatment",
                      "Pre-clean part to remove cutting fluid and chips before carburizing.",
                      None, conditional=True)
        description = heat_plan.get("description") or HEAT_NAME[heat]
        target_hardness = request["global_requirements"].get("target_hardness_hrc")
        case_depth = request["global_requirements"].get("case_depth_mm")
        requirements = []
        if target_hardness is not None:
            requirements.append(f"target {target_hardness:g} HRC")
        if case_depth is not None:
            requirements.append(f"effective case depth {case_depth:g} mm")
        if requirements:
            description += "; " + ", ".join(requirements)
        add_operation(operations, "Heat Treatment", "heat_treatment", description, "Heat Treatment")
        add_operation(operations, "Clean Quench Oil", "heat_treatment",
                      "Clean quench oil residue from the part after quenching.", None, conditional=True)
        add_operation(operations, "Temper", "heat_treatment",
                      "Low-temperature temper to relieve stress and stabilize structure.",
                      "Heat Treatment", conditional=True)
        add_operation(operations, "Shot Blast", "heat_treatment",
                      "Shot blast to remove oxide scale and induce surface compressive stress.",
                      None, conditional=True)
        add_operation(operations, "Heat-treatment Inspection", "heat_treatment",
                      "Inspect hardness, effective case depth and distortion after heat treatment.",
                      None, conditional=True)
        add_operation(operations, "Lapping Center Holes", "datum_recovery",
                      "Lap center holes to recover the finishing datum after heat treatment.",
                      None, conditional=True)
        add_operation(operations, "Rough Grind OD", "precision_finish",
                      "Rough grind external profile before spiral finishing.",
                      "Cylindrical Grinding", conditional=True)
        if worm_fid:
            add_operation(operations, "Rough Grind Spiral", "precision_finish",
                          f"{worm_fid} Worm, rough grind the worm spiral profile.",
                          "Worm Grinding", worm_fid, True)
        add_operation(operations, "Low-temp Aging", "feature_after_heat",
                      "Low-temperature aging to relieve stress after rough grinding.",
                      "Heat Treatment", conditional=True)
        add_operation(operations, "Lapping Center Holes", "feature_after_heat",
                      "Re-lap center holes before final grinding.", None, conditional=True)
        add_operation(operations, "Semi-finish Grind OD", "feature_after_heat",
                      "Semi-finish grind external profile.", "Cylindrical Grinding", conditional=True)
        if worm_fid:
            add_operation(operations, "Semi-finish Grind Spiral", "feature_after_heat",
                          f"{worm_fid} Worm, semi-finish grind the worm spiral profile.",
                          "Worm Grinding", worm_fid, True)
        add_operation(operations, "Finish Grind OD & End Faces", "feature_after_heat",
                      "Finish grind external profile and end faces to final size.",
                      "Cylindrical Grinding", conditional=True)
        if worm_fid:
            add_operation(operations, "Finish Grind Spiral", "feature_after_heat",
                          f"{worm_fid} Worm, finish grind the worm spiral profile to final accuracy.",
                          "Worm Grinding", worm_fid, True)
    elif heat in ("nitriding", "induction_hardening"):
        # Finish turning (before nitriding) -> rough grinding before nitriding to control case depth -> nitriding -> center-hole lapping -> finish grind OD/thread profile
        add_operation(operations, "Finish Turning", "finish_before_heat",
                      "Finish turn external profile before nitriding (nitrided case cannot be turned).",
                      "ISO Turning")
        if is_hollow:
            add_operation(operations, "Finish Boring", "finish_before_heat",
                          f"Finish bore inner diameter to {inner_dia} mm before nitriding.",
                          "Boring", feature_id=main_bore_fid)
        split_features = _append_soft_features(
            operations, geometry, choices, inner_dia, is_hollow, True, own_primary={"worm"})
        add_operation(operations, "Rough Grind OD", "feature_before_heat",
                      "Rough grind external profile before nitriding to control nitrided case depth.",
                      "Cylindrical Grinding", conditional=True)
        if worm_fid:
            add_operation(operations, "Rough Grind Spiral", "feature_before_heat",
                          f"{worm_fid} Worm, rough grind the worm spiral before nitriding.",
                          "Worm Grinding", worm_fid, True)
        if pre_treatment:
            add_operation(operations, pre_treatment["name"], "pre_heat_treatment",
                          pre_treatment["description"], "Heat Treatment", conditional=True)
        description = heat_plan.get("description") or HEAT_NAME[heat]
        target_hardness = request["global_requirements"].get("target_hardness_hrc")
        requirements = []
        if target_hardness is not None:
            requirements.append(f"target {target_hardness:g} HRC")
        if requirements:
            description += "; " + ", ".join(requirements)
        add_operation(operations, "Heat Treatment", "heat_treatment", description, "Heat Treatment")
        add_operation(operations, "Lapping Center Holes", "datum_recovery",
                      "Lap center holes to recover the finishing datum after heat treatment.",
                      None, conditional=True)
        add_operation(operations, "Finish Grind OD", "precision_finish",
                      "Finish grind external profile to final size after nitriding.",
                      "Cylindrical Grinding", conditional=True)
        if worm_fid:
            add_operation(operations, "Finish Grind Spiral", "precision_finish",
                          f"{worm_fid} Worm, finish grind the worm spiral to final accuracy after nitriding.",
                          "Worm Grinding", worm_fid, True)
        add_operation(operations, "Low-temp Aging", "feature_after_heat",
                      "Low-temperature aging to relieve stress after grinding.",
                      "Heat Treatment", conditional=True)
    else:
        # Quench-tempered worm: heat treatment -> datum recovery -> finish turning -> rough grinding -> aging -> finish grinding
        split_features = _append_soft_features(
            operations, geometry, choices, inner_dia, is_hollow, True, own_primary={"worm"})
        if pre_treatment:
            add_operation(operations, pre_treatment["name"], "pre_heat_treatment",
                          pre_treatment["description"], "Heat Treatment", conditional=True)
        description = heat_plan.get("description") or HEAT_NAME[heat]
        target_hardness = request["global_requirements"].get("target_hardness_hrc")
        requirements = []
        if target_hardness is not None:
            requirements.append(f"target {target_hardness:g} HRC")
        if requirements:
            description += "; " + ", ".join(requirements)
        add_operation(operations, "Heat Treatment", "heat_treatment", description, "Heat Treatment")
        if heat_plan.get("requires_datum_recovery", True):
            add_operation(operations, "Repair Center Holes", "datum_recovery",
                          "Recover finishing datum after heat treatment.", None)
        add_operation(operations, "Finish Turning", "finish",
                      "Finish turn external profile to near-final size (quench-tempered, machinable).",
                      "ISO Turning")
        add_operation(operations, "Rough Grind OD", "precision_finish",
                      "Rough grind external profile before spiral finishing.",
                      "Cylindrical Grinding", conditional=True)
        if worm_fid:
            add_operation(operations, "Rough Grind Spiral", "precision_finish",
                          f"{worm_fid} Worm, rough grind the worm spiral profile.",
                          "Worm Grinding", worm_fid, True)
        add_operation(operations, "Low-temp Aging", "feature_after_heat",
                      "Low-temperature aging to relieve stress after rough grinding.",
                      "Heat Treatment", conditional=True)
        add_operation(operations, "Lapping Center Holes", "feature_after_heat",
                      "Re-lap center holes before final grinding.", None, conditional=True)
        add_operation(operations, "Finish Grind OD & End Faces", "feature_after_heat",
                      "Finish grind external profile and end faces to final size.",
                      "Cylindrical Grinding", conditional=True)
        if worm_fid:
            add_operation(operations, "Finish Grind Spiral", "feature_after_heat",
                          f"{worm_fid} Worm, finish grind the worm spiral profile to final accuracy.",
                          "Worm Grinding", worm_fid, True)

    _append_post_finish_features(
        operations, geometry, choices, inner_dia, is_hollow, True, split_features,
        own_primary={"worm"})

    add_operation(operations, "Chamfer & Deburr", "deburr",
                  "Chamfer sharp edges and deburr all machined features per drawing.", None)
    if surface != "none":
        add_operation(operations, "Surface Treatment", "surface_treatment",
                      SURFACE_NAME[surface], None, conditional=True)
    add_operation(operations, "Magnetic Particle / NDT Inspection", "inspection",
                  "Magnetic particle or NDT inspection of ground surfaces for defects.",
                  None, conditional=True)
    add_operation(operations, "Final Inspection", "inspection",
                  "Check segment dimensions, feature positions, tolerance, roughness and appearance.", None)
    add_operation(operations, "Cleaning", "packaging",
                  "Clean part to remove cutting fluid, chips and contaminants.", None)
    add_operation(operations, "Packaging", "packaging",
                  "Apply rust preventive oil and package for shipment.", None)

    for index, operation in enumerate(operations, start=1):
        operation["operation_no"] = index
    return operations


def _build_surface_hardened_shaft_route(
    request: dict[str, Any], geometry: dict[str, Any], choices: dict[str, str],
) -> list[dict[str, Any]]:
    """Route for surface-hardened shafts (nitriding/induction hardening): main shafts, precision spline shafts, hollow shafts.

    Cut stock -> rough turning -> (deep hole) -> semi-finish turning -> stabilization -> finish turning
    (before hardening) -> soft-state features -> (rough grinding before nitriding) -> nitriding/induction
    hardening -> semi-finish grinding -> finish grind OD -> grind features -> final-machining features ->
    lapping and polishing -> inspection -> final check. After nitriding/induction hardening the surface
    is hardened and can only be ground, not finish turned.
    """
    operations: list[dict[str, Any]] = []
    heat = request["global_requirements"]["heat_treatment"]
    surface = request["global_requirements"]["surface_treatment"]
    heat_plan = request.get("heat_treatment_plan", {})
    pre_treatment = heat_plan.get("pre_treatment")
    material = request.get("material", "45")
    material_props = get_material_properties(material)
    blank_type = request.get("blank_type", "solid")
    inner_dia = request.get("blank_inner_diameter_mm")
    is_hollow = blank_type == "hollow" and inner_dia
    main_bore_fid = _main_bore_feature_id(geometry, is_hollow, inner_dia)

    material_notes = ""
    if material_props["machinability"] == "difficult":
        material_notes = f" ({material} is difficult to machine, reduce cutting parameters)"

    blank_desc = (
        f"tube stock OD{request['blank_diameter_mm']}mm ID{inner_dia}mm"
        if is_hollow else f"bar stock, total length {geometry['total_length_mm']} mm"
    )
    add_operation(operations, "Blanking", "blank",
                  f"Cut from {blank_desc}, reserve machining allowance.", None)
    add_operation(operations, "Face Turning", "datum",
                  f"Turn both faces to establish axial datum.{material_notes}", "ISO Turning")
    add_operation(operations, "Center Drilling", "datum",
                  "Drill center holes at both ends for center clamping.", "Drilling")
    add_operation(operations, "Rough Turning", "rough",
                  f"Rough turn stepped profile with allowance.{material_notes}", "ISO Turning")
    if is_hollow:
        # Deep-hole chain: deep-hole drilling (L/D>5) -> rough boring
        try:
            deep_hole = geometry["total_length_mm"] / inner_dia > 5
        except (TypeError, ZeroDivisionError):
            deep_hole = False
        if deep_hole:
            add_operation(operations, "Deep Hole Drilling", "rough",
                          f"Deep-hole drill inner bore (L/D > 5) to {inner_dia + 2} mm with finishing allowance.",
                          "Drilling", feature_id=main_bore_fid, conditional=True)
        add_operation(operations, "Rough Boring", "rough",
                      f"Rough bore inner diameter to {inner_dia + 1} mm with finishing allowance.",
                      "Boring", feature_id=main_bore_fid)
    add_operation(operations, "Semi-finish Turning", "semi_finish",
                  f"Semi-finish turn segments with finishing allowance.{material_notes}", "ISO Turning")
    if is_hollow:
        add_operation(operations, "Expand-ream Stepped Bore", "semi_finish",
                      f"Expand and ream the stepped bore to {inner_dia} mm before finishing.",
                      "Boring", feature_id=main_bore_fid, conditional=True)
    # Stabilization: relieves residual stress for nitrided/precision shafts (before finishing operations)
    add_operation(operations, "Stabilization / Aging", "semi_finish",
                  "Stabilization / aging heat treatment to relieve residual stress before finishing (per drawing).",
                  "Heat Treatment", conditional=True)

    # Finish turning before hardening: after nitriding/induction hardening the hardened case can only be ground
    add_operation(operations, "Finish Turning", "finish_before_heat",
                  "Finish turn external profile to near-final size before hardening (hardened case cannot be turned).",
                  "ISO Turning")
    if is_hollow:
        add_operation(operations, "Finish Boring", "finish_before_heat",
                      f"Finish bore inner diameter to {inner_dia} mm before hardening.",
                      "Boring", feature_id=main_bore_fid)

    split_features = _append_soft_features(operations, geometry, choices, inner_dia, is_hollow, True)

    # Rough grinding before nitriding: controls the nitrided case thickness (not needed for induction hardening)
    if heat == "nitriding":
        add_operation(operations, "Rough Grind OD", "feature_before_heat",
                      "Rough grind external profile before nitriding to control nitrided case depth.",
                      "Cylindrical Grinding", conditional=True)

    if pre_treatment:
        add_operation(operations, pre_treatment["name"], "pre_heat_treatment",
                      pre_treatment["description"], "Heat Treatment", conditional=True)

    description = heat_plan.get("description") or HEAT_NAME[heat]
    target_hardness = request["global_requirements"].get("target_hardness_hrc")
    requirements = []
    if target_hardness is not None:
        requirements.append(f"target {target_hardness:g} HRC")
    if requirements:
        description += "; " + ", ".join(requirements)
    add_operation(operations, "Heat Treatment", "heat_treatment", description, "Heat Treatment")
    if heat_plan.get("requires_datum_recovery", True):
        add_operation(operations, "Repair Center Holes", "datum_recovery",
                      "Recover finishing datum after heat treatment.", None)

    add_operation(operations, "Semi-finish Grind OD", "precision_finish",
                  "Semi-finish grind external profile after hardening.", "Cylindrical Grinding", conditional=True)
    add_operation(operations, "Finish Grind OD", "precision_finish",
                  "Finish grind external profile to final size after hardening.",
                  "Cylindrical Grinding", conditional=True)

    _append_hard_finish_features(operations, geometry, split_features)

    _append_post_finish_features(operations, geometry, choices, inner_dia, is_hollow, True, split_features)

    add_operation(operations, "Lapping & Polishing", "feature_before_inspection",
                  "Lap and polish precision surfaces to final finish.", None, conditional=True)

    add_operation(operations, "Chamfer & Deburr", "deburr",
                  "Chamfer sharp edges and deburr all machined features per drawing.", None)
    if surface != "none":
        add_operation(operations, "Surface Treatment", "surface_treatment",
                      SURFACE_NAME[surface], None, conditional=True)
    add_operation(operations, "Magnetic Particle / NDT Inspection", "inspection",
                  "Magnetic particle or NDT inspection of ground surfaces for defects.",
                  None, conditional=True)
    add_operation(operations, "Final Inspection", "inspection",
                  "Check segment dimensions, feature positions, tolerance, roughness and appearance.", None)
    add_operation(operations, "Cleaning", "packaging",
                  "Clean part to remove cutting fluid, chips and contaminants.", None)
    add_operation(operations, "Packaging", "packaging",
                  "Apply rust preventive oil and package for shipment.", None)

    for index, operation in enumerate(operations, start=1):
        operation["operation_no"] = index
    return operations
