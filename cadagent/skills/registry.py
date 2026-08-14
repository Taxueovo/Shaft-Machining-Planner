"""
================================================

Skill registry data models

================================================
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Optional


class SkillType(str, Enum):
    """Skill type enum"""
    LOCAL = "local"
    MCP_HTTP = "mcp_http"
    DOWNLOADABLE = "downloadable"


class LoadStrategy(str, Enum):
    """Load strategy enum"""
    PRELOAD = "preload"      # preloaded at startup
    LAZY = "lazy"            # loaded on first access
    ON_DEMAND = "on_demand"  # loaded on explicit request


@dataclass
class SkillDefinition:
    """
    Skill definition

    Represents the configuration of one skill in the registry
    """
    name: str
    type: SkillType
    path: Optional[str] = None
    url: Optional[str] = None
    load_strategy: LoadStrategy = LoadStrategy.LAZY
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Type conversion"""
        if isinstance(self.type, str):
            self.type = SkillType(self.type)
        if isinstance(self.load_strategy, str):
            self.load_strategy = LoadStrategy(self.load_strategy)

    @property
    def is_remote(self) -> bool:
        """Whether the skill is remote"""
        return self.type in (SkillType.MCP_HTTP, SkillType.DOWNLOADABLE)

    @property
    def location(self) -> str:
        """Get the skill location"""
        return self.url or self.path or ""


@dataclass
class LoadedSkill:
    """
    Loaded skill

    Represents a skill instance loaded at runtime
    """
    definition: SkillDefinition
    module: Optional[Any] = None
    instance: Optional[Any] = None
    loaded: bool = False
    error: Optional[str] = None

    @property
    def name(self) -> str:
        """Skill name"""
        return self.definition.name

    @property
    def tools(self) -> list:
        """Get the tool list provided by the skill"""
        if not self.loaded or self.instance is None:
            return []
        return getattr(self.instance, 'tools', [])
