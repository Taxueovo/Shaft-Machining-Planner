"""
Pydantic Schemas for API Request/Response
"""

from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List


# ==============================================================================
# Request Models
# ==============================================================================

class ExtractFeaturesRequest(BaseModel):
    """Request model for feature extraction."""
    session_id: str = Field(..., description="Session ID from upload response")


class ChatRequest(BaseModel):
    """Request model for chat with AI CAE expert."""
    session_id: str = Field(..., description="Session ID from upload response")
    message: str = Field(..., description="User message")
    images: Optional[Dict[str, str]] = Field(
        default=None, 
        description="Base64 encoded images for context"
    )
    features_json: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Feature JSON data for context"
    )
    initialize_context: bool = Field(
        default=False,
        description="Whether to initialize with multimodal context"
    )


# ==============================================================================
# Response Models
# ==============================================================================

class ImageViewData(BaseModel):
    """Data for a single view image."""
    base64: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    name: Optional[str] = None


class UploadRenderResponse(BaseModel):
    """Response model for upload and render endpoint."""
    success: bool
    session_id: str
    file_hash: str
    file_path: str
    file_name: str
    images: Dict[str, ImageViewData]
    render_success: bool
    message: str = "Processing completed"


class ExtractFeaturesResponse(BaseModel):
    """Response model for feature extraction."""
    session_id: str
    features_json: Optional[Dict[str, Any]] = None
    extraction_success: bool
    error: Optional[str] = None
    message: str = "Feature extraction completed"


class PlanningInputResponse(BaseModel):
    """Response model for /api/v1/planning-input.

    Returns a peagent ``PlanningRequest`` compatible draft plus confidence
    and warnings, so the peagent frontend can pre-fill its form.
    """
    success: bool
    source_file: str
    session_id: str
    render_images: Dict[str, ImageViewData] = {}
    planning_request: Optional[Dict[str, Any]] = None
    confidence: Optional[Dict[str, Any]] = None
    warnings: List[str] = []
    extraction_success: bool = False
    validation: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: str = ""


class ChatChunkResponse(BaseModel):
    """Response model for chat chunks."""
    content: str = ""


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str
    detail: Optional[str] = None


# ==============================================================================
# Internal Data Models
# ==============================================================================

class ConversationMessage(BaseModel):
    """Single conversation message in history."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: float  # Unix timestamp


class SessionData(BaseModel):
    """Internal session data storage model with layered memory management."""
    session_id: str
    file_path: str
    file_hash: str
    file_name: str
    
    # === Long-term Memory (Persistent) ===
    images: Dict[str, bytes] = {}  # Base64 rendered images
    features_json: Optional[Dict[str, Any]] = None  # Full feature data
    feature_summary: Optional[str] = None  # LLM-generated summary of features
    
    # === Short-term Memory (Sliding Window) ===
    conversation_history: List[ConversationMessage] = []  # Recent conversation turns
    
    # === State Flags ===
    render_completed: bool = False
    extraction_completed: bool = False
    context_initialized: bool = False  # Whether initial context was sent to LLM
    
    # === Configuration ===
    max_history_turns: int = 5  # Sliding window size (keep recent N turns)
    max_tokens_warning: int = 3000  # Warn when context exceeds this
    
    def add_message(self, role: str, content: str) -> None:
        """Add a message to conversation history with timestamp."""
        import time
        self.conversation_history.append(
            ConversationMessage(role=role, content=content, timestamp=time.time())
        )
        self._trim_history()
    
    def _trim_history(self) -> None:
        """Trim history to keep only recent N turns (sliding window)."""
        if len(self.conversation_history) > self.max_history_turns * 2:
            # Keep pairs: user + assistant = 1 turn
            self.conversation_history = self.conversation_history[-self.max_history_turns * 2:]
    
    def get_history_for_llm(self) -> List[Dict[str, str]]:
        """Get conversation history formatted for LLM API."""
        return [
            {"role": msg.role, "content": msg.content}
            for msg in self.conversation_history
        ]
    
    def clear_history(self) -> None:
        """Clear conversation history (keep features/images)."""
        self.conversation_history = []
