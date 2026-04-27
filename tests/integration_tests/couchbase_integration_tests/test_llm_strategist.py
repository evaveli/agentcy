import uuid

import pytest

from src.agentcy.agent_runtime.services import llm_strategist
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
async def test_llm_strategist_writes_strategy():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"llm_strategist_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    plan_id = str(uuid.uuid4())

    try:
        draft = PlanDraft(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            graph_spec={
                "tasks": [
                    {"task_id": "t1", "assigned_agent": "a", "required_capabilities": ["plan"]},
                    {"task_id": "t2", "assigned_agent": "b", "required_capabilities": ["execute"]},
                ],
                "edges": [{"from": "t1", "to": "t2"}],
            },
        )
        store.save_plan_draft(username=username, draft=draft)

        message = {"username": username, "pipeline_id": pipeline_id, "plan_id": plan_id, "data": {}}
        result = await llm_strategist.run(rm, "run-1", "llm_strategist", None, message)
        assert result["strategy_id"]

        strategies = store.list_strategy_plans(username=username, plan_id=plan_id)
        assert strategies
    finally:
        pool.close_all()
