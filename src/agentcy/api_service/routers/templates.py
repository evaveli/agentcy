# src/agentcy/api_service/routers/templates.py
"""
REST API for the Agent Template catalog.

Templates are pre-defined agent blueprints that the NL → Pipeline compiler
uses to match natural-language workflow steps to concrete agents.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from agentcy.api_service.dependecies import get_rm
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.agent_template_model import (
    AgentTemplate,
    TemplateCategory,
)

router = APIRouter(prefix="/templates", tags=["templates"])


def _store(rm: ResourceManager):
    store = rm.template_store
    if store is None:
        raise HTTPException(500, "Template store is not configured")
    return store


# ──────────────────────────────────────────────────────────────────────────
# Stats  (must be defined BEFORE /{username}/{template_id} to avoid
#         FastAPI matching "stats" as a template_id path parameter)
# ──────────────────────────────────────────────────────────────────────────
@router.get("/{username}/stats/count", summary="Count templates")
def count_templates(
    username: str,
    enabled: Optional[bool] = Query(None),
    rm: ResourceManager = Depends(get_rm),
) -> Dict[str, int]:
    store = _store(rm)
    return {"count": store.count(username=username, enabled=enabled)}


# ──────────────────────────────────────────────────────────────────────────
# List / Browse
# ──────────────────────────────────────────────────────────────────────────
@router.get("/{username}", summary="List agent templates")
def list_templates(
    username: str,
    category: Optional[str] = Query(None, description="Filter by category"),
    capability: Optional[str] = Query(None, description="Filter by capability"),
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    limit: Optional[int] = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
    rm: ResourceManager = Depends(get_rm),
) -> List[Dict[str, Any]]:
    store = _store(rm)
    return store.list(
        username=username,
        category=category,
        capability=capability,
        enabled=enabled,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )


# ──────────────────────────────────────────────────────────────────────────
# Get single
# ──────────────────────────────────────────────────────────────────────────
@router.get("/{username}/{template_id}", summary="Get an agent template")
def get_template(
    username: str,
    template_id: str,
    rm: ResourceManager = Depends(get_rm),
) -> Dict[str, Any]:
    store = _store(rm)
    doc = store.get(username=username, template_id=template_id)
    if doc is None:
        raise HTTPException(404, f"Template {template_id} not found")
    return doc


# ──────────────────────────────────────────────────────────────────────────
# Create / Update
# ──────────────────────────────────────────────────────────────────────────
@router.post(
    "/{username}",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new agent template",
)
def create_template(
    username: str,
    payload: AgentTemplate,
    rm: ResourceManager = Depends(get_rm),
) -> Dict[str, str]:
    store = _store(rm)
    template_id = store.upsert(
        username=username,
        template=payload.model_dump(mode="json"),
    )
    return {"template_id": template_id}


@router.put("/{username}/{template_id}", summary="Update an agent template")
def update_template(
    username: str,
    template_id: str,
    payload: AgentTemplate,
    rm: ResourceManager = Depends(get_rm),
) -> Dict[str, str]:
    store = _store(rm)
    existing = store.get(username=username, template_id=template_id)
    if existing is None:
        raise HTTPException(404, f"Template {template_id} not found")
    data = payload.model_dump(mode="json")
    data["template_id"] = template_id
    store.upsert(username=username, template=data)
    return {"template_id": template_id, "detail": "updated"}


# ──────────────────────────────────────────────────────────────────────────
# Delete
# ──────────────────────────────────────────────────────────────────────────
@router.delete("/{username}/{template_id}", summary="Delete an agent template")
def delete_template(
    username: str,
    template_id: str,
    rm: ResourceManager = Depends(get_rm),
) -> Dict[str, str]:
    store = _store(rm)
    deleted = store.delete(username=username, template_id=template_id)
    if not deleted:
        raise HTTPException(404, f"Template {template_id} not found")
    return {"detail": "deleted"}
