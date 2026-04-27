import uuid

import pytest

from src.agentcy.agent_runtime.services import input_validator
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_input_validator_passes_and_does_not_write_specs():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"input_validator_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    run_id = f"run-{uuid.uuid4()}"
    message = {
        "username": username,
        "pipeline_id": pipeline_id,
        "pipeline_run_id": run_id,
        "data": {"task_description": "validate this"},
    }

    try:
        result = await input_validator.run(rm, run_id, "input_validator", None, message)
        assert result["validated"] is True

        specs = store.list_task_specs(username=username)
        assert specs == []
    finally:
        pool.close_all()
