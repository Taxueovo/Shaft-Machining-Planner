"""
================================================

Business services module - ShaftPlanner

Contains:
- 3D model rendering
- Feature extraction
- BREP format conversion

Note: uses lazy imports so the package stays usable when dependencies are missing
================================================
"""

import logging

logger = logging.getLogger(__name__)


def __getattr__(name):
    """
    Lazy import - only imports the submodule when the attribute is accessed

    This avoids the whole package failing to import when dependencies such as OCC are unavailable
    """
    if name == "generate_multi_view_images":
        from cadagent.services.renderer import generate_multi_view_images
        return generate_multi_view_images
    elif name == "FeatureExtractor":
        from cadagent.services.extractor import FeatureExtractor
        return FeatureExtractor
    elif name == "extract_features":
        from cadagent.services.extractor import extract_features
        return extract_features
    elif name == "convert_stp_to_brep_occ":
        from cadagent.services.convert_brep import convert_stp_to_brep_occ
        return convert_stp_to_brep_occ
    raise AttributeError(f"module 'services' has no attribute '{name}'")


__all__ = [
    "generate_multi_view_images",
    "FeatureExtractor",
    "extract_features",
    "convert_stp_to_brep_occ",
]
