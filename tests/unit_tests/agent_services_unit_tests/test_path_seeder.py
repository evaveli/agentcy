import pytest

from src.agentcy.agent_runtime.services.path_seeder import run
from src.agentcy.pydantic_models.multi_agent_pipeline import TaskSpec


class _FakeStore:
    def __init__(self) -> None:
        self.specs = []
        self.markers = []

    def list_task_specs(self, *, username):
        items = list(self.specs)
        return items, len(items)

    def add_affordance_marker(self, *, username, marker, ttl_seconds=None):
        self.markers.append((username, marker))
        return marker.marker_id


class _FakeRegistry:
    def __init__(self, agents):
        self._agents = agents

    def list(self, *, username):
        return list(self._agents)


class _FakeRM:
    def __init__(self, store, registry):
        self.graph_marker_store = store
        self.agent_registry_store = registry


@pytest.mark.asyncio
async def test_path_seeder_creates_marker():
    store = _FakeStore()
    store.specs.append(
        TaskSpec(
            task_id="task-1",
            username="alice",
            description="plan",
            required_capabilities=["plan"],
        ).model_dump(mode="json")
    )
    registry = _FakeRegistry(
        [{"agent_id": "agent-1", "capabilities": ["plan"], "status": "idle"}]
    )
    rm = _FakeRM(store, registry)
    message = {"username": "alice", "data": {}}

    result = await run(rm, "run-1", "path_seeder", None, message)
    assert result["markers_created"] == 1
    _, marker = store.markers[0]
    assert marker.agent_id == "agent-1"
    assert 0.0 <= marker.intensity <= 1.0
