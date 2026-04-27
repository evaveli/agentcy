import uuid

import pytest

from src.agentcy.agent_runtime.services import failure_escalation
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.multi_agent_pipeline import ExecutionOutcome, ExecutionReport, PlanDraft


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_failure_escalation_records_notice():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"failure_escalation_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    plan_id = str(uuid.uuid4())

    try:
        draft = PlanDraft(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            graph_spec={"tasks": [], "edges": []},
        )
        store.save_plan_draft(username=username, draft=draft)

        report = ExecutionReport(
            plan_id=plan_id,
            outcomes=[ExecutionOutcome(task_id="t1", success=False)],
            success_rate=0.0,
        )
        store.save_execution_report(username=username, report=report)

        message = {
            "username": username,
            "pipeline_id": pipeline_id,
            "plan_id": plan_id,
            "pipeline_run_id": "run-1",
            "data": {"attempts": 2, "max_retries": 2},
        }
        result = await failure_escalation.run(rm, "run-1", "failure_escalation", None, message)
        assert result["escalated"] is True

        notices = store.list_escalation_notices(username=username, pipeline_run_id="run-1")
        assert notices
    finally:
        pool.close_all()
