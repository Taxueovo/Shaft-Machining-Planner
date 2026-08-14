#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cylinder Features Extraction Module

Responsibilities:
- Outer cylindrical face recognition (Rotor_Core_Fit, Bearing_Seat)
- Inner bore feature extraction
- Stepped shaft segment classification
"""

from typing import Dict, List, Any, Optional, Tuple
from .base_loader import BaseBREPLoader

# Constants
RADIUS_TOLERANCE = 0.1  # Tolerance for radius comparison (mm) - relaxed for bore grouping
RADIAL_HOLE_RADIUS_MAX = 10.0  # Maximum radius for radial holes (mm)
EXCLUSION_ZONE_MARGIN = 5.0  # mm - safety margin


class CylinderFeatureExtractor:
    """
    Cylinder Feature Extractor
    Used to identify and classify cylindrical features on a shaft
    """

    def __init__(self, base_loader: BaseBREPLoader):
        """
        Initialize the extractor

        Args:
            base_loader: BaseBREPLoader instance
        """
        self.base_loader = base_loader

        # Feature containers
        self.outer_cylinders = []
        self.inner_bores = []
        self.shoulder_planes = []

    def extract(self, exclusion_zones: Optional[List[Dict]] = None, gear_parameters: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Run cylinder feature extraction

        Args:
            exclusion_zones: List of mutually exclusive zones, each containing position_start, position_end
            gear_parameters: List of gear parameters, used to distinguish shaft segments from gear outer envelope surfaces

        Returns:
            dict: Classified feature data
        """
        self.exclusion_zones = exclusion_zones or []
        self.gear_parameters = gear_parameters or []  # Store gear parameters for exclusion checks

        if not self.base_loader.faces_data:
            self.base_loader.traverse_faces()

        if self.base_loader.main_axis is None:
            self.base_loader.detect_main_axis()

        # Classify cylinders (with exclusion zone filtering)
        self._classify_cylinders()

        # Return classified features
        return self._build_result()

    def _is_in_exclusion_zone(self, position: float) -> bool:
        """
        Check whether a cylinder position falls inside a mutually exclusive zone

        Args:
            position: Position of the cylinder along the main axis

        Returns:
            bool: True if inside an exclusion zone
        """
        if not self.exclusion_zones:
            return False

        for zone in self.exclusion_zones:
            start = zone.get('position_start', 0) - EXCLUSION_ZONE_MARGIN
            end = zone.get('position_end', 0) + EXCLUSION_ZONE_MARGIN
            if start <= position <= end:
                return True
        return False

    def _get_exclusion_info(self, position: float) -> Optional[Dict]:
        """
        Get the exclusion zone info at the given position (including gear parameters)

        Args:
            position: Position of the cylinder along the main axis

        Returns:
            dict: Contains position_start, position_end, addendum_radius, etc.; None if not in any zone
        """
        if not self.exclusion_zones:
            return None

        for idx, zone in enumerate(self.exclusion_zones):
            start = zone.get('position_start', 0) - EXCLUSION_ZONE_MARGIN
            end = zone.get('position_end', 0) + EXCLUSION_ZONE_MARGIN
            if start <= position <= end:
                # Found a matching zone; try to get the gear parameters
                info = {
                    'position_start': zone.get('position_start', 0),
                    'position_end': zone.get('position_end', 0),
                    'addendum_radius': 0.0
                }
                # Match gear_parameters by index (assumes the same order)
                if hasattr(self, 'gear_parameters') and self.gear_parameters:
                    if idx < len(self.gear_parameters):
                        gear = self.gear_parameters[idx]
                        info['addendum_radius'] = gear.get('addendum_radius', 0)
                return info
        return None

    def _classify_cylinders(self) -> None:
        """
        Classify cylindrical faces: outer cylinders, inner bores, radial holes
        """
        all_cylinders = []

        for face_data in self.base_loader.faces_data:
            if face_data['surface_type'] != 'cylinder':
                continue

            radius = face_data['radius']
            # Use the geometric axis of the underlying cylinder (cylinder.Location())
            # Faces of the same bore then share the same center, avoiding splits caused by face centroids
            center = face_data.get('center')
            axis_dir = face_data['axis_direction']

            # Check if axis is parallel to main axis
            is_parallel = self.base_loader._is_axis_parallel(
                axis_dir, self.base_loader.main_axis
            )

            if is_parallel:
                all_cylinders.append({
                    'radius': radius,
                    'center': center,
                    'phys_center': face_data.get('phys_center'),
                    'area': face_data['area'],
                    'is_reversed': face_data['is_reversed'],
                    'v_length': face_data.get('v_length', 0),
                    'u_angle': face_data.get('u_angle', 0)
                })

        # Classify parallel cylinders based on radius and is_reversed
        # Outer cylinders: largest radius OR normal points outward (not reversed)
        # Inner bores: smaller radius AND normal points inward (reversed)
        if all_cylinders:
            radii = sorted(set(c['radius'] for c in all_cylinders), reverse=True)
            max_radius = radii[0] if radii else 0

            # Group by radius first
            radius_groups = {}
            for cyl in all_cylinders:
                r = round(cyl['radius'], 1)  # Use relaxed tolerance for grouping
                if r not in radius_groups:
                    radius_groups[r] = []
                radius_groups[r].append(cyl)

            # Classify each group - with position-based clustering
            for radius, cyls in radius_groups.items():
                # Check if any face in this group has is_reversed=False (outer)
                has_outer = any(not c['is_reversed'] for c in cyls)

                if radius >= max_radius * 0.95 or has_outer:
                    # This is an outer cylinder - cluster by axial position
                    # Cluster cylinders by their axial position to handle same-radius segments at different positions
                    position_clusters = self._cluster_by_position(cyls)

                    for cluster in position_clusters:
                        avg_position = cluster['avg_position']

                        # Check whether inside an exclusion zone (gear outer envelope filtering)
                        # Check whether the cylinder radius is close to the gear addendum_radius
                        # If the cylinder radius is much smaller than addendum_radius (e.g. <50%), it is a shaft segment and should not be excluded
                        exclusion_info = self._get_exclusion_info(avg_position)
                        if exclusion_info:
                            addendum_r = exclusion_info.get('addendum_radius', 0)
                            if addendum_r > 0 and cluster['max_length'] > 0:
                                ratio = radius / addendum_r
                                if ratio >= 0.5:
                                    continue  # Exclude the gear outer envelope surface
                            else:
                                if self._is_in_exclusion_zone(avg_position):
                                    continue
                        elif self._is_in_exclusion_zone(avg_position):
                            continue

                        self.outer_cylinders.append({
                            'radius': round(radius, 4),
                            'position_x': round(avg_position, 4),
                            'length': round(cluster['max_length'], 4),
                            'area': round(cluster['total_area'], 4)
                        })
                else:
                    # This is an inner bore - use v_length
                    for cyl in cyls:
                        ax_pos = self._get_axis_position(cyl['center'])
                        v_len = cyl.get('v_length', 0)
                        self._add_inner_bore(cyl['radius'], ax_pos, v_len, cyl['center'])

    def _has_similar_radius(self, items: List[Dict], radius: float) -> bool:
        """
        Check whether any item in the list has a similar radius
        """
        return any(abs(item['radius'] - radius) < RADIUS_TOLERANCE for item in items)

    def _estimate_face_length(self, cyl_data: Dict) -> float:
        """
        Estimate the length of a cylindrical face (along the main axis)
        Estimated from the face area and radius
        """
        radius = cyl_data['radius']
        area = cyl_data['area']
        if radius > 0 and area > 0:
            # Length ≈ Area / (2πr) for a cylindrical surface
            return round(area / (2 * 3.14159 * radius), 4)
        return 0.0

    def _get_axis_position(self, center: Dict[str, float]) -> float:
        """
        Get the position coordinate along the main axis

        Args:
            center: Center point coordinates

        Returns:
            float: Position along the main axis
        """
        main_axis = self.base_loader.main_axis
        # Project center point onto main axis direction
        return (center['x'] * main_axis['x'] +
                center['y'] * main_axis['y'] +
                center['z'] * main_axis['z'])

    def _add_inner_bore(self, radius: float, axis_position: float, face_length: float = 0.0,
                        center: Dict[str, float] = None) -> None:
        """
        Add an inner bore feature (polar coordinate geometric clustering algorithm)

        Uses (radius, position_x, pitch_radius, angle) as a composite key to identify distinct holes.
        Multiple faces of the same physical hole match the same key.

        Args:
            radius: Inner bore radius
            axis_position: Position coordinate along the main axis
            face_length: Estimated length of a single face
            center: Cylinder face center coordinates {x, y, z}
        """
        import math

        # Compute pitch_radius (perpendicular distance to the main axis) and angle
        pitch_radius = 0.0
        angle = 0.0

        if center:
            pitch_radius = math.hypot(center.get('x', 0), center.get('z', 0))
            angle = math.degrees(math.atan2(center.get('z', 0), center.get('x', 0)))
            if angle < 0:
                angle += 360

        # Create the composite key identifying which hole this face belongs to
        RADIUS_TOL = 0.1
        POS_TOL = 0.5
        PITCH_TOL = 0.5
        ANGLE_TOL = 15.0

        # Look for a matching bore entry
        matched_bore = None
        for bore in self.inner_bores:
            # Check radius tolerance
            if abs(bore['radius'] - radius) >= RADIUS_TOL:
                continue
            # Check axial position tolerance
            if abs(bore['position_x'] - axis_position) >= POS_TOL:
                continue
            # Check pitch_radius tolerance
            if abs(bore.get('pitch_radius', 0) - pitch_radius) >= PITCH_TOL:
                continue

            # Check the angle difference
            existing_angles = bore.get('angles', [bore.get('angle', 0)])
            angle_matched = False
            for existing_angle in existing_angles:
                angle_diff = abs(angle - existing_angle)
                if angle_diff > 180:
                    angle_diff = 360 - angle_diff
                if angle_diff <= ANGLE_TOL:
                    angle_matched = True
                    break

            if angle_matched:
                # Found the bore this face belongs to
                matched_bore = bore
                break

        if matched_bore:
            # Update the existing bore
            matched_bore['x_positions'].append(axis_position)
            matched_bore['x_min'] = min(matched_bore['x_positions'])
            matched_bore['x_max'] = max(matched_bore['x_positions'])
            matched_bore['length'] = round(matched_bore['x_max'] - matched_bore['x_min'], 4)
            matched_bore['face_lengths'].append(face_length)
            # Record the angle
            if 'angles' not in matched_bore:
                matched_bore['angles'] = [matched_bore.get('angle', 0)]
            matched_bore['angles'].append(round(angle, 2))
            matched_bore['angles'] = list(set(matched_bore['angles']))  # Deduplicate
        else:
            # Add a new bore
            new_bore = {
                'radius': round(radius, 4),
                'position_x': round(axis_position, 4),
                'x_min': axis_position,
                'x_max': axis_position,
                'x_positions': [axis_position],
                'length': 0.0,
                'face_lengths': [face_length],
                'pitch_radius': round(pitch_radius, 4),
                'angle': round(angle, 2),
                'angles': [round(angle, 2)],
                'count': 1  # Initial count is 1
            }
            self.inner_bores.append(new_bore)

    def _build_result(self) -> Dict[str, Any]:
        """
        Build the classification result

        Returns:
            dict: Classified feature data
        """
        classified_cylinders = []

        if self.outer_cylinders:
            # Sort by length to find Rotor_Core_Fit (longest)
            sorted_by_length = sorted(self.outer_cylinders, key=lambda x: x['length'], reverse=True)
            rotor_idx = 0

            for cyl in self.outer_cylinders:
                if cyl['radius'] > 30 and cyl['length'] > 0:
                    # Determine if this is the Rotor_Core_Fit (longest large cylinder)
                    if cyl['length'] == sorted_by_length[0]['length']:
                        feature_type = "Rotor_Core_Fit"
                    else:
                        feature_type = None  # No type for others
                else:
                    feature_type = None

                entry = {
                    'radius': cyl['radius'],
                    'position_x': cyl['position_x'],
                    'length': cyl['length'],
                    'area': cyl['area']
                }
                if feature_type:
                    entry['type'] = feature_type
                classified_cylinders.append(entry)

        processed_bores = []
        bore_summary = {}  # For aggregate statistics

        if self.inner_bores:
            sorted_bores = sorted(self.inner_bores, key=lambda x: (x['radius'], x['position_x'], x.get('pitch_radius', 0)), reverse=True)
            for bore in sorted_bores:
                # Use face_lengths to calculate actual length
                valid_lengths = [l for l in bore['face_lengths'] if l > 0]
                actual_length = max(valid_lengths) if valid_lengths else 0

                entry = {
                    'radius': bore['radius'],
                    'position_x': bore['position_x'],
                    'length': round(actual_length, 4)
                }

                # Add fields specific to distributed holes
                if bore.get('pitch_radius', 0) > 0.1:
                    entry['pitch_radius'] = bore.get('pitch_radius', 0)
                    entry['angle'] = bore.get('angle', 0)

                processed_bores.append(entry)

                # Aggregate statistics
                key = (round(bore['radius'], 1), round(bore['position_x'], 1))
                if key not in bore_summary:
                    bore_summary[key] = {
                        'radius': bore['radius'],
                        'position_x': bore['position_x'],
                        'count': 0,
                        'pitch_radii': set()
                    }
                bore_summary[key]['count'] += 1
                bore_summary[key]['pitch_radii'].add(round(bore.get('pitch_radius', 0), 1))

        # Build the summary
        bore_summaries = []
        for key, info in sorted(bore_summary.items(), key=lambda x: x[0][0], reverse=True):
            summary_entry = {
                'radius': info['radius'],
                'position_x': info['position_x'],
                'count': info['count'],
                'pitch_radii': sorted(list(info['pitch_radii']))
            }
            bore_summaries.append(summary_entry)

        return {
            'outer_cylinders': classified_cylinders,
            'inner_bore': processed_bores,
            'inner_bore_summary': bore_summaries
        }

    def _detect_segments(self, x_positions: List[float]) -> List[Dict[str, Any]]:
        """
        Detect discontinuous segments - cluster analysis of X positions

        Args:
            x_positions: List of all X positions

        Returns:
            list: Start/end positions and lengths of each segment
        """
        if not x_positions:
            return []

        # Sort positions
        sorted_pos = sorted(x_positions)

        # Cluster positions with gap > 5mm as separate segments
        segments = []
        current_segment = [sorted_pos[0]]
        GAP_THRESHOLD = 5.0  # mm

        for pos in sorted_pos[1:]:
            if pos - current_segment[-1] > GAP_THRESHOLD:
                # Save current segment
                seg_x_min = min(current_segment)
                seg_x_max = max(current_segment)
                segments.append({
                    'x_min': round(seg_x_min, 4),
                    'x_max': round(seg_x_max, 4),
                    'length': round(seg_x_max - seg_x_min, 4)
                })
                # Start new segment
                current_segment = [pos]
            else:
                current_segment.append(pos)

        # Don't forget last segment
        if current_segment:
            seg_x_min = min(current_segment)
            seg_x_max = max(current_segment)
            segments.append({
                'x_min': round(seg_x_min, 4),
                'x_max': round(seg_x_max, 4),
                'length': round(seg_x_max - seg_x_min, 4)
            })

        return segments

    def _cluster_by_position(self, cyls: List[Dict]) -> List[Dict]:
        """
        Cluster cylindrical faces of the same radius by axial position
        Cylinders whose axial spacing exceeds the threshold are treated as separate segments

        Args:
            cyls: List of cylindrical faces with the same radius

        Returns:
            list: List of clustered segments, each containing avg_position, max_length, total_area
        """
        if not cyls:
            return []

        # Compute the axial position of each cylinder.
        # Use the face's physical centroid (phys_center) rather than the origin of the underlying
        # infinite surface (cylinder.Location()): Location is the same point for coaxial multi-segment
        # cylinders (e.g. equal diameters at both shaft ends), which wrongly merges the two ends into
        # one segment, silently dropping the cylinder segment at the right end of a symmetric/mirrored
        # shaft and losing its segment length.
        cyls_with_pos = []
        for cyl in cyls:
            pos = self._get_axis_position(cyl.get('phys_center') or cyl['center'])
            cyls_with_pos.append({
                'cyl': cyl,
                'position': pos
            })

        # Sort by position
        cyls_with_pos.sort(key=lambda x: x['position'])

        # Cluster: spacing beyond GAP_THRESHOLD is treated as a different segment
        GAP_THRESHOLD = 5.0  # mm
        clusters = []
        current_cluster = [cyls_with_pos[0]]

        for item in cyls_with_pos[1:]:
            if item['position'] - current_cluster[-1]['position'] > GAP_THRESHOLD:
                # Save the current cluster
                positions = [c['position'] for c in current_cluster]
                lengths = [c['cyl'].get('v_length', 0) for c in current_cluster]
                areas = [c['cyl']['area'] for c in current_cluster]

                clusters.append({
                    'avg_position': sum(positions) / len(positions),
                    'max_length': max(lengths) if lengths else 0,
                    'total_area': sum(areas)
                })
                # Start a new cluster
                current_cluster = [item]
            else:
                current_cluster.append(item)

        # Handle the last cluster
        if current_cluster:
            positions = [c['position'] for c in current_cluster]
            lengths = [c['cyl'].get('v_length', 0) for c in current_cluster]
            areas = [c['cyl']['area'] for c in current_cluster]

            clusters.append({
                'avg_position': sum(positions) / len(positions),
                'max_length': max(lengths) if lengths else 0,
                'total_area': sum(areas)
            })

        return clusters

    def get_statistics(self) -> Dict[str, int]:
        """
        Get statistics

        Returns:
            dict: Statistics
        """
        return {
            'outer_cylinder_count': len(self.outer_cylinders),
            'inner_bore_count': len(self.inner_bores)
        }
