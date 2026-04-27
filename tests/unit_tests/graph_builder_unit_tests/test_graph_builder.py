import pytest

from src.agentcy.agent_runtime.services.graph_builder import (
    _best_bids,
    build_plan_draft,
    run,
)
from src.agentcy.pydantic_models.multi_agent_pipeline import TaskSpec


class _FakeStore:
    def __init__(self):
        self.specs_by_user = {}
        self.bids_by_user = {}
        self.cfps_by_user = {}
        self.saved = []
        self.awards = []
        self.reservations = []

    def list_task_specs(self, *, username):
        items = list(self.specs_by_user.get(username, []))
        return items, len(items)

    def list_bids(self, *, username):
        items = list(self.bids_by_user.get(username, []))
        return items, len(items)

    def list_cfps(self, *, username, task_id=None, status=None):
        items = list(self.cfps_by_user.get(username, []))
        if task_id:
            items = [item for item in items if item.get("task_id") == task_id]
        if status:
            items = [item for item in items if item.get("status") == status]
        return items, len(items)

    def add_cfp(self, *, username, cfp):
        doc = cfp.model_dump(mode="json")
        self.cfps_by_user.setdefault(username, []).append(doc)
        return doc.get("cfp_id")

    def add_contract_award(self, *, username, award):
        self.awards.append((username, award))
        return award.award_id

    def add_reservation_marker(self, *, username, marker, ttl_seconds=None):
        self.reservations.append((username, marker))
        return marker.marker_id

    def save_plan_draft(self, *, username, draft):
        self.saved.append((username, draft))

    def save_plan_revision(self, *, username, revision):
        pass

    def save_evaluation_sequence(self, *, username, seq):
        pass


class _FakeRM:
    def __init__(self, store, registry=None, service_store=None):
        self.graph_marker_store = store
        self.agent_registry_store = registry
        self.service_store = service_store


class _FakeRegistry:
    def __init__(self, entries):
        self._entries = entries

    def list(self, *, username):
        return list(self._entries.get(username, []))


class _FakeServiceStore:
    def __init__(self, entries):
        self._entries = entries

    def list_all(self, username):
        return list(self._entries.get(username, []))


def test_best_bids_picks_highest_score():
    bids = [
        {"task_id": "task-1", "bidder_id": "agent-a", "bid_score": 0.3, "bid_id": "b1", "cfp_id": "cfp-1"},
        {"task_id": "task-1", "bidder_id": "agent-b", "bid_score": 0.9, "bid_id": "b2", "cfp_id": "cfp-1"},
        {"task_id": "task-2", "bidder_id": "agent-c", "bid_score": 0.5, "bid_id": "b3", "cfp_id": "cfp-2"},
    ]
    best = _best_bids(bids)
    assert best["task-1"]["bidder_id"] == "agent-b"
    assert best["task-2"]["bidder_id"] == "agent-c"
    filtered = _best_bids(bids, allowed_cfp_ids={"cfp-1"})
    assert "task-2" not in filtered


@pytest.mark.asyncio
async def test_build_plan_draft_selects_bids_and_edges():
    store = _FakeStore()
    rm = _FakeRM(store)
    username = "alice"
    pipeline_id = "pipeline-1"

    spec_root = TaskSpec(
        task_id="task-root",
        username=username,
        description="root task",
        required_capabilities=["plan"],
        tags=["core"],
    )
    spec_child = TaskSpec(
        task_id="task-child",
        username=username,
        description="child task",
        required_capabilities=["execute"],
        tags=["core"],
        metadata={"depends_on": ["task-root"]},
    )
    store.specs_by_user[username] = [
        spec_root.model_dump(mode="json"),
        spec_child.model_dump(mode="json"),
    ]
    store.bids_by_user[username] = [
        {"task_id": "task-root", "bidder_id": "agent-low", "bid_score": 0.4, "bid_id": "bid-1", "cfp_id": "cfp-1"},
        {"task_id": "task-root", "bidder_id": "agent-high", "bid_score": 0.95, "bid_id": "bid-2", "cfp_id": "cfp-1"},
        {"task_id": "task-child", "bidder_id": "agent-child", "bid_score": 0.8, "bid_id": "bid-3", "cfp_id": "cfp-1"},
    ]
    store.cfps_by_user[username] = [
        {"cfp_id": "cfp-1", "task_id": "task-root", "status": "open"},
        {"cfp_id": "cfp-1", "task_id": "task-child", "status": "open"},
    ]

    draft = await build_plan_draft(rm, username=username, pipeline_id=pipeline_id)

    assert draft.pipeline_id == pipeline_id
    assert store.saved and store.saved[0][1].plan_id == draft.plan_id

    tasks = {task["task_id"]: task for task in draft.graph_spec["tasks"]}
    assert tasks["task-root"]["assigned_agent"] == "agent-high"
    assert tasks["task-root"]["bid_score"] == 0.95
    assert tasks["task-root"]["award_id"]
    assert {"from": "task-root", "to": "task-child"} in draft.graph_spec["edges"]
    assert store.awards


@pytest.mark.asyncio
async def test_build_plan_draft_warns_without_specs():
    store = _FakeStore()
    rm = _FakeRM(store)
    username = "bob"
    pipeline_id = "pipeline-2"

    draft = await build_plan_draft(rm, username=username, pipeline_id=pipeline_id)
    assert "no_task_specs" in draft.graph_spec.get("warnings", [])


@pytest.mark.asyncio
async def test_build_plan_draft_sets_service_name_from_registry():
    store = _FakeStore()
    username = "alice"
    pipeline_id = "pipeline-3"
    registry = _FakeRegistry(
        {
            username: [
                {"agent_id": "agent-high", "service_name": "graph_builder"},
            ]
        }
    )
    service_store = _FakeServiceStore({username: [{"service_name": "graph_builder"}]})
    rm = _FakeRM(store, registry=registry, service_store=service_store)

    spec = TaskSpec(
        task_id="task-root",
        username=username,
        description="root task",
        required_capabilities=["plan"],
    )
    store.specs_by_user[username] = [spec.model_dump(mode="json")]
    store.bids_by_user[username] = [
        {"task_id": "task-root", "bidder_id": "agent-high", "bid_score": 0.9, "bid_id": "bid-1", "cfp_id": "cfp-1"},
    ]
    store.cfps_by_user[username] = [
        {"cfp_id": "cfp-1", "task_id": "task-root", "status": "open"},
    ]

    draft = await build_plan_draft(rm, username=username, pipeline_id=pipeline_id)
    tasks = {task["task_id"]: task for task in draft.graph_spec["tasks"]}
    assert tasks["task-root"]["service_name"] == "graph_builder"


@pytest.mark.asyncio
async def test_run_requires_pipeline_and_username():
    store = _FakeStore()
    rm = _FakeRM(store)

    with pytest.raises(ValueError):
        await run(rm, "run-1", "graph-builder", None, {"username": "alice"})
