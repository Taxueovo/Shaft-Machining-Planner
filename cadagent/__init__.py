"""
ShaftPlanner - 3D Part Knowledge Management System
================================================

A multimodal 3D CAD model analysis and AI conversation system based on
a multi-agent architecture.

Top-level subpackages:
    - config    : Application settings, LLM configuration, prompt templates
    - core      : Core LLM client wrappers and shared capabilities
    - agents    : Business agents (e.g. CAE expert)
    - services  : Business services (rendering, feature extraction, BREP conversion)
    - Scripts   : Geometry / BREP feature extraction algorithms
    - ui        : FastAPI backend + Chainlit frontend
"""

__version__ = "1.0.0"
__app_name__ = "ShaftPlanner"
