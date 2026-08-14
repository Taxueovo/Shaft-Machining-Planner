#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Main BREP Feature Extraction Runner (Orchestrator mode - adjusted extraction order)

Integrates all feature extraction modules and outputs unified JSON
"""

import json
import os
import sys

from .base_loader import BaseBREPLoader
from .cylinder_features import CylinderFeatureExtractor
from .spline_features import SplineFeatureExtractor
from .radial_holes import RadialHoleExtractor
from .keyway_features import KeywayExtractor
from .gear_features import GearFeatureExtractor


def extract_features(brep_path: str, output_path: str = 'shaft_features.json',
                     verbose: bool = True, progress_callback=None):
    """
    Run the complete feature extraction pipeline (Orchestrator mode)

    Order adjusted: gears first, cylinders last, so that gear outer envelope surfaces are not
    misidentified as standalone cylinders

    Args:
        brep_path: Path to the BREP file
        output_path: Path to the output JSON file
        verbose: Whether to print detailed logs
        progress_callback: Optional callback (done, total), passed through to the gear Z-axis scan and called once per completed section
            (for UI progress bars). When None, behavior is identical to the legacy version.

    Returns:
        dict: Extracted feature data
    """
    print("=" * 60)
    print("BREP Feature Extraction - Modular Version")
    print("=" * 60)

    # Step 1: Load BREP file
    base_loader = BaseBREPLoader(brep_path)
    if not base_loader.load_brep():
        raise RuntimeError("Failed to load BREP file")

    # Step 2: Compute bounding box
    base_loader.compute_bounding_box()

    # Step 3: Initialize main axis (avoids recursion)
    base_loader.init_main_axis()

    # Step 4: Traverse faces (uses the default value set by init_main_axis)
    base_loader.traverse_faces()

    # Step 5: Detect main axis (updated to the correct direction after traversal)
    base_loader.detect_main_axis()

    # Step 6: [Orchestrator] Extract gears first to obtain the exclusion zones
    if verbose:
        print("\n--- Extracting Gear Features (First) ---")
    gear_extractor = GearFeatureExtractor(base_loader, progress_callback=progress_callback)
    gear_features = gear_extractor.extract()

    # Build the exclusion zone list
    exclusion_zones = []
    if gear_features.get('gear_zones'):
        exclusion_zones = gear_features['gear_zones']
        if verbose:
            print(f"[Orchestrator] Found {len(exclusion_zones)} gear zones for exclusion")

    # Step 7: Extract other features (splines, etc.)
    if verbose:
        print("\n--- Extracting Spline Features ---")
    spline_extractor = SplineFeatureExtractor(base_loader)
    spline_zone = spline_extractor.extract()

    if verbose:
        print("\n--- Extracting Radial Hole Features ---")
    radial_extractor = RadialHoleExtractor(base_loader)
    radial_features = radial_extractor.extract()

    if verbose:
        print("\n--- Extracting Keyway Features ---")
    keyway_extractor = KeywayExtractor(base_loader)
    keyway_features = keyway_extractor.extract()

    # Step 8: [Orchestrator] Extract cylindrical faces last, passing the exclusion zones to filter out gear outer envelope surfaces
    if verbose:
        print("\n--- Extracting Cylinder Features (Last, with exclusion) ---")
    cylinder_extractor = CylinderFeatureExtractor(base_loader)
    # Pass gear parameters to distinguish shaft segments from gear outer envelope surfaces
    cylinder_features = cylinder_extractor.extract(
        exclusion_zones=exclusion_zones,
        gear_parameters=gear_features.get('parameters', [])
    )

    # Step 9: Build final result
    dimensions = base_loader.get_overall_dimensions()

    result = {
        'part_name': os.path.basename(brep_path),
        'overall_dimensions': dimensions,
        'main_axis': [
            base_loader.main_axis['x'],
            base_loader.main_axis['y'],
            base_loader.main_axis['z']
        ],
        'features': {
            'outer_cylinders': cylinder_features['outer_cylinders'],
            'inner_bore': cylinder_features['inner_bore'],
            'inner_bore_summary': cylinder_features.get('inner_bore_summary', []),
            'radial_oil_holes': radial_features,
            'keyways': keyway_features,
            'spline_zone': spline_zone,
            'gear_features': gear_features
        },
        'statistics': {
            'total_faces': base_loader.total_faces,
            'cylinder_faces': base_loader.cylinder_faces,
            'plane_faces': base_loader.plane_faces,
            'spline_faces': base_loader.spline_faces,
            'outer_cylinder_count': len(cylinder_features['outer_cylinders']),
            'inner_bore_count': len(cylinder_features['inner_bore']),
            'radial_hole_count': radial_features['count'],
            'keyway_count': keyway_features['count'],
            'gear_count': gear_features['gear_count']
        }
    }

    # Save JSON
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nFeatures saved to: {output_path}")

    # Print summary
    if verbose:
        print("\n" + "=" * 60)
        print("Feature Extraction Summary")
        print("=" * 60)
        print(f"Part: {result['part_name']}")
        print(f"Overall Length: {result['overall_dimensions']['length']:.4f} mm")
        print(f"Max Diameter: {result['overall_dimensions']['max_diameter']:.4f} mm")
        print(f"Main Axis: {result['main_axis']}")
        print(f"\nOuter Cylinders: {len(result['features']['outer_cylinders'])}")
        for cyl in result['features']['outer_cylinders']:
            type_str = cyl.get('type', 'Cylinder')
            print(f"  - {type_str}: r={cyl['radius']:.4f} mm, len={cyl['length']:.4f}")
        print(f"\nInner Bores: {len(result['features']['inner_bore'])}")
        for bore in result['features']['inner_bore'][:5]:
            print(f"  - r={bore['radius']:.4f} mm")
        if len(result['features']['inner_bore']) > 5:
            print(f"  ... and {len(result['features']['inner_bore']) - 5} more")
        print(f"\nRadial Oil Holes: {result['features']['radial_oil_holes']['count']}")
        if radial_features.get('holes_per_position'):
            print("  Holes per position:")
            for pos, count in radial_features['holes_per_position'].items():
                print(f"    Z={pos}: {count} hole(s)")

        # Print keyway details
        print(f"\nKeyways: {result['features']['keyways']['count']}")
        if result['features']['keyways'].get('keyways'):
            for kw in result['features']['keyways']['keyways'][:5]:
                print(f"  - Type: {kw['type']}, Position: {kw['position_axial']:.1f} mm, Width: {kw['width']:.2f} mm")
            if len(result['features']['keyways']['keyways']) > 5:
                print(f"  ... and more")

        # Print spline zone details
        spline_zone = result['features']['spline_zone']
        print(f"\nSpline Zone Detected: {spline_zone['detected']}")
        if spline_zone['detected'] and spline_zone.get('parameters'):
            params = spline_zone['parameters']
            print(f"  - Spline Type: {params.get('spline_type', 'unknown')}")
            print(f"  - Tooth Count: {params.get('tooth_count', 0)}")
            print(f"  - Major Diameter (D): {params.get('major_diameter', 0):.4f} mm")
            print(f"  - Minor Diameter (d): {params.get('minor_diameter', 0):.4f} mm")
            print(f"  - Module (m): {params.get('module', 'N/A')}")
            print(f"  - Pressure Angle: {params.get('pressure_angle', 'N/A')} deg")
            print(f"  - Key Width (B): {params.get('key_width_B', 'N/A')} mm")

        # Print gear zone details
        gear_features = result['features']['gear_features']
        print(f"\nGear Zone Detected: {gear_features['detected']}")
        if gear_features['detected'] and gear_features.get('parameters'):
            for i, gear in enumerate(gear_features['parameters']):
                print(f"  Gear #{i+1}:")
                print(f"    - Tooth Count: {gear.get('tooth_count', 0)}")
                print(f"    - Module (m): {gear.get('module', 0):.4f} mm")
                print(f"    - Pressure Angle: {gear.get('pressure_angle', 0):.1f} deg")
                print(f"    - Addendum Radius: {gear.get('addendum_radius', 0):.4f} mm")
                print(f"    - Dedendum Radius: {gear.get('dedendum_radius', 0):.4f} mm")
                print(f"    - Pitch Radius: {gear.get('pitch_radius', 0):.4f} mm")
                print(f"    - Gear Type: {gear.get('gear_type', 'spur')}")
                helix_deg = gear.get('helix_angle_deg', 0)
                if helix_deg > 0:
                    print(f"    - Helix Angle: {helix_deg:.2f} deg")

        # Print spline zone details (should be None for gear-only parts)
        if result['features']['spline_zone'].get('detected'):
            print(f"\n  [WARNING] Spline zone detected - check if this is actually a gear")
        print("=" * 60)

    return result


def main():
    """
    Main function
    """
    import argparse

    parser = argparse.ArgumentParser(
        description='BREP Feature Extractor - Modular Version'
    )
    parser.add_argument(
        'brep_file',
        nargs='?',
        default='3D/shaft_1_fromSTEP.brep',
        help='Path to the BREP file (default: 3D/shaft_1_fromSTEP.brep)'
    )
    parser.add_argument(
        '-o', '--output',
        default='shaft_features.json',
        help='Path to the output JSON file (default: shaft_features.json)'
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Quiet mode; do not print detailed logs'
    )

    args = parser.parse_args()

    try:
        extract_features(args.brep_file, args.output, verbose=not args.quiet)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
