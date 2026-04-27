import json
import uuid

import pytest

from src.agentcy.agent_runtime.services import ethics_checker
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
async def test_ethics_checker_flags_missing_human_approval(monkeypatch):
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"ethics_checker_{uuid.uuid4()}"
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
                requires_human_approval=True,
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

        message = {"username": username, "pipeline_id": pipeline_id, "plan_id": plan_id, "data": {}}
        response = json.dumps({"approved": False, "issues": ["policy_violation"]})
        monkeypatch.setenv("LLM_ETHICS_PROVIDER", "openai")
        monkeypatch.setattr(ethics_checker, "LLM_Connector", _fake_llm(response))
        result = await ethics_checker.run(rm, "run-1", "ethics_checker", None, message)
        assert result["approved"] is False

        checks = store.list_ethics_checks(username=username, plan_id=plan_id)
        assert checks
    finally:
        pool.close_all()


def _fake_llm(response_text: str):
    class _FakeLLM:
        def __init__(self, provider):
            self.provider = provider

        async def start(self):
            return None

        async def stop(self):
            return None

        async def handle_incoming_requests(self, requests):
            return {request_id: response_text for request_id, _ in requests}

    return _FakeLLM
