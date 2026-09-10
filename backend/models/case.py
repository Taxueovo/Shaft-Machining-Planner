# 定义案例、工序、列表摘要和筛选请求的数据结构。
"""Case data models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# 定义案例工序的编号、名称、设备及工艺说明等字段。
class ProcessStep(BaseModel):
    """Process step."""

    step_no: int = Field(description="Step sequence number")
    name: str = Field(description="Operation name")
    stage: str = Field(description="Process stage")
    description: str = Field(description="Operation description")
    machine: Optional[str] = Field(default=None, description="Machine tool")
    tool: Optional[str] = Field(default=None, description="Cutting tool")


# 定义案例列表摘要，避免每次列表查询传输完整工艺路线。
class CaseMetadata(BaseModel):
    """Case metadata."""

    case_id: str = Field(description="Unique case identifier")
    part_name: str = Field(description="Part name")
    taxonomy_id: str = Field(description="Taxonomy node ID")
    industry: str = Field(description="Industry tag")
    application: Optional[str] = Field(default=None, description="Application scenario")
    material: str = Field(description="Material grade")
    heat_treatment: Optional[str] = Field(default=None, description="Heat treatment")
    tolerance: Optional[str] = Field(default=None, description="Tolerance grade (e.g. IT6)")
    surface_roughness: Optional[str] = Field(
        default=None, description="Surface roughness (e.g. Ra0.8)"
    )
    length_mm: Optional[float] = Field(default=None, description="Part length in mm")
    diameter_mm: Optional[float] = Field(default=None, description="Part diameter in mm")
    main_features: list[str] = Field(default_factory=list, description="Main features")
    description: Optional[str] = Field(default=None, description="Case description")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# 在案例摘要基础上保存完整工艺路线与补充信息。
class Case(CaseMetadata):
    """Full case (including process route)."""

    process_plan: list[ProcessStep] = Field(default_factory=list, description="Process plan")
    segments: list[dict] = Field(default_factory=list, description="Shaft segment definitions")
    features: list[dict] = Field(default_factory=list, description="Feature definitions")
    notes: Optional[str] = Field(default=None, description="Additional notes")

    # 从完整案例提取列表摘要，保持元数据字段一致。
    def to_metadata(self) -> CaseMetadata:
        """Convert to metadata only."""
        return CaseMetadata(
            case_id=self.case_id,
            part_name=self.part_name,
            taxonomy_id=self.taxonomy_id,
            industry=self.industry,
            application=self.application,
            material=self.material,
            heat_treatment=self.heat_treatment,
            tolerance=self.tolerance,
            surface_roughness=self.surface_roughness,
            length_mm=self.length_mm,
            diameter_mm=self.diameter_mm,
            main_features=self.main_features,
            description=self.description,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


# 定义案例筛选和分页请求的字段约束。
class CaseSearchRequest(BaseModel):
    """Case search request."""

    keyword: Optional[str] = Field(default=None, description="Search keyword")
    taxonomy_id: Optional[str] = Field(default=None, description="Filter by taxonomy node")
    industry: Optional[str] = Field(default=None, description="Filter by industry")
    material: Optional[str] = Field(default=None, description="Filter by material")
    tolerance: Optional[str] = Field(default=None, description="Filter by tolerance")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
