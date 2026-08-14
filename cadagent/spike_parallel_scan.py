#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Step 1 spike: verify that the gear Z-axis section scan "parallel" output matches
the "serial" one, and time both.

The same part is run twice through GearFeatureExtractor:
  - max_workers=1     -> serial (same behaviour as the legacy version)
  - max_workers=N     -> parallel (multi-process, ProcessPoolExecutor)

Compares whether the section_profiles and gear_zones / parameters values match,
then prints the elapsed time for each run and the speedup.

Usage (py310 conda environment):
    cd ShaftPlanner
    python cadagent/spike_parallel_scan.py <path.step|path.brep> [--workers N]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAD = ROOT / "cadagent"
sys.path.insert(0, str(CAD))
sys.path.insert(0, str(CAD / "Scripts"))

from Scripts.base_loader import BaseBREPLoader  # noqa: E402
from Scripts.gear_features import GearFeatureExtractor  # noqa: E402
from services.convert_brep import convert_stp_to_brep_occ  # noqa: E402


def _prepare_loader(path: Path) -> BaseBREPLoader:
    """STEP -> BREP -> full load + main-axis detection (same as main_extractor).

    The BREP produced by the conversion is written to the system temp directory,
    so the source file directory is not polluted.
    """
    import tempfile
    if path.suffix.lower() in (".stp", ".step"):
        brep = Path(tempfile.gettempdir()) / (path.stem + "_spike.brep")
        if not convert_stp_to_brep_occ(str(path), str(brep)):
            raise RuntimeError(f"STEP -> BREP conversion failed: {path}")
        path = brep
    if not path.exists():
        raise RuntimeError(f"BREP not found: {path}")

    loader = BaseBREPLoader(str(path))
    if not loader.load_brep():
        raise RuntimeError(f"Failed to load BREP: {path}")
    loader.compute_bounding_box()
    loader.init_main_axis()
    loader.traverse_faces()
    loader.detect_main_axis()
    return loader


def _run(loader, max_workers: int):
    extractor = GearFeatureExtractor(loader, max_workers=max_workers)
    t0 = time.perf_counter()
    result = extractor.extract()
    elapsed = time.perf_counter() - t0
    return extractor, result, elapsed


def _assert_close(a: float, b: float, tol: float, what: str):
    if abs(a - b) > tol:
        raise AssertionError(f"{what}: {a} vs {b} (diff {abs(a - b):.3g} > tol {tol})")


def _compare(serial_ex, parallel_ex, serial_res, parallel_res):
    """Assert that the serial and parallel results are identical."""
    sp = serial_ex.section_profiles
    pp = parallel_ex.section_profiles
    if len(sp) != len(pp):
        raise AssertionError(f"section count: serial={len(sp)} parallel={len(pp)}")
    for a, b in zip(sp, pp):
        _assert_close(a["position"], b["position"], 1e-9, "profile position")
        _assert_close(a["max_radius"], b["max_radius"], 1e-6, "profile max_radius")
        _assert_close(a["min_radius"], b["min_radius"], 1e-6, "profile min_radius")
        _assert_close(a["radius_diff"], b["radius_diff"], 1e-6, "profile radius_diff")
        if a["point_count"] != b["point_count"]:
            raise AssertionError(f"point_count: {a['point_count']} vs {b['point_count']}")
    print(f"[OK] section_profiles identical: {len(sp)} sections, "
          f"max radius_diff delta < 1e-6")

    if serial_res["detected"] != parallel_res["detected"] or serial_res["gear_count"] != parallel_res["gear_count"]:
        raise AssertionError(
            f"gear detection mismatch: {serial_res['detected']}/{serial_res['gear_count']} "
            f"vs {parallel_res['detected']}/{parallel_res['gear_count']}"
        )
    for zs, zp in zip(serial_res["gear_zones"], parallel_res["gear_zones"]):
        for k in ("position_start", "position_end", "mid_position"):
            _assert_close(zs[k], zp[k], 1e-3, f"gear zone {k}")
    for ps, pp_ in zip(serial_res["parameters"], parallel_res["parameters"]):
        if ps["tooth_count"] != pp_["tooth_count"]:
            raise AssertionError(f"tooth_count: {ps['tooth_count']} vs {pp_['tooth_count']}")
        if ps["gear_type"] != pp_["gear_type"]:
            raise AssertionError(f"gear_type: {ps['gear_type']} vs {pp_['gear_type']}")
        for k in ("module", "addendum_radius", "dedendum_radius",
                  "tooth_height", "pressure_angle", "helix_angle"):
            _assert_close(ps[k], pp_[k], 1e-3, f"gear param {k}")
    print(f"[OK] gear_zones/parameters identical: {serial_res['gear_count']} zone(s)")
    return True


def main() -> int:
    # The Windows console/redirected streams default to cp1252, which cannot
    # print all unicode characters; force UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Parallel Z-axis scan spike: serial vs parallel consistency + timing")
    parser.add_argument("part", help="STEP/STP or BREP file path")
    parser.add_argument("--workers", type=int, default=0,
                        help="number of parallel workers (default: automatic = CPU count - 1)")
    args = parser.parse_args()

    part = Path(args.part)
    workers = args.workers or max(1, (__import__("os").cpu_count() or 1) - 1)

    loader = _prepare_loader(part)

    # Serial
    ser_ex, ser_res, t_serial = _run(loader, max_workers=1)
    print(f"\n[serial]  elapsed = {t_serial:.2f}s")

    # Parallel
    par_ex, par_res, t_parallel = _run(loader, max_workers=workers)
    print(f"[parallel workers={workers}] elapsed = {t_parallel:.2f}s")

    # Consistency
    _compare(ser_ex, par_ex, ser_res, par_res)

    speedup = t_serial / t_parallel if t_parallel > 0 else float("inf")
    print(f"\n=== Results identical, speedup {speedup:.2f}x "
          f"({t_serial:.2f}s -> {t_parallel:.2f}s) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
