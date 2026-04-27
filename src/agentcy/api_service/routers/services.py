#src/agentcy/api_service/routers/services.py

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from agentcy.pydantic_models.service_registration_model import ServiceRegistration
from agentcy.pydantic_models.commands import RegisterServiceCommand
from agentcy.api_service.dependecies import get_rm, get_publisher, CommandPublisher, ResourceManager

router = APIRouter()
log    = logging.getLogger(__name__)


@router.post("/services/{username}", status_code=status.HTTP_202_ACCEPTED)
async def upsert_service(
    username: str,
    payload: ServiceRegistration,
    rm:  ResourceManager   = Depends(get_rm),
    pub: CommandPublisher  = Depends(get_publisher),
):
    store = rm.service_store
    if store is None:
        raise HTTPException(500, "Service store is not configured")
    sid = store.upsert(username, payload)
    cmd = RegisterServiceCommand(username=username, service=payload)
    await pub.publish("commands.register_service", cmd)    # async heavy work
    return {"service_id": sid, "detail": "queued for processing"}


@router.get("/services/{username}/{service_id}")
async def get_service(
    username: str,
    service_id: str,
    rm: ResourceManager = Depends(get_rm),
):  
    store = rm.service_store
    if store is None:
        raise HTTPException(500, "Service store is not configured")
    doc = store.get(username, service_id)
    if doc is None:
        raise HTTPException(404, "Service not found")
    return doc


@router.get("/services/{username}")
async def list_services(username: str, rm: ResourceManager = Depends(get_rm)):
    store = rm.service_store
    if store is None:
        raise HTTPException(500, "Service store is not configured")
    return store.list_all(username)


@router.delete("/services/{username}/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
    username: str,
    service_id: str,
    rm: ResourceManager = Depends(get_rm),
):  
    store = rm.service_store
    if store is None:
        raise HTTPException(500, "Service store is not configured")
    store.delete(username, service_id)


@router.get("/schema/service", response_model=dict)
async def get_service_schema():
    return ServiceRegistration.model_json_schema()