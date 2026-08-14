#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Gear Feature Extraction Module (refactored v9 - based on the 2D section scanning
architecture of gear_features1.py)

Optimizations:
1. Lowered MIN_TOOTH_HEIGHT_DIFF from 1.0 to 0.4 (detects small-module gears)
2. Introduced an ISO standard module fitting algorithm
3. Adopted a 2D section scanning + polar coordinate peak detection architecture
"""

import math
import logging
import os
from typing import Callable, Dict, List, Any, Tuple, Optional

from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Section
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_SOLID, TopAbs_SHELL, TopAbs_COMPOUND
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.gp import gp_Pnt, gp_Dir, gp_Pln
from OCC.Core.TopoDS import TopoDS_Shape, TopoDS_Edge
from OCC.Core.BRep import BRep_Tool

from .base_loader import BaseBREPLoader

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ============ Core parameters ============
SAMPLE_POINT_COUNT = 3600      # Substantially increased sampling point count
SECTION_STEP_MM = 5.0          # Z-axis scan step (mm)
# [Optimization 1] Lowered threshold to detect small-module gears
MIN_TOOTH_HEIGHT_DIFF = 0.4    # mm Minimum tooth height (threshold for classifying a gear zone)
PEAK_NEIGHBOR_COUNT = 3        # Number of neighbor points for peak detection
PEAK_RADIUS_RATIO = 0.9        # Peak radius threshold (r > r_max * 0.9)

# ISO standard module series
STANDARD_MODULES = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 8.0, 10.0]

# Z-axis section scan parallel switch (multiprocessing; measured 2.81x speedup on real parts
# with results identical to serial; a worker process exception raises BrokenProcessPool
# which falls back to serial automatically without hanging the main flow).
# - CAD_GEAR_PARALLEL_SCAN=0 / false / off -> serial (identical to the legacy version)
# - CAD_GEAR_PARALLEL_SCAN=1 (default)      -> parallel (multiprocessing)
# - CAD_GEAR_PARALLEL_WORKERS=N             -> worker process count (default = CPU cores - 1)
PARALLEL_SCAN_ENABLED = os.getenv("CAD_GEAR_PARALLEL_SCAN", "1").strip().lower() not in ("0", "false", "off")
PARALLEL_MAX_WORKERS = int(os.getenv("CAD_GEAR_PARALLEL_WORKERS", "0") or 0)
# =================================


def _section_shape(shape: Optional[TopoDS_Shape]) -> Optional[TopoDS_Shape]:
    """Extract a SOLID/SHELL from a COMPOUND for section cutting (consistent with _get_section_shape)."""
    if shape is None:
        return None
    if shape.ShapeType() == TopAbs_COMPOUND:
        explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        if explorer.More():
            return explorer.Current()
        explorer = TopExp_Explorer(shape, TopAbs_SHELL)
        if explorer.More():
            return explorer.Current()
    return shape


def _analyze_section_for_worker(shape: TopoDS_Shape, position: float,
                                main_axis: Dict[str, float]) -> Optional[Dict[str, Any]]:
    """Single-section analysis for a multiprocessing worker.

    Logic is identical to ``GearFeatureExtractor._analyze_section_at_position``,
    but returns a pickleable dict (centroid as an (x, y, z) tuple rather than an OCC gp_Pnt).
    Returns None on failure.
    """
    try:
        ax_x, ax_y, ax_z = main_axis["x"], main_axis["y"], main_axis["z"]
        plane = gp_Pln(gp_Pnt(ax_x * position, ax_y * position, ax_z * position),
                       gp_Dir(ax_x, ax_y, ax_z))
        section = BRepAlgoAPI_Section(shape, plane, False)
        section.Build()
        if not section.IsDone():
            return None

        edges = []
        explorer = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
        while explorer.More():
            edge = explorer.Current()
            if edge:
                edges.append(edge)
            explorer.Next()
        if not edges:
            return None

        all_points = []
        points_per_edge = max(1, SAMPLE_POINT_COUNT // len(edges))
        for edge in edges:
            adaptor = BRepAdaptor_Curve(edge)
            first = adaptor.FirstParameter()
            last = adaptor.LastParameter()
            if last <= first or points_per_edge <= 1:
                continue
            step = (last - first) / (points_per_edge - 1)
            for i in range(points_per_edge):
                all_points.append(adaptor.Value(first + i * step))
        if len(all_points) < 10:
            return None

        sx = sy = sz = 0.0
        for pnt in all_points:
            sx += pnt.X()
            sy += pnt.Y()
            sz += pnt.Z()
        n = len(all_points)
        cx, cy, cz = sx / n, sy / n, sz / n

        if abs(main_axis["x"]) > 0.5:
            axis_idx = 0
        elif abs(main_axis["y"]) > 0.5:
            axis_idx = 1
        else:
            axis_idx = 2

        polar_data = []
        for pnt in all_points:
            if axis_idx == 0:
                r = math.sqrt((pnt.Y() - cy) ** 2 + (pnt.Z() - cz) ** 2)
                theta = math.atan2(pnt.Z() - cz, pnt.Y() - cy)
            elif axis_idx == 1:
                r = math.sqrt((pnt.X() - cx) ** 2 + (pnt.Z() - cz) ** 2)
                theta = math.atan2(pnt.Z() - cz, pnt.X() - cx)
            else:
                r = math.sqrt((pnt.X() - cx) ** 2 + (pnt.Y() - cy) ** 2)
                theta = math.atan2(pnt.Y() - cy, pnt.X() - cx)
            polar_data.append((r, theta))

        polar_data.sort(key=lambda x: x[1])
        radii = [r for r, theta in polar_data]
        max_radius = max(radii) if radii else 0.0
        outer_radii = [r for r in radii if r > max_radius * 0.85]
        min_radius = min(outer_radii) if outer_radii else 0.0

        return {
            "position": position,
            "max_radius": max_radius,
            "min_radius": min_radius,
            "radius_diff": max_radius - min_radius,
            "point_count": len(polar_data),
            "centroid": (cx, cy, cz),
            "polar_data": polar_data,
        }
    except Exception:
        return None


def _process_chunk(args: Tuple[str, List[float], Dict[str, float]]) -> List[Tuple[float, Dict[str, Any]]]:
    """Multiprocessing worker: loads the BREP independently and analyzes a chunk of sections.
    Returns a pickleable list of (position, profile) pairs.

    .. note:: Superseded by ``_process_chunk_with_progress`` (chunk return + shared progress counter).
        Kept only to minimize diff / allow fallback.
    """
    brep_path, positions_chunk, main_axis = args
    loader = BaseBREPLoader(brep_path)
    if not loader.load_brep():
        return []
    shape = _section_shape(loader.shape)
    if shape is None:
        return []
    out = []
    for pos in positions_chunk:
        profile = _analyze_section_for_worker(shape, pos, main_axis)
        if profile:
            out.append((pos, profile))
    return out


# In-process shared state for parallel workers: BREP shape (loaded once by the initializer)
# + progress counter.
# Note: returning per-section results (polar_data with 3600 samples) per task measurably
# slowed things down on this machine (~7-10x per-task pickle round trip), so the parallel
# implementation still dispatches "chunk returns a list" (one large pickle per worker);
# fine-grained progress instead uses a shared ``multiprocessing.Value`` counter that each
# worker increments per completed section and the main process polls, avoiding extra
# cross-process result round trips.
_WORKER_SECTION_SHAPE = None
_WORKER_SECTION_AXIS = None
_WORKER_PROGRESS_COUNTER = None


def _init_section_worker(brep_path: str, main_axis: Dict[str, float], progress_counter=None) -> None:
    """Process pool worker initialization: each worker loads the BREP only once; the shape and
    progress counter are stored in module-level globals.

    Set to None on load failure (or when no valid SOLID/SHELL can be extracted); all sections
    of that worker then return empty.
    """
    global _WORKER_SECTION_SHAPE, _WORKER_SECTION_AXIS, _WORKER_PROGRESS_COUNTER
    _WORKER_SECTION_SHAPE = None
    loader = BaseBREPLoader(brep_path)
    if loader.load_brep():
        _WORKER_SECTION_SHAPE = _section_shape(loader.shape)
    _WORKER_SECTION_AXIS = main_axis
    _WORKER_PROGRESS_COUNTER = progress_counter


def _advance_progress() -> None:
    """Increment the shared progress counter once per completed section (no-op without a counter)."""
    counter = _WORKER_PROGRESS_COUNTER
    if counter is not None:
        with counter.get_lock():
            counter.value += 1


def _process_chunk_with_progress(args: Tuple[str, List[float], Dict[str, float]]) -> List[Tuple[float, Dict[str, Any]]]:
    """Chunk worker: loads the BREP independently, analyzes a chunk of sections, and increments
    the shared progress counter per completed section.

    Returns a pickleable list of (position, profile) pairs (same as the legacy ``_process_chunk``,
    keeping the high throughput of a single large pickle return; progress is reported via the
    shared counter).
    """
    brep_path, positions_chunk, main_axis = args
    loader = BaseBREPLoader(brep_path)
    if not loader.load_brep():
        return []
    shape = _section_shape(loader.shape)
    if shape is None:
        return []
    out = []
    for pos in positions_chunk:
        profile = _analyze_section_for_worker(shape, pos, main_axis)
        if profile:
            out.append((pos, profile))
        _advance_progress()
    return out


class GearFeatureExtractor:
    """
    Gear Feature Extractor (based on a 2D section scanning architecture)
    """

    def __init__(self, base_loader: BaseBREPLoader, max_workers: Optional[int] = None,
                 progress_callback: Optional[Callable[[int, int], None]] = None):
        self.base_loader = base_loader
        self.gear_zones = []
        self.section_profiles = []
        # max_workers: None -> decided by the CAD_GEAR_PARALLEL_SCAN environment variable;
        #               1 -> force serial; >1 -> force parallel
        self.max_workers = max_workers
        # progress_callback(done, total): called once per completed Z-axis scan section (for progress bars).
        # When None, behavior is identical to the legacy version.
        self._progress_callback = progress_callback

    def extract(self) -> Dict[str, Any]:
        """Run gear feature extraction"""
        if self.base_loader.bounding_box is None:
            self.base_loader.compute_bounding_box()

        self._z_axis_scan()
        self._detect_gear_zones_from_profiles()

        return self._build_result()

    @property
    def main_axis(self):
        return self.base_loader.main_axis

    def _get_main_axis_vector(self, main_axis: Optional[Dict[str, float]] = None) -> Tuple[float, float, float]:
        """Get the main axis vector"""
        ax = main_axis if main_axis is not None else self.main_axis
        return (ax['x'], ax['y'], ax['z'])

    def _get_axis_index(self, main_axis: Optional[Dict[str, float]] = None) -> int:
        """Get the main axis direction index (0=x, 1=y, 2=z)"""
        ax = main_axis if main_axis is not None else self.main_axis
        if abs(ax['x']) > 0.5:
            return 0
        elif abs(ax['y']) > 0.5:
            return 1
        else:
            return 2

    def _get_radial_coords(self, pnt: gp_Pnt, origin: gp_Pnt,
                           main_axis: Optional[Dict[str, float]] = None) -> Tuple[float, float]:
        """Get the radial coordinates (r, theta) of a point"""
        idx = self._get_axis_index(main_axis)
        if idx == 0:
            dy = pnt.Y() - origin.Y()
            dz = pnt.Z() - origin.Z()
            r = math.sqrt(dy**2 + dz**2)
            theta = math.atan2(dz, dy)
        elif idx == 1:
            dx = pnt.X() - origin.X()
            dz = pnt.Z() - origin.Z()
            r = math.sqrt(dx**2 + dz**2)
            theta = math.atan2(dz, dx)
        else:
            dx = pnt.X() - origin.X()
            dy = pnt.Y() - origin.Y()
            r = math.sqrt(dx**2 + dy**2)
            theta = math.atan2(dy, dx)
        return r, theta

    # ==================== Z-axis scanning method ====================

    def _z_axis_scan(self) -> None:
        """Z-axis scan: cut a section every 5mm along the main axis bounding_box range

        Step 1: supports parallel processing. Each section is independent; when parallel,
        results are reordered by axial position, identical to serial. Serial by default
        (same behavior as the legacy version), enabled via ``max_workers`` or the
        ``CAD_GEAR_PARALLEL_SCAN`` environment variable.
        """
        bbox = self.base_loader.bounding_box
        if bbox is None:
            logger.warning("No bounding box available")
            return

        xmin, ymin, zmin, xmax, ymax, zmax = bbox
        idx = self._get_axis_index()

        if idx == 0:
            z_start, z_end = xmin, xmax
        elif idx == 1:
            z_start, z_end = ymin, ymax
        else:
            z_start, z_end = zmin, zmax

        logger.info(f"Z-axis scan: [{z_start:.2f}, {z_end:.2f}], step={SECTION_STEP_MM}mm")

        positions = []
        current_pos = z_start
        while current_pos <= z_end:
            positions.append(current_pos)
            current_pos += SECTION_STEP_MM
        total_sections = len(positions)

        if self._use_parallel_scan() and total_sections > 1:
            try:
                profiles = self._scan_positions_parallel(positions, total_sections)
            except Exception:
                # Exception inside the scan (e.g. an individual OCC operation failing) -> fall back to serial without affecting the main flow
                logger.warning("Parallel scan failed; falling back to serial scan.", exc_info=True)
                profiles = self._scan_positions_serial(positions, total_sections)
        else:
            profiles = self._scan_positions_serial(positions, total_sections)

        self.section_profiles = profiles
        logger.info(f"Scanned {len(self.section_profiles)} sections")

    def _use_parallel_scan(self) -> bool:
        """Whether parallel scanning is enabled (max_workers takes precedence, then the environment variable)."""
        if self.max_workers == 1:
            return False
        if self.max_workers is not None and self.max_workers > 1:
            return True
        return PARALLEL_SCAN_ENABLED

    def _scan_workers(self) -> int:
        """Number of parallel workers (default = CPU cores - 1, at least 1)."""
        if self.max_workers is not None and self.max_workers > 1:
            return self.max_workers
        return PARALLEL_MAX_WORKERS or max(1, (os.cpu_count() or 1) - 1)

    def _scan_positions_serial(self, positions: List[float], total_sections: int) -> List[Dict[str, Any]]:
        """Serial section-by-section scan (same behavior as the legacy version)."""
        profiles = []
        for section_index, current_pos in enumerate(positions, 1):
            profile = self._analyze_section_at_position(current_pos)
            if profile:
                profiles.append(profile)
            if self._progress_callback:
                self._progress_callback(section_index, total_sections)
            if section_index % 5 == 0 or section_index == total_sections:
                logger.info(
                    f"  [progress] section {section_index}/{total_sections} "
                    f"@ {current_pos:.1f}mm (profiles={len(profiles)})"
                )
        return profiles

    def _scan_positions_parallel(self, positions: List[float], total_sections: int) -> List[Dict[str, Any]]:
        """Multiprocess parallel scan.

        The threading approach proved ineffective: pythonocc's recomputation
        (BRepAlgoAPI_Section.Build) holds the GIL, so threads merely queue up
        (measured speedup of 1.00x on real parts). Switched to multiprocessing, where
        each process has its own GIL and its own OCC:
        - Each worker process loads the shape once from the BREP file via
          ``_init_section_worker`` (no sharing, no reference count contention; load count
          = number of workers, equivalent to the legacy chunk scheme);
        - Sections are split round-robin as ``positions[i::workers]`` (chunk tasks), and each
          worker returns its entire chunk of sections as one list (one large pickle, measured
          much faster than per-section returns);
        - Fine-grained progress uses a shared ``multiprocessing.Value`` counter: each worker
          increments it per completed section, while this method runs the process pool in a
          separate thread and the main thread polls the counter every 0.5s calling
          ``progress_callback(done, total)`` (for progress bars);
        - Results are pickleable (centroid as a tuple) and reordered by axial position,
          identical to serial.
        """
        import time as _time
        from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
        from multiprocessing import Value

        workers = self._scan_workers()
        if workers <= 1 or len(positions) <= 1:
            return self._scan_positions_serial(positions, total_sections)
        workers = min(workers, len(positions))

        brep_path = getattr(self.base_loader, "brep_path", None)
        if not brep_path:
            return self._scan_positions_serial(positions, total_sections)

        main_axis = dict(self.main_axis)  # Snapshot; safe for read-only use across processes
        chunks = [positions[i::workers] for i in range(workers)]
        tasks = [(brep_path, chunk, main_axis) for chunk in chunks]
        logger.info(
            f"Z-axis scan (parallel, multiprocessing): {total_sections} sections, workers={workers}"
        )

        # Shared progress counter: workers increment per completed section; the main process polls it for the progress bar.
        counter = Value("i", 0)

        def _map_in_thread():
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_section_worker,
                initargs=(brep_path, main_axis, counter),
            ) as pool:
                return list(pool.map(_process_chunk_with_progress, tasks))

        collected = []
        with ThreadPoolExecutor(max_workers=1) as executor:
            fut = executor.submit(_map_in_thread)
            while not fut.done():
                if self._progress_callback:
                    with counter.get_lock():
                        done = counter.value
                    self._progress_callback(done, total_sections)
                _time.sleep(0.5)
            chunk_results = fut.result()

        collected = [p for chunk in chunk_results for p in chunk]
        collected.sort(key=lambda item: item[0])
        logger.info(f"  [progress] section {total_sections}/{total_sections} (profiles={len(collected)})")
        return [profile for _, profile in collected]

    def _create_section_plane(self, position: float,
                              main_axis: Optional[Dict[str, float]] = None) -> gp_Pln:
        """Create a section plane perpendicular to the main axis"""
        ax_x, ax_y, ax_z = self._get_main_axis_vector(main_axis)
        loc = gp_Pnt(ax_x * position, ax_y * position, ax_z * position)
        normal = gp_Dir(ax_x, ax_y, ax_z)
        return gp_Pln(loc, normal)

    def _get_section_shape(self) -> Optional[TopoDS_Shape]:
        """Extract a SOLID from the COMPOUND for section cutting"""
        shape = self.base_loader.shape
        if shape is None:
            return None

        if shape.ShapeType() == TopAbs_COMPOUND:
            explorer = TopExp_Explorer(shape, TopAbs_SOLID)
            if explorer.More():
                logger.debug("Using SOLID from COMPOUND")
                return explorer.Current()

            explorer = TopExp_Explorer(shape, TopAbs_SHELL)
            if explorer.More():
                logger.debug("Using SHELL from COMPOUND")
                return explorer.Current()

        logger.debug(f"Using original shape type: {shape.ShapeType()}")
        return shape

    def _extract_section_edges(self, shape: TopoDS_Shape, section_plane: gp_Pln) -> List[TopoDS_Edge]:
        """Extract section edges - pure mathematical sectioning directly with gp_Pln"""
        try:
            section = BRepAlgoAPI_Section(shape, section_plane, False)
            section.Build()

            if not section.IsDone():
                logger.debug("Section build failed")
                return []

            edges = []
            explorer = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
            while explorer.More():
                edge = explorer.Current()
                if edge:
                    edges.append(edge)
                explorer.Next()

            return edges

        except Exception as e:
            logger.debug(f"Section extraction failed: {e}")
            return []

    def _sample_curve_points(self, edge: TopoDS_Edge, num_points: int) -> List[gp_Pnt]:
        """Uniform parametric sampling of an edge - using BRepAdaptor_Curve"""
        try:
            adaptor = BRepAdaptor_Curve(edge)

            first = adaptor.FirstParameter()
            last = adaptor.LastParameter()

            if last <= first or num_points <= 1:
                return []

            points = []
            step = (last - first) / (num_points - 1)

            for i in range(num_points):
                t = first + i * step
                pnt = adaptor.Value(t)
                points.append(pnt)

            return points

        except Exception as e:
            logger.debug(f"Curve sampling failed: {e}")
            return []

    def _analyze_section_at_position(self, position: float,
                                     shape: Optional[TopoDS_Shape] = None,
                                     main_axis: Optional[Dict[str, float]] = None) -> Optional[Dict[str, Any]]:
        """Analyze the section at the given position.

        ``shape`` / ``main_axis`` can be passed in optionally; for parallel scanning each
        worker gets its own independent shape (isolating OCC objects and avoiding the
        reference-count contention of sharing a shape across threads, which can crash the
        process); by default the shared shape / main_axis of self.base_loader is used.
        """
        try:
            target_shape = shape if shape is not None else self._get_section_shape()
            if target_shape is None:
                return None

            plane = self._create_section_plane(position, main_axis)
            edges = self._extract_section_edges(target_shape, plane)

            if not edges:
                return None

            all_points = []
            points_per_edge = max(1, SAMPLE_POINT_COUNT // len(edges))

            for edge in edges:
                pts = self._sample_curve_points(edge, points_per_edge)
                all_points.extend(pts)

            if len(all_points) < 10:
                return None

            centroid = self._compute_centroid(all_points)

            polar_data = []
            for pnt in all_points:
                r, theta = self._get_radial_coords(pnt, centroid, main_axis)
                polar_data.append((r, theta))

            polar_data.sort(key=lambda x: x[1])

            radii = [r for r, theta in polar_data]
            max_radius = max(radii) if radii else 0

            # Only compute the minimum of the outer profile; ignore inner bores
            outer_radii = [r for r in radii if r > max_radius * 0.85]
            min_radius = min(outer_radii) if outer_radii else 0

            return {
                'position': position,
                'max_radius': max_radius,
                'min_radius': min_radius,
                'radius_diff': max_radius - min_radius,
                'point_count': len(polar_data),
                'centroid': centroid,
                'polar_data': polar_data
            }

        except Exception as e:
            logger.debug(f"Section analysis failed at {position}: {e}")
            return None

    def _compute_centroid(self, points: List[gp_Pnt]) -> gp_Pnt:
        """Compute the arithmetic mean of the sample points as the centroid"""
        if not points:
            return gp_Pnt(0, 0, 0)

        sum_x, sum_y, sum_z = 0.0, 0.0, 0.0
        for pnt in points:
            sum_x += pnt.X()
            sum_y += pnt.Y()
            sum_z += pnt.Z()

        n = len(points)
        return gp_Pnt(sum_x / n, sum_y / n, sum_z / n)

    # ==================== Gear zone detection ====================

    def _detect_gear_zones_from_profiles(self) -> None:
        """Detect gear zones based on the sections' radius_diff"""
        if not self.section_profiles:
            logger.info("No section profiles to analyze")
            return

        gear_sections = []
        for profile in self.section_profiles:
            is_gear = profile['radius_diff'] > MIN_TOOTH_HEIGHT_DIFF
            gear_sections.append({
                'position': profile['position'],
                'is_gear': is_gear,
                'max_radius': profile['max_radius'],
                'min_radius': profile['min_radius'],
                'radius_diff': profile['radius_diff'],
                'polar_data': profile['polar_data'],
                'centroid': profile['centroid']
            })

        current_zone = None
        zones = []

        for section in gear_sections:
            if section['is_gear']:
                if current_zone is None:
                    current_zone = {
                        'start': section['position'],
                        'end': section['position'],
                        'sections': [section]
                    }
                else:
                    current_zone['end'] = section['position']
                    current_zone['sections'].append(section)
            else:
                if current_zone is not None:
                    zones.append(current_zone)
                    current_zone = None

        if current_zone is not None:
            zones.append(current_zone)

        for zone in zones:
            self._analyze_gear_zone(zone)

    def _analyze_gear_zone(self, zone: Dict) -> None:
        """Analyze a gear zone, computing tooth count and parameters"""
        sections = zone['sections']
        if not sections:
            return

        mid_idx = len(sections) // 2
        mid_section = sections[mid_idx]

        polar_data = mid_section['polar_data']
        max_radius = mid_section['max_radius']
        min_radius = mid_section['min_radius']

        tooth_count, _ = self._find_peaks_precise(polar_data, max_radius)

        if tooth_count == 0:
            return

        # [Optimization 2] Introduced the ISO standard module fitting algorithm
        raw_module = (2 * max_radius) / (tooth_count + 2) if tooth_count > 0 else 0

        if raw_module > 0:
            closest_module = min(STANDARD_MODULES, key=lambda x: abs(x - raw_module))
            if abs(raw_module - closest_module) / raw_module < 0.15:
                module = closest_module
            else:
                module = raw_module
        else:
            module = 0.0

        # [Optimization 3] Compute the axial width for geometric consistency validation
        axial_width = zone['end'] - zone['start']
        tooth_height = max_radius - min_radius

        # Mechanical geometric consistency filtering - prevents chamfer/spline false positives
        if not self._is_valid_gear_geometry(tooth_count, max_radius, min_radius,
                                              module, axial_width, polar_data):
            logger.debug(f"Gear zone filtered: z={tooth_count}, r={max_radius:.2f}, "
                        f"m={module:.4f}, h={tooth_height:.2f}, b={axial_width:.2f}")
            return

        helix_angle = 0.0
        if mid_idx + 1 < len(sections):
            sec1 = sections[mid_idx]
            sec2 = sections[mid_idx + 1]
        else:
            sec1 = sections[mid_idx - 1]
            sec2 = sections[mid_idx]

        helix_angle = self._calculate_helix_angle(sec1, sec2, max_radius, tooth_count)

        gear_type = 'helical' if abs(helix_angle) > 2.0 else 'spur'

        self.gear_zones.append({
            'position_start': round(zone['start'], 4),
            'position_end': round(zone['end'], 4),
            'mid_position': round(mid_section['position'], 4),
            'tooth_count': tooth_count,
            'module': round(module, 4),
            'addendum_radius': round(max_radius, 4),
            'dedendum_radius': round(min_radius, 4),
            'tooth_height': round(max_radius - min_radius, 4),
            'section_count': len(sections),
            'polar_sample_count': len(polar_data),
            'helix_angle': helix_angle,
            'gear_type': gear_type
        })

        logger.info(f"Gear zone: z=[{zone['start']:.2f}, {zone['end']:.2f}], "
                   f"z={tooth_count}, m={module:.4f} (raw={raw_module:.4f})")

    def _find_peaks_precise(self, polar_data: List[Tuple[float, float]],
                            r_max: float) -> Tuple[int, List[float]]:
        """Precise local maxima detection"""
        if not polar_data or len(polar_data) < 7:
            return 0, []

        radii = [r for r, theta in polar_data]
        outer_radii = [r for r in radii if r > r_max * 0.85]
        r_min = min(outer_radii) if outer_radii else r_max

        if (r_max - r_min) < MIN_TOOTH_HEIGHT_DIFF:
            return 0, []

        n = len(radii)

        peaks = []
        threshold = r_max * PEAK_RADIUS_RATIO

        for i in range(PEAK_NEIGHBOR_COUNT, n - PEAK_NEIGHBOR_COUNT):
            curr = radii[i]
            is_peak = True

            for j in range(1, PEAK_NEIGHBOR_COUNT + 1):
                if curr <= radii[i - j]:
                    is_peak = False
                    break

            if is_peak:
                for j in range(1, PEAK_NEIGHBOR_COUNT + 1):
                    if curr <= radii[i + j]:
                        is_peak = False
                        break

            if is_peak and curr > threshold:
                peaks.append(i)

        if not peaks:
            return 0, []

        unique_peaks = []
        peaks.sort()

        for peak_idx in peaks:
            if not unique_peaks:
                unique_peaks.append(peak_idx)
            else:
                angle_diff = abs(polar_data[peak_idx][1] - polar_data[unique_peaks[-1]][1])
                if angle_diff > math.pi / 120:
                    unique_peaks.append(peak_idx)
                elif radii[peak_idx] > radii[unique_peaks[-1]]:
                    unique_peaks[-1] = peak_idx

        tooth_count = len(unique_peaks)
        peak_angles = [polar_data[i][1] for i in unique_peaks]

        return tooth_count, peak_angles

    def _calculate_helix_angle(self, sec1: Dict, sec2: Dict, r_max: float, z: int) -> float:
        """Compute the helix angle (from the phase difference between adjacent sections)"""
        try:
            polar1 = sec1.get('polar_data', [])
            polar2 = sec2.get('polar_data', [])

            if not polar1 or not polar2:
                return 0.0

            _, angles1 = self._find_peaks_precise(polar1, r_max)
            _, angles2 = self._find_peaks_precise(polar2, r_max)

            n1, n2 = len(angles1), len(angles2)
            if n1 == 0 or n2 == 0:
                return 0.0

            max_count = max(n1, n2)
            min_count = min(n1, n2)
            diff_ratio = (max_count - min_count) / max_count if max_count > 0 else 0

            if diff_ratio > 0.20:
                logger.debug(f"Tooth count diff too large: {n1} vs {n2}, ratio={diff_ratio:.2%}")
                return 0.0

            delta_z = abs(sec2['position'] - sec1['position'])
            if delta_z < 0.001:
                return 0.0

            pitch_radius = r_max - (2 * r_max) / (z + 2) if z > 0 else r_max * 0.8

            all_deltas = []
            base_angles = angles1 if min_count == n1 else angles2
            target_angles = angles2 if min_count == n1 else angles1

            for theta_base in base_angles:
                best_delta = float('inf')
                for theta_target in target_angles:
                    delta = theta_target - theta_base
                    while delta > math.pi:
                        delta -= 2 * math.pi
                    while delta < -math.pi:
                        delta += 2 * math.pi

                    if abs(delta) < abs(best_delta):
                        best_delta = delta

                all_deltas.append(best_delta)

            if len(all_deltas) < 3:
                avg_delta_theta = sum(all_deltas) / len(all_deltas) if all_deltas else 0.0
            else:
                mean = sum(all_deltas) / len(all_deltas)
                variance = sum((d - mean) ** 2 for d in all_deltas) / len(all_deltas)
                std = math.sqrt(variance) if variance > 0 else 0.0

                filtered_deltas = [d for d in all_deltas if abs(d - mean) <= 2 * std]

                if len(filtered_deltas) < 2:
                    avg_delta_theta = mean
                else:
                    avg_delta_theta = sum(filtered_deltas) / len(filtered_deltas)

            beta_rad = math.atan(pitch_radius * avg_delta_theta / delta_z)
            beta_deg = math.degrees(beta_rad)

            return round(beta_deg, 2)

        except Exception as e:
            logger.debug(f"Helix angle calculation failed: {e}")
            return 0.0

    def _build_result(self) -> Dict[str, Any]:
        """Build the output result"""
        if not self.gear_zones:
            return {
                'detected': False,
                'gear_count': 0,
                'gear_zones': [],
                'parameters': []
            }

        return {
            'detected': True,
            'gear_count': len(self.gear_zones),
            'gear_zones': [
                {
                    'position_start': g['position_start'],
                    'position_end': g['position_end'],
                    'mid_position': g['mid_position']
                }
                for g in self.gear_zones
            ],
            'parameters': [
                {
                    'tooth_count': g['tooth_count'],
                    'module': g['module'],
                    'addendum_radius': g['addendum_radius'],
                    'dedendum_radius': g['dedendum_radius'],
                    'tooth_height': g['tooth_height'],
                    'pressure_angle': 20.0,
                    'gear_type': g.get('gear_type', 'spur'),
                    'helix_angle': g.get('helix_angle', 0.0)
                }
                for g in self.gear_zones
            ]
        }

    def get_statistics(self) -> Dict[str, Any]:
        return {
            'gear_zone_count': len(self.gear_zones),
            'section_count': len(self.section_profiles)
        }

    def _is_valid_gear_geometry(self, tooth_count: int, max_radius: float, min_radius: float,
                                module: float, axial_width: float, polar_data: List) -> bool:
        """Comprehensive mechanical geometric consistency validation - prevents chamfer/spline false positives"""

        # Rule 1: Extreme tooth count filter (relaxed limits to protect real boundary cases)
        if tooth_count < 6 or tooth_count > 300:
            return False

        # Rule 2: 2D single-piece noise / thin-slice filter (uses the face-width-to-module ratio)
        # A real gear's thickness must withstand loads; typically b > 1.5m
        if axial_width < 1.5 * module:
            return False

        # Rule 3: Exclude short-tooth splines (uses the tooth-height-to-module ratio)
        # The theoretical h/m of a standard gear is 2.25; below 1.5 is treated as a spline
        tooth_height = max_radius - min_radius
        if (tooth_height / module) < 1.5:
            return False

        # Rule 4: Peak uniformity / sampling quality filter
        # If there are too few polar data points, this section is likely an invalid outline of fragmented faces
        if len(polar_data) < 50:
            return False

        return True
