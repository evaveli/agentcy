import uuid

import pytest

from src.agentcy.agent_runtime.services import path_seeder
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
async def test_path_seeder_writes_markers():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    registry = rm.agent_registry_store
    assert store is not None
    assert registry is not None

    username = f"path_seeder_{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"
    agent_id = f"agent-{uuid.uuid4()}"

    try:
        store.upsert_task_spec(
            username=username,
            spec=TaskSpec(
                task_id=task_id,
                username=username,
                description="seed task",
                required_capabilities=["execute"],
            ),
        )
        registry.upsert(
            username=username,
            entry=AgentRegistryEntry(
                agent_id=agent_id,
                service_name="executor",
                status=AgentStatus.IDLE,
                capabilities=["execute"],
            ),
        )

        message = {"username": username, "data": {}}
        result = await path_seeder.run(rm, "run-1", "path_seeder", None, message)
        assert result["markers_created"] == 1

        markers = store.list_affordance_markers(username=username, task_id=task_id)
        assert markers
        assert markers[0]["agent_id"] == agent_id
    finally:
        pool.close_all()
