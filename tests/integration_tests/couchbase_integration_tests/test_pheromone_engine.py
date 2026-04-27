import uuid

import pytest

from src.agentcy.agent_runtime.services import pheromone_engine
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.multi_agent_pipeline import AffordanceMarker


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_pheromone_engine_decays_markers(monkeypatch):
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"pheromone_{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"
    agent_id = f"agent-{uuid.uuid4()}"

    try:
        marker = AffordanceMarker(
            task_id=task_id,
            agent_id=agent_id,
            intensity=1.0,
        )
        store.add_affordance_marker(username=username, marker=marker)
        monkeypatch.setenv("PHEROMONE_DECAY_FACTOR", "0.5")

        message = {"username": username, "data": {}}
        result = await pheromone_engine.run(rm, "run-1", "pheromone_engine", None, message)
        assert result["mode"] == "decay"

        markers = store.list_affordance_markers(username=username, task_id=task_id)
        assert markers
        assert markers[0]["intensity"] == pytest.approx(0.5)
    finally:
        pool.close_all()
