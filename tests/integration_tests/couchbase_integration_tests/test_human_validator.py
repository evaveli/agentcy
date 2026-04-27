import uuid

import pytest

from src.agentcy.agent_runtime.services import human_validator
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft, TaskSpec


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_human_validator_stores_approval():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"human_validator_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    plan_id = str(uuid.uuid4())

    try:
        store.upsert_task_spec(
            username=username,
            spec=TaskSpec(
                task_id="task-1",
                username=username,
                description="plan",
                required_capabilities=["plan"],
                metadata={"pipeline_id": pipeline_id},
            ),
        )
        draft = PlanDraft(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            graph_spec={
                "tasks": [{"task_id": "task-1", "assigned_agent": "agent-a", "required_capabilities": ["plan"]}],
                "edges": [],
            },
        )
        store.save_plan_draft(username=username, draft=draft)

        message = {"username": username, "pipeline_id": pipeline_id, "plan_id": plan_id, "data": {"approved": True}}
        result = await human_validator.run(rm, "run-1", "human_validator", None, message)
        assert result["approved"] is True
        approvals = store.list_human_approvals(username=username, plan_id=plan_id)
        assert approvals
    finally:
        pool.close_all()
