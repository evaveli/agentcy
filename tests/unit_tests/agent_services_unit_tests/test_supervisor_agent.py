import json

import pytest

from src.agentcy.agent_runtime.services import supervisor_agent


class _FakeStore:
    def __init__(self) -> None:
        self.saved = []

    def upsert_task_spec(self, *, username, spec):
        self.saved.append((username, spec))

    def list_task_specs(self, *, username):
        return [], 0


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store


def _fake_llm(responses):
    class _FakeLLM:
        def __init__(self, provider):
            self.provider = provider
            self._responses = list(responses)
            self._idx = 0

        async def start(self):
            return None

        async def stop(self):
            return None

        async def handle_incoming_requests(self, requests):
            response = self._responses[min(self._idx, len(self._responses) - 1)]
            self._idx += 1
            return {request_id: response for request_id, _ in requests}

    return _FakeLLM


@pytest.mark.asyncio
async def test_supervisor_agent_creates_task_specs(monkeypatch):
    store = _FakeStore()
    rm = _FakeRM(store)
    message = {
        "username": "alice",
        "pipeline_id": "pipe-1",
        "pipeline_run_id": "run-1",
        "data": {"task_description": "plan something"},
    }

    response = json.dumps(
        {
            "task_specs": [
                {
                    "task_id": "llm-1",
                    "description": "plan something",
                    "required_capabilities": ["plan"],
                    "tags": ["demo"],
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

    monkeypatch.setenv("LLM_SUPERVISOR_PROVIDER", "openai")
    monkeypatch.setattr(supervisor_agent, "LLM_Connector", _fake_llm([response]))

    result = await supervisor_agent.run(rm, "run-1", "supervisor_agent", None, message)
    assert result["created"] == 1
    assert result["task_ids"] == ["llm-1"]
    assert result["llm_attempts"] == 1

    _, spec = store.saved[0]
    assert spec.description == "plan something"
    assert spec.metadata["priority"] == 2
    assert spec.metadata["task_type"] == "planning"


@pytest.mark.asyncio
async def test_supervisor_agent_retries_on_invalid_response(monkeypatch):
    store = _FakeStore()
    rm = _FakeRM(store)
    message = {
        "username": "alice",
        "pipeline_id": "pipe-2",
        "pipeline_run_id": "run-2",
        "data": {"task_description": "retry"},
    }

    bad = "{not-json"
    good = json.dumps(
        {
            "task_specs": [
                {
                    "task_id": "llm-2",
                    "description": "retry",
                    "required_capabilities": ["plan"],
                    "tags": [],
                    "risk_level": "medium",
                    "requires_human_approval": False,
                    "task_type": "planning",
                    "priority": 3,
                    "stimulus": 0.5,
                    "reward": 2.1,
                }
            ]
        }
    )

    monkeypatch.setenv("LLM_SUPERVISOR_PROVIDER", "openai")
    monkeypatch.setattr(supervisor_agent, "LLM_Connector", _fake_llm([bad, good]))

    result = await supervisor_agent.run(rm, "run-2", "supervisor_agent", None, message)
    assert result["created"] == 1
    assert result["llm_attempts"] == 2
    assert store.saved
