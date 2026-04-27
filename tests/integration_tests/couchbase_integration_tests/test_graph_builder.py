import uuid

import pytest

from src.agentcy.agent_runtime.services.graph_builder import build_plan_draft
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.multi_agent_pipeline import BlueprintBid, TaskSpec


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_graph_builder_creates_plan_draft():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"graph_builder_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    root_task = f"task-{uuid.uuid4()}"
    child_task = f"task-{uuid.uuid4()}"

    try:
        store.upsert_task_spec(
            username=username,
            spec=TaskSpec(
                task_id=root_task,
                username=username,
                description="root task",
                required_capabilities=["plan"],
            ),
        )
        store.upsert_task_spec(
            username=username,
            spec=TaskSpec(
                task_id=child_task,
                username=username,
                description="child task",
                required_capabilities=["execute"],
                metadata={"depends_on": [root_task]},
            ),
        )
        store.add_bid(
            username=username,
            bid=BlueprintBid(task_id=root_task, bidder_id="agent-low", bid_score=0.4),
        )
        store.add_bid(
            username=username,
            bid=BlueprintBid(task_id=root_task, bidder_id="agent-high", bid_score=0.9),
        )
        store.add_bid(
            username=username,
            bid=BlueprintBid(task_id=child_task, bidder_id="agent-child", bid_score=0.7),
        )

        draft = await build_plan_draft(rm, username=username, pipeline_id=pipeline_id)
        saved = store.get_plan_draft(username=username, plan_id=draft.plan_id)
        assert saved is not None

        tasks = {task["task_id"]: task for task in saved["graph_spec"]["tasks"]}
        assert tasks[root_task]["assigned_agent"] == "agent-high"
        assert tasks[root_task].get("award_id")
        assert {"from": root_task, "to": child_task} in saved["graph_spec"]["edges"]

        awards = store.list_contract_awards(username=username, task_id=root_task)
        assert awards
    finally:
        pool.close_all()
