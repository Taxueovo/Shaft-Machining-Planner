#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Radial Hole Feature Extraction Module (refactored v2 - exact computation with the PythonOCC B-Rep API)

Core principles:
1. Use Axis().Direction() to check whether the axis is perpendicular to the main axis
2. Anti-hallucination filtering: discard faces that are too small in area (noise/chamfers)
3. Use math.atan2(y, x) to compute exact circumferential angles
4. Improved penetration topology detection
"""

import math
import logging
from typing import Dict, List, Any, Optional, Tuple

from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Cylinder
from OCC.Core.gp import gp_Dir

from .base_loader import BaseBREPLoader

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ============ Core parameters ============
AXIS_PERPENDICULAR_TOL = 0.1    # Axis perpendicularity tolerance
MIN_HOLE_AREA_MM2 = 1.0         # Minimum hole area threshold (mm^2) - noise suppression
MIN_HOLE_RADIUS = 0.5           # Minimum hole radius (mm)
MAX_HOLE_RADIUS_RATIO = 0.5     # Maximum hole radius / outer cylinder radius ratio
ANGLE_GROUPING_TOL = 15.0       # Angle grouping tolerance (degrees)
Z_POSITION_TOL = 2.0            # Z position grouping tolerance (mm)
# =================================


class RadialHoleExtractor:
    """
    Radial Hole Feature Extractor (refactored)
    Performs exact computation using the PythonOCC B-Rep API
    """

    def __init__(self, base_loader: BaseBREPLoader):
        self.base_loader = base_loader
        self.radial_holes = []

    def extract(self) -> Dict[str, Any]:
        """Run radial hole feature extraction"""
        if not self.base_loader.faces_data:
            self.base_loader.traverse_faces()

        if self.base_loader.main_axis is None:
            self.base_loader.detect_main_axis()

        self._detect_radial_holes()
        return self._build_result()

    def _detect_radial_holes(self) -> None:
        """
        Detect radial holes
        1. Filter inner cylindrical faces
        2. Check whether the axis is perpendicular to the main axis
        3. Filter out small noisy faces
        4. Compute angular positions
        """
        # Get the main axis information
        main_axis = self.base_loader.main_axis
        main_dir = gp_Dir(main_axis['x'], main_axis['y'], main_axis['z'])

        # Get the maximum outer cylinder radius
        max_outer_radius = self._get_max_outer_radius()

        for face_data in self.base_loader.faces_data:
            # Only inspect cylindrical faces
            if face_data['surface_type'] != 'cylinder':
                continue

            # Only inspect inner surfaces
            if face_data.get('surface_classification') != 'inner':
                continue

            radius = face_data['radius']
            center = face_data['center']
            axis_dir = face_data.get('axis_direction')
            area = face_data.get('area', 0)

            logger.debug(f"Checking cylinder: r={radius:.4f}, area={area:.2f}, center=({center['x']:.2f}, {center['y']:.2f}, {center['z']:.2f})")

            # ========== Anti-hallucination filtering ==========
            if not self._filter_noise_faces(radius, area, max_outer_radius):
                continue

            # ========== Axis perpendicularity check ==========
            if not self._is_radial_axis(axis_dir, main_dir):
                logger.debug(f"Axis not perpendicular to main axis, skipping")
                continue

            # ========== Penetration topology check ==========
            if not self._check_penetration_topology(radius, max_outer_radius):
                logger.debug(f"Not penetrating outer surface, skipping")
                continue

            # ========== Duplicate check ==========
            if self._is_duplicate(radius, center):
                logger.debug(f"Duplicate hole detected, skipping")
                continue

            # Compute the angular position
            angular_pos = self._compute_angular_position(center)

            # Add the hole
            hole_info = {
                'radius': round(radius, 4),
                'center_x': round(center['x'], 4),
                'center_y': round(center['y'], 4),
                'center_z': round(center['z'], 4),
                'angular_position': round(angular_pos, 2),
                'area': round(area, 4)
            }

            self.radial_holes.append(hole_info)
            logger.info(f"Detected radial hole: r={radius:.4f}, angle={angular_pos:.2f}°, z={center['z']:.2f}")

    def _filter_noise_faces(self, radius: float, area: float,
                           max_outer_radius: float) -> bool:
        """
        Anti-hallucination filtering: discard faces that are too small in area (noise/chamfers)
        """
        # Area threshold
        if area < MIN_HOLE_AREA_MM2:
            logger.debug(f"Discarding face with area {area:.4f} < {MIN_HOLE_AREA_MM2}")
            return False

        # Radius threshold
        if radius < MIN_HOLE_RADIUS:
            logger.debug(f"Discarding face with radius {radius:.4f} < {MIN_HOLE_RADIUS}")
            return False

        # Radius ratio threshold
        if max_outer_radius > 0:
            radius_ratio = radius / max_outer_radius
            if radius_ratio > MAX_HOLE_RADIUS_RATIO:
                logger.debug(f"Discarding face with radius_ratio {radius_ratio:.4f} > {MAX_HOLE_RADIUS_RATIO}")
                return False

        return True

    def _is_radial_axis(self, axis_dir: Optional[Dict], main_dir: gp_Dir) -> bool:
        """
        Check whether the axis is perpendicular to the main axis
        Computes the dot product with Axis().Direction().Dot(main_dir)
        """
        if axis_dir is None:
            return False

        # Create the hole axis direction
        hole_dir = gp_Dir(axis_dir['x'], axis_dir['y'], axis_dir['z'])

        # Compute the dot product
        dot = abs(hole_dir.Dot(main_dir))

        logger.debug(f"Axis dot product: {dot:.4f}")

        # Perpendicularity check: the dot product should be near 0
        return dot < AXIS_PERPENDICULAR_TOL

    def _check_penetration_topology(self, hole_radius: float,
                                   max_outer_radius: float) -> bool:
        """
        Check penetration topology
        The bore radius should be clearly smaller than the outer cylinder
        """
        if max_outer_radius <= 0:
            return True

        radius_ratio = hole_radius / max_outer_radius
        return radius_ratio < MAX_HOLE_RADIUS_RATIO

    def _is_duplicate(self, radius: float, center: Dict) -> bool:
        """
        Check whether the hole duplicates an existing one
        """
        radius_tol = 0.5
        z_tol = Z_POSITION_TOL
        angle_tol = ANGLE_GROUPING_TOL

        for h in self.radial_holes:
            # Check radius
            if abs(h['radius'] - radius) >= radius_tol:
                continue

            # Check the Z position
            if abs(h['center_z'] - center['z']) >= z_tol:
                continue

            # Check the angle
            h_angle = h.get('angular_position', 0)
            new_angle = self._compute_angular_position(center)

            angle_diff = abs(new_angle - h_angle)
            if angle_diff > 180:
                angle_diff = 360 - angle_diff

            if angle_diff >= angle_tol:
                continue

            return True

        return False

    def _compute_angular_position(self, center: Dict) -> float:
        """
        Compute the circumferential angular position of a hole
        Project onto the plane orthogonal to the main axis and compute the angle with atan2
        """
        main_axis = self.base_loader.main_axis

        if abs(main_axis['z']) > 0.5:
            # Main axis is Z, use the XY plane
            angle = math.degrees(math.atan2(center['y'], center['x']))
        elif abs(main_axis['x']) > 0.5:
            # Main axis is X, use the YZ plane
            angle = math.degrees(math.atan2(center['z'], center['y']))
        else:
            # Main axis is Y, use the XZ plane
            angle = math.degrees(math.atan2(center['z'], center['x']))

        # Normalize to [0, 360)
        if angle < 0:
            angle += 360

        return angle

    def _get_max_outer_radius(self) -> float:
        """Get the maximum outer cylinder radius"""
        max_radius = 0.0
        for face_data in self.base_loader.faces_data:
            if face_data.get('surface_classification') == 'outer':
                if face_data['surface_type'] == 'cylinder':
                    radius = face_data.get('radius', 0)
                    if radius > max_radius:
                        max_radius = radius

        return max_radius if max_radius > 0 else 50.0

    def _build_result(self) -> Dict[str, Any]:
        """
        Build the radial hole result
        Includes angular distribution information
        """
        result = {
            'count': len(self.radial_holes),
            'radius': 0.0,
            'axial_positions': [],
            'holes_per_position': {},
            'angular_positions': [],
            'holes_per_angle': {},
            'radial_positions': [],
            'angles_per_position': {}
        }

        if not self.radial_holes:
            return result

        # Compute the average radius
        avg_radius = sum(h['radius'] for h in self.radial_holes) / len(self.radial_holes)
        result['radius'] = round(avg_radius, 4)

        # Group by Z position
        z_grouped = {}
        for h in self.radial_holes:
            z_key = round(h['center_z'], 1)
            if z_key not in z_grouped:
                z_grouped[z_key] = []
            z_grouped[z_key].append(h)

        result['axial_positions'] = sorted(z_grouped.keys())

        for z_pos, holes in z_grouped.items():
            result['holes_per_position'][str(z_pos)] = len(holes)
            # Exact angle of each hole at every axial position (for use by peagent hole_angle_deg)
            result['angles_per_position'][str(z_pos)] = sorted(
                round(h['angular_position'], 1) for h in holes
            )

        # Group by angle
        angle_grouped = {}
        for h in self.radial_holes:
            # Normalize the angle to the 0-360 range
            angle = h['angular_position']
            # Group into 30-degree bins
            angle_bin = int(angle / 30) * 30
            angle_key = f"{angle_bin}"

            if angle_key not in angle_grouped:
                angle_grouped[angle_key] = []
            angle_grouped[angle_key].append(h)

        result['angular_positions'] = sorted(angle_grouped.keys())
        for angle_pos, holes in angle_grouped.items():
            result['holes_per_angle'][angle_pos] = len(holes)

        # Radial positions
        for h in self.radial_holes:
            result['radial_positions'].append({
                'x': h['center_x'],
                'y': h['center_y'],
                'angle': h['angular_position']
            })

        return result
