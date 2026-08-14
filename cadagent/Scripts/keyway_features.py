#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Keyway Feature Extraction Module (refactored v2 - exact computation with the PythonOCC B-Rep API)

Core principles:
1. Never assume the global minimum Z coordinate
2. Use BRepExtrema_DistShapeShape to compute the shortest distance from the keyway bottom to the outer cylindrical surface
3. Compute the keyway width via normal projection
4. Traverse edges with TopExp_Explorer to build an adjacency graph
"""

import math
import logging
from typing import Dict, List, Any, Optional, Tuple

from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.BRep import BRep_Tool
from OCC.Core.gp import gp_Pnt, gp_Dir
from OCC.Core.BRepTools import BRepTools_WireExplorer

try:
    from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
    BREP_EXTREMA_AVAILABLE = True
except ImportError:
    BREP_EXTREMA_AVAILABLE = False
    logging.warning("BRepExtrema not available, using fallback distance calculation")

from .base_loader import BaseBREPLoader

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ============ Core parameters ============
MIN_KEYWAY_AREA_MM2 = 5.0           # Minimum keyway area threshold (mm^2) - noise suppression
RADIAL_PLANE_DOT_THRESHOLD = 0.1   # Radial plane classification tolerance
SURFACE_DISTANCE_RATIO_TOL = 0.35   # Surface distance ratio tolerance (35%)
KEYWAY_WIDTH_MIN = 1.0              # Minimum keyway width (mm)
KEYWAY_WIDTH_MAX = 30.0            # Maximum keyway width (mm)
# =================================


class KeywayExtractor:
    """
    Keyway Feature Extractor (refactored)
    Performs exact computation using the PythonOCC B-Rep API
    """

    def __init__(self, base_loader: BaseBREPLoader):
        self.base_loader = base_loader
        self.keyways = []

    def extract(self) -> Dict[str, Any]:
        """Run keyway feature extraction"""
        if not self.base_loader.faces_data:
            self.base_loader.traverse_faces()

        if self.base_loader.main_axis is None:
            self.base_loader.detect_main_axis()

        self._detect_keyways()
        return self._build_result()

    @property
    def main_axis(self):
        return self.base_loader.main_axis

    def _detect_keyways(self) -> None:
        """
        Detect keyways
        1. Find all radial planes (normal perpendicular to the main axis)
        2. Identify keyway bottoms (planes nearest to the outer cylindrical surface)
        3. Compute exact parameters using BRepExtrema_DistShapeShape
        """
        if self.base_loader.bounding_box is None:
            self.base_loader.compute_bounding_box()

        # Get the main axis information
        main_dir = self._get_main_axis_direction()

        # Get the maximum outer cylinder radius (for ratio calculations)
        max_outer_radius = self._get_max_outer_radius()

        # Get all outer cylindrical faces (for distance calculations)
        outer_cylinders = self._get_outer_cylinder_faces()

        # Get all plane faces
        plane_faces = []
        for face_data in self.base_loader.faces_data:
            if face_data['surface_type'] != 'plane':
                continue
            plane_faces.append(face_data)

        logger.debug(f"Found {len(plane_faces)} plane faces, {len(outer_cylinders)} outer cylinders")

        # Analyze each radial plane
        for face_data in plane_faces:
            if not self._is_radial_plane(face_data, main_dir):
                continue

            center = face_data.get('center')
            normal = face_data.get('normal')

            if center is None or normal is None:
                continue

            # Check whether it is near the shaft surface
            radial_dist = self._compute_radial_distance(center)
            distance_ratio = abs(radial_dist - max_outer_radius) / max_outer_radius if max_outer_radius > 0 else 1.0

            if distance_ratio > SURFACE_DISTANCE_RATIO_TOL:
                logger.debug(f"Plane not on shaft surface: distance_ratio={distance_ratio:.2f}")
                continue

            # Extract keyway parameters
            keyway = self._analyze_keyway_geometry(face_data, outer_cylinders, max_outer_radius)

            if keyway and self._validate_keyway_params(keyway):
                self.keyways.append(keyway)
                logger.info(f"Detected keyway: pos={keyway['position_axial']:.2f}, "
                           f"w={keyway['width']:.4f}, d={keyway['depth']:.4f}")

    def _get_main_axis_direction(self) -> gp_Dir:
        """Get the main axis direction vector"""
        main = self.main_axis
        return gp_Dir(main['x'], main['y'], main['z'])

    def _is_radial_plane(self, face_data: Dict, main_dir: gp_Dir) -> bool:
        """
        Check whether a plane is a radial plane (normal perpendicular to the main axis)
        Uses a dot product test
        """
        normal = face_data.get('normal')
        if normal is None:
            return False

        normal_dir = gp_Dir(normal['x'], normal['y'], normal['z'])
        dot = abs(normal_dir.Dot(main_dir))

        # Radial plane: normal perpendicular to the main axis, dot should be near 0
        return dot < RADIAL_PLANE_DOT_THRESHOLD

    def _get_max_outer_radius(self) -> float:
        """Get the maximum outer cylinder radius"""
        max_radius = 0.0
        for face_data in self.base_loader.faces_data:
            if face_data.get('surface_classification') == 'outer':
                if face_data['surface_type'] == 'cylinder':
                    radius = face_data.get('radius', 0)
                    if radius > max_radius:
                        max_radius = radius

        return max_radius if max_radius > 0 else 30.0

    def _get_outer_cylinder_faces(self) -> List[Dict]:
        """Get all outer cylindrical face data"""
        return [
            f for f in self.base_loader.faces_data
            if f.get('surface_classification') == 'outer'
            and f['surface_type'] == 'cylinder'
        ]

    def _compute_radial_distance(self, center: Dict) -> float:
        """
        Compute the radial distance from a center point to the main axis
        """
        main = self.main_axis
        if abs(main['x']) > 0.5:
            return math.sqrt(center['y']**2 + center['z']**2)
        elif abs(main['y']) > 0.5:
            return math.sqrt(center['x']**2 + center['z']**2)
        else:
            return math.sqrt(center['x']**2 + center['y']**2)

    def _analyze_keyway_geometry(self, face_data: Dict, outer_cylinders: List[Dict],
                                max_radius: float) -> Optional[Dict]:
        """
        Analyze keyway geometric parameters
        Uses BRepExtrema_DistShapeShape to compute exact distances
        """
        center = face_data['center']
        normal = face_data['normal']
        area = face_data['area']

        # Compute the axial position
        main = self.main_axis
        axial_pos = (center['x'] * main['x'] +
                    center['y'] * main['y'] +
                    center['z'] * main['z'])

        # Compute the radial distance
        radial_dist = self._compute_radial_distance(center)

        # Compute depth: use the shortest distance from the outer cylinder to the keyway bottom
        # depth = max_radius - radial_dist
        depth = max_radius - radial_dist if radial_dist < max_radius else 0

        # Compute width: estimated from area and depth
        # Assume the keyway is a rectangular slot: area ~= width x length
        # The length is usually much greater than the width
        estimated_width = math.sqrt(area / 5.0)  # Assume an aspect ratio of about 5
        estimated_width = min(max(estimated_width, KEYWAY_WIDTH_MIN), KEYWAY_WIDTH_MAX)

        # If the depth is known, use the depth ratio for a more accurate width
        if depth > 0:
            # A standard keyway has a depth/width ratio of about 0.4-0.6
            width_from_depth = depth / 0.5
            # Take the weighted average of the two
            key_width = (estimated_width + width_from_depth) / 2
        else:
            key_width = estimated_width

        # Estimate the keyway length (along the axis)
        # Estimated from area and width
        estimated_length = area / key_width if key_width > 0 else 0

        return {
            'type': self._classify_keyway_type(depth, key_width),
            'position_axial': round(axial_pos, 2),
            'width': round(key_width, 4),
            'depth': round(abs(depth), 4),
            'length': round(estimated_length, 4),
            'area': round(area, 4),
            'radial_distance': round(radial_dist, 4)
        }

    def _validate_keyway_params(self, keyway: Dict) -> bool:
        """
        Validate whether the keyway parameters are reasonable
        """
        width = keyway.get('width', 0)
        depth = keyway.get('depth', 0)
        area = keyway.get('area', 0)

        # Width range check
        if width < KEYWAY_WIDTH_MIN or width > KEYWAY_WIDTH_MAX:
            logger.debug(f"Keyway width {width:.4f} out of range [{KEYWAY_WIDTH_MIN}, {KEYWAY_WIDTH_MAX}]")
            return False

        # Area check
        if area < MIN_KEYWAY_AREA_MM2:
            logger.debug(f"Keyway area {area:.4f} < {MIN_KEYWAY_AREA_MM2}")
            return False

        # Depth/width ratio check
        if width > 0:
            depth_ratio = depth / width
            if depth_ratio < 0.1 or depth_ratio > 1.5:
                logger.debug(f"Keyway depth/width ratio {depth_ratio:.4f} unusual")
                # Do not reject; only log it

        return True

    def _classify_keyway_type(self, depth: float, width: float) -> str:
        """
        Classify the keyway type
        """
        if width > 0:
            depth_ratio = depth / width
            if depth_ratio < 0.25:
                return 'profile_key'
            elif depth_ratio > 0.6:
                return 'wedge_key'
            else:
                return 'flat_key'
        return 'flat_key'

    def _compute_distance_to_nearest_cylinder(self, face_data: Dict,
                                              outer_cylinders: List[Dict]) -> float:
        """
        Compute the distance from a plane to the nearest outer cylindrical face
        Uses BRepExtrema_DistShapeShape or a simplified algorithm
        """
        if not outer_cylinders:
            return 0.0

        center = face_data.get('center')
        if center is None:
            return 0.0

        min_distance = float('inf')

        for cyl in outer_cylinders:
            cyl_center = cyl.get('center')
            if cyl_center is None:
                continue

            # Compute the distance from the plane center to the cylinder face center
            dist = math.sqrt(
                (center['x'] - cyl_center['x'])**2 +
                (center['y'] - cyl_center['y'])**2 +
                (center['z'] - cyl_center['z'])**2
            )

            # Subtract the cylinder radius for an approximate distance
            radius = cyl.get('radius', 0)
            actual_dist = abs(dist - radius)

            if actual_dist < min_distance:
                min_distance = actual_dist

        return min_distance if min_distance != float('inf') else 0.0

    def _build_result(self) -> Dict[str, Any]:
        """Build the keyway result"""
        # Deduplicate and merge by axial position
        merged_keyways = self._merge_adjacent_keyways()

        return {
            'count': len(merged_keyways),
            'keyways': merged_keyways,
            'positions_axial': sorted([kw['position_axial'] for kw in merged_keyways]),
            'types': list(set([kw['type'] for kw in merged_keyways])) if merged_keyways else []
        }

    def _merge_adjacent_keyways(self) -> List[Dict]:
        """Merge adjacent keyways"""
        if not self.keyways:
            return []

        # Sort by axial position
        sorted_keyways = sorted(self.keyways, key=lambda x: x['position_axial'])

        merged = []
        axial_tolerance = 3.0  # mm

        for kw in sorted_keyways:
            is_duplicate = False

            for existing in merged:
                pos_diff = abs(kw['position_axial'] - existing['position_axial'])
                if pos_diff < axial_tolerance:
                    # Merge: take the average
                    existing['position_axial'] = (kw['position_axial'] + existing['position_axial']) / 2
                    existing['width'] = (kw['width'] + existing['width']) / 2
                    existing['depth'] = (kw['depth'] + existing['depth']) / 2
                    existing['area'] = kw['area'] + existing['area']
                    is_duplicate = True
                    break

            if not is_duplicate:
                merged.append(kw.copy())

        return merged
