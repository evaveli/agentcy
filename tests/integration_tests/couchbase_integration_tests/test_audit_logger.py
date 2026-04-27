import uuid

import pytest

from src.agentcy.agent_runtime.services import audit_logger
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.multi_agent_pipeline import ExecutionReport, PlanDraft


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_audit_logger_writes_log():
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"audit_logger_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    plan_id = str(uuid.uuid4())

    try:
        draft = PlanDraft(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            graph_spec={"tasks": [], "edges": []},
            is_valid=True,
        )
        store.save_plan_draft(username=username, draft=draft)
        store.save_execution_report(username=username, report=ExecutionReport(plan_id=plan_id, success_rate=1.0))

        message = {
            "username": username,
            "pipeline_id": pipeline_id,
            "plan_id": plan_id,
            "pipeline_run_id": "run-1",
            "data": {},
        }
        result = await audit_logger.run(rm, "run-1", "audit_logger", None, message)
        assert result["logged"] is True

        audits = store.list_audit_logs(username=username, pipeline_run_id="run-1")
        assert audits
    finally:
        pool.close_all()
