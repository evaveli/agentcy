import uuid

import pytest

from src.agentcy.agent_runtime.services import system_executor
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
async def test_system_executor_writes_execution_report():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"system_executor_{uuid.uuid4()}"
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
                "edges": [],
            },
        )
        store.save_plan_draft(username=username, draft=draft)

        message = {
            "username": username,
            "pipeline_id": pipeline_id,
            "plan_id": plan_id,
            "pipeline_run_id": "run-1",
            "data": {"fail_task_ids": ["t2"]},
        }
        result = await system_executor.run(rm, "run-1", "system_executor", None, message)
        assert result["execution_report_id"]

        reports = store.list_execution_reports(username=username, plan_id=plan_id)
        assert reports
    finally:
        pool.close_all()
