import uuid

import pytest

from src.agentcy.agent_runtime.services import blueprint_bidder
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.agent_registry_model import AgentRegistryEntry, AgentStatus
from src.agentcy.pydantic_models.multi_agent_pipeline import TaskSpec


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_blueprint_bidder_writes_bids():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    registry = rm.agent_registry_store
    assert store is not None
    assert registry is not None

    username = f"blueprint_bidder_{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"
    agent_id = f"agent-{uuid.uuid4()}"

    try:
        store.upsert_task_spec(
            username=username,
            spec=TaskSpec(
                task_id=task_id,
                username=username,
                description="plan task",
                required_capabilities=["plan"],
            ),
        )
        registry.upsert(
            username=username,
            entry=AgentRegistryEntry(
                agent_id=agent_id,
                service_name="planner",
                status=AgentStatus.IDLE,
                capabilities=["plan"],
                tags=["core"],
            ),
        )

        message = {"username": username, "data": {}}
        result = await blueprint_bidder.run(rm, "run-1", "blueprint_bidder", None, message)
        assert result["bids_created"] == 1
        assert result["cfps_created"] == 1

        bids = store.list_bids(username=username)
        assert bids
        assert bids[0]["bidder_id"] == agent_id

        cfps = store.list_cfps(username=username, task_id=task_id)
        assert cfps
    finally:
        pool.close_all()
