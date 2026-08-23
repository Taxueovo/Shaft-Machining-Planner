"""Rule constants: feature mappings, material properties, machining parameters."""

from __future__ import annotations

from typing import Any, Optional


# ============================================================
# Feature names and machining operation mapping
# ============================================================

FEATURE_NAME = {
    "keyway": "Keyway",
    "hole": "Hole",
    "flat": "Flat",
    "thread": "Thread",
    "knurl": "Knurl",
    "bearing_seat": "Bearing Seat",
    "spline": "Spline",
    "taper": "Taper",
    "groove": "Groove",
    "seal_area": "Seal Area",
    "gear_teeth": "Gear Teeth",
    "flange": "Flange",
    "bore": "Bore",
    "cam": "Cam",
    "worm": "Worm",
    "crank_pin": "Crank Pin",
}

FEATURE_PROCESS = {
    "keyway": "Indexable Milling",
    "hole": "Drilling",
    "flat": "Indexable Milling",
    "thread": "Threading",
    "knurl": None,
    "bearing_seat": "Cylindrical Grinding",
    "spline": "Gear Hobbing",
    "taper": "Taper Turning",
    "groove": "Grooving",
    "seal_area": "Cylindrical Grinding",
    "gear_teeth": "Gear Hobbing",
    "flange": "Turning + Drilling",
    "bore": "Boring",
    "cam": "Cam Grinding",
    "worm": "Worm Grinding",
    "crank_pin": "ISO Turning",
}

HEAT_NAME = {
    "none": "None",
    "normalizing": "Normalizing",
    "quench_temper": "Quench and Temper",
    "carburize_quench": "Carburize and Quench",
    "quench_and_temper": "Quench and Temper",
    "nitriding": "Nitriding",
    "induction_hardening": "Induction Hardening",
}

SURFACE_NAME = {
    "none": "None",
    "blackening": "Blackening",
    "chrome_plating": "Chrome Plating",
    "zinc_plating": "Zinc Plating",
    "dacromet": "Dacromet",
}


# ============================================================
# Material machining properties
# ============================================================

MATERIAL_PROPERTIES: dict[str, dict[str, Any]] = {
    "45": {
        "iso_category": "P",
        "machinability": "good",
        "hardness": "medium",
        "cutting_speed_factor": 1.0,
        "feed_factor": 1.0,
        "notes": "Quality carbon structural steel, most common motor shaft material",
        "recommended_heat_treatment": "quench_temper",
    },
    "40Cr": {
        "iso_category": "P",
        "machinability": "good",
        "hardness": "medium_high",
        "cutting_speed_factor": 0.9,
        "feed_factor": 0.95,
        "notes": "Alloy structural steel, good comprehensive properties after quench and temper",
        "recommended_heat_treatment": "quench_temper",
    },
    "42CrMo": {
        "iso_category": "P",
        "machinability": "moderate",
        "hardness": "high",
        "cutting_speed_factor": 0.85,
        "feed_factor": 0.9,
        "notes": "High-strength alloy steel, first choice for heavy-duty shafts",
        "recommended_heat_treatment": "quench_temper",
    },
    "35CrMo": {
        "iso_category": "P",
        "machinability": "good",
        "hardness": "medium_high",
        "cutting_speed_factor": 0.88,
        "feed_factor": 0.92,
        "notes": "Medium-carbon alloy steel, high fatigue strength",
        "recommended_heat_treatment": "quench_temper",
    },
    "20Cr": {
        "iso_category": "P",
        "machinability": "moderate",
        "hardness": "medium",
        "cutting_speed_factor": 0.85,
        "feed_factor": 0.9,
        "notes": "Carburizing steel, used for camshafts and wear-resistant parts",
        "recommended_heat_treatment": "carburize_quench",
    },
    "20CrMnTi": {
        "iso_category": "P",
        "machinability": "moderate",
        "hardness": "medium",
        "cutting_speed_factor": 0.85,
        "feed_factor": 0.9,
        "notes": "Carburizing steel, hard surface tough core",
        "recommended_heat_treatment": "carburize_quench",
    },
    "Q235": {
        "iso_category": "P",
        "machinability": "excellent",
        "hardness": "low",
        "cutting_speed_factor": 1.1,
        "feed_factor": 1.1,
        "notes": "Plain carbon steel, low-cost light-duty shaft",
        "recommended_heat_treatment": "none",
    },
    "45Mn2": {
        "iso_category": "P",
        "machinability": "good",
        "hardness": "medium_high",
        "cutting_speed_factor": 0.9,
        "feed_factor": 0.95,
        "notes": "Quenched and tempered steel, high-strength medium-duty shaft",
        "recommended_heat_treatment": "quench_temper",
    },
    "303": {
        "iso_category": "M",
        "machinability": "moderate",
        "hardness": "medium",
        "cutting_speed_factor": 0.65,
        "feed_factor": 0.75,
        "notes": "Free-machining austenitic stainless steel, excellent machinability",
        "recommended_heat_treatment": "none",
    },
    "304": {
        "iso_category": "M",
        "machinability": "difficult",
        "hardness": "medium",
        "cutting_speed_factor": 0.6,
        "feed_factor": 0.7,
        "notes": "Austenitic stainless steel, corrosion-resistant general purpose",
        "recommended_heat_treatment": "none",
        "machining_notes": "Stainless steel machining requires lower cutting speed, use cobalt tools",
    },
    "316": {
        "iso_category": "M",
        "machinability": "difficult",
        "hardness": "medium",
        "cutting_speed_factor": 0.55,
        "feed_factor": 0.65,
        "notes": "Acid-alkali resistant, chemical equipment shaft",
        "recommended_heat_treatment": "none",
        "machining_notes": "316 is more difficult to machine than 304, recommend coated carbide tools",
    },
    "2Cr13": {
        "iso_category": "M",
        "machinability": "moderate",
        "hardness": "medium_high",
        "cutting_speed_factor": 0.7,
        "feed_factor": 0.8,
        "notes": "Martensitic stainless steel, can be strengthened by heat treatment",
        "recommended_heat_treatment": "quench_temper",
    },
    "1Cr17Ni2": {
        "iso_category": "M",
        "machinability": "moderate",
        "hardness": "high",
        "cutting_speed_factor": 0.65,
        "feed_factor": 0.75,
        "notes": "High-strength stainless steel",
        "recommended_heat_treatment": "quench_temper",
    },
    "GCr15": {
        "iso_category": "H",
        "machinability": "difficult",
        "hardness": "very_high",
        "cutting_speed_factor": 0.5,
        "feed_factor": 0.6,
        "notes": "High-carbon chromium bearing steel, high hardness high wear resistance",
        "recommended_heat_treatment": "quench_temper",
        "machining_notes": "Bearing steel is hard, use ceramic or CBN tools for finishing",
    },
    "GCr15SiMn": {
        "iso_category": "H",
        "machinability": "difficult",
        "hardness": "very_high",
        "cutting_speed_factor": 0.48,
        "feed_factor": 0.58,
        "notes": "Large section bearing steel",
        "recommended_heat_treatment": "quench_temper",
    },
    "6061": {
        "iso_category": "N",
        "machinability": "excellent",
        "hardness": "low",
        "cutting_speed_factor": 3.0,
        "feed_factor": 1.5,
        "notes": "Lightweight shaft, easy to cut",
        "recommended_heat_treatment": "none",
        "machining_notes": "Aluminum cutting speed can be increased 2-3x, ensure chip evacuation",
    },
    "7075": {
        "iso_category": "N",
        "machinability": "good",
        "hardness": "medium",
        "cutting_speed_factor": 2.5,
        "feed_factor": 1.3,
        "notes": "High-strength aluminum alloy, aerospace shaft",
        "recommended_heat_treatment": "none",
    },
    "H62": {
        "iso_category": "N",
        "machinability": "excellent",
        "hardness": "low",
        "cutting_speed_factor": 2.0,
        "feed_factor": 1.2,
        "notes": "Easy to cut, corrosion resistant",
        "recommended_heat_treatment": "none",
    },
}


def get_material_properties(material: str) -> dict[str, Any]:
    """Get material machining properties, unknown material returns defaults."""
    material_upper = material.strip().upper()
    for key, props in MATERIAL_PROPERTIES.items():
        if key.upper() == material_upper:
            return props
    for key, props in MATERIAL_PROPERTIES.items():
        if len(key) >= 2 and (key.upper() in material_upper or material_upper in key.upper()):
            return props
    return {
        "iso_category": "P",
        "machinability": "moderate",
        "hardness": "medium",
        "cutting_speed_factor": 1.0,
        "feed_factor": 1.0,
        "notes": "Unknown material, treated as carbon steel",
        "recommended_heat_treatment": "none",
    }


def is_high_precision(
    upper: Optional[float], lower: Optional[float], roughness: Optional[float]
) -> bool:
    values = [abs(v) for v in (upper, lower) if v is not None]
    return (bool(values) and max(values) <= 0.02) or (roughness is not None and roughness <= 0.8)


def is_feature_high_precision(feature: Any) -> bool:
    """Determine whether the feature requires a precision machining chain.

    Bearing seats often specify accuracy via IT grades, so the generic
    tolerance/roughness fields alone are not sufficient.
    """

    def value(name: str) -> Any:
        return feature.get(name) if isinstance(feature, dict) else getattr(feature, name, None)

    if is_high_precision(
        value("tolerance_upper_mm"),
        value("tolerance_lower_mm"),
        value("roughness_ra"),
    ):
        return True
    return value("feature_type") == "bearing_seat" and str(
        value("bearing_seat_tolerance") or ""
    ).upper() in {"IT5", "IT6"}


def requires_grinding(
    upper: Optional[float], lower: Optional[float], roughness: Optional[float]
) -> bool:
    """Whether grinding is required: tolerance <=0.01mm or roughness Ra <=0.4."""
    values = [abs(v) for v in (upper, lower) if v is not None]
    return (bool(values) and max(values) <= 0.01) or (roughness is not None and roughness <= 0.4)


# ============================================================
# Feature machining timing classification
# ============================================================

FEATURE_SUPPORTS_SPLIT = {
    "keyway": True,
    "hole": True,
    "flat": True,
    "thread": True,
    "knurl": False,
    "bearing_seat": True,
    "spline": True,
    "taper": True,
    "groove": False,
    "seal_area": True,
    "gear_teeth": True,
    "flange": False,
    "bore": True,
    "cam": False,
    "worm": False,
    "crank_pin": False,
}

FEATURE_REQUIRED_PROCESS = {
    "keyway": {"Indexable Milling"},
    "hole": {"Drilling"},
    "flat": {"Indexable Milling"},
    "thread": {"Threading"},
    "knurl": set(),
    "bearing_seat": {"ISO Turning", "Cylindrical Grinding"},
    "spline": {"Gear Hobbing"},
    "taper": {"Taper Turning"},
    "groove": {"Grooving"},
    "seal_area": {"ISO Turning", "Cylindrical Grinding"},
    "gear_teeth": {"Gear Hobbing"},
    "flange": {"ISO Turning", "Drilling"},
    "bore": {"Boring"},
    "cam": {"Cam Grinding"},
    "worm": {"Worm Grinding", "ISO Turning"},
    "crank_pin": {"ISO Turning", "Cylindrical Grinding"},
}
