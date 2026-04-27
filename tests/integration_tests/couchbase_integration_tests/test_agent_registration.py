import uuid

import pytest

from src.agentcy.agent_runtime.services import agent_registration
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.service_registration_model import ServiceRegistration, RuntimeEnum


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


def _service(name: str) -> ServiceRegistration:
    return ServiceRegistration(
        service_id=uuid.uuid4(),
        service_name=name,
        runtime=RuntimeEnum.PYTHON_PLUGIN,
        artifact={"kind": "entry", "entry": f"agentcy.agent_runtime.services.{name}:run"},
        base_url=None,
        healthcheck_endpoint={
            "name": "health",
            "path": "/health",
            "methods": ["GET"],
            "description": "health",
            "parameters": [],
        },
    )


@pytest.mark.asyncio
async def test_agent_registration_seeds_registry_entries():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    registry = rm.agent_registry_store
    services = rm.service_store
    assert registry is not None
    assert services is not None

    username = f"agent_registration_{uuid.uuid4()}"
    services.upsert(username, _service("graph_builder"))
    services.upsert(username, _service("plan_validator"))

    try:
        result = await agent_registration.run(
            rm, "run-1", "agent_registration", None, {"username": username}
        )
        assert result["registered"] == 2

        entry = registry.get(username=username, agent_id="graph_builder")
        assert entry is not None
        assert "graph_builder" in entry.get("capabilities", [])
    finally:
        pool.close_all()
