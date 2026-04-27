import json
import uuid

import pytest

from src.agentcy.agent_runtime.services import supervisor_agent
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


@pytest.mark.asyncio
async def test_supervisor_agent_writes_task_specs(monkeypatch):
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"supervisor_{uuid.uuid4()}"
    pipeline_id = f"pipeline-{uuid.uuid4()}"
    run_id = f"run-{uuid.uuid4()}"
    message = {
        "username": username,
        "pipeline_id": pipeline_id,
        "pipeline_run_id": run_id,
        "data": {"task_description": "supervise this"},
    }

    response = json.dumps(
        {
            "task_specs": [
                {
                    "task_id": "llm-task",
                    "description": "supervise this",
                    "required_capabilities": ["plan"],
                    "tags": ["core"],
                    "risk_level": "low",
                    "requires_human_approval": False,
                    "task_type": "planning",
                    "priority": 2,
                    "stimulus": 0.4,
                    "reward": 1.2,
                }
            ]
        }
    )

    try:
        monkeypatch.setenv("LLM_SUPERVISOR_PROVIDER", "openai")
        monkeypatch.setattr(supervisor_agent, "LLM_Connector", _fake_llm(response))
        result = await supervisor_agent.run(rm, run_id, "supervisor_agent", None, message)
        assert result["created"] == 1

        specs = store.list_task_specs(username=username)
        assert specs
        assert specs[0]["description"] == "supervise this"
        assert specs[0]["metadata"]["task_type"] == "planning"
    finally:
        pool.close_all()
