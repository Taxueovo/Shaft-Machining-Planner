"""Input Pydantic models."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ShaftSegment(BaseModel):
    segment_id: str = Field(min_length=1, max_length=30)
    diameter_mm: float = Field(gt=0)
    length_mm: float = Field(gt=0)
    diameter_upper_deviation_mm: Optional[float] = None
    diameter_lower_deviation_mm: Optional[float] = None
    roughness_ra: Optional[float] = Field(default=None, gt=0)
    # Additional geometry information (input-provided, preserved when the segment round-trips)
    surface_area_mm2: Optional[float] = Field(default=None, gt=0)
    segment_type: Optional[str] = None


class FeatureInput(BaseModel):
    feature_id: str = Field(min_length=1, max_length=30)
    feature_type: Literal[
        "keyway", "hole", "flat", "thread", "knurl",
        "bearing_seat", "spline", "taper", "groove",
        "seal_area", "gear_teeth", "flange", "bore",
        "cam", "worm", "crank_pin",
    ]
    positioning_mode: Literal["segment_relative", "global_absolute"]
    segment_index: Optional[int] = Field(default=None, ge=1)
    segment_offset_mm: Optional[float] = Field(default=None, ge=0)
    global_position_mm: Optional[float] = Field(default=None, ge=0)

    tolerance_upper_mm: Optional[float] = None
    tolerance_lower_mm: Optional[float] = None
    roughness_ra: Optional[float] = Field(default=None, gt=0)
    processing_timing: Literal[
        "undecided",
        "before_heat_treatment",
        "before_and_after_heat_treatment",
    ] = "undecided"

    keyway_width_mm: Optional[float] = Field(default=None, gt=0)
    keyway_depth_mm: Optional[float] = Field(default=None, gt=0)
    # Keyway cross-section classification: profile_key / wedge_key / flat_key
    keyway_type: Optional[str] = None
    hole_diameter_mm: Optional[float] = Field(default=None, gt=0)
    hole_type: Optional[Literal["through", "blind"]] = None
    hole_depth_mm: Optional[float] = Field(default=None, gt=0)
    hole_direction: Optional[Literal["radial", "axial"]] = None
    # Number of holes at the same axial position (e.g. 2/4 radial oil holes)
    hole_count: Optional[int] = Field(default=None, ge=1)
    # Angular start angle of the hole (deg): angle of the first hole when multiple holes share a position
    hole_angle_deg: Optional[float] = Field(default=None, ge=0, lt=360)
    flat_width_mm: Optional[float] = Field(default=None, gt=0)
    thread_specification: Optional[str] = None
    thread_handedness: Optional[Literal["right", "left"]] = None
    thread_accuracy_grade: Optional[str] = None
    knurl_type: Optional[Literal["straight", "diamond"]] = None
    feature_length_mm: Optional[float] = Field(default=None, gt=0)

    bearing_seat_diameter_mm: Optional[float] = Field(default=None, gt=0)
    bearing_seat_tolerance: Optional[str] = None
    spline_type: Optional[Literal["involute", "straight"]] = None
    spline_teeth: Optional[int] = Field(default=None, gt=0)
    spline_module: Optional[float] = Field(default=None, gt=0)
    # Spline centering and gear-hobbing parameters
    spline_major_diameter_mm: Optional[float] = Field(default=None, gt=0)
    spline_minor_diameter_mm: Optional[float] = Field(default=None, gt=0)
    spline_pressure_angle_deg: Optional[float] = Field(default=None, gt=0)
    spline_key_width_mm: Optional[float] = Field(default=None, gt=0)
    taper_ratio: Optional[float] = Field(default=None, gt=0)
    taper_large_diameter_mm: Optional[float] = Field(default=None, gt=0)
    taper_length_mm: Optional[float] = Field(default=None, gt=0)
    groove_type: Optional[Literal["snap_ring", "thread_relief", "undercut", "seal"]] = None
    groove_width_mm: Optional[float] = Field(default=None, gt=0)
    groove_depth_mm: Optional[float] = Field(default=None, gt=0)
    seal_type: Optional[Literal["rubber", "mechanical", "labyrinth"]] = None
    seal_diameter_mm: Optional[float] = Field(default=None, gt=0)
    gear_module: Optional[float] = Field(default=None, gt=0)
    gear_teeth: Optional[int] = Field(default=None, gt=0)
    gear_pressure_angle: Optional[float] = Field(default=None, gt=0)
    gear_face_width_mm: Optional[float] = Field(default=None, gt=0)
    # Gear details: spur/helical + helix angle + full tooth height + tip/root circle diameters
    gear_type: Optional[Literal["spur", "helical"]] = None
    helix_angle_deg: Optional[float] = Field(default=None, ge=0, lt=90)
    gear_tooth_height_mm: Optional[float] = Field(default=None, gt=0)
    # Gear tip circle diameter (addendum x2) / root circle diameter (dedendum x2):
    # basis for blank rough turning and gear hobbing
    gear_outer_diameter_mm: Optional[float] = Field(default=None, gt=0)
    gear_root_diameter_mm: Optional[float] = Field(default=None, gt=0)
    # Whether the gear needs post-heat-treatment finishing (gear grinding / hard hobbing):
    # True -> schedule gear finishing after hardening; None/False -> keep the existing
    # logic of splitting finishing only for high-precision gears.
    gear_finish_required: Optional[bool] = None
    flange_diameter_mm: Optional[float] = Field(default=None, gt=0)
    flange_thickness_mm: Optional[float] = Field(default=None, gt=0)
    flange_holes: Optional[int] = Field(default=None, ge=0)
    # Bore feature: stepped/multi-segment bore, one feature per segment
    bore_diameter_mm: Optional[float] = Field(default=None, gt=0)
    bore_length_mm: Optional[float] = Field(default=None, gt=0)
    bore_through: Optional[bool] = None
    # Cam (camshaft cam profile): only feature_length_mm is required, the rest are optional
    cam_type: Optional[Literal["grinding", "milling"]] = None
    cam_lobe_count: Optional[int] = Field(default=None, ge=1)
    cam_base_circle_diameter_mm: Optional[float] = Field(default=None, gt=0)
    cam_lobe_lift_mm: Optional[float] = Field(default=None, gt=0)
    # Worm (worm thread profile): only feature_length_mm is required, the rest are optional
    worm_module: Optional[float] = Field(default=None, gt=0)
    worm_starts: Optional[int] = Field(default=None, ge=1)
    worm_pressure_angle_deg: Optional[float] = Field(default=None, ge=0, lt=90)
    worm_outer_diameter_mm: Optional[float] = Field(default=None, gt=0)
    # Crank pin (crankshaft eccentric connecting-rod journal): only feature_length_mm is required, the rest are optional
    crank_pin_diameter_mm: Optional[float] = Field(default=None, gt=0)
    crank_pin_width_mm: Optional[float] = Field(default=None, gt=0)
    crank_offset_mm: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_feature(self) -> "FeatureInput":
        if self.positioning_mode == "segment_relative":
            if self.segment_index is None or self.segment_offset_mm is None:
                raise ValueError("Segment relative positioning requires segment index and offset.")
        elif self.global_position_mm is None:
            raise ValueError("Global absolute positioning requires global position.")

        required: dict[str, Any]
        if self.feature_type == "keyway":
            required = {
                "keyway_width_mm": self.keyway_width_mm,
                "keyway_depth_mm": self.keyway_depth_mm,
                "feature_length_mm": self.feature_length_mm,
            }
        elif self.feature_type == "hole":
            required = {
                "hole_diameter_mm": self.hole_diameter_mm,
                "hole_type": self.hole_type,
                "hole_direction": self.hole_direction,
            }
            if self.hole_type == "blind" and self.hole_depth_mm is None:
                raise ValueError("Blind hole requires depth.")
        elif self.feature_type == "flat":
            required = {
                "flat_width_mm": self.flat_width_mm,
                "feature_length_mm": self.feature_length_mm,
            }
        elif self.feature_type == "thread":
            required = {
                "thread_specification": self.thread_specification,
                "thread_handedness": self.thread_handedness,
                "feature_length_mm": self.feature_length_mm,
            }
        elif self.feature_type == "knurl":
            required = {
                "knurl_type": self.knurl_type,
                "feature_length_mm": self.feature_length_mm,
            }
        elif self.feature_type == "bearing_seat":
            required = {
                "bearing_seat_diameter_mm": self.bearing_seat_diameter_mm,
                "feature_length_mm": self.feature_length_mm,
            }
        elif self.feature_type == "spline":
            required = {
                "spline_type": self.spline_type,
                "spline_teeth": self.spline_teeth,
                "feature_length_mm": self.feature_length_mm,
            }
        elif self.feature_type == "taper":
            required = {
                "taper_ratio": self.taper_ratio,
                "taper_large_diameter_mm": self.taper_large_diameter_mm,
                "taper_length_mm": self.taper_length_mm,
            }
        elif self.feature_type == "groove":
            required = {
                "groove_type": self.groove_type,
                "groove_width_mm": self.groove_width_mm,
                "groove_depth_mm": self.groove_depth_mm,
            }
        elif self.feature_type == "seal_area":
            required = {
                "seal_type": self.seal_type,
                "seal_diameter_mm": self.seal_diameter_mm,
                "feature_length_mm": self.feature_length_mm,
            }
        elif self.feature_type == "gear_teeth":
            required = {
                "gear_module": self.gear_module,
                "gear_teeth": self.gear_teeth,
                "gear_face_width_mm": self.gear_face_width_mm,
            }
        elif self.feature_type == "flange":
            required = {
                "flange_diameter_mm": self.flange_diameter_mm,
                "flange_thickness_mm": self.flange_thickness_mm,
            }
        elif self.feature_type == "bore":
            required = {
                "bore_diameter_mm": self.bore_diameter_mm,
                "bore_length_mm": self.bore_length_mm,
            }
        else:
            required = {
                "feature_length_mm": self.feature_length_mm,
            }

        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise ValueError("Feature missing required parameters: " + ", ".join(missing))
        return self


class GlobalRequirements(BaseModel):
    heat_treatment: Literal[
        "none",
        "normalizing",
        "quench_temper",
        "carburize_quench",
        "quench_and_temper",
        "nitriding",
        "induction_hardening",
    ] = "none"
    heat_treatment_note: Optional[str] = None
    target_hardness_hrc: Optional[float] = Field(default=None, gt=0, le=75)
    case_depth_mm: Optional[float] = Field(default=None, gt=0, le=20)
    blank_condition: Literal["bar", "forged", "normalized", "annealed", "unknown"] = "unknown"
    pre_heat_treatment: Literal["auto", "none", "normalizing", "annealing", "stress_relief"] = "auto"
    surface_treatment: Literal[
        "none",
        "blackening",
        "chrome_plating",
        "zinc_plating",
        "dacromet",
    ] = "none"
    batch_quantity: int = Field(default=1, ge=1)


class FeatureChoice(BaseModel):
    feature_id: str
    processing_timing: Literal[
        "before_heat_treatment",
        "before_and_after_heat_treatment",
    ]


class ChoicesRequest(BaseModel):
    choices: list[FeatureChoice] = Field(min_length=1)
