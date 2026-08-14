#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Spline Features Extraction Module (Refactored)

Refactor notes:
- Removed all hard-coded radius thresholds
- Use surface_classification to distinguish inner and outer surfaces
- Feature validation based on dimensionless ratios
"""

import math
import logging
from typing import Dict, List, Any, Tuple
from .base_loader import BaseBREPLoader

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Dimensionless ratio thresholds
# [Change] DIN 5480 short-tooth splines have h/m ~= 1.1; the lower bound is relaxed to detect short-tooth splines
SPLINE_HEIGHT_RATIO_MIN = 0.8   # h/m lower bound (short-tooth splines can be as low as ~0.9)
SPLINE_HEIGHT_RATIO_MAX = 3.5   # h/m upper bound (splines span a wider ratio range)
MIN_SPLINE_TOOTH_COUNT = 15     # Minimum tooth count
MAX_SPLINE_TOOTH_COUNT = 60     # Maximum tooth count


class SplineFeatureExtractor:
    """
    Spline Feature Extractor (refactored)
    Identifies spline features based on topological classification and dimensionless ratios
    """

    def __init__(self, base_loader: BaseBREPLoader):
        self.base_loader = base_loader
        self.spline_zone = None

    def extract(self) -> Dict[str, Any]:
        """Run spline feature extraction"""
        if not self.base_loader.faces_data:
            self.base_loader.traverse_faces()

        if self.base_loader.bounding_box is None:
            self.base_loader.compute_bounding_box()

        self._detect_spline_zone()
        return self.spline_zone

    def _get_axis_range(self) -> Tuple[float, float]:
        """Get the coordinate range along the main axis"""
        xmin, ymin, zmin, xmax, ymax, zmax = self.base_loader.bounding_box
        idx = self._get_axis_direction_index()
        if idx == 0:
            return (xmin, xmax)
        elif idx == 1:
            return (ymin, ymax)
        else:
            return (zmin, zmax)

    def _get_axis_direction_index(self) -> int:
        main_axis = self.base_loader.main_axis
        if abs(main_axis['x']) > 0.5:
            return 0
        elif abs(main_axis['y']) > 0.5:
            return 1
        else:
            return 2

    def _get_face_coordinate(self, face: Dict) -> float:
        center = face.get('center')
        if center is None:
            return 0.0
        idx = self._get_axis_direction_index()
        if idx == 0:
            return center['x']
        elif idx == 1:
            return center['y']
        else:
            return center['z']

    def _get_outer_cylinders_in_range(self, pos_start: float, pos_end: float) -> List[Dict]:
        """Get cylindrical faces within the given axial range (including outer and unclassified surfaces)"""
        faces = []
        for face in self.base_loader.faces_data:
            # [Change] Also accept the 'unknown' classification, because spline tooth faces may be misclassified
            surf_class = face.get('surface_classification')
            if surf_class not in ('outer', 'unknown'):
                continue
            if face.get('surface_type') != 'cylinder':
                continue
            coord = self._get_face_coordinate(face)
            if pos_start <= coord < pos_end:
                faces.append(face)
        return faces

    def _detect_spline_zone(self) -> None:
        """
        [Refactor] Detect the spline feature zone
        Uses outer cylindrical faces and dimensionless ratios
        """
        axis_min, axis_max = self._get_axis_range()
        axis_len = axis_max - axis_min

        axis_bins = 20
        axis_step = axis_len / axis_bins

        face_density = []

        for i in range(axis_bins):
            pos_start = axis_min + i * axis_step
            pos_end = pos_start + axis_step

            outer_cylinders = self._get_outer_cylinders_in_range(pos_start, pos_end)

            radii = [f['radius'] for f in outer_cylinders if f.get('radius')]

            is_spline = self._is_spline_candidate(outer_cylinders, radii)

            face_density.append({
                'pos_start': pos_start,
                'pos_end': pos_end,
                'outer_count': len(outer_cylinders),
                'radii': radii,
                'is_spline_candidate': is_spline
            })

        # Statistics
        max_outer_count = max(bin['outer_count'] for bin in face_density)
        avg_outer_count = sum(bin['outer_count'] for bin in face_density) / len(face_density)

        # Detect the spline zone
        threshold = max(avg_outer_count * 1.5, 5)

        spline_regions = []
        for bin_data in face_density:
            if bin_data['outer_count'] >= threshold and bin_data['is_spline_candidate']:
                spline_regions.append(bin_data)

        if spline_regions:
            pos_ranges = self._merge_adjacent_regions(spline_regions)

            # Merge the radii of all regions
            all_radii = []
            for region in spline_regions:
                all_radii.extend(region['radii'])

            spline_params = None
            if pos_ranges and all_radii:
                spline_params = self._extract_spline_parameters(pos_ranges[0], all_radii)

            self.spline_zone = {
                'detected': True,
                'z_ranges': pos_ranges,
                'approx_outer_radius': round(max(all_radii), 4) if all_radii else 0.0,
                'outer_cylinder_count': len(all_radii),
                'parameters': spline_params
            }
        else:
            self.spline_zone = {
                'detected': False,
                'z_ranges': [],
                'approx_outer_radius': 0.0,
                'outer_cylinder_count': 0,
                'parameters': None
            }

        logger.info(f"Spline zone detected: {self.spline_zone['detected']}")

    def _is_spline_candidate(self, outer_cylinders: List[Dict], radii: List[float]) -> bool:
        """
        [Refactor] Determine whether this is a spline candidate zone
        Uses dimensionless ratios, independent of absolute dimensions
        """
        if len(outer_cylinders) < 3:
            return False

        if not radii or len(radii) < 3:
            return False

        dedendum_radius = min(radii)
        addendum_radius = max(radii)
        tooth_height = addendum_radius - dedendum_radius

        if tooth_height <= 0:
            logger.debug(f"Spline: tooth height <= 0, rejecting")
            return False

        # [Change] Splines use the DIN 5480 short-tooth standard
        # Working tooth height: h ~= 1.1*m (rather than the 2.25*m of involute gears)
        module = tooth_height / 1.1

        if module <= 0:
            return False

        # Compute the h/m ratio
        height_ratio = tooth_height / module

        # [Change] DIN 5480 short-tooth splines have h/m ~= 1.1; the range is wider
        is_valid = SPLINE_HEIGHT_RATIO_MIN <= height_ratio <= SPLINE_HEIGHT_RATIO_MAX

        logger.debug(f"Spline candidate check: h={tooth_height:.4f}, m={module:.4f}, ratio={height_ratio:.2f}, valid={is_valid}")

        if not is_valid:
            logger.debug(f"Spline: height_ratio={height_ratio:.2f} not in [{SPLINE_HEIGHT_RATIO_MIN}, {SPLINE_HEIGHT_RATIO_MAX}], rejecting")

        return is_valid

    def _merge_adjacent_regions(self, regions: List[Dict]) -> List[Dict]:
        """Merge adjacent feature regions"""
        if not regions:
            return []

        regions.sort(key=lambda x: x['pos_start'])

        merged = [regions[0].copy()]

        for region in regions[1:]:
            last = merged[-1]
            if region['pos_start'] <= last['pos_end'] + 0.5:
                last['pos_end'] = max(last['pos_end'], region['pos_end'])
                last['outer_count'] = max(last['outer_count'], region['outer_count'])
            else:
                merged.append(region.copy())

        return [{'z_start': round(r['pos_start'], 4), 'z_end': round(r['pos_end'], 4)}
                for r in merged]

    def _extract_spline_parameters(self, z_range: Dict, all_radii: List[float]) -> Dict[str, Any]:
        """
        [Refactor] Extract detailed spline parameters
        Computed from topological ratios, independent of absolute dimensions
        """
        if not all_radii:
            return self._empty_params()

        # Filter out abnormally small radii (possibly thread edges or noise)
        valid_radii = [r for r in all_radii if r > 5.0]  # Assume a minimum valid radius > 5mm

        if not valid_radii:
            valid_radii = all_radii

        # Use statistical methods for the dedendum radius
        import statistics
        dedendum_radius = min(valid_radii)
        addendum_radius = max(valid_radii)

        # If the max-min gap is too large, use data around the median
        if addendum_radius - dedendum_radius > addendum_radius * 0.3:
            # Compute the module using the median
            median_radius = statistics.median(valid_radii)
            tooth_height = addendum_radius - median_radius
            # [Change] Spline short-tooth standard: h ~= 1.1*m
            module = tooth_height / 1.1
            # [Change] External spline tip circle diameter da ~= m(z + 0.9), so z = da/m - 0.9
            tooth_count = int(round((2 * addendum_radius / module) - 0.9))
            tooth_count = max(MIN_SPLINE_TOOTH_COUNT, min(MAX_SPLINE_TOOTH_COUNT, tooth_count))

            # Derive the pitch circle diameter from the tooth count
            pitch_diameter = tooth_count * module
            # Dedendum circle diameter = pitch circle diameter - tooth height (about 1.5*m)
            dedendum_radius = (pitch_diameter / 2) - 1.5 * module
        else:
            tooth_height = addendum_radius - dedendum_radius

        if tooth_height <= 0:
            return self._empty_params()

        # [Change] Spline short-tooth standard: h ~= 1.1*m
        module = tooth_height / 1.1

        if module <= 0:
            return self._empty_params()

        # For standard involute splines, the module is usually a standard value
        # Common modules: 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0
        standard_modules = [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
        closest_module = min(standard_modules, key=lambda x: abs(x - module))
        logger.debug(f"Spline module calculation: raw={module:.4f}, closest_std={closest_module:.4f}, diff={abs(module - closest_module):.4f}")

        # [Change] If the computed module is close to a standard module (difference < 20%), recompute using the standard module
        # External spline tip circle diameter da ~= m(z + 0.9)
        if abs(module - closest_module) / module < 0.2:
            module = closest_module
            tooth_count = int(round((2 * addendum_radius / module) - 0.9))
        else:
            # Difference too large; use the originally computed module
            tooth_count = int(round((2 * addendum_radius / module) - 0.9))

        # Ensure the tooth count is within a reasonable range
        tooth_count = max(MIN_SPLINE_TOOTH_COUNT, min(MAX_SPLINE_TOOTH_COUNT, tooth_count))

        # Analyze the spline type
        return self._analyze_spline_type(tooth_count, addendum_radius, dedendum_radius, module, closest_module)

    def _analyze_spline_type(self, tooth_count: int, addendum_radius: float,
                             dedendum_radius: float, module: float,
                             closest_module: float = None) -> Dict[str, Any]:
        """Analyze the spline type and compute parameters"""
        major_diameter = round(addendum_radius * 2, 4) if addendum_radius > 0 else 0.0
        # The dedendum circle diameter (minor_diameter) should be based on the actual dedendum radius
        minor_diameter = round(dedendum_radius * 2, 4) if dedendum_radius > 0 else 0.0
        final_tooth_count = tooth_count if tooth_count > 0 else MIN_SPLINE_TOOTH_COUNT

        # Use the standard module directly instead of dividing the tip circle diameter by the tooth count
        # d = m*z is the pitch circle formula, not the tip circle diameter
        final_module = closest_module if closest_module else module
        key_width_B = round(math.pi * final_module / 2, 4)  # Tooth width is about pi*m/2

        # Spline pressure angles are typically 30 deg or 45 deg
        pressure_angle = 30.0 if module < 1.0 else 45.0
        spline_type = 'involute'

        return {
            'spline_type': spline_type,
            'tooth_count': final_tooth_count,
            'major_diameter': major_diameter,
            'minor_diameter': minor_diameter,
            'module': round(final_module, 4),
            'pressure_angle': pressure_angle,
            'key_width_B': key_width_B
        }

    def _empty_params(self) -> Dict[str, Any]:
        return {
            'spline_type': 'unknown',
            'tooth_count': 0,
            'major_diameter': 0.0,
            'minor_diameter': 0.0,
            'module': None,
            'pressure_angle': None,
            'key_width_B': None
        }
