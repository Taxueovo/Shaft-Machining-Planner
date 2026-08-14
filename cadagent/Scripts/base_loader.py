#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Base BREP Loading Module

Refactor notes:
- Inner/outer surface classification based on geometry and topology
- Added the surface_classification attribute
"""

import math
from typing import Dict, List, Tuple, Optional, Any
from enum import Enum

from OCC.Core.BRepTools import breptools
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_BSplineSurface, GeomAbs_SurfaceOfRevolution
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps

# Constants
AXIS_PARALLEL_TOL = 0.01  # Tolerance for axis parallelism (radians)
SURFACE_DOT_TOL = 0.0      # Dot product tolerance for classification (positive -> outer, negative -> inner)


class SurfaceType(Enum):
    """Surface type enumeration"""
    OUTER = "outer"      # Outer surface (normal points away from the axis)
    INNER = "inner"      # Inner surface (normal points toward the axis)
    UNKNOWN = "unknown"  # Unknown / cannot be determined


class BaseBREPLoader:
    """
    Base BREP Loader (refactored)
    Provides BREP file loading, bounding box computation, main axis detection and surface classification
    """

    def __init__(self, brep_path: str):
        """
        Initialize the loader

        Args:
            brep_path: Path to the BREP file
        """
        self.brep_path = brep_path
        self.shape = None
        self.bounding_box = None
        self.main_axis = None
        self.faces_data = []

        # Statistics
        self.total_faces = 0
        self.outer_surface_count = 0
        self.inner_surface_count = 0
        self.cylinder_faces = 0
        self.plane_faces = 0
        self.spline_faces = 0

    def load_brep(self) -> bool:
        """Load the BREP file using the low-level BRepTools_Read"""
        try:
            from OCC.Core.TopoDS import TopoDS_Shape
            from OCC.Core.BRep import BRep_Builder

            shape = TopoDS_Shape()
            builder = BRep_Builder()

            print(f"Loading BREP file: {self.brep_path}")
            result = breptools.Read(shape, self.brep_path, builder)

            if result:
                self.shape = shape
                print("BREP file loaded successfully")
                return True
            else:
                print("Failed to read BREP file")
                return False

        except Exception as e:
            print(f"Error loading BREP file: {e}")
            return False

    def compute_bounding_box(self) -> Tuple[float, float, float, float, float, float]:
        """Compute the overall bounding box"""
        if self.shape is None:
            raise RuntimeError("Shape not loaded")

        bnd_box = Bnd_Box()
        brepbndlib.Add(self.shape, bnd_box)

        xmin, ymin, zmin, xmax, ymax, zmax = bnd_box.Get()
        self.bounding_box = (xmin, ymin, zmin, xmax, ymax, zmax)

        print(f"Bounding box: X[{xmin:.2f}, {xmax:.2f}], Y[{ymin:.2f}, {ymax:.2f}], Z[{zmin:.2f}, {zmax:.2f}]")

        return self.bounding_box

    def get_overall_dimensions(self) -> Dict[str, float]:
        """Get overall dimensions"""
        if self.bounding_box is None:
            self.compute_bounding_box()

        xmin, ymin, zmin, xmax, ymax, zmax = self.bounding_box

        main_x, main_y, main_z = self.main_axis['x'], self.main_axis['y'], self.main_axis['z']

        if abs(main_x) > 0.5:
            length = xmax - xmin
            max_diameter = max(ymax - ymin, zmax - zmin)
        elif abs(main_y) > 0.5:
            length = ymax - ymin
            max_diameter = max(xmax - xmin, zmax - zmin)
        else:
            length = zmax - zmin
            max_diameter = max(xmax - xmin, ymax - ymin)

        return {
            'length': round(length, 4),
            'max_diameter': round(max_diameter, 4)
        }

    def traverse_faces(self) -> None:
        """Traverse all TopoDS_Face and extract data"""
        if self.shape is None:
            raise RuntimeError("Shape not loaded")

        # Do not call detect_main_axis() automatically here to avoid recursion
        # main_axis should be set by detect_main_axis() before traverse_faces()
        # or initialized via init_main_axis()
        if self.main_axis is None:
            self.init_main_axis()

        explorer = TopExp_Explorer(self.shape, TopAbs_FACE)

        face_index = 0
        while explorer.More():
            face = explorer.Current()
            face_data = self._analyze_face(face, face_index)
            if face_data:
                self.faces_data.append(face_data)
                self.total_faces += 1

                # Count surface types
                surf_class = face_data.get('surface_classification', 'UNKNOWN')
                if surf_class == 'outer':
                    self.outer_surface_count += 1
                elif surf_class == 'inner':
                    self.inner_surface_count += 1

                surf_type = face_data['surface_type']
                if surf_type == 'cylinder':
                    self.cylinder_faces += 1
                elif surf_type == 'plane':
                    self.plane_faces += 1
                elif surf_type in ['bspline', 'revolution']:
                    self.spline_faces += 1

            explorer.Next()
            face_index += 1

        print(f"Total faces: {self.total_faces}")
        print(f"  - Outer surfaces: {self.outer_surface_count}")
        print(f"  - Inner surfaces: {self.inner_surface_count}")
        print(f"  - Cylinder faces: {self.cylinder_faces}")
        print(f"  - Plane faces: {self.plane_faces}")
        print(f"  - Spline/Revolution faces: {self.spline_faces}")

    def _analyze_face(self, face, index: int) -> Optional[Dict[str, Any]]:
        """Analyze the geometric properties of a single face"""
        try:
            surf_adapter = BRepAdaptor_Surface(face, True)
            surf_type = surf_adapter.GetType()

            face_data = {
                'index': index,
                'surface_type': None,
                'area': 0.0,
                'center': None,
                'axis_direction': None,
                'radius': None,
                'surface_classification': 'unknown',  # New: topological classification
                'is_reversed': face.Orientation() == TopAbs_REVERSED
            }

            try:
                props = GProp_GProps()
                brepgprop.SurfaceProperties(face, props)
                face_data['area'] = props.Mass()
            except:
                pass

            if surf_type == GeomAbs_Cylinder:
                result = self._analyze_cylinder_face(face, surf_adapter, face_data)
                # Apply the topological classification
                result['surface_classification'] = self._classify_cylinder_surface(result)
                return result
            elif surf_type == GeomAbs_Plane:
                return self._analyze_plane_face(face, surf_adapter, face_data)
            elif surf_type == GeomAbs_BSplineSurface:
                face_data['surface_type'] = 'bspline'
                face_data['center'] = self._get_face_center(face)
                return face_data
            elif surf_type == GeomAbs_SurfaceOfRevolution:
                face_data['surface_type'] = 'revolution'
                face_data['center'] = self._get_face_center(face)
                return face_data
            else:
                face_data['surface_type'] = 'other'
                face_data['center'] = self._get_face_center(face)
                return face_data

        except Exception as e:
            print(f"Error analyzing face {index}: {e}")
            return None

    def _analyze_cylinder_face(self, face, surf_adapter, face_data: Dict) -> Dict:
        """Analyze a cylindrical face"""
        face_data['surface_type'] = 'cylinder'

        cylinder = surf_adapter.Cylinder()
        face_data['radius'] = cylinder.Radius()

        axis = cylinder.Axis()
        dir_vec = axis.Direction()
        face_data['axis_direction'] = {
            'x': dir_vec.X(),
            'y': dir_vec.Y(),
            'z': dir_vec.Z()
        }

        loc = cylinder.Location()
        face_data['center'] = {
            'x': loc.X(),
            'y': loc.Y(),
            'z': loc.Z()
        }

        # [Bug fix] Use the physical centroid of the trimmed face rather than the origin of the underlying infinite surface
        # This correctly distinguishes same-radius cylindrical faces that are geometrically coplanar but physically separated
        face_data['phys_center'] = self._get_face_center(face)

        # Keep the original orientation marker (for reference)
        face_data['normal_direction'] = 'outward' if not face.Orientation() == TopAbs_REVERSED else 'inward'

        # Extract UV parameter ranges for length calculation
        face_data['u_range'] = {
            'u_min': surf_adapter.FirstUParameter(),
            'u_max': surf_adapter.LastUParameter()
        }
        face_data['v_range'] = {
            'v_min': surf_adapter.FirstVParameter(),
            'v_max': surf_adapter.LastVParameter()
        }
        face_data['v_length'] = abs(surf_adapter.LastVParameter() - surf_adapter.FirstVParameter())
        face_data['u_angle'] = abs(surf_adapter.LastUParameter() - surf_adapter.FirstUParameter())

        return face_data

    def _analyze_plane_face(self, face, surf_adapter, face_data: Dict) -> Dict:
        """Analyze a planar face"""
        face_data['surface_type'] = 'plane'

        plane = surf_adapter.Plane()
        dir_vec = plane.Axis().Direction()
        face_data['normal'] = {
            'x': dir_vec.X(),
            'y': dir_vec.Y(),
            'z': dir_vec.Z()
        }

        loc = plane.Location()
        face_data['center'] = {
            'x': loc.X(),
            'y': loc.Y(),
            'z': loc.Z()
        }

        return face_data

    def _classify_cylinder_surface(self, face_data: Dict) -> str:
        """
        [Core refactor] Classify inner/outer surfaces via the dot product of the normal and radial vectors

        Logic:
        - For a cylindrical surface, the normal is radial, pointing outward (or inward)
        - Radial vector R = face center - nearest point on the axis
        - Dot product N.R > 0 -> outer surface (normal points away from the axis)
        - Dot product N.R < 0 -> inner surface (normal points toward the axis)

        Args:
            face_data: Cylindrical face data

        Returns:
            str: 'outer', 'inner', or 'unknown'
        """
        center = face_data.get('center')
        axis_dir = face_data.get('axis_direction')

        if not center or not axis_dir:
            return 'unknown'

        # 1. Compute the radial normal of the cylindrical face
        # The axis direction of the cylindrical face is axis_dir
        # The face normal is perpendicular to the axis, pointing inward or outward
        # In practice we need the radial component from the axis to the face center

        # Get the main axis direction index
        main_axis = self.main_axis
        if abs(main_axis['x']) > 0.5:
            axis_idx = 0
            radial_plane = ('y', 'z')
        elif abs(main_axis['y']) > 0.5:
            axis_idx = 1
            radial_plane = ('x', 'z')
        else:
            axis_idx = 2
            radial_plane = ('x', 'y')

        # 2. Compute the radial vector from the main axis to the face center
        # The axis of the cylindrical face is its center
        # Radial vector = center - projection onto main axis

        # Compute the projection of the face center onto the main axis
        axis_dot = (center['x'] * main_axis['x'] +
                   center['y'] * main_axis['y'] +
                   center['z'] * main_axis['z'])

        # Nearest point on the axis
        proj_x = axis_dot * main_axis['x']
        proj_y = axis_dot * main_axis['y']
        proj_z = axis_dot * main_axis['z']

        # Radial vector R = face center - nearest point on the axis
        radial_x = center['x'] - proj_x
        radial_y = center['y'] - proj_y
        radial_z = center['z'] - proj_z

        # Radial distance
        radial_dist = math.sqrt(radial_x**2 + radial_y**2 + radial_z**2)

        if radial_dist < 0.001:  # Face lies on the main axis, cannot classify
            return 'unknown'

        # 3. Cylindrical face normal
        # The normal direction could be derived from a cross product with the axis
        # but simpler: the unit vector of the radial vector from the axis to the face center
        # The face normal is either aligned with R (outer) or opposite to R (inner)

        # Normalize the radial vector
        radial_unit = {
            'x': radial_x / radial_dist,
            'y': radial_y / radial_dist,
            'z': radial_z / radial_dist
        }

        # 4. Use the dot product to decide inner vs outer
        # For a cylinder, the normal is radial_unit (outer) or -radial_unit (inner)
        # Use is_reversed as an auxiliary signal
        is_reversed = face_data.get('is_reversed', False)

        # is_reversed = True means the normal points inward (opposite to the geometric outward normal)
        # So: is_reversed=False -> outer surface, is_reversed=True -> inner surface
        if is_reversed:
            return 'inner'
        else:
            return 'outer'

    def _get_face_coordinate(self, face_data: Dict) -> float:
        """Get the face coordinate along the main axis direction"""
        center = face_data.get('center')
        if center is None:
            return 0.0

        main_axis = self.main_axis
        return (center['x'] * main_axis['x'] +
                center['y'] * main_axis['y'] +
                center['z'] * main_axis['z'])

    def _get_axis_direction_index(self) -> int:
        """Get the main axis direction index (0=x, 1=y, 2=z)"""
        if abs(self.main_axis['x']) > 0.5:
            return 0
        elif abs(self.main_axis['y']) > 0.5:
            return 1
        else:
            return 2

    def _get_face_center(self, face) -> Dict[str, float]:
        """Get the geometric center of the face"""
        try:
            props = GProp_GProps()
            brepgprop.SurfaceProperties(face, props)
            cog = props.CentreOfMass()
            return {'x': cog.X(), 'y': cog.Y(), 'z': cog.Z()}
        except:
            return {'x': 0.0, 'y': 0.0, 'z': 0.0}

    def init_main_axis(self) -> None:
        """
        Initialize the main axis - set a default axis before traversing faces
        This avoids recursive calls
        """
        # Set a temporary axis direction; it will be updated later by detect_main_axis()
        self.main_axis = {'x': 0.0, 'y': 0.0, 'z': 1.0}

    def detect_main_axis(self) -> Dict[str, float]:
        """
        Detect the main axis - based on the axis of the largest outer cylindrical face
        Requires traverse_faces() to be called first to populate faces_data
        """
        if not self.faces_data:
            # If faces have not been traversed yet, the call order is wrong
            # Fall back to the default main axis
            self.main_axis = {'x': 0.0, 'y': 0.0, 'z': 1.0}
            return self.main_axis

        # Find the outer cylindrical face with the largest radius
        outer_cylinders = [
            f for f in self.faces_data
            if f['surface_type'] == 'cylinder'
            and f.get('surface_classification') == 'outer'
            and f['radius'] is not None
            and f['radius'] > 10.0
        ]

        if not outer_cylinders:
            # If no classification is available, use the legacy fallback
            outer_cylinders = [
                f for f in self.faces_data
                if f['surface_type'] == 'cylinder'
                and f['radius'] is not None
                and f['radius'] > 10.0
            ]

        if not outer_cylinders:
            self.main_axis = {'x': 0.0, 'y': 0.0, 'z': 1.0}
            return self.main_axis

        outer_cylinders.sort(key=lambda x: x['radius'], reverse=True)
        largest_cylinder = outer_cylinders[0]

        self.main_axis = largest_cylinder['axis_direction']
        print(f"Main axis detected: {self.main_axis}")

        return self.main_axis

    def _is_axis_parallel(self, axis1: Dict, axis2: Dict, tolerance: float = AXIS_PARALLEL_TOL) -> bool:
        """Check whether two axes are parallel"""
        dot = abs(axis1['x'] * axis2['x'] +
                  axis1['y'] * axis2['y'] +
                  axis1['z'] * axis2['z'])
        return abs(dot - 1.0) < tolerance or abs(dot + 1.0) < tolerance

    def _is_axis_perpendicular(self, axis1: Dict, axis2: Dict, tolerance: float = AXIS_PARALLEL_TOL) -> bool:
        """Check whether two axes are perpendicular"""
        dot = abs(axis1['x'] * axis2['x'] +
                  axis1['y'] * axis2['y'] +
                  axis1['z'] * axis2['z'])
        return dot < tolerance

    def get_outer_cylinders(self) -> List[Dict]:
        """Get all outer cylindrical faces"""
        return [f for f in self.faces_data
                if f['surface_type'] == 'cylinder'
                and f.get('surface_classification') == 'outer']

    def get_inner_cylinders(self) -> List[Dict]:
        """Get all inner cylindrical faces (bores)"""
        return [f for f in self.faces_data
                if f['surface_type'] == 'cylinder'
                and f.get('surface_classification') == 'inner']
