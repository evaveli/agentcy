"""Tests for the CNP Manager consumer and its Pydantic models."""
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from agentcy.pydantic_models.commands import (
    CFPBroadcastEvent,
    CNPCycleCompletedEvent,
    CNPCycleStartedEvent,
    CNPRoundCompletedEvent,
    RunCNPCycleCommand,
    SchemaVersion,
)
from agentcy.pydantic_models.multi_agent_pipeline import (
    CNPCycleState,
    CNPCycleStatus,
    PlanDraft,
)


# ---------------------------------------------------------------------------
# Model round-trip tests
# ---------------------------------------------------------------------------
def test_run_cnp_cycle_command_roundtrip():
    cmd = RunCNPCycleCommand(
        username="alice",
        pipeline_id="pipe-1",
        task_ids=["t1", "t2"],
        max_rounds=5,
        bid_timeout_seconds=60,
    )
    data = cmd.model_dump_json()
    restored = RunCNPCycleCommand.model_validate_json(data)

    assert restored.username == "alice"
    assert restored.pipeline_id == "pipe-1"
    assert restored.task_ids == ["t1", "t2"]
    assert restored.max_rounds == 5
    assert restored.bid_timeout_seconds == 60
    assert restored.request_id  # auto-generated


def test_run_cnp_cycle_command_defaults():
    cmd = RunCNPCycleCommand(
        username="bob",
        pipeline_id="pipe-2",
    )
    assert cmd.task_ids == []
    assert cmd.max_rounds is None
    assert cmd.bid_timeout_seconds is None
    assert cmd.pipeline_run_id is None
    assert cmd.request_id  # auto uuid4


def test_cnp_cycle_state_model():
    state = CNPCycleState(
        username="alice",
        pipeline_id="pipe-1",
    )
    assert state.status == CNPCycleStatus.STARTED
    assert state.cycle_id  # auto uuid4
    assert state.current_round == 0
    assert state.max_rounds == 3
    assert state.round_history == []
    assert state.error is None
    assert state.completed_at is None


def test_cnp_cycle_started_event_roundtrip():
    evt = CNPCycleStartedEvent(
        username="alice",
        pipeline_id="pipe-1",
        cycle_id="cycle-abc",
        task_count=3,
        max_rounds=5,
        timestamp=datetime.now(timezone.utc),
    )
    data = evt.model_dump_json()
    restored = CNPCycleStartedEvent.model_validate_json(data)
    assert restored.cycle_id == "cycle-abc"
    assert restored.task_count == 3
    assert restored.max_rounds == 5


def test_cnp_cycle_completed_event_roundtrip():
    evt = CNPCycleCompletedEvent(
        username="alice",
        pipeline_id="pipe-1",
        cycle_id="cycle-abc",
        plan_id="plan-1",
        total_rounds=2,
        total_bids=5,
        tasks_awarded=3,
        tasks_unawarded=0,
        timestamp=datetime.now(timezone.utc),
    )
    data = evt.model_dump_json()
    restored = CNPCycleCompletedEvent.model_validate_json(data)
    assert restored.total_rounds == 2
    assert restored.tasks_awarded == 3


# ---------------------------------------------------------------------------
# Store tests (using the same FakePool pattern from graph_store tests)
# ---------------------------------------------------------------------------
from couchbase.exceptions import DocumentNotFoundException


class _FakeResult:
    def __init__(self, value):
        self.content_as = {dict: value}


class _FakeCollection:
    def __init__(self):
        self._data = {}

    def upsert(self, key, value, **_kw):
        self._data[key] = value
        return _FakeResult(value)

    def get(self, key, **_kw):
        if key not in self._data:
            raise DocumentNotFoundException()
        return _FakeResult(self._data[key])


class _FakeCluster:
    def __init__(self, data):
        self._data = data

    def query(self, statement, *args, **kwargs):
        match = re.search(r"LIKE '([^']+)%'", statement)
        prefix = match.group(1) if match else ""
        matched = [
            (key, doc) for key, doc in self._data.items() if key.startswith(prefix)
        ]
        # Handle COUNT(*) queries
        if "COUNT(*)" in statement.upper():
            return [{"total": len(matched)}]
        rows = []
        for key, doc in matched:
            row = {"id": key}
            row.update(doc)
            rows.append(row)
        return rows


class _FakeBundle:
    def __init__(self, collection):
        self.cluster = _FakeCluster(collection._data)
        self._collection = collection

    def collection(self, _logical):
        return self._collection


class _FakePool:
    def __init__(self):
        self._collection = _FakeCollection()

    @contextmanager
    def collections(self, *_keys, **_kw):
        yield self._collection

    def acquire(self, *_a, **_kw):
        return _FakeBundle(self._collection)

    def release(self, _bundle):
        return None


def test_save_and_get_cnp_cycle():
    from agentcy.orchestrator_core.stores.graph_marker_store import GraphMarkerStore

    store = GraphMarkerStore(_FakePool())
    cycle = CNPCycleState(
        username="alice",
        pipeline_id="pipe-1",
        status=CNPCycleStatus.BIDDING,
        task_ids=["t1", "t2"],
    )
    key = store.save_cnp_cycle(username="alice", cycle=cycle)
    assert key == f"cnp_cycle::alice::{cycle.cycle_id}"

    doc = store.get_cnp_cycle(username="alice", cycle_id=cycle.cycle_id)
    assert doc is not None
    assert doc["pipeline_id"] == "pipe-1"
    assert doc["status"] == "bidding"
    assert doc["task_ids"] == ["t1", "t2"]


def test_get_cnp_cycle_not_found():
    from agentcy.orchestrator_core.stores.graph_marker_store import GraphMarkerStore

    store = GraphMarkerStore(_FakePool())
    doc = store.get_cnp_cycle(username="alice", cycle_id="nonexistent")
    assert doc is None


def test_list_cnp_cycles():
    from agentcy.orchestrator_core.stores.graph_marker_store import GraphMarkerStore

    store = GraphMarkerStore(_FakePool())

    for i in range(3):
        cycle = CNPCycleState(
            username="alice",
            pipeline_id=f"pipe-{i}",
            status=CNPCycleStatus.COMPLETED,
        )
        store.save_cnp_cycle(username="alice", cycle=cycle)

    items, total = store.list_cnp_cycles(username="alice")
    assert total == 3
    assert len(items) == 3


def test_list_cnp_cycles_filter_by_status():
    from agentcy.orchestrator_core.stores.graph_marker_store import GraphMarkerStore

    store = GraphMarkerStore(_FakePool())

    c1 = CNPCycleState(username="alice", pipeline_id="pipe-1", status=CNPCycleStatus.COMPLETED)
    c2 = CNPCycleState(username="alice", pipeline_id="pipe-2", status=CNPCycleStatus.FAILED)
    store.save_cnp_cycle(username="alice", cycle=c1)
    store.save_cnp_cycle(username="alice", cycle=c2)

    items, total = store.list_cnp_cycles(username="alice", status="completed")
    assert total == 1
    assert items[0]["status"] == "completed"


def test_list_cnp_cycles_filter_by_pipeline_id():
    from agentcy.orchestrator_core.stores.graph_marker_store import GraphMarkerStore

    store = GraphMarkerStore(_FakePool())

    c1 = CNPCycleState(username="alice", pipeline_id="pipe-A")
    c2 = CNPCycleState(username="alice", pipeline_id="pipe-B")
    store.save_cnp_cycle(username="alice", cycle=c1)
    store.save_cnp_cycle(username="alice", cycle=c2)

    items, total = store.list_cnp_cycles(username="alice", pipeline_id="pipe-A")
    assert total == 1
    assert items[0]["pipeline_id"] == "pipe-A"


# ---------------------------------------------------------------------------
# Handler tests (mock store + mock bidder/builder/seeder)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handle_no_store():
    from agentcy.orchestrator_core.consumers.cnp_manager import handle_run_cnp_cycle

    cmd = RunCNPCycleCommand(username="alice", pipeline_id="pipe-1")
    rm = MagicMock()
    rm.graph_marker_store = None

    result = await handle_run_cnp_cycle(cmd, rm, AsyncMock())
    assert result is None


@pytest.mark.asyncio
async def test_handle_happy_path():
    from agentcy.orchestrator_core.consumers.cnp_manager import handle_run_cnp_cycle

    cmd = RunCNPCycleCommand(
        username="alice",
        pipeline_id="pipe-1",
        task_ids=["t1"],
    )

    store = MagicMock()
    store.save_cnp_cycle = MagicMock()
    store.list_task_specs = MagicMock(return_value=([], 0))
    store.list_bids = MagicMock(return_value=([{"task_id": "t1", "bid_score": 0.9}], 1))

    rm = MagicMock()
    rm.graph_marker_store = store

    publish = AsyncMock()

    plan_draft = PlanDraft(
        plan_id="plan-abc",
        username="alice",
        pipeline_id="pipe-1",
        graph_spec={"assignments": {"t1": "agent-a"}},
    )

    with patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.blueprint_bidder"
    ) as mock_bidder, patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.build_plan_draft",
        new_callable=AsyncMock,
        return_value=plan_draft,
    ), patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.path_seeder"
    ) as mock_seeder:
        mock_bidder.run = AsyncMock(return_value={
            "bids_created": 1,
            "cfps_created": 1,
            "cfp_ids": ["cfp-1"],
            "required_capabilities": ["plan"],
            "stimulus": 0.5,
        })
        mock_seeder.run = AsyncMock(return_value={})

        result = await handle_run_cnp_cycle(cmd, rm, publish)

    assert result is not None
    assert result.status == CNPCycleStatus.COMPLETED
    assert result.plan_id == "plan-abc"
    assert result.total_bids == 1

    # Check events: started + broadcast + round + completed = 4
    assert publish.await_count == 4

    # Verify event types via routing keys
    routing_keys = [call.args[1] for call in publish.call_args_list]
    assert "events.cnp_cycle_started" in routing_keys
    assert "events.cfp_broadcast" in routing_keys
    assert "events.cnp_round_completed" in routing_keys
    assert "events.cnp_cycle_completed" in routing_keys


@pytest.mark.asyncio
async def test_handle_multi_round_escalation():
    """First round covers 0/2 tasks, second round covers both."""
    from agentcy.orchestrator_core.consumers.cnp_manager import handle_run_cnp_cycle

    cmd = RunCNPCycleCommand(
        username="alice",
        pipeline_id="pipe-1",
        task_ids=["t1", "t2"],
        max_rounds=3,
    )

    round_counter = {"n": 0}

    def _list_bids(**kwargs):
        round_counter["n"] += 1
        if round_counter["n"] == 1:
            return ([{"task_id": "t1", "bid_score": 0.8}], 1)
        return ([
            {"task_id": "t1", "bid_score": 0.8},
            {"task_id": "t2", "bid_score": 0.7},
        ], 2)

    store = MagicMock()
    store.save_cnp_cycle = MagicMock()
    store.list_task_specs = MagicMock(return_value=([], 0))
    store.list_bids = MagicMock(side_effect=_list_bids)

    rm = MagicMock()
    rm.graph_marker_store = store

    publish = AsyncMock()

    plan_draft = PlanDraft(
        plan_id="plan-multi",
        username="alice",
        pipeline_id="pipe-1",
        graph_spec={"assignments": {"t1": "a1", "t2": "a2"}},
    )

    with patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.blueprint_bidder"
    ) as mock_bidder, patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.build_plan_draft",
        new_callable=AsyncMock,
        return_value=plan_draft,
    ), patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.path_seeder"
    ) as mock_seeder:
        mock_bidder.run = AsyncMock(return_value={
            "bids_created": 1,
            "cfps_created": 1,
            "stimulus": 0.5,
        })
        mock_seeder.run = AsyncMock(return_value={})

        result = await handle_run_cnp_cycle(cmd, rm, publish)

    assert result is not None
    assert result.status == CNPCycleStatus.COMPLETED
    assert result.current_round == 2  # stopped after round 2
    assert len(result.round_history) == 2


@pytest.mark.asyncio
async def test_handle_bidder_failure():
    """Bidder throws on a round — round continues with 0 bids."""
    from agentcy.orchestrator_core.consumers.cnp_manager import handle_run_cnp_cycle

    cmd = RunCNPCycleCommand(
        username="alice",
        pipeline_id="pipe-1",
        task_ids=["t1"],
        max_rounds=1,
    )

    store = MagicMock()
    store.save_cnp_cycle = MagicMock()
    store.list_task_specs = MagicMock(return_value=([], 0))
    store.list_bids = MagicMock(return_value=([{"task_id": "t1"}], 1))

    rm = MagicMock()
    rm.graph_marker_store = store

    publish = AsyncMock()

    plan_draft = PlanDraft(
        plan_id="plan-x",
        username="alice",
        pipeline_id="pipe-1",
        graph_spec={"assignments": {"t1": "a1"}},
    )

    with patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.blueprint_bidder"
    ) as mock_bidder, patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.build_plan_draft",
        new_callable=AsyncMock,
        return_value=plan_draft,
    ), patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.path_seeder"
    ) as mock_seeder:
        mock_bidder.run = AsyncMock(side_effect=RuntimeError("bidder exploded"))
        mock_seeder.run = AsyncMock(return_value={})

        result = await handle_run_cnp_cycle(cmd, rm, publish)

    # Cycle should still complete (bidder failure is non-fatal per round)
    assert result is not None
    assert result.status == CNPCycleStatus.COMPLETED
    assert result.round_history[0]["bids_collected"] == 0


@pytest.mark.asyncio
async def test_handle_graph_builder_failure():
    """build_plan_draft throws → cycle status=FAILED."""
    from agentcy.orchestrator_core.consumers.cnp_manager import handle_run_cnp_cycle

    cmd = RunCNPCycleCommand(
        username="alice",
        pipeline_id="pipe-1",
        task_ids=["t1"],
        max_rounds=1,
    )

    store = MagicMock()
    store.save_cnp_cycle = MagicMock()
    store.list_task_specs = MagicMock(return_value=([], 0))
    store.list_bids = MagicMock(return_value=([{"task_id": "t1"}], 1))

    rm = MagicMock()
    rm.graph_marker_store = store

    publish = AsyncMock()

    with patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.blueprint_bidder"
    ) as mock_bidder, patch(
        "agentcy.orchestrator_core.consumers.cnp_manager.build_plan_draft",
        new_callable=AsyncMock,
        side_effect=RuntimeError("graph builder exploded"),
    ):
        mock_bidder.run = AsyncMock(return_value={"bids_created": 1, "stimulus": 0.5})

        result = await handle_run_cnp_cycle(cmd, rm, publish)

    assert result is not None
    assert result.status == CNPCycleStatus.FAILED
    assert result.error == "graph_builder_failed"


# ---------------------------------------------------------------------------
# Consumer feature flag test
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_feature_flag_off():
    """Consumer exits without declaring queue when CNP_MANAGER_ENABLE=0."""
    from agentcy.orchestrator_core.consumers.cnp_manager import cnp_manager_consumer

    rm = MagicMock()
    rm.rabbit_mgr = MagicMock()

    with patch.dict(os.environ, {"CNP_MANAGER_ENABLE": "0"}):
        await cnp_manager_consumer(rm)

    # rabbit_mgr.get_channel should NOT have been called
    rm.rabbit_mgr.get_channel.assert_not_called()
