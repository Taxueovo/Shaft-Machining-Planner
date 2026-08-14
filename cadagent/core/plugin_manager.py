"""
================================================

Plugin Manager

Dynamically loads and manages skills/plugins for ShaftPlanner system.

Core Features:
- Read and parse registry.yaml
- Dynamic loading of local skill modules (importlib)
- On-demand loading simulation for third-party modules
- Support for preload/lazy/on_demand loading strategies
- MCP standardized tool wrapping

================================================
"""

import importlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

from cadagent.skills.registry import (
    SkillDefinition,
    LoadedSkill,
    SkillType,
    LoadStrategy,
)
from cadagent.core.mcp_client import (
    MCPTool,
    MCPToolParameter,
    MCPResponse,
    LocalMCPWrapper,
    create_mcp_tool_from_function,
    convert_tools_to_openai_format,
)


logger = logging.getLogger(__name__)


# ==============================================================================
# Custom Exceptions
# ==============================================================================

class PluginError(Exception):
    """Base exception for plugin system"""
    pass


class RegistryError(PluginError):
    """Registry error - YAML parsing or loading failed"""
    pass


class SkillNotFoundError(PluginError):
    """Skill not found"""
    pass


class SkillLoadError(PluginError):
    """Skill loading failed"""
    pass


# ==============================================================================
# PluginManager Class
# ==============================================================================

class PluginManager:
    """
    Dynamic Plugin/Skill Manager
    
    Loads skill configurations from registry.yaml and
    dynamically loads modules based on loading strategy.
    
    Usage:
        manager = PluginManager()
        manager.initialize()  # Load all preload skills
        
        # On-demand loading
        skill = manager.load_skill("feature_extractor")
        
        # Get all active tools
        tools = manager.get_active_tools()
    """
    
    def __init__(self, registry_path: Optional[str] = None):
        """
        Initialize plugin manager
        
        Args:
            registry_path: Registry file path, defaults to skills/registry.yaml
        """
        self._registry_path = registry_path
        self._skills: Dict[str, SkillDefinition] = {}
        self._loaded_skills: Dict[str, LoadedSkill] = {}
        self._initialized = False
        self._mcp_tools_cache: List[MCPTool] = []
    
    # --------------------------------------------------------------------------
    # Initialization
    # --------------------------------------------------------------------------
    
    def initialize(self) -> None:
        """
        Initialize manager, load registry and preload skills
        """
        if self._initialized:
            logger.warning("PluginManager already initialized")
            return
        
        self._load_registry()
        self._preload_skills()
        
        self._initialized = True
        logger.info(
            f"PluginManager initialized with {len(self._skills)} skills, "
            f"{len(self._loaded_skills)} preloaded"
        )
    
    def _get_registry_path(self) -> Path:
        """Get registry file path"""
        if self._registry_path:
            return Path(self._registry_path)
        
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent
        return project_root / "skills" / "registry.yaml"
    
    def _load_registry(self) -> None:
        """Load and parse registry.yaml"""
        registry_path = self._get_registry_path()
        
        if not registry_path.exists():
            raise RegistryError(f"Registry file not found: {registry_path}")
        
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise RegistryError(f"Failed to parse registry YAML: {e}")
        except Exception as e:
            raise RegistryError(f"Failed to read registry file: {e}")
        
        if not data or 'skills' not in data:
            raise RegistryError("Invalid registry format: 'skills' key not found")
        
        for skill_data in data['skills']:
            try:
                skill_def = self._parse_skill_definition(skill_data)
                self._skills[skill_def.name] = skill_def
            except Exception as e:
                logger.warning(f"Failed to parse skill definition: {e}")
                continue
        
        logger.debug(f"Loaded {len(self._skills)} skill definitions from registry")
    
    def _parse_skill_definition(self, data: Dict[str, Any]) -> SkillDefinition:
        """Parse skill definition data"""
        required_fields = ['name', 'type']
        for field in required_fields:
            if field not in data:
                raise RegistryError(f"Missing required field: {field}")
        
        return SkillDefinition(
            name=data['name'],
            type=data['type'],
            path=data.get('path'),
            url=data.get('url'),
            load_strategy=data.get('load_strategy', 'lazy'),
            description=data.get('description', ''),
            metadata=data.get('metadata', {}),
        )
    
    def _preload_skills(self) -> None:
        """Preload all preload strategy skills"""
        for name, skill_def in self._skills.items():
            if skill_def.load_strategy == LoadStrategy.PRELOAD:
                try:
                    self._load_skill_instance(skill_def)
                except Exception as e:
                    logger.warning(f"Failed to preload skill '{name}': {e}")
    
    # --------------------------------------------------------------------------
    # Skill Loading
    # --------------------------------------------------------------------------
    
    def load_skill(self, skill_name: str) -> Optional[LoadedSkill]:
        """
        Dynamically load specified skill
        
        Args:
            skill_name: Skill name
            
        Returns:
            LoadedSkill: Loaded skill instance
            
        Raises:
            SkillNotFoundError: Skill not in registry
        """
        if skill_name not in self._skills:
            raise SkillNotFoundError(f"Skill '{skill_name}' not found in registry")
        
        if skill_name in self._loaded_skills:
            loaded_skill = self._loaded_skills[skill_name]
            if loaded_skill.loaded:
                logger.debug(f"Skill '{skill_name}' already loaded")
                return loaded_skill
        
        skill_def = self._skills[skill_name]
        return self._load_skill_instance(skill_def)
    
    def _load_skill_instance(self, skill_def: SkillDefinition) -> LoadedSkill:
        """Load skill instance"""
        loaded_skill = LoadedSkill(definition=skill_def)
        
        try:
            if skill_def.type == SkillType.LOCAL:
                self._load_local_skill(skill_def, loaded_skill)
            elif skill_def.type == SkillType.MCP_HTTP:
                self._load_mcp_http_skill(skill_def, loaded_skill)
            elif skill_def.type == SkillType.DOWNLOADABLE:
                self._load_downloadable_skill(skill_def, loaded_skill)
            else:
                raise SkillLoadError(f"Unknown skill type: {skill_def.type}")
            
            loaded_skill.loaded = True
            # Clear MCP tools cache when new skill is loaded
            self._mcp_tools_cache = []
            logger.info(f"Successfully loaded skill: {skill_def.name}")
            
        except Exception as e:
            loaded_skill.error = str(e)
            logger.error(f"Failed to load skill '{skill_def.name}': {e}")
        
        self._loaded_skills[skill_def.name] = loaded_skill
        return loaded_skill
    
    def _load_local_skill(
        self,
        skill_def: SkillDefinition,
        loaded_skill: LoadedSkill
    ) -> None:
        """Load local skill module"""
        if not skill_def.path:
            raise SkillLoadError(f"Local skill '{skill_def.name}' has no path")
        
        try:
            module = importlib.import_module(skill_def.path)
            loaded_skill.module = module
            
            if hasattr(module, 'get_instance'):
                loaded_skill.instance = module.get_instance()
            elif hasattr(module, 'Skill'):
                loaded_skill.instance = module.Skill()
            else:
                loaded_skill.instance = module
                
        except ImportError as e:
            raise SkillLoadError(f"Module not found: {skill_def.path}") from e
        except Exception as e:
            raise SkillLoadError(f"Failed to import module: {e}") from e
    
    def _load_mcp_http_skill(
        self,
        skill_def: SkillDefinition,
        loaded_skill: LoadedSkill
    ) -> None:
        """Load MCP HTTP skill (simulated)"""
        if not skill_def.url:
            raise SkillLoadError(f"MCP HTTP skill '{skill_def.name}' has no URL")
        
        logger.info(f"Loading MCP HTTP skill: {skill_def.name} -> {skill_def.url}")
        
        class MCPHTTPToolProxy:
            """MCP HTTP Tool Proxy (simulated)"""
            
            def __init__(self, url: str, metadata: Dict, skill_name: str, description: str):
                self.url = url
                self.metadata = metadata
                self.name = skill_name
                self.description = description
            
            @property
            def tools(self) -> List[Dict]:
                return [
                    {
                        "name": f"{skill_def.name}_analyze",
                        "description": self.description,
                        "url": self.url,
                    }
                ]
        
        loaded_skill.instance = MCPHTTPToolProxy(
            url=skill_def.url,
            metadata=skill_def.metadata,
            skill_name=skill_def.name,
            description=skill_def.description
        )
    
    def _load_downloadable_skill(
        self,
        skill_def: SkillDefinition,
        loaded_skill: LoadedSkill
    ) -> None:
        """Load downloadable skill (simulated)"""
        if not skill_def.path:
            raise SkillLoadError(f"Downloadable skill '{skill_def.name}' has no path")
        
        logger.info(f"Loading downloadable skill: {skill_def.name}")
        
        class DownloadableToolPlaceholder:
            """Downloadable Tool Placeholder (simulated)"""
            
            def __init__(self, download_url: str, metadata: Dict, skill_name: str, description: str):
                self.download_url = download_url
                self.metadata = metadata
                self.name = skill_name
                self.description = description
                self.downloaded = False
            
            def download(self) -> bool:
                logger.info(f"Downloading skill from: {self.download_url}")
                self.downloaded = True
                return True
            
            @property
            def tools(self) -> List[Dict]:
                return [
                    {
                        "name": self.name,
                        "description": self.description,
                        "status": "installed" if self.downloaded else "pending",
                    }
                ]
        
        loaded_skill.instance = DownloadableToolPlaceholder(
            download_url=skill_def.path,
            metadata=skill_def.metadata,
            skill_name=skill_def.name,
            description=skill_def.description
        )
    
    def unload_skill(self, skill_name: str) -> bool:
        """Unload skill"""
        if skill_name not in self._loaded_skills:
            logger.warning(f"Skill '{skill_name}' not loaded")
            return False
        
        try:
            del self._loaded_skills[skill_name]
            self._mcp_tools_cache = []
            logger.info(f"Skill '{skill_name}' unloaded")
            return True
        except Exception as e:
            logger.error(f"Failed to unload skill '{skill_name}': {e}")
            return False
    
    # --------------------------------------------------------------------------
    # MCP Tool Wrapping
    # --------------------------------------------------------------------------
    
    def _wrap_skill_as_mcp_tools(self, loaded_skill: LoadedSkill) -> List[MCPTool]:
        """
        Wrap skill's tools as MCP standardized MCPTool objects
        
        Args:
            loaded_skill: Loaded skill instance
            
        Returns:
            List of MCPTool objects
        """
        mcp_tools = []
        skill_tools = loaded_skill.tools
        skill_name = loaded_skill.definition.name
        
        # Create a unique executor for each tool using closure
        for tool in skill_tools:
            tool_name = tool.get('name', '')
            tool_desc = tool.get('description', '')
            tool_params = tool.get('parameters', {})
            
            # Convert parameters to MCPToolParameter list
            mcp_params = self._convert_params_to_mcp(tool_params)
            
            # Create executor with proper closure binding
            exec_skill_name = skill_name  # Capture by value
            exec_tool_name = tool_name    # Capture by value
            
            async def executor(params: dict, s_name: str = exec_skill_name, t_name: str = exec_tool_name) -> MCPResponse:
                return await self._execute_skill_tool(s_name, t_name, params)
            
            # Create LocalMCPWrapper
            wrapper = LocalMCPWrapper(
                name=tool_name,
                description=tool_desc,
                func=executor,
                parameters=mcp_params,
                source=skill_name
            )
            
            mcp_tool = MCPTool(
                schema=wrapper.get_tool_schema(),
                server=wrapper,
                source=skill_name
            )
            
            mcp_tools.append(mcp_tool)
        
        return mcp_tools
    
    def _convert_params_to_mcp(self, params: Dict) -> List[MCPToolParameter]:
        """Convert skill parameters to MCPToolParameter list"""
        mcp_params = []
        
        if isinstance(params, dict):
            for name, spec in params.items():
                if isinstance(spec, dict):
                    param_type = spec.get('type', 'string')
                    required = spec.get('required', False)
                    description = spec.get('description', '')
                else:
                    param_type = 'string'
                    required = False
                    description = ''
                
                mcp_params.append(MCPToolParameter(
                    name=name,
                    type=param_type,
                    required=required,
                    description=description
                ))
        
        return mcp_params
    
    async def _execute_skill_tool(self, skill_name: str, tool_name: str, params: dict) -> MCPResponse:
        """Execute skill tool and wrap result in MCPResponse"""
        if skill_name not in self._loaded_skills:
            return MCPResponse(
                status="error",
                message=f"Skill '{skill_name}' not loaded",
                error_code="SkillNotLoaded"
            )
        
        loaded_skill = self._loaded_skills[skill_name]
        instance = loaded_skill.instance
        
        try:
            # Check if instance has execute method
            if hasattr(instance, 'execute'):
                result = instance.execute(tool_name, params)
            elif hasattr(instance, 'call'):
                result = instance.call(tool_name, params)
            else:
                result = {"status": "simulated", "tool": tool_name, "params": params}
            
            return MCPResponse(
                status="success",
                data=result,
                message=f"Tool '{tool_name}' executed successfully"
            )
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            return MCPResponse(
                status="error",
                message=str(e),
                error_code=type(e).__name__
            )
    
    # --------------------------------------------------------------------------
    # Tool Query
    # --------------------------------------------------------------------------
    
    def get_active_tools(self) -> List[Dict[str, Any]]:
        """
        Get all active tools (legacy dict format)
        
        Returns:
            List of tool dictionaries
        """
        tools = []
        
        for name, loaded_skill in self._loaded_skills.items():
            if loaded_skill.loaded and loaded_skill.instance:
                skill_tools = loaded_skill.tools
                for tool in skill_tools:
                    tool_copy = tool.copy()
                    tool_copy['source'] = name
                    tool_copy['skill_type'] = loaded_skill.definition.type.value
                    tools.append(tool_copy)
        
        return tools
    
    def get_active_mcp_tools(self) -> List[MCPTool]:
        """
        Get all active tools as MCP standardized MCPTool objects
        
        Returns:
            List of MCPTool ready for LangChain/LangGraph bind_tools
            
        Usage:
            # For OpenAI function calling
            tools = manager.get_active_mcp_tools()
            openai_schemas = [t.to_openai_format() for t in tools]
            llm.bind_tools(openai_schemas)
            
            # Execute tool
            response = await tools[0].execute({"brep_file": "test.stp"})
        """
        if self._mcp_tools_cache:
            return self._mcp_tools_cache
        
        mcp_tools = []
        for name, loaded_skill in self._loaded_skills.items():
            if loaded_skill.loaded and loaded_skill.instance:
                try:
                    wrapped = self._wrap_skill_as_mcp_tools(loaded_skill)
                    mcp_tools.extend(wrapped)
                except Exception as e:
                    logger.warning(f"Failed to wrap tools for skill '{name}': {e}")
        
        self._mcp_tools_cache = mcp_tools
        return mcp_tools
    
    def get_openai_tools(self) -> List[Dict]:
        """
        Get all active tools in OpenAI function calling format
        
        Returns:
            List of OpenAI function calling schemas
        """
        mcp_tools = self.get_active_mcp_tools()
        return convert_tools_to_openai_format(mcp_tools)
    
    def get_skill_info(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """Get skill metadata"""
        if skill_name not in self._skills:
            return None
        
        skill_def = self._skills[skill_name]
        loaded_skill = self._loaded_skills.get(skill_name)
        
        return {
            "name": skill_def.name,
            "type": skill_def.type.value,
            "description": skill_def.description,
            "load_strategy": skill_def.load_strategy.value,
            "location": skill_def.location,
            "metadata": skill_def.metadata,
            "loaded": loaded_skill.loaded if loaded_skill else False,
            "error": loaded_skill.error if loaded_skill else None,
        }
    
    def list_skills(self) -> List[str]:
        """Get all registered skill names"""
        return list(self._skills.keys())
    
    def list_loaded_skills(self) -> List[str]:
        """Get all loaded skill names"""
        return [
            name for name, skill in self._loaded_skills.items()
            if skill.loaded
        ]
    
    # --------------------------------------------------------------------------
    # Context Manager Support
    # --------------------------------------------------------------------------
    
    def __enter__(self):
        """Context manager entry"""
        self.initialize()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self._loaded_skills.clear()
        self._mcp_tools_cache = []
        return False


# ==============================================================================
# Global Singleton
# ==============================================================================

_global_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """
    Get global plugin manager singleton
    
    Returns:
        PluginManager instance
    """
    global _global_manager
    
    if _global_manager is None:
        _global_manager = PluginManager()
        _global_manager.initialize()
    
    return _global_manager