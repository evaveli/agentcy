import uuid

import pytest

from src.agentcy.agent_runtime.services.plan_cache import cache_plan_draft
from src.agentcy.agent_runtime.services.plan_validator import validate_plan_draft
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_plan_validation_and_cache_roundtrip():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"plan_validation_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    plan_id = str(uuid.uuid4())

    try:
        draft = PlanDraft(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            graph_spec={
                "tasks": [
                    {"task_id": "t1", "assigned_agent": "agent-a", "required_capabilities": ["plan"]},
                    {"task_id": "t2", "assigned_agent": "agent-b", "required_capabilities": ["execute"]},
                ],
                "edges": [{"from": "t1", "to": "t2"}],
            },
        )
        store.save_plan_draft(username=username, draft=draft)

        validated = await validate_plan_draft(rm, username=username, pipeline_id=pipeline_id)
        assert validated.is_valid is True

        cached = await cache_plan_draft(rm, username=username, pipeline_id=pipeline_id)
        assert cached["cached"] is True

        saved = store.get_plan_draft(username=username, plan_id=plan_id)
        assert saved
        assert saved["cached"] is True
    finally:
        pool.close_all()
