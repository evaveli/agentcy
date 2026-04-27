# src/agentcy/api_service/routers/agent_registry.py

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from agentcy.api_service.dependecies import get_rm
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.orchestrator_core.stores.agent_registry_store import AgentNotFound
from agentcy.pydantic_models.agent_registry_model import AgentRegistryEntry, AgentStatus

router = APIRouter()


class AgentHeartbeatRequest(BaseModel):
    status: Optional[AgentStatus] = None
    ttl_seconds: Optional[int] = Field(default=None, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/agent-registry/{username}",
    status_code=status.HTTP_201_CREATED,
    response_model=dict,
)
async def register_agent(
    username: str,
    payload: AgentRegistryEntry,
    ttl_seconds: Optional[int] = Query(default=None, ge=0),
    rm: ResourceManager = Depends(get_rm),
):
    store = rm.agent_registry_store
    if store is None:
        raise HTTPException(500, "Agent registry store is not configured")
    store.upsert(username=username, entry=payload, ttl_seconds=ttl_seconds)
    doc = store.get(username=username, agent_id=payload.agent_id)
    if doc is None:
        raise HTTPException(500, "Agent registry write failed")
    return doc


@router.post(
    "/agent-registry/{username}/{agent_id}/heartbeat",
    status_code=status.HTTP_200_OK,
    response_model=dict,
)
async def heartbeat(
    username: str,
    agent_id: str,
    payload: AgentHeartbeatRequest,
    rm: ResourceManager = Depends(get_rm),
):
    store = rm.agent_registry_store
    if store is None:
        raise HTTPException(500, "Agent registry store is not configured")
    try:
        doc = store.heartbeat(
            username=username,
            agent_id=agent_id,
            status=payload.status,
            ttl_seconds=payload.ttl_seconds,
            metadata=payload.metadata,
        )
    except AgentNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return doc


@router.get(
    "/agent-registry/{username}/{agent_id}",
    status_code=status.HTTP_200_OK,
    response_model=dict,
)
async def get_agent(
    username: str,
    agent_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = rm.agent_registry_store
    if store is None:
        raise HTTPException(500, "Agent registry store is not configured")
    doc = store.get(username=username, agent_id=agent_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return doc


@router.get("/agent-registry/{username}", response_model=List[dict])
async def list_agents(
    username: str,
    service_name: Optional[str] = None,
    capability: Optional[str] = None,
    status_filter: Optional[AgentStatus] = Query(default=None, alias="status"),
    tags: Optional[List[str]] = Query(default=None),
    rm: ResourceManager = Depends(get_rm),
):
    store = rm.agent_registry_store
    if store is None:
        raise HTTPException(500, "Agent registry store is not configured")
    return store.list(
        username=username,
        service_name=service_name,
        capability=capability,
        status=status_filter,
        tags=tags,
    )


@router.delete(
    "/agent-registry/{username}/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agent(
    username: str,
    agent_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = rm.agent_registry_store
    if store is None:
        raise HTTPException(500, "Agent registry store is not configured")
    store.delete(username=username, agent_id=agent_id)
