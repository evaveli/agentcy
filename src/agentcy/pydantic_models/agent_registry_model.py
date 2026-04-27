from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentStatus(str, Enum):
    ONLINE = "online"
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    UNHEALTHY = "unhealthy"


class AgentRegistryEntry(BaseModel):
    agent_id: str
    service_name: str
    owner: Optional[str] = None
    description: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    status: AgentStatus = AgentStatus.ONLINE
    metadata: Dict[str, Any] = Field(default_factory=dict)
    policy: Dict[str, Any] = Field(default_factory=dict)
    registered_at: datetime = Field(default_factory=_utcnow)
    last_heartbeat: datetime = Field(default_factory=_utcnow)
    expires_at: Optional[datetime] = None
