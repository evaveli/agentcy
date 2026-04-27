import pytest

from src.agentcy.agent_runtime.services.pheromone_engine import run
from src.agentcy.pydantic_models.multi_agent_pipeline import AffordanceMarker, TaskSpec


class _FakeStore:
    def __init__(self) -> None:
        self.markers = []
        self.specs = []

    def list_task_specs(self, *, username):
        items = list(self.specs)
        return items, len(items)

    def list_affordance_markers(self, *, username, task_id=None, agent_id=None):
        items = list(self.markers)
        if task_id:
            items = [item for item in items if item.get("task_id") == task_id]
        if agent_id:
            items = [item for item in items if item.get("agent_id") == agent_id]
        return items, len(items)

    def add_affordance_marker(self, *, username, marker, ttl_seconds=None):
        doc = marker.model_dump(mode="json")
        for idx, existing in enumerate(self.markers):
            if existing.get("marker_id") == marker.marker_id:
                self.markers[idx] = doc
                return marker.marker_id
        self.markers.append(doc)
        return marker.marker_id


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store


@pytest.mark.asyncio
async def test_pheromone_engine_decays_markers(monkeypatch):
    store = _FakeStore()
    marker = AffordanceMarker(
        task_id="task-1",
        agent_id="agent-1",
        intensity=1.0,
    )
    store.markers.append(marker.model_dump(mode="json"))
    rm = _FakeRM(store)
    monkeypatch.setenv("PHEROMONE_DECAY_FACTOR", "0.5")

    result = await run(
        rm,
        "run-1",
        "pheromone_engine",
        None,
        {"username": "alice", "data": {}},
    )

    assert result["mode"] == "decay"
    assert store.markers[0]["intensity"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_pheromone_engine_applies_feedback(monkeypatch):
    store = _FakeStore()
    store.specs.append(
        TaskSpec(
            task_id="task-2",
            username="alice",
            description="plan",
            required_capabilities=["plan"],
        ).model_dump(mode="json")
    )
    marker = AffordanceMarker(
        task_id="task-2",
        agent_id="agent-2",
        intensity=0.4,
    )
    store.markers.append(marker.model_dump(mode="json"))
    rm = _FakeRM(store)
    monkeypatch.setenv("PHEROMONE_SUCCESS_BONUS", "0.2")

    payload = {
        "feedback": [{"task_id": "task-2", "agent_id": "agent-2", "success": True}]
    }
    result = await run(
        rm,
        "run-2",
        "pheromone_engine",
        None,
        {"username": "alice", "data": payload},
    )

    assert result["mode"] == "feedback"
    assert store.markers[0]["intensity"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_pheromone_engine_reads_nested_feedback(monkeypatch):
    store = _FakeStore()
    store.specs.append(
        TaskSpec(
            task_id="task-3",
            username="alice",
            description="execute",
            required_capabilities=["execute"],
        ).model_dump(mode="json")
    )
    marker = AffordanceMarker(
        task_id="task-3",
        agent_id="agent-3",
        intensity=0.2,
    )
    store.markers.append(marker.model_dump(mode="json"))
    rm = _FakeRM(store)
    monkeypatch.setenv("PHEROMONE_SUCCESS_BONUS", "0.3")

    nested_payload = {
        "payload": {
            "payload": {
                "result": {
                    "task_outcomes": [
                        {"task_id": "task-3", "agent_id": "agent-3", "success": True}
                    ]
                }
            }
        }
    }
    result = await run(
        rm,
        "run-3",
        "pheromone_engine",
        None,
        {"username": "alice", "data": nested_payload},
    )

    assert result["mode"] == "feedback"
    assert store.markers[0]["intensity"] == pytest.approx(0.5)
