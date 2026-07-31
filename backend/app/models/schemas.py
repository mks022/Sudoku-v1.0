from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProviderConfigIn(BaseModel):
    kind: str = Field(..., min_length=1, max_length=64, description="Free-form kind, e.g. llm/stt/tts/vad/mcp")
    provider: str = Field(..., min_length=1, max_length=128, description="Any provider label")
    api_key: str = ""
    clear_api_key: bool = False
    base_url: str = ""
    model: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_fallback: bool = False
    priority: int = 0


class ProviderConfigOut(BaseModel):
    id: int
    kind: str
    provider: str
    api_key: str = ""
    api_key_masked: str = ""
    base_url: str = ""
    model: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    is_fallback: bool = False
    priority: int = 0
    updated_at: datetime
    protocol: str = ""


class ProviderTemplateApply(BaseModel):
    template_id: str
    api_key: str = ""
    enabled: bool = True
    is_fallback: bool = False
    priority: int = 0
    provider: str = ""
    base_url: str = ""
    model: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

class CallStatus(str, Enum):
    active = "active"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class CallCreate(BaseModel):
    channel: str = "voice"  # voice | chat | hybrid
    title: str = ""


class CallOut(BaseModel):
    id: int
    channel: str
    title: str
    status: CallStatus
    started_at: datetime
    ended_at: Optional[datetime] = None
    transcript_preview: str = ""
    message_count: int = 0
    fault_events: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageRole(str, Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class MessageIn(BaseModel):
    content: str
    call_id: Optional[int] = None
    channel: str = "chat"


class MessageOut(BaseModel):
    id: int
    call_id: int
    role: MessageRole
    content: str
    created_at: datetime
    meta: dict[str, Any] = Field(default_factory=dict)


class TransactionKind(str, Enum):
    stt = "stt"
    llm = "llm"
    tts = "tts"
    vad = "vad"
    mcp = "mcp"
    rag = "rag"
    network = "network"
    system = "system"
    chat = "chat"
    narration = "narration"


class TransactionEvent(BaseModel):
    id: str
    call_id: Optional[int] = None
    kind: TransactionKind
    status: str  # started | success | retry | fallback | failed | info
    title: str
    detail: str = ""
    provider: str = ""
    latency_ms: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    meta: dict[str, Any] = Field(default_factory=dict)


class RagDocumentIn(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source: str = "manual"


class RagDocumentOut(RagDocumentIn):
    id: int
    created_at: datetime


class SessionStart(BaseModel):
    channel: str = "hybrid"
    title: str = "Live session"


class ChatReply(BaseModel):
    call_id: int
    user_message: MessageOut
    assistant_message: MessageOut
    rag_context: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    narrations: list[dict[str, Any]] = Field(default_factory=list)
    transactions: list[TransactionEvent] = Field(default_factory=list)
