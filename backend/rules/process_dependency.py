"""Process dependency handling module."""

from __future__ import annotations

from typing import List, Dict, Any

# Define simple operation dependencies: follow-up operations automatically inserted
# after an operation completes
PROCESS_DEPENDENCY: dict[str, List[str]] = {
    # These keys serve as backup/hints; real dependencies are resolved by name matching
    "Gear Hobbing": ["Deburring"],
    "Spline Hobbing": ["Brush Deburring"],
    "Heat Treatment": ["Repair Center Holes"],
    "External Grinding": ["Final Inspection"],
}


def _resolve_dependency_for_name(name: str) -> str | None:
    """Resolve the dependent operation name to insert for a given operation name (simplified implementation)."""
    if not name:
        return None
    # Map directly against the known dependency table
    if name in PROCESS_DEPENDENCY:
        procs = PROCESS_DEPENDENCY.get(name) or []
        return procs[0] if procs else None
    lower = name.lower()
    if "gear hob" in lower:
        return "Deburring"
    if "hob spline" in lower:
        return "Brush Deburring"
    if "heat treatment" in lower:
        return "Repair Center Holes"
    if "external grinding" in lower:
        return "Final Inspection"
    return None


def apply_dependencies(route: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Auto-insert dependency operations into the route based on process dependencies.

    Implementation notes:
    - Matches common dependencies based on the output operation name;
    - When a dependency matches, a new operation is inserted after the original one,
      using the dependency's name, the same stage, and a description stating the auto-injection reason.
    - Compatibility design: if no dependency matches or the operation is undefined, the route is left unchanged.
    """
    if not route:
        return route
    new_route: List[Dict[str, Any]] = []
    for op in route:
        new_route.append(op)
        name = op.get("name") or ""
        dep_name = _resolve_dependency_for_name(str(name))
        if not dep_name:
            continue
        # Avoid inserting a duplicate operation with the same name
        if new_route and new_route[-1].get("name") == dep_name:
            continue
        new_route.append({
            "operation_no": 0,
            "name": dep_name,
            "stage": op.get("stage"),
            "description": f"Auto-inserted dependency: {dep_name} after {op.get('name')}",
            "process_category": None,
            "feature_id": op.get("feature_id"),
            "conditional": False,
        })
    return new_route
