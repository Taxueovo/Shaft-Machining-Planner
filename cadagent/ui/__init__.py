"""
================================================

UI Module - 3D Model Feature Analyzer

Provides visualization and analysis interfaces for 3D CAD models.
Uses lazy imports so the whole package remains importable when
optional dependencies are missing.
================================================
"""


def __getattr__(name):
    """
    Lazy import - imports the submodule only when the attribute is accessed.
    """
    if name == "generate_multi_view_images":
        from cadagent.services.renderer import generate_multi_view_images
        return generate_multi_view_images
    if name == "read_model_file":
        from cadagent.services.renderer import read_model_file
        return read_model_file
    if name == "convert_stp_to_brep_occ":
        from cadagent.services.convert_brep import convert_stp_to_brep_occ
        return convert_stp_to_brep_occ
    if name == "extract_features":
        from cadagent.services.extractor import extract_features
        return extract_features

    raise AttributeError(f"module 'ui' has no attribute '{name}'")


__all__ = [
    "generate_multi_view_images",
    "read_model_file",
    "convert_stp_to_brep_occ",
    "extract_features",
]
