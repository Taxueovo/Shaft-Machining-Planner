"""Material cost estimation module."""

from __future__ import annotations

import math
from typing import Any


# Static price table (CNY/kg, reference prices; density in g/cm3)
MATERIAL_PRICES: dict[str, dict[str, float]] = {
    "45":        {"price_per_kg": 5.5,  "density": 7.85},
    "40Cr":      {"price_per_kg": 6.8,  "density": 7.85},
    "42CrMo":    {"price_per_kg": 7.2,  "density": 7.85},
    "35CrMo":    {"price_per_kg": 7.0,  "density": 7.85},
    "20CrMnTi":  {"price_per_kg": 7.5,  "density": 7.85},
    "20Cr":      {"price_per_kg": 6.5,  "density": 7.85},
    "Q235":      {"price_per_kg": 4.2,  "density": 7.85},
    "45Mn2":     {"price_per_kg": 6.5,  "density": 7.85},
    "303":       {"price_per_kg": 16.0, "density": 7.93},
    "304":       {"price_per_kg": 18.0, "density": 7.93},
    "316":       {"price_per_kg": 22.0, "density": 7.98},
    "2Cr13":     {"price_per_kg": 15.0, "density": 7.75},
    "1Cr17Ni2":  {"price_per_kg": 16.0, "density": 7.75},
    "GCr15":     {"price_per_kg": 8.0,  "density": 7.81},
    "GCr15SiMn": {"price_per_kg": 8.5,  "density": 7.81},
    "6061":      {"price_per_kg": 25.0, "density": 2.70},
    "7075":      {"price_per_kg": 35.0, "density": 2.81},
    "H62":       {"price_per_kg": 55.0, "density": 8.50},
}


def estimate_material_cost(
    material: str,
    blank_type: str,
    outer_dia_mm: float,
    inner_dia_mm: float | None,
    length_mm: float,
) -> dict[str, Any]:
    """Estimate material cost.

    Parameters
    ----------
    material : str
        Material grade.
    blank_type : str
        Blank type: "solid" or "hollow".
    outer_dia_mm : float
        Outer diameter (mm).
    inner_dia_mm : float or None
        Inner diameter (mm), required for hollow blanks.
    length_mm : float
        Total length (mm).

    Returns
    -------
    dict
        Contains weight_kg, price_per_kg, estimated_cost.
    """
    # Look up material properties
    mat_key = material.strip().upper()
    mat_info = None
    for key, info in MATERIAL_PRICES.items():
        if key.upper() == mat_key:
            mat_info = info
            break
    if mat_info is None:
        # Default values
        mat_info = {"price_per_kg": 5.0, "density": 7.85}

    density = mat_info["density"]  # g/cm3
    price_per_kg = mat_info["price_per_kg"]  # CNY/kg

    # Compute volume (mm3)
    outer_r = outer_dia_mm / 2
    if blank_type == "hollow" and inner_dia_mm:
        inner_r = inner_dia_mm / 2
        volume_mm3 = math.pi * (outer_r ** 2 - inner_r ** 2) * length_mm
    else:
        volume_mm3 = math.pi * outer_r ** 2 * length_mm

    # Convert to weight (kg)
    # density unit g/cm3 = g/(1000mm3) = 1e-3 g/mm3 = 1e-6 kg/mm3
    weight_kg = volume_mm3 * density * 1e-6

    # Estimate cost
    estimated_cost = weight_kg * price_per_kg

    return {
        "material": material,
        "blank_type": blank_type,
        "weight_kg": round(weight_kg, 2),
        "price_per_kg": price_per_kg,
        "estimated_cost": round(estimated_cost, 2),
        "currency": "CNY",
    }
