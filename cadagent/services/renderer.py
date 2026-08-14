"""
================================================

PythonOCC Offscreen Renderer Service

3D model multi-view rendering service
Offscreen rendering with OpenCASCADE

================================================
"""

import os
import tempfile
import logging
import time
import glob
from typing import Dict, Tuple

# ==============================================================================
# PythonOCC Imports
# ==============================================================================

try:
    # Core geometry reading
    from OCC.Core.STEPControl import STEPControl_Reader
    from OCC.Core.BRepTools import breptools
    from OCC.Core.BRep import BRep_Builder
    from OCC.Core.TopoDS import TopoDS_Shape

    # Offscreen rendering
    from OCC.Display.OCCViewer import OffscreenRenderer
    from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB

    OCC_AVAILABLE = True
except ImportError as e:
    OCC_AVAILABLE = False
    logging.warning(f"PythonOCC not available: {e}")

logger = logging.getLogger(__name__)


# ==============================================================================
# Model Reading Functions
# ==============================================================================

def read_step_file(file_path: str) -> TopoDS_Shape:
    """Reads a STEP file and returns the TopoDS_Shape."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    step_reader = STEPControl_Reader()
    status = step_reader.ReadFile(file_path)

    if status != 1:
        raise ValueError(f"Failed to read STEP file: {file_path}")

    step_reader.TransferRoot()
    shape = step_reader.Shape()

    if shape.IsNull():
        raise ValueError(f"STEP file produced null shape: {file_path}")

    logger.info(f"Successfully loaded STEP file: {file_path}")
    return shape


def read_brep_file(file_path: str) -> TopoDS_Shape:
    """Reads a BREP file and returns the TopoDS_Shape."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    builder = BRep_Builder()
    shape = TopoDS_Shape()
    success = breptools.Read(shape, file_path, builder)

    if not success:
        raise ValueError(f"Failed to read BREP file: {file_path}")

    if shape.IsNull():
        raise ValueError(f"BREP file produced null shape: {file_path}")

    logger.info(f"Successfully loaded BREP file: {file_path}")
    return shape


def read_model_file(file_path: str) -> TopoDS_Shape:
    """Reads a CAD model file based on extension (.stp, .step, .brep)."""
    file_path_lower = file_path.lower()

    if file_path_lower.endswith(('.stp', '.step')):
        return read_step_file(file_path)
    elif file_path_lower.endswith('.brep'):
        return read_brep_file(file_path)
    else:
        raise ValueError(f"Unsupported file format. Supported: .stp, .step, .brep")


# ==============================================================================
# View Definitions
# ==============================================================================

def get_view_definitions() -> Dict[str, Dict]:
    """Returns predefined camera view configurations."""
    return {
        'front': {
            'name': 'Front View',
            'view_method': 'View_Front'
        },
        'top': {
            'name': 'Top View',
            'view_method': 'View_Top'
        },
        'right': {
            'name': 'Right View',
            'view_method': 'View_Right'
        },
        'isometric': {
            'name': 'Isometric View',
            'view_method': 'View_Iso'
        }
    }


# ==============================================================================
# Multi-View Image Generation
# ==============================================================================

def generate_multi_view_images(
    file_path: str,
    image_size: Tuple[int, int] = (1200, 900)
) -> Dict[str, Dict]:
    """
    Generates multi-view renders of a 3D model using OffscreenRenderer.

    Produces engineering-style renders with:
    - Professional lighting setup
    - Clean neutral background
    - Proper material shading

    Args:
        file_path: Path to the CAD model file (.stp, .step, .brep)
        image_size: Tuple of (width, height) for output images

    Returns:
        Dict with view name as key and dict containing:
            - 'image': bytes of the rendered image
            - 'file_path': path to saved image file
            - 'name': display name of the view
            - 'success': bool indicating if render succeeded
    """
    results = {}
    temp_dir = None

    try:
        if not OCC_AVAILABLE:
            logger.error("PythonOCC is not available")
            for view_name in ['front', 'top', 'right', 'isometric']:
                results[view_name] = {
                    'image': None, 'file_path': None, 'name': view_name.title(),
                    'success': False, 'error': 'PythonOCC not available'
                }
            return results

        # Read the model
        logger.info(f"Loading model: {file_path}")
        shape = read_model_file(file_path)

        # Get view definitions
        views = get_view_definitions()

        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix="3d_view_")

        # Initialize offscreen renderer with larger resolution
        width, height = image_size
        renderer = OffscreenRenderer(screen_size=(width, height))

        # Set professional engineering-style background
        # Gradient from light gray (#E8E8E8) to white (#FFFFFF)
        renderer.set_bg_gradient_color([232, 232, 232], [255, 255, 255])

        # Display the shape with metallic steel blue color for professional look
        # Steel blue color: (0.4, 0.5, 0.65) - professional CAD appearance
        renderer.DisplayShape(
            shape,
            update=True,
            color=Quantity_Color(0.4, 0.5, 0.65, Quantity_TOC_RGB)
        )
        logger.info("Shape displayed with professional steel blue color")

        # Clean up initial auto-dump files
        for f in glob.glob("capture-*.jpeg") + glob.glob("capture-*.jpg"):
            try:
                os.remove(f)
            except:
                pass

        logger.info("Offscreen renderer initialized, generating views...")

        # Generate each view
        for view_name, view_config in views.items():
            logger.info(f"Rendering {view_config['name']}...")

            try:
                # Switch view direction
                view_method = getattr(renderer, view_config['view_method'])
                view_method()

                # Fit to screen and adjust view parameters
                renderer.FitAll()

                # Define output file path
                file_name = f"{view_name}_view.jpeg"
                output_path = os.path.join(temp_dir, file_name)

                # Export using the correct method: ExportToImage
                renderer.ExportToImage(output_path)

                # Verify file was created
                if os.path.exists(output_path):
                    # Read image data
                    with open(output_path, 'rb') as f:
                        image_data = f.read()

                    results[view_name] = {
                        'image': image_data,
                        'file_path': output_path,
                        'name': view_config['name'],
                        'success': True,
                        'error': None
                    }
                    logger.info(f"{view_config['name']} rendered successfully: {output_path}")
                else:
                    raise RuntimeError(f"ExportToImage did not create file: {output_path}")

            except Exception as e:
                logger.error(f"Failed to render {view_name}: {e}")
                results[view_name] = {
                    'image': None, 'file_path': None, 'name': view_config['name'],
                    'success': False, 'error': str(e)
                }

        return results

    except Exception as e:
        logger.error(f"Multi-view generation failed: {e}")
        for view_name in ['front', 'top', 'right', 'isometric']:
            if view_name not in results:
                results[view_name] = {
                    'image': None, 'file_path': None, 'name': view_name.title(),
                    'success': False, 'error': str(e)
                }
        return results
