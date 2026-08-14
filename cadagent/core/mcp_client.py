"""
================================================

MCP Client - Model Context Protocol Client

Provides standardized tool interfaces for the ShaftPlanner
multi-agent system, supporting both sync and async execution.

Core Components:
- MCPToolSchema: OpenAI function calling format
- MCPResponse: Standardized execution response
- BaseMCPServer: Abstract base for all MCP tools
- LocalMCPWrapper: Wrapper for local Python functions
- MCPTool: Complete MCP tool object

================================================
"""

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


# ==============================================================================
# Pydantic Models
# ==============================================================================

class MCPToolParameter(BaseModel):
    """Tool parameter definition"""
    name: str = Field(..., description="Parameter name")
    type: str = Field(..., description="Parameter type: string, number, boolean, array, object")
    description: str = Field(default="", description="Parameter description")
    required: bool = Field(default=False, description="Whether parameter is required")


class MCPToolSchema(BaseModel):
    """
    OpenAI Function Calling format Tool Schema
    
    Converts to OpenAI's function calling format:
    {
        "type": "function",
        "function": {
            "name": "...",
            "description": "...",
            "parameters": {...}
        }
    }
    """
    name: str = Field(..., description="Tool name")
    description: str = Field(default="", description="Tool description")
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema format parameters"
    )
    
    def to_openai_schema(self) -> Dict[str, Any]:
        """
        Convert to OpenAI function calling format
        
        Returns:
            Dict compatible with OpenAI's function calling
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }
    
    def model_dump_openai(self) -> Dict[str, Any]:
        """
        Alias for OpenAI compatibility
        
        Returns:
            OpenAI function calling format dict
        """
        return self.to_openai_schema()


class MCPResponse(BaseModel):
    """
    MCP Tool Execution Response - Standardized format
    
    Provides consistent response structure for LLM interpretation:
    - status: success/error for easy branching
    - data: actual result payload
    - message: human-readable message
    - error_code: machine-readable error type
    
    Example:
        MCPResponse(
            status="success",
            data={"features": [...]},
            message="Feature extraction completed"
        )
        
        MCPResponse(
            status="error",
            data=None,
            message="File not found",
            error_code="FileNotFoundError"
        )
    """
    status: str = Field(
        default="success",
        description="Execution status: 'success' or 'error'"
    )
    data: Optional[Any] = Field(
        default=None,
        description="Result payload"
    )
    message: str = Field(
        default="",
        description="Human-readable message"
    )
    error_code: Optional[str] = Field(
        default=None,
        description="Machine-readable error code"
    )
    
    @property
    def is_success(self) -> bool:
        """Check if execution was successful"""
        return self.status == "success"
    
    @property
    def is_error(self) -> bool:
        """Check if execution failed"""
        return self.status == "error"
    
    def to_langchain_format(self) -> str:
        """
        Convert to LangChain tool response format
        
        Returns:
            String representation for LangChain
        """
        if self.is_success:
            return f"Success: {self.message}" if self.message else str(self.data)
        return f"Error [{self.error_code}]: {self.message}"


# ==============================================================================
# BaseMCPServer - Abstract Base Class
# ==============================================================================

class BaseMCPServer(ABC):
    """
    Abstract base class for all MCP servers/tools
    
    All skills (local or remote) must implement this interface:
    - get_tool_schema(): Returns OpenAI-compatible tool schema
    - execute(): Unified execution entry point (async)
    
    Usage:
        class MyTool(BaseMCPServer):
            @property
            def name(self) -> str:
                return "my_tool"
            
            def get_tool_schema(self) -> MCPToolSchema:
                return MCPToolSchema(...)
            
            async def execute(self, params: dict) -> MCPResponse:
                return MCPResponse(status="success", data=result)
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool/Skill name"""
        raise NotImplementedError
    
    @property
    def description(self) -> str:
        """Tool description (default empty)"""
        return ""
    
    @abstractmethod
    def get_tool_schema(self) -> MCPToolSchema:
        """
        Get OpenAI function calling format schema
        
        Returns:
            MCPToolSchema: Tool schema in OpenAI format
        """
        raise NotImplementedError
    
    @abstractmethod
    async def execute(self, params: dict) -> MCPResponse:
        """
        Execute the tool with given parameters
        
        Args:
            params: Dictionary of parameters
            
        Returns:
            MCPResponse: Standardized execution response
        """
        raise NotImplementedError
    
    def execute_sync(self, params: dict) -> MCPResponse:
        """
        Synchronous execution wrapper
        
        Provides sync compatibility for non-async scenarios.
        Internally runs the async execute method.
        
        Args:
            params: Dictionary of parameters
            
        Returns:
            MCPResponse: Standardized execution response
        """
        try:
            # No running loop - safe to use asyncio.run()
            return asyncio.run(self.execute(params))
        except RuntimeError as e:
            if "asyncio.run() cannot be called from a running event loop" in str(e):
                # Already in async context, use run_until_complete
                loop = asyncio.get_event_loop()
                return loop.run_until_complete(self.execute(params))
            raise
        except Exception as e:
            logger.error(f"Sync execution failed: {e}")
            return MCPResponse(
                status="error",
                message=str(e),
                error_code=type(e).__name__
            )


# ==============================================================================
# LocalMCPWrapper - Wrapper for Local Functions
# ==============================================================================

class LocalMCPWrapper(BaseMCPServer):
    """
    Wrapper for local Python functions to MCP standard
    
    Converts regular Python functions (sync or async) into
    standardized MCP tools with proper schema and response handling.
    
    Usage:
        def calculate_cost(material: str, volume: float) -> float:
            return volume * prices[material]
        
        wrapper = LocalMCPWrapper(
            name="calculate_cost",
            description="Calculate material cost",
            func=calculate_cost,
            parameters=[
                MCPToolParameter(name="material", type="string", required=True),
                MCPToolParameter(name="volume", type="number", required=True),
            ]
        )
        
        response = await wrapper.execute({"material": "steel", "volume": 100})
    
    Async support:
        async def fetch_erp_data(item_id: str) -> dict:
            return await http_client.get(f"/api/items/{item_id}")
        
        wrapper = LocalMCPWrapper(
            name="fetch_erp_data",
            description="Fetch ERP data",
            func=fetch_erp_data,
            parameters=[...]
        )
    """
    
    def __init__(
        self,
        name: str,
        description: str,
        func: Union[Callable, Coroutine],
        parameters: Optional[List[MCPToolParameter]] = None,
        source: str = "local"
    ):
        """
        Initialize LocalMCPWrapper
        
        Args:
            name: Tool name
            description: Tool description
            func: Python function (sync or async) to wrap
            parameters: List of parameter definitions
            source: Source identifier (e.g., skill name)
        """
        self._name = name
        self._description = description
        self._func = func
        self._parameters = parameters or []
        self._source = source
        
        # Validate function signature
        self._validate_function()
    
    def _validate_function(self) -> None:
        """Validate function signature against parameters"""
        sig = inspect.signature(self._func)
        param_names = {p.name for p in self._parameters if p.required}
        func_params = set(sig.parameters.keys())
        
        missing = param_names - func_params
        if missing:
            logger.warning(
                f"Function {self._name} missing required parameters: {missing}"
            )
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def description(self) -> str:
        return self._description
    
    @property
    def source(self) -> str:
        return self._source
    
    @property
    def is_async(self) -> bool:
        """Check if wrapped function is async"""
        return asyncio.iscoroutinefunction(self._func)
    
    def get_tool_schema(self) -> MCPToolSchema:
        """
        Generate OpenAI function calling schema
        
        Returns:
            MCPToolSchema with JSON Schema parameters
        """
        # Build JSON Schema format
        properties = {}
        required = []
        
        for param in self._parameters:
            param_schema = {"description": param.description}
            
            # Map to JSON Schema type
            if param.type == "number":
                param_schema["type"] = "number"
            elif param.type == "boolean":
                param_schema["type"] = "boolean"
            elif param.type == "array":
                param_schema["type"] = "array"
            elif param.type == "object":
                param_schema["type"] = "object"
            else:
                param_schema["type"] = "string"
            
            properties[param.name] = param_schema
            
            if param.required:
                required.append(param.name)
        
        parameters = {
            "type": "object",
            "properties": properties,
        }
        
        if required:
            parameters["required"] = required
        
        return MCPToolSchema(
            name=self._name,
            description=self._description,
            parameters=parameters
        )
    
    async def execute(self, params: dict) -> MCPResponse:
        """
        Execute the wrapped function
        
        Args:
            params: Parameters to pass to function
            
        Returns:
            MCPResponse with execution result
        """
        try:
            # Call the function (async or sync)
            if asyncio.iscoroutinefunction(self._func):
                result = await self._func(**params)
            else:
                result = self._func(**params)
            
            return MCPResponse(
                status="success",
                data=result,
                message=f"{self._name} executed successfully"
            )
            
        except TypeError as e:
            # Parameter validation error
            logger.warning(f"Parameter validation failed for {self._name}: {e}")
            return MCPResponse(
                status="error",
                message=f"Invalid parameters: {str(e)}",
                error_code="TypeError"
            )
        except Exception as e:
            # General execution error
            logger.error(f"Execution failed for {self._name}: {e}")
            return MCPResponse(
                status="error",
                message=str(e),
                error_code=type(e).__name__
            )
    
    def execute_sync(self, params: dict) -> MCPResponse:
        """
        Synchronous execution wrapper
        
        For sync functions, directly calls the function.
        For async functions, runs in a new event loop.
        
        Args:
            params: Dictionary of parameters
            
        Returns:
            MCPResponse: Standardized execution response
        """
        try:
            # For sync functions, call directly
            if not asyncio.iscoroutinefunction(self._func):
                result = self._func(**params)
                return MCPResponse(
                    status="success",
                    data=result,
                    message=f"{self._name} executed successfully"
                )
            
            # For async functions, need event loop
            return asyncio.run(self._func(**params))
            
        except TypeError as e:
            logger.warning(f"Parameter validation failed for {self._name}: {e}")
            return MCPResponse(
                status="error",
                message=f"Invalid parameters: {str(e)}",
                error_code="TypeError"
            )
        except Exception as e:
            logger.error(f"Execution failed for {self._name}: {e}")
            return MCPResponse(
                status="error",
                message=str(e),
                error_code=type(e).__name__
            )


# ==============================================================================
# MCPTool - Complete Tool Object
# ==============================================================================

class MCPTool(BaseMCPServer):
    """
    Complete MCP Tool object
    
    Encapsulates schema and server reference for execution.
    Designed for direct use with LangChain/LangGraph bind_tools.
    
    Usage:
        tool = MCPTool(
            schema=schema,
            server=my_server,
            source="feature_extractor"
        )
        
        # Get OpenAI format
        openai_format = tool.to_openai_format()
        
        # Execute
        response = await tool.execute({"brep_file": "test.stp"})
    """
    
    def __init__(
        self,
        schema: MCPToolSchema,
        server: BaseMCPServer,
        source: str = ""
    ):
        """
        Initialize MCPTool
        
        Args:
            schema: Tool schema in OpenAI format
            server: MCP server that executes the tool
            source: Source identifier (e.g., skill name)
        """
        self._schema = schema
        self._server = server
        self._source = source
    
    @property
    def name(self) -> str:
        return self._schema.name
    
    @property
    def description(self) -> str:
        return self._schema.description
    
    @property
    def source(self) -> str:
        return self._source
    
    @property
    def schema(self) -> MCPToolSchema:
        """Get the tool schema"""
        return self._schema
    
    @property
    def server(self) -> BaseMCPServer:
        """Get the underlying server"""
        return self._server
    
    def get_tool_schema(self) -> MCPToolSchema:
        """Get tool schema"""
        return self._schema
    
    def to_openai_format(self) -> Dict[str, Any]:
        """
        Get OpenAI function calling format
        
        Returns:
            Dict ready for llm.bind_tools()
        """
        return self._schema.to_openai_schema()
    
    async def execute(self, params: dict) -> MCPResponse:
        """
        Execute the tool
        
        Args:
            params: Tool parameters
            
        Returns:
            MCPResponse with execution result
        """
        return await self._server.execute(params)
    
    def __call__(self, params: dict) -> MCPResponse:
        """
        Synchronous call wrapper
        
        Args:
            params: Tool parameters
            
        Returns:
            MCPResponse (sync)
        """
        return self._server.execute_sync(params)


# ==============================================================================
# Utility Functions
# ==============================================================================

def create_mcp_tool_from_function(
    name: str,
    description: str,
    func: Union[Callable, Coroutine],
    parameters: List[MCPToolParameter],
    source: str = "local"
) -> MCPTool:
    """
    Factory function to create MCPTool from a Python function
    
    Args:
        name: Tool name
        description: Tool description
        func: Python function (sync or async)
        parameters: List of parameter definitions
        source: Source identifier
        
    Returns:
        MCPTool ready for use
    """
    wrapper = LocalMCPWrapper(
        name=name,
        description=description,
        func=func,
        parameters=parameters,
        source=source
    )
    
    return MCPTool(
        schema=wrapper.get_tool_schema(),
        server=wrapper,
        source=source
    )


def convert_tools_to_openai_format(tools: List["MCPTool"]) -> List[Dict]:
    """
    Convert list of MCPTool to OpenAI function calling format
    
    Args:
        tools: List of MCPTool objects
        
    Returns:
        List of OpenAI function calling schemas
    """
    return [tool.to_openai_format() for tool in tools]