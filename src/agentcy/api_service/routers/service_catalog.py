from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, Literal
from agentcy.api_service.dependecies import get_rm, ResourceManager

router = APIRouter(prefix="/catalog", tags=["catalog"])

@router.get("/{username}/artifacts")
async def list_artifacts(
    username: str,
    kind: Optional[Literal["wheel","oci"]] = Query(None),
    status: Optional[Literal["dev","stg","prod"]] = Query(None),
    name: Optional[str] = Query(None),
    version: Optional[str] = Query(None),
    rm: ResourceManager = Depends(get_rm),
):
    if rm.catalog_user_store is None:
        raise HTTPException(500, "Catalog store is not configured")
    return await rm.catalog_user_store.query(username=username, kind=kind, status=status, name=name, version=version)  # type: ignore


@router.get("/{username}/artifacts/{kind}/{name}/{version}")
async def resolve_artifact(
    username: str,
    kind: Literal["wheel","oci"],
    name: str,
    version: str,
    status: Literal["dev","stg","prod"] = "prod",
    rm: ResourceManager = Depends(get_rm),
):
    if rm.catalog_user_store is None:
        raise HTTPException(500, "Catalog store is not configured")
    doc = await rm.catalog_user_store.resolve(username=username, kind=kind, name=name, version=version, status=status)
    if not doc:
        raise HTTPException(404, "Not found")
    return doc