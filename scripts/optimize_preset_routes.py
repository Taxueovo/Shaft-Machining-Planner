#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Rewrite the process_plan of preset cases (data/cases.json) according to real production practice.

Based on the typical process routes for each shaft type researched in docs/PROCESS_ROUTES_REFERENCE.md.
Corrections:
- Carburized and quenched parts: finish turning is moved before heat treatment (finish_before_heat);
  after quenching the hardened surfaces can only be ground;
- Complete the carburize-quench chain (pre-clean → carburize → clean quench oil → temper → shot blast → heat-treatment inspection);
- Complete the finishing steps (dynamic balancing / stress relief / center-hole grinding / MPI / packaging).
Only process_plan is modified; segments/features/metadata are untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_FILE = ROOT / "data" / "cases.json"

# Commonly used description fragments
_blank = "Cut {bar} stock, reserve machining allowance."
_face = "Turn both faces to establish axial datum."
_center = "Drill center holes at both ends for center clamping."
_rough = "Rough turn stepped profile with machining allowance."
_semi = "Semi-finish turn segments with finishing allowance."
_ht_q_t = "Quench and temper to drawing-specified hardness."
_ht_carb = "Carburize, quench and low-temperature temper; control case depth and distortion."
_repair = "Regrind center holes to recover the finishing datum."
_finish = "Finish turn stepped profile to final dimensions."
_grind = "Grind precision surfaces (bearing seats) to final size and Ra."
_inspect = "Check dimensions, tolerances, roughness and feature positions."


def _st(no, name, stage, desc, machine=None):
    s = {"step_no": no, "name": name, "stage": stage, "description": desc}
    if machine:
        s["machine"] = machine
    return s


# ============================================================
# Route templates
# ============================================================


def _qt_base(material_blank="φ35 bar", boring=False, stress_relief=False):
    """Quench-and-temper shaft base chain (Blank → ... → quench & temper → repair center holes → finish turning → grinding)."""
    steps = [
        _st(1, "Blanking", "blank", _blank.format(bar=material_blank), "Band saw"),
        _st(2, "Face Turning", "datum", _face, "CNC lathe"),
        _st(3, "Center Drilling", "datum", _center, "Center drill"),
        _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    ]
    if boring:
        steps.append(
            _st(
                5,
                "Rough Boring",
                "rough",
                "Bore inner diameter with finishing allowance.",
                "Boring machine",
            )
        )
    if stress_relief:
        steps.append(
            _st(
                6,
                "Stress Relief",
                "semi_finish",
                "Stress-relief heat treatment to remove machining stress.",
                "Heat treatment furnace",
            )
        )
        steps.append(_st(7, "Semi-finish Turning", "semi_finish", _semi, "CNC lathe"))
    else:
        steps.append(_st(5, "Semi-finish Turning", "semi_finish", _semi, "CNC lathe"))
    steps.append(
        _st(len(steps) + 1, "Heat Treatment", "heat_treatment", _ht_q_t, "Heat treatment furnace")
    )
    steps.append(
        _st(len(steps) + 1, "Repair Center Holes", "datum_recovery", _repair, "Center hole grinder")
    )
    steps.append(_st(len(steps) + 1, "Finish Turning", "finish", _finish, "CNC lathe"))
    if boring:
        steps.append(
            _st(
                len(steps) + 1,
                "Finish Boring",
                "finish",
                "Finish bore inner diameter to final size.",
                "Boring machine",
            )
        )
    return steps


def _carb_base(material_blank="φ28 bar", spline=False, gear=False, cam=False):
    """Carburize-quench chain (finish turning moved before heat treatment + pre-clean → carburize → clean quench oil → temper → shot blast → inspection)."""
    steps = [
        _st(1, "Blanking", "blank", _blank.format(bar=material_blank), "Band saw"),
        _st(2, "Face Turning", "datum", _face, "CNC lathe"),
        _st(3, "Center Drilling", "datum", _center, "Center drill"),
        _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
        _st(5, "Semi-finish Turning", "semi_finish", _semi, "CNC lathe"),
        # Finish turning must happen before carburizing (soft state): after quenching the hardened surface can only be ground
        _st(
            6,
            "Finish Turning",
            "finish_before_heat",
            "Finish turn non-tooth surfaces before carburizing (soft state).",
            "CNC lathe",
        ),
    ]
    if spline:
        steps.append(
            _st(
                7,
                "Spline Milling",
                "feature_before_heat",
                "Mill spline in soft state, leave grinding allowance.",
                "Spline milling machine",
            )
        )
    if gear:
        steps.append(
            _st(
                len(steps) + 1,
                "Hobbing",
                "feature_before_heat",
                "Hob gear teeth in soft state, leave grinding allowance.",
                "Gear hobbing machine",
            )
        )
        steps.append(
            _st(
                len(steps) + 1,
                "Gear Chamfer",
                "feature_before_heat",
                "Chamfer gear tooth edges before carburizing.",
                "Gear chamfering machine",
            )
        )
    if cam:
        steps.append(
            _st(
                len(steps) + 1,
                "Cam Milling",
                "feature_before_heat",
                "Mill cam lobes in soft state.",
                "CNC milling machine",
            )
        )
    steps.extend(
        [
            _st(
                len(steps) + 1,
                "Pre-Clean",
                "heat_treatment",
                "Pre-clean part to remove cutting fluid and chips.",
                "Parts washer",
            ),
            _st(
                len(steps) + 1,
                "Heat Treatment",
                "heat_treatment",
                _ht_carb,
                "Vacuum / atmosphere carburizing furnace",
            ),
            _st(
                len(steps) + 1,
                "Clean Quench Oil",
                "heat_treatment",
                "Clean quench oil residue after quenching.",
                "Parts washer",
            ),
            _st(
                len(steps) + 1,
                "Temper",
                "heat_treatment",
                "Low-temperature temper (~160-200°C) to relieve stress.",
                "Tempering furnace",
            ),
            _st(
                len(steps) + 1,
                "Shot Blast",
                "heat_treatment",
                "Shot blast to remove oxide scale and strengthen surface.",
                "Shot blasting machine",
            ),
            _st(
                len(steps) + 1,
                "Heat-treatment Inspection",
                "heat_treatment",
                "Inspect hardness, case depth and distortion.",
                "Hardness tester",
            ),
            _st(
                len(steps) + 1,
                "Center-hole Chamfer Grinding",
                "datum_recovery",
                "Grind center-hole chamfers to recover finishing datum.",
                "Center hole grinder",
            ),
            _st(
                len(steps) + 1,
                "External Cylindrical Grinding",
                "precision_finish",
                "Grind external cylindrical surfaces to final size (hardened).",
                "External cylindrical grinder",
            ),
            _st(
                len(steps) + 1,
                "Shot Peening",
                "precision_finish",
                "Shot peen surfaces to induce compressive residual stress.",
                "Shot peening machine",
            ),
        ]
    )
    if gear:
        steps.append(
            _st(
                len(steps) + 1,
                "Gear Grinding",
                "feature_after_heat",
                "Grind gear teeth to final accuracy.",
                "Gear grinding machine",
            )
        )
    if spline:
        steps.append(
            _st(
                len(steps) + 1,
                "Spline Grinding",
                "feature_after_heat",
                "Grind spline teeth to final accuracy.",
                "Spline grinding machine",
            )
        )
    if cam:
        steps.append(
            _st(
                len(steps) + 1,
                "Journal Grinding",
                "feature_after_heat",
                "Grind bearing journals to final size.",
                "External cylindrical grinder",
            )
        )
    return steps


# ============================================================
# Complete process routes per preset
# ============================================================

ROUTES = {}

# ── MS-* motor shafts ──
ROUTES["MS-001"] = _qt_base("φ35 bar") + [
    _st(9, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(
        10,
        "Milling Keyway",
        "feature_before_inspection",
        "Mill keyway to drawing width/depth.",
        "Keyway milling machine",
    ),
    _st(11, "Threading", "feature_before_inspection", "Cut thread per drawing.", "CNC lathe"),
    _st(12, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["MS-002"] = _qt_base("φ30 bar") + [
    _st(9, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(
        10,
        "Milling Keyway",
        "feature_before_inspection",
        "Mill keyway to drawing width/depth.",
        "Keyway milling machine",
    ),
    _st(11, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["MS-003"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ22 bar"), "Band saw"),
    _st(2, "Face Turning", "datum", _face, "CNC lathe"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    _st(5, "Finish Turning", "finish", _finish, "CNC lathe"),
    _st(
        6,
        "Milling Keyway",
        "feature_before_inspection",
        "Mill keyway to drawing width/depth.",
        "Keyway milling machine",
    ),
    _st(7, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["MS-004"] = _qt_base("φ30×8 tube", boring=True) + [
    _st(
        9,
        "Milling Keyway",
        "feature_before_inspection",
        "Mill keyway to drawing width/depth.",
        "Keyway milling machine",
    ),
    _st(10, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["MS-005"] = _carb_base("φ22 bar", spline=True) + [
    _st(
        len(_carb_base("φ22 bar", spline=True)) + 1,
        "Threading",
        "feature_before_inspection",
        "Cut thread per drawing.",
        "CNC lathe",
    ),
    _st(
        len(_carb_base("φ22 bar", spline=True)) + 2, "Final Inspection", "inspection", _inspect, ""
    ),
]

ROUTES["MS-006"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ18 bar"), "Band saw"),
    _st(2, "Face Turning", "datum", _face, "CNC lathe"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    _st(5, "Finish Turning", "finish", _finish, "CNC lathe"),
    _st(6, "Threading", "feature_before_inspection", "Cut thread per drawing.", "CNC lathe"),
    _st(
        7,
        "Milling Flat",
        "feature_before_inspection",
        "Mill flat to drawing across-flats.",
        "Vertical milling machine",
    ),
    _st(8, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["MS-007"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ28 bar"), "Band saw"),
    _st(2, "Face Turning", "datum", _face, "CNC lathe"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    _st(5, "Heat Treatment", "heat_treatment", _ht_q_t, "Heat treatment furnace"),
    _st(6, "Repair Center Holes", "datum_recovery", _repair, "Center hole grinder"),
    _st(7, "Finish Turning", "finish", _finish, "CNC lathe"),
    _st(
        8,
        "Milling D Flat",
        "feature_before_inspection",
        "Mill D flat to drawing across-flats.",
        "Vertical milling machine",
    ),
    _st(9, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["MS-07A"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ16 bar"), "Band saw"),
    _st(2, "Face Turning", "datum", _face, "CNC lathe"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    _st(5, "Finish Turning", "finish", _finish, "CNC lathe"),
    _st(
        6,
        "Milling First D Flat",
        "feature_before_inspection",
        "Mill first D flat.",
        "Vertical milling machine",
    ),
    _st(
        7,
        "Milling Second D Flat",
        "feature_before_inspection",
        "Mill second D flat (opposite side).",
        "Vertical milling machine",
    ),
    _st(8, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["MS-008"] = _qt_base("φ32 bar") + [
    _st(9, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(
        10,
        "Milling Keyway",
        "feature_before_inspection",
        "Mill keyway to drawing width/depth.",
        "Keyway milling machine",
    ),
    _st(11, "Threading", "feature_before_inspection", "Cut thread per drawing.", "CNC lathe"),
    _st(12, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["MS-009"] = _qt_base("φ40 bar") + [
    _st(9, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(10, "Threading", "feature_before_inspection", "Cut thread per drawing.", "CNC lathe"),
    _st(
        11,
        "Drilling Balancing Holes",
        "feature_before_inspection",
        "Drill balancing holes at marked positions.",
        "Radial drilling machine",
    ),
    _st(
        12,
        "Dynamic Balancing",
        "packaging",
        "Dynamic balance correction to target grade.",
        "Balancing machine",
    ),
    _st(13, "Final Inspection", "inspection", _inspect, ""),
]

# ── GS-* gear shafts ──
ROUTES["GS-001"] = _carb_base("φ28 bar", spline=True, gear=True) + [
    _st(
        len(_carb_base("φ28 bar", spline=True, gear=True)) + 1,
        "Threading",
        "feature_before_inspection",
        "Cut thread per drawing.",
        "CNC lathe",
    ),
    _st(
        len(_carb_base("φ28 bar", spline=True, gear=True)) + 2,
        "Magnetic Particle Inspection",
        "inspection",
        "MPI of ground gear teeth for grinding cracks.",
        "MPI bench",
    ),
    _st(
        len(_carb_base("φ28 bar", spline=True, gear=True)) + 3,
        "Final Inspection",
        "inspection",
        _inspect,
        "",
    ),
]

ROUTES["GS-002"] = _carb_base("φ26 bar", gear=True) + [
    _st(
        len(_carb_base("φ26 bar", gear=True)) + 1,
        "Threading",
        "feature_before_inspection",
        "Cut thread per drawing.",
        "CNC lathe",
    ),
    _st(len(_carb_base("φ26 bar", gear=True)) + 2, "Final Inspection", "inspection", _inspect, ""),
]

# 45 steel quench-and-tempered gear shaft: hobbing before quench & temper (soft state), gear is not carburized
ROUTES["GS-003"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ30 bar"), "Band saw"),
    _st(2, "Face Turning", "datum", _face, "CNC lathe"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    _st(5, "Semi-finish Turning", "semi_finish", _semi, "CNC lathe"),
    _st(
        6,
        "Gear Hobbing",
        "feature_before_heat",
        "Hob gear teeth in soft state.",
        "Gear hobbing machine",
    ),
    _st(7, "Heat Treatment", "heat_treatment", _ht_q_t, "Heat treatment furnace"),
    _st(8, "Repair Center Holes", "datum_recovery", _repair, "Center hole grinder"),
    _st(9, "Finish Turning", "finish", _finish, "CNC lathe"),
    _st(10, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(
        11,
        "Milling Keyway",
        "feature_before_inspection",
        "Mill keyway to drawing width/depth.",
        "Keyway milling machine",
    ),
    _st(12, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["GS-004"] = _carb_base("φ30 bar", spline=True, gear=True) + [
    _st(
        len(_carb_base("φ30 bar", spline=True, gear=True)) + 1,
        "Final Inspection",
        "inspection",
        _inspect,
        "",
    ),
]

# ── DS / PS / CS / SP / CM / CK / SS / RS ──
ROUTES["DS-001"] = _qt_base("φ45 bar") + [
    _st(9, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(
        10,
        "Spline Milling",
        "feature_before_inspection",
        "Mill spline on the output end.",
        "Spline milling machine",
    ),
    _st(11, "Threading", "feature_before_inspection", "Cut thread per drawing.", "CNC lathe"),
    _st(
        12,
        "Dynamic Balancing",
        "packaging",
        "Dynamic balance to target grade.",
        "Balancing machine",
    ),
    _st(13, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["PS-001"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ25 bar"), "Band saw"),
    _st(2, "Face Turning", "datum", _face, "CNC lathe"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    _st(5, "Semi-finish Turning", "semi_finish", _semi, "CNC lathe"),
    _st(6, "Finish Turning", "finish", _finish, "CNC lathe"),
    _st(7, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(
        8,
        "Milling Keyway",
        "feature_before_inspection",
        "Mill keyway to drawing width/depth.",
        "Keyway milling machine",
    ),
    _st(9, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["CS-001"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ28 bar"), "Band saw"),
    _st(2, "Face Turning", "datum", _face, "CNC lathe"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    _st(
        5,
        "Eccentric Turning",
        "semi_finish",
        "Turn eccentric pin surface with allowance.",
        "CNC lathe",
    ),
    _st(6, "Heat Treatment", "heat_treatment", _ht_q_t, "Heat treatment furnace"),
    _st(7, "Repair Center Holes", "datum_recovery", _repair, "Center hole grinder"),
    _st(8, "Finish Turning", "finish", _finish, "CNC lathe"),
    _st(
        9,
        "Grinding",
        "precision_finish",
        "Grind journal and eccentric pin to final size.",
        "External cylindrical grinder",
    ),
    _st(10, "Threading", "feature_before_inspection", "Cut thread per drawing.", "CNC lathe"),
    _st(11, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["SP-001"] = _qt_base("φ60 bar", boring=True, stress_relief=True) + [
    _st(
        10,
        "External Grinding",
        "precision_finish",
        "Grind external bearing seats and journals.",
        "External cylindrical grinder",
    ),
    _st(
        11,
        "Internal Grinding",
        "precision_finish",
        "Grind internal bore to final size.",
        "Internal cylindrical grinder",
    ),
    _st(
        12,
        "Taper Grinding",
        "precision_finish",
        "Grind spindle taper to gauge contact.",
        "Taper grinder",
    ),
    _st(
        13, "Threading", "feature_before_inspection", "Cut spindle thread per drawing.", "CNC lathe"
    ),
    _st(
        14, "Dynamic Balancing", "packaging", "High-precision dynamic balance.", "Balancing machine"
    ),
    _st(15, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["CM-001"] = _carb_base("φ35 bar", cam=True) + [
    _st(
        len(_carb_base("φ35 bar", cam=True)) + 1,
        "Threading",
        "feature_before_inspection",
        "Cut thread per drawing.",
        "CNC lathe",
    ),
    _st(len(_carb_base("φ35 bar", cam=True)) + 2, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["CK-001"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ50 bar"), "Band saw"),
    _st(2, "Face Milling", "datum", "Mill both faces and locate total length.", "Milling machine"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", "Rough turn main journals with allowance.", "CNC lathe"),
    _st(
        5,
        "Rough Turning Pins",
        "rough",
        "Rough turn crank pins (offset) with allowance.",
        "CNC crankshaft lathe",
    ),
    _st(
        6,
        "Oil Hole Drilling",
        "semi_finish",
        "Drill lubrication oil holes through journals and pins.",
        "Gun drilling machine",
    ),
    _st(7, "Heat Treatment", "heat_treatment", _ht_q_t, "Heat treatment furnace"),
    _st(8, "Repair Center Holes", "datum_recovery", _repair, "Center hole grinder"),
    _st(9, "Finish Turning", "finish", "Finish turn main journals.", "CNC lathe"),
    _st(10, "Finish Turning Pins", "finish", "Finish turn crank pins.", "CNC crankshaft lathe"),
    _st(
        11,
        "Journal Grinding",
        "precision_finish",
        "Grind main journals to final size.",
        "Crankshaft grinder",
    ),
    _st(
        12,
        "Pin Grinding",
        "precision_finish",
        "Grind crank pins to final size (radius fillets).",
        "Crankshaft grinder",
    ),
    _st(
        13,
        "Dynamic Balancing",
        "packaging",
        "Balance crankshaft to target grade.",
        "Balancing machine",
    ),
    _st(14, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["SS-001"] = [
    _st(1, "Blanking", "blank", _blank.format(bar="φ28 bar"), "Band saw"),
    _st(2, "Face Turning", "datum", _face, "CNC lathe"),
    _st(3, "Center Drilling", "datum", _center, "Center drill"),
    _st(4, "Rough Turning", "rough", _rough, "CNC lathe"),
    _st(5, "Semi-finish Turning", "semi_finish", _semi, "CNC lathe"),
    _st(
        6,
        "Spline Milling",
        "feature_before_heat",
        "Mill spline in soft state.",
        "Spline milling machine",
    ),
    _st(7, "Heat Treatment", "heat_treatment", _ht_q_t, "Heat treatment furnace"),
    _st(8, "Repair Center Holes", "datum_recovery", _repair, "Center hole grinder"),
    _st(9, "Finish Turning", "finish", _finish, "CNC lathe"),
    _st(10, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(11, "Threading", "feature_before_inspection", "Cut thread per drawing.", "CNC lathe"),
    _st(
        12,
        "Dynamic Balancing",
        "packaging",
        "Dynamic balance to target grade.",
        "Balancing machine",
    ),
    _st(13, "Final Inspection", "inspection", _inspect, ""),
]

ROUTES["RS-001"] = _qt_base("φ80 bar", stress_relief=True) + [
    _st(9, "Grinding", "precision_finish", _grind, "External cylindrical grinder"),
    _st(
        10,
        "Milling Keyway",
        "feature_before_inspection",
        "Mill keyway to drawing width/depth.",
        "Keyway milling machine",
    ),
    _st(11, "Threading", "feature_before_inspection", "Cut thread per drawing.", "CNC lathe"),
    _st(12, "Final Inspection", "inspection", _inspect, ""),
]


def renumber(steps):
    for i, s in enumerate(steps, 1):
        s["step_no"] = i
    return steps


def main():
    with open(CASES_FILE, encoding="utf-8") as f:
        cases = json.load(f)
    items = cases if isinstance(cases, list) else cases["cases"]

    missing = [cid for cid in ROUTES if cid not in {c["case_id"] for c in items}]
    if missing:
        raise SystemExit(f"ROUTES has ids not in cases.json: {missing}")

    updated = []
    for c in items:
        cid = c["case_id"]
        if cid in ROUTES:
            c["process_plan"] = renumber(ROUTES[cid])
            c["updated_at"] = c.get("updated_at")
            updated.append(cid)
        else:
            print(f"  [skip] {cid} (no route authored)")

    with open(CASES_FILE, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=2)

    print(f"Updated {len(updated)} cases: {', '.join(updated)}")
    print(f"Wrote {CASES_FILE}")


if __name__ == "__main__":
    main()
