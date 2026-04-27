#src/agentcy/api_service/routers/discovery.py

import logging
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict
from agentcy.consul_utils.consul_discovery import ConsulConfig, ServiceDiscovery

router = APIRouter()
log = logging.getLogger(__name__)

async def _discovery_dep():
    disc = ServiceDiscovery(ConsulConfig())
    try:
        yield disc
    finally:
        await disc.close()

@router.get("/discover/{service_name}", response_model=List[Dict[str, str]])
async def discover_service(
    service_name: str,
    disc: ServiceDiscovery = Depends(_discovery_dep),
):
    try:
        entries = await disc.discover_service_http(service_name)
        if not entries:
            raise HTTPException(404, f"No instances for '{service_name}'")
        return entries
    except Exception as exc:
        log.exception("Consul lookup failed")
        raise HTTPException(500, str(exc))

