import pytest

from src.agentcy.agent_runtime.services.blueprint_bidder import run
from src.agentcy.pydantic_models.multi_agent_pipeline import TaskSpec


class _FakeStore:
    def __init__(self) -> None:
        self.specs = []
        self.bids = []
        self.cfps = []

    def list_task_specs(self, *, username):
        items = list(self.specs)
        return items, len(items)

    def list_cfps(self, *, username, task_id=None, status=None):
        items = list(self.cfps)
        if task_id:
            items = [cfp for cfp in items if cfp.get("task_id") == task_id]
        if status:
            items = [cfp for cfp in items if cfp.get("status") == status]
        return items, len(items)

    def add_cfp(self, *, username, cfp):
        doc = cfp.model_dump(mode="json")
        self.cfps.append(doc)
        return doc.get("cfp_id")

    def add_bid(self, *, username, bid):
        if hasattr(bid, "model_dump"):
            doc = bid.model_dump(mode="json")
        else:
            doc = dict(bid)
        self.bids.append(doc)
        return "bid-1"

    def list_bids(self, *, username, task_id=None, bidder_id=None):
        items = list(self.bids)
        if task_id:
            items = [bid for bid in items if bid.get("task_id") == task_id]
        if bidder_id:
            items = [bid for bid in items if bid.get("bidder_id") == bidder_id]
        return items, len(items)


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
async def test_blueprint_bidder_creates_bids():
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

    result = await run(rm, "run-1", "blueprint_bidder", None, message)
    assert result["bids_created"] == 1
    assert result["cfps_created"] == 1
    assert store.bids[0]["bidder_id"] == "agent-1"
    assert store.bids[0]["cfp_id"] is not None


@pytest.mark.asyncio
async def test_blueprint_bidder_falls_back_to_system():
    store = _FakeStore()
    store.specs.append(
        TaskSpec(
            task_id="task-2",
            username="bob",
            description="execute",
            required_capabilities=["execute"],
        ).model_dump(mode="json")
    )
    rm = _FakeRM(store, registry=None)
    message = {"username": "bob", "data": {}}

    result = await run(rm, "run-2", "blueprint_bidder", None, message)
    assert result["bids_created"] == 1
    assert result["cfps_created"] == 1
    assert store.bids[0]["bidder_id"] == "system"


@pytest.mark.asyncio
async def test_blueprint_bidder_respects_min_score(monkeypatch):
    monkeypatch.setenv("CNP_MIN_BID_SCORE", "0.9")
    store = _FakeStore()
    store.specs.append(
        TaskSpec(
            task_id="task-3",
            username="mira",
            description="plan",
            required_capabilities=["plan"],
        ).model_dump(mode="json")
    )
    registry = _FakeRegistry(
        [{"agent_id": "agent-2", "capabilities": ["plan"], "status": "idle"}]
    )
    rm = _FakeRM(store, registry)
    message = {"username": "mira", "data": {}}

    result = await run(rm, "run-3", "blueprint_bidder", None, message)
    assert result["bids_created"] == 0
    assert store.bids == []


@pytest.mark.asyncio
async def test_blueprint_bidder_dedupes_existing_bids(monkeypatch):
    monkeypatch.delenv("CNP_BIDDER_MODE", raising=False)
    store = _FakeStore()
    task = TaskSpec(
        task_id="task-4",
        username="ava",
        description="plan",
        required_capabilities=["plan"],
    )
    store.specs.append(task.model_dump(mode="json"))
    store.cfps.append(
        {
            "cfp_id": "cfp-1",
            "task_id": task.task_id,
            "status": "open",
            "round": 1,
        }
    )
    store.bids.append(
        {
            "task_id": task.task_id,
            "bidder_id": "agent-3",
            "bid_score": 0.5,
            "cfp_id": "cfp-1",
        }
    )
    registry = _FakeRegistry(
        [{"agent_id": "agent-3", "capabilities": ["plan"], "status": "idle"}]
    )
    rm = _FakeRM(store, registry)
    message = {"username": "ava", "data": {}}

    result = await run(rm, "run-4", "blueprint_bidder", None, message)
    assert result["bids_created"] == 0
    assert len(store.bids) == 1
