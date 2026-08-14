# BREP Feature Extraction Methods

> Compiled from the feature extraction modules under `Scripts/`
> Last updated: 2026-05-21

---

## 1. Module Structure

```
Scripts/
├── base_loader.py          # BREP base loading, main axis detection, face analysis, inner/outer surface classification
├── cylinder_features.py    # Cylinder feature extraction (outer cylinders, inner bores, stepped shaft segments)
├── keyway_features.py      # Keyway feature extraction (flat keys, wedge keys, profile keys)
├── radial_holes.py         # Radial hole feature extraction (oil thrower holes, oil passages)
├── spline_features.py      # Spline feature extraction (involute spline parameters)
├── gear_features.py        # Gear feature extraction (Z-axis scanning method v9)
└── main_extractor.py       # Main runner script integrating all modules
```

---

## 2. Feature Capture Methods per Module

### 2.1 Base Functionality (`BaseBREPLoader`)

| Function | Method |
|-----|------|
| BREP file loading | `BRepTools.Read()` |
| Main axis detection | Axis direction of the outer cylindrical face with the largest radius (>10mm) |
| Face type recognition | `BRepAdaptor_Surface` yields surface types: cylinder, plane, bspline, revolution |
| Geometric property computation | `brepgprop.SurfaceProperties()` computes area and centroid |
| **Inner/outer surface classification** | Based on the `is_reversed` flag: `is_reversed=False` -> outer surface, `is_reversed=True` -> inner surface |

**SurfaceType enum**
| Classification | Description |
|-----|------|
| `outer` | Outer surface (normal points away from the axis) |
| `inner` | Inner surface (normal points toward the axis) |
| `unknown` | Unknown / cannot be determined |

---

### 2.2 Cylinder Features (`CylinderFeatureExtractor`)

| Feature type | Capture method |
|---------|---------|
| **Outer cylinders** | 1. Filter cylindrical faces whose axis is **parallel to the main axis**<br>2. Classify: `surface_classification='outer'` or the largest radius<br>3. Use the **V parameter range (v_length)** to compute the actual axial length<br>4. Classify: radius >30mm and longest segment -> `Rotor_Core_Fit` |
| **Inner bore** | 1. Axis **parallel to the main axis**<br>2. Classify: `surface_classification='inner'` or radius smaller than the outer cylinder<br>3. Group by radius and record the axial position range |
| **Stepped shaft segment length** | Computed from the **V parameter range** (v_length) of the cylindrical faces |

**Core classification logic**:
- Parallel test: `|axis1·axis2| ≈ 1`
- Radius tolerance: 0.1 mm
- Length computation: `v_length = |LastVParameter - FirstVParameter|`

**Key parameters**:
| Parameter | Value | Description |
|-----|-----|-----|
| `RADIUS_TOLERANCE` | 0.1 mm | Radius comparison tolerance for cylindrical faces |
| `RADIAL_HOLE_RADIUS_MAX` | 10.0 mm | Maximum radial hole radius |
| `EXCLUSION_ZONE_MARGIN` | 5.0 mm | Safety margin around exclusion zones |

---

### 2.3 Keyway Features (`KeywayExtractor`)

| Feature type | Capture method |
|---------|---------|
| **Keyway detection** | 1. Filter **plane faces** (surface_type == 'plane')<br>2. Normal **perpendicular to the main axis** (dot < 0.1, radial plane)<br>3. Located near the shaft surface (radial distance deviation < 30%) |
| **Keyway classification** | By depth/width ratio:<br>• `flat_key`: 0.25 <= depth ratio <= 0.6<br>• `wedge_key`: depth ratio > 0.6<br>• `profile_key`: depth ratio < 0.25 |
| **Parameter extraction** | Width: estimated from area and aspect ratio<br>Depth: max_radius - radial distance<br>Axial position: coordinate projected onto the main axis |

**[v2 refactor] key changes**:
- Exact distances computed with BRepExtrema_DistShapeShape
- Absolute area thresholds replaced with ratio-based calculations
- Keyway width computed via normal projection

**Key parameters**:
| Parameter | Value | Description |
|-----|-----|-----|
| `MIN_KEYWAY_AREA_MM2` | 5.0 mm² | Minimum keyway area (noise suppression) |
| `RADIAL_PLANE_DOT_THRESHOLD` | 0.1 | Radial plane classification tolerance |
| `SURFACE_DISTANCE_RATIO_TOL` | 0.35 | Surface distance ratio tolerance (35%) |
| `KEYWAY_WIDTH_MIN` | 1.0 mm | Minimum keyway width |
| `KEYWAY_WIDTH_MAX` | 30.0 mm | Maximum keyway width |

**Computation formulas**:
```
depth = max_outer_radius - radial_distance
estimated_width = sqrt(area / 5.0)  # Assumes an aspect ratio of about 5
keyway_type = classify_by_depth_ratio(depth, width)
```

---

### 2.4 Radial Hole Features (`RadialHoleExtractor`)

| Feature type | Capture method |
|---------|---------|
| **Radial hole detection** | 1. Filter **inner cylindrical faces** (`surface_classification='inner'`)<br>2. Axis **perpendicular to the main axis** (dot < 0.1)<br>3. Penetration topology check: bore radius < 50% of the outer cylinder radius<br>4. Deduplication: same position, same radius, angle difference < 15° |
| **Group statistics** | Grouped by Z axis position; outputs the hole count per position |
| **Angular position** | Circumferential angle computed with `atan2(y, x)` |

**[v2 refactor] key changes**:
- Axis perpendicularity checked with `Axis().Direction()`
- Anti-hallucination filtering: discards faces that are too small in area
- Improved penetration topology detection

**Key parameters**:
| Parameter | Value | Description |
|-----|-----|-----|
| `AXIS_PERPENDICULAR_TOL` | 0.1 | Axis perpendicularity tolerance |
| `MIN_HOLE_AREA_MM2` | 1.0 mm² | Minimum hole area (noise suppression) |
| `MIN_HOLE_RADIUS` | 0.5 mm | Minimum hole radius |
| `MAX_HOLE_RADIUS_RATIO` | 0.5 | Maximum hole radius / outer cylinder radius ratio |
| `ANGLE_GROUPING_TOL` | 15.0° | Angle grouping tolerance |
| `Z_POSITION_TOL` | 2.0 mm | Z position grouping tolerance |

**Computation formulas**:
```
# Axis perpendicularity check
dot = |hole_dir · main_dir|
is_radial = dot < AXIS_PERPENDICULAR_TOL

# Penetration topology check
radius_ratio = hole_radius / max_outer_radius
is_penetrating = radius_ratio < MAX_HOLE_RADIUS_RATIO

# Angle computation
angle = degrees(atan2(y, x))  # projected onto the plane orthogonal to the main axis
```

---

### 2.5 Spline Features (`SplineFeatureExtractor`)

| Feature type | Capture method |
|---------|---------|
| **Spline zone detection** | 1. Divide the main axis into 20 bins<br>2. Count the outer cylindrical faces per bin<br>3. Bins whose face density exceeds the threshold (avg x 1.5 or >= 5) are marked as spline zones<br>4. Validate spline candidate zones with the **h/m ratio** |
| **Major/minor diameter extraction** | • Tip circle radius: maximum cylindrical face radius in the zone<br>• Dedendum circle radius: minimum cylindrical face radius in the zone |
| **Tooth count** | DIN 5480 short-tooth splines: `z = (D/m) - 0.9`, with `m = h/1.1` |
| **Type determination** | Involute splines, pressure angle 30° or 45° |

**[v2 refactor] key changes**:
- All hard-coded radius thresholds removed
- Outer cylindrical faces detected with `surface_classification='outer'`
- Feature validation based on the **h/m dimensionless ratio** (DIN 5480 short-tooth standard)

**Key parameters**:
| Parameter | Value | Description |
|-----|-----|-----|
| `SPLINE_HEIGHT_RATIO_MIN` | 0.8 | h/m lower bound (DIN 5480 short teeth) |
| `SPLINE_HEIGHT_RATIO_MAX` | 3.5 | h/m upper bound |
| `MIN_SPLINE_TOOTH_COUNT` | 15 | Minimum tooth count |
| `MAX_SPLINE_TOOTH_COUNT` | 60 | Maximum tooth count |

**Computation formulas**:
```
# Spline working tooth height (DIN 5480 short teeth)
module = tooth_height / 1.1

# External spline tooth count
tooth_count = (2 * addendum_radius / module) - 0.9

# Module fitting
standard_modules = [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
closest_module = min(standard_modules, key=lambda x: abs(x - raw_module))
```

---

### 2.6 Gear Features (`GearFeatureExtractor`)

| Feature type | Capture method |
|---------|---------|
| **Gear zone detection** | 1. Z-axis scanning: cut a section every 5mm along the main axis<br>2. Analyze the radius difference of each section (max_radius - min_radius)<br>3. Sections with a radius difference > 0.4mm are marked as gear zones<br>4. Merge adjacent gear zones |
| **Tip/dedendum circles** | • Tip circle radius: maximum of the section's outer boundary<br>• Dedendum circle radius: minimum of the outer boundary (only points > 0.85 x r_max) |
| **Tooth count** | Precise peak detection: 3600 sample points, local maxima method |
| **Helix angle** | From the phase difference between adjacent sections: `β = arctan(r × Δθ / ΔZ)` |
| **Gear type** | Helix angle > 2° -> helical gear, otherwise -> spur gear |

**[v9 refactor] key changes**:
- Completely rebuilt around the **Z-axis scanning method**
- Section cutting with BRepAlgoAPI_Section
- Curve sampling with BRepAdaptor_Curve
- Introduced the **ISO standard module fitting algorithm**
- Introduced the **mechanical geometric consistency filter**

**Key parameters**:
| Parameter | Value | Description |
|-----|-----|-----|
| `SAMPLE_POINT_COUNT` | 3600 | Sample points per edge |
| `SECTION_STEP_MM` | 5.0 mm | Z-axis scan step |
| `MIN_TOOTH_HEIGHT_DIFF` | 0.4 mm | Minimum tooth height threshold (lowered to detect small-module gears) |
| `PEAK_NEIGHBOR_COUNT` | 3 | Neighbor points for peak detection |
| `PEAK_RADIUS_RATIO` | 0.9 | Peak radius threshold |

**ISO standard module series**:
```python
STANDARD_MODULES = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 8.0, 10.0]
```

**Module fitting algorithm**:
```python
raw_module = (2 * max_radius) / (tooth_count + 2)
closest_module = min(STANDARD_MODULES, key=lambda x: abs(x - raw_module))
if abs(raw_module - closest_module) / raw_module < 0.15:
    module = closest_module  # Fitted to the standard value
else:
    module = raw_module  # Keep the raw value (possibly an inch-series DP gear)
```

**Mechanical geometric consistency filter (`_is_valid_gear_geometry`)**:

| Rule | Condition | Excludes |
|------|------|----------|
| Rule 1: Extreme tooth count | `6 <= tooth_count <= 300` | Transition faces / anomalies |
| Rule 2: Thin-slice noise | `axial_width >= 1.5m` | 2D single pieces |
| Rule 3: Short-tooth splines | `tooth_height/m >= 1.5` | Spline false positives |
| Rule 4: Sampling quality | `len(polar_data) >= 50` | Invalid outlines of fragmented faces |

**Computation formulas**:
```
# Tooth height / module ratio
height_ratio = (max_radius - min_radius) / module

# Face width / module ratio
width_ratio = axial_width / module

# Helix angle
beta_rad = arctan(pitch_radius * avg_delta_theta / delta_z)
```

---

## 3. Complete Feature Extraction Pipeline

```
main_extractor.py
│
├─ 1. BaseBREPLoader.load_brep()            Load the BREP file
├─ 2. compute_bounding_box()                Compute the bounding box
├─ 3. traverse_faces()                      Traverse all faces and extract geometric data
│   └─ _classify_cylinder_surface()         Classify inner/outer surfaces based on is_reversed
├─ 4. detect_main_axis()                    Detect the main axis direction
├─ 5. CylinderFeatureExtractor.extract()    Extract outer cylinders / inner bores
│   └─ Exact lengths computed with v_length
├─ 6. SplineFeatureExtractor.extract()      Extract spline zones and parameters
│   └─ Validated with the h/m dimensionless ratio (DIN 5480)
├─ 7. RadialHoleExtractor.extract()         Extract radial holes
│   └─ Penetration topology detection
├─ 8. KeywayExtractor.extract()             Extract keyways
│   └─ Classification by depth ratio
├─ 9. GearFeatureExtractor.extract()        Extract gear zones and parameters
│   ├─ _z_axis_scan()                      Z-axis section scan
│   ├─ _find_peaks_precise()               Precise tooth count detection
│   ├─ _is_valid_gear_geometry()           Mechanical geometric consistency filter
│   └─ _calculate_helix_angle()            Helix angle computation (outlier filtering)
│
└─ Output JSON
```

---

## 4. Key Threshold Summary

### 4.1 Cylinder Features
| Parameter | Value | Description |
|-----|-----|-----|
| `RADIUS_TOLERANCE` | 0.1 mm | Radius comparison tolerance for cylindrical faces |
| `RADIAL_HOLE_RADIUS_MAX` | 10.0 mm | Maximum radial hole radius |
| `EXCLUSION_ZONE_MARGIN` | 5.0 mm | Safety margin around exclusion zones |

### 4.2 Keyway Features
| Parameter | Value | Description |
|-----|-----|-----|
| `MIN_KEYWAY_AREA_MM2` | 5.0 mm² | Minimum keyway area (noise suppression) |
| `RADIAL_PLANE_DOT_THRESHOLD` | 0.1 | Radial plane classification tolerance |
| `SURFACE_DISTANCE_RATIO_TOL` | 0.35 | Surface distance ratio tolerance (35%) |
| `KEYWAY_WIDTH_MIN` | 1.0 mm | Minimum keyway width |
| `KEYWAY_WIDTH_MAX` | 30.0 mm | Maximum keyway width |

### 4.3 Radial Hole Features
| Parameter | Value | Description |
|-----|-----|-----|
| `AXIS_PERPENDICULAR_TOL` | 0.1 | Axis perpendicularity tolerance |
| `MIN_HOLE_AREA_MM2` | 1.0 mm² | Minimum hole area (noise suppression) |
| `MIN_HOLE_RADIUS` | 0.5 mm | Minimum hole radius |
| `MAX_HOLE_RADIUS_RATIO` | 0.5 | Maximum hole radius / outer cylinder radius ratio |
| `ANGLE_GROUPING_TOL` | 15.0° | Angle grouping tolerance |
| `Z_POSITION_TOL` | 2.0 mm | Z position grouping tolerance |

### 4.4 Spline Features
| Parameter | Value | Description |
|-----|-----|-----|
| `SPLINE_HEIGHT_RATIO_MIN` | 0.8 | h/m lower bound (DIN 5480 short teeth) |
| `SPLINE_HEIGHT_RATIO_MAX` | 3.5 | h/m upper bound |
| `MIN_SPLINE_TOOTH_COUNT` | 15 | Minimum tooth count |
| `MAX_SPLINE_TOOTH_COUNT` | 60 | Maximum tooth count |

### 4.5 Gear Features
| Parameter | Value | Description |
|-----|-----|-----|
| `SAMPLE_POINT_COUNT` | 3600 | Sample points per edge |
| `SECTION_STEP_MM` | 5.0 mm | Z-axis scan step |
| `MIN_TOOTH_HEIGHT_DIFF` | 0.4 mm | Minimum tooth height threshold |
| `PEAK_NEIGHBOR_COUNT` | 3 | Neighbor points for peak detection |
| `PEAK_RADIUS_RATIO` | 0.9 | Peak radius threshold |

---

## 5. Output Field Summary

### 5.1 Cylinder Output
```json
{
  "outer_cylinders": [
    {
      "radius": 35.0,
      "position_x": 50.0,
      "length": 25.0,
      "type": "Rotor_Core_Fit"
    }
  ],
  "inner_bore": [
    {
      "radius": 15.0,
      "position_x": 100.0,
      "length": 20.0
    }
  ]
}
```

### 5.2 Keyway Output
```json
{
  "count": 1,
  "keyways": [
    {
      "type": "flat_key",
      "position_axial": 45.0,
      "width": 8.0,
      "depth": 4.0,
      "length": 20.0
    }
  ]
}
```

### 5.3 Radial Hole Output
```json
{
  "count": 4,
  "radius": 3.5,
  "axial_positions": [30.0, 45.0, 60.0, 75.0],
  "holes_per_position": {"30.0": 1, "45.0": 1, ...},
  "angular_positions": ["0", "90", "180", "270"]
}
```

### 5.4 Spline Output
```json
{
  "detected": true,
  "z_ranges": [{"z_start": 80.0, "z_end": 110.0}],
  "parameters": {
    "spline_type": "involute",
    "tooth_count": 24,
    "major_diameter": 48.0,
    "minor_diameter": 41.0,
    "module": 2.0,
    "pressure_angle": 30.0
  }
}
```

### 5.5 Gear Output
```json
{
  "detected": true,
  "gear_count": 1,
  "gear_zones": [{"position_start": 120.0, "position_end": 150.0, "mid_position": 135.0}],
  "parameters": [
    {
      "tooth_count": 40,
      "module": 2.5,
      "addendum_radius": 52.5,
      "dedendum_radius": 46.875,
      "tooth_height": 5.625,
      "pressure_angle": 20.0,
      "gear_type": "helical",
      "helix_angle": 15.0
    }
  ]
}
```

---

## 6. Version History

| Version | Date | Change Description |
|------|------|----------|
| v1 | 2026-05-19 | Initial version, face density statistics method |
| v2 | 2026-05-19 | Cylinder feature refactor, introduced v_length |
| v3 | 2026-05-19 | Keyway feature refactor, introduced topological constraints |
| v4 | 2026-05-20 | Spline feature refactor, introduced the h/m ratio |
| v5 | 2026-05-20 | Radial hole feature refactor, introduced penetration topology detection |
| v6 | 2026-05-20 | Gear features introduced B-Spline detection |
| v7 | 2026-05-20 | Gear features: fixed axial/radial computation bugs |
| v8 | 2026-05-20 | Gear features introduced Bnd_Box bounding box computation |
| v9 | 2026-05-21 | Gear features rebuilt as 2D section scanning + ISO module fitting + geometric consistency filter |

---

## 7. Refactor Notes

### 7.1 Refactor Goals
- Remove all hard-coded radius thresholds
- Use `surface_classification` to distinguish inner and outer surfaces
- Validate features based on dimensionless ratios
- Strengthen topology detection

### 7.2 Key Changes
| Module | Before | After |
|-----|-------|-------|
| base_loader | No inner/outer classification | Classification based on is_reversed |
| cylinder_features | Simplified length estimation | Exact lengths computed with v_length |
| keyway_features | Hard-coded area thresholds | Topological constraints + depth ratio |
| radial_holes | Hard-coded radius thresholds | Penetration topology + axis perpendicularity |
| spline_features | Hard-coded radius ranges | h/m dimensionless ratio (DIN 5480) |
| gear_features | Face density statistics | Z-axis scanning + section analysis + ISO module fitting + geometric consistency filter |

### 7.3 Core Algorithm Improvements
- **Gear features**: face density statistics replaced by Z-axis section scanning; sample points raised from tens to 3600+
- **Keyway features**: area filtering replaced by topological constraints and depth ratios
- **Radial holes**: radius filtering replaced by penetration topology detection
- **Spline features**: hard-coded ranges replaced by DIN 5480 h/m ratio validation
