import uuid

import pytest

from src.agentcy.agent_runtime.services.llm_strategist_loop import apply_suggestion_decision
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft, PlanSuggestion


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_apply_suggestion_updates_plan_revision(monkeypatch):
    monkeypatch.setenv("SEMANTIC_RDF_EXPORT", "0")
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"revision_{uuid.uuid4().hex[:6]}"
    pipeline_id = f"pipe-{uuid.uuid4().hex[:6]}"
    plan_id = str(uuid.uuid4())

    graph_spec = {
        "tasks": [
            {
                "task_id": "t1",
                "assigned_agent": "agent-a",
                "required_capabilities": ["plan"],
                "tags": ["core"],
                "task_type": "plan",
            }
        ],
        "edges": [],
    }

    try:
        draft = PlanDraft(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            revision=1,
            graph_spec=graph_spec,
            is_valid=True,
        )
        store.save_plan_draft(username=username, draft=draft)

        suggestion = PlanSuggestion(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            base_revision=1,
            candidate_revision=2,
            delta={"task_overrides": {"t1": {"tags": ["updated"]}}},
            graph_spec=graph_spec,
        )
        store.save_plan_suggestion(username=username, suggestion=suggestion)

        updated = await apply_suggestion_decision(
            rm,
            username=username,
            suggestion_id=suggestion.suggestion_id,
            approved=True,
            approver="tester",
        )
        assert updated is not None
        assert updated.revision == 2
        assert updated.graph_spec["tasks"][0]["tags"] == ["updated"]
    finally:
        pool.close_all()
