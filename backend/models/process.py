"""Process Pydantic models."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class ProcessStage(str, Enum):
    """Process stage enumeration - Single source for all stage values."""
    blank = "blank"
    datum = "datum"
    rough = "rough"
    semi_finish = "semi_finish"
    finish_before_heat = "finish_before_heat"
    feature_before_heat = "feature_before_heat"
    pre_heat_treatment = "pre_heat_treatment"
    heat_treatment = "heat_treatment"
    datum_recovery = "datum_recovery"
    finish = "finish"
    feature_after_heat = "feature_after_heat"
    precision_finish = "precision_finish"
    precision_feature = "precision_feature"
    feature_before_inspection = "feature_before_inspection"
    deburr = "deburr"
    surface_treatment = "surface_treatment"
    inspection = "inspection"
    packaging = "packaging"


# Stage dependency rules: (pre-stage, post-stage)
STAGE_DEPENDENCY_RULES: list[tuple[str, str]] = [
    ("blank", "datum"),
    ("datum", "rough"),
    ("rough", "semi_finish"),
    ("semi_finish", "feature_before_heat"),
    ("semi_finish", "pre_heat_treatment"),
    # Carburized/quenched parts: finish turning must be completed before heat treatment (soft state);
    # after quenching the hardened surface can only be ground
    ("semi_finish", "finish_before_heat"),
    ("finish_before_heat", "feature_before_heat"),
    ("finish_before_heat", "pre_heat_treatment"),
    ("finish_before_heat", "heat_treatment"),
    ("pre_heat_treatment", "heat_treatment"),
    ("heat_treatment", "datum_recovery"),
    ("datum_recovery", "finish"),
    ("semi_finish", "finish"),
    ("finish", "precision_finish"),
    # Finish grinding and final feature machining, both located off the final OD datum,
    # must occur after finish turning; when OD finish grinding is present, complete it
    # before machining relative features such as keyways, grooves and holes.
    ("precision_finish", "feature_after_heat"),
    ("precision_finish", "precision_feature"),
    ("precision_finish", "feature_before_inspection"),
    ("feature_after_heat", "feature_before_inspection"),
    ("precision_feature", "feature_before_inspection"),
    ("finish", "feature_after_heat"),
    ("finish", "precision_feature"),
    ("finish", "feature_before_inspection"),
    # Deburring/chamfering: after all finishing and final feature machining, before surface treatment and final inspection
    ("feature_before_inspection", "deburr"),
    ("deburr", "surface_treatment"),
    ("deburr", "inspection"),
    ("surface_treatment", "inspection"),
    ("finish", "inspection"),
    ("inspection", "packaging"),
]

# Mandatory operation names (LLM cannot delete)
MANDATORY_OPERATION_NAMES = {
    "Blanking", "Face Turning", "Center Drilling",
    "Rough Turning", "Semi-finish Turning", "Finish Turning", "Final Inspection",
}


class ProcessOperation(BaseModel):
    """Single operation model."""
    operation_no: int = Field(ge=1)
    name: str = Field(min_length=1)
    stage: ProcessStage
    description: str = ""
    process_category: Optional[str] = None
    feature_id: Optional[str] = None
    conditional: bool = False


class RouteCustomizeRequest(BaseModel):
    """User-customized process route request (route adjusted by the user before the process card is generated).

    Each operation keeps its original operation_no as a stable resource key - after reordering,
    machine/tool recommendations still follow the operation; new operations receive a new unique
    operation_no from the frontend (no resource match).
    """

    operations: list[ProcessOperation] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_operation_no(self) -> "RouteCustomizeRequest":
        numbers = [op.operation_no for op in self.operations]
        if len(numbers) != len(set(numbers)):
            raise ValueError("operation_no must be unique.")
        return self


class ResourceStatus(str, Enum):
    """Resource validation status."""
    satisfied = "satisfied"
    not_satisfied = "not_satisfied"
    not_covered = "not_covered"
    not_applicable = "not_applicable"
    unknown = "unknown"


class ValidationIssue(BaseModel):
    """Validation issue structured output."""
    error_code: str
    object_id: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    message: str
    severity: str = "error"


class FeatureProcessStrategy(str, Enum):
    """Feature processing strategy."""
    single_stage_before_heat = "single_stage_before_heat"
    single_stage_after_finish = "single_stage_after_finish"
    rough_before_heat_finish_after_heat = "rough_before_heat_finish_after_heat"


class LLMRouteOutput(BaseModel):
    """LLM process route output contract."""
    process_route: list[ProcessOperation]


class ResourceRecommendation(BaseModel):
    """LLM resource recommendation output contract."""
    recommended_machine: Optional[str] = None
    machine_candidate_ids: list[str] = []
    process_consolidation_suggestions: list[str] = []
    risk_operations: list[dict[str, Any]] = []
    overall_score: int = Field(default=50, ge=0, le=100)
    summary: str = ""
