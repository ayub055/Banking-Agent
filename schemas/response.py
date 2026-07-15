"""Response schemas for pipeline output."""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

from .intent import ParsedIntent


class ToolResult(BaseModel):
    """Result from a single tool execution."""
    tool_name: str
    args: Dict[str, Any]
    result: Dict[str, Any]
    success: bool = True
    error: Optional[str] = None


class AuditLog(BaseModel):
    """Audit trail for a single query."""
    timestamp: datetime = Field(default_factory=datetime.now)
    query: str
    parsed_intent: ParsedIntent
    tools_executed: List[ToolResult]
    response: str
    latency_ms: float
    success: bool = True
    error: Optional[str] = None


class PipelineResponse(BaseModel):
    """Final response from the pipeline."""
    answer: str
    data: Dict[str, Any] = Field(default_factory=dict)
    intent: ParsedIntent
    tools_used: List[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
