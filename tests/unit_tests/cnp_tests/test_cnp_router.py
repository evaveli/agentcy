"""Tests for expanded CNP router endpoints."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from agentcy.pydantic_models.commands import (
    ReassignTaskCommand,
    TaskReassignedEvent,
)


# ---------------------------------------------------------------------------
# Model round-trip tests
# ---------------------------------------------------------------------------
def test_reassign_task_command_roundtrip():
    cmd = ReassignTaskCommand(
        username="alice",
        pipeline_id="pipe-1",
        pipeline_run_id="run-1",
        plan_id="plan-1",
        task_id="t1",
        failed_agent_id="agent-a",
        reason="task_failure",
    )
    data = cmd.model_dump_json()
    restored = ReassignTaskCommand.model_validate_json(data)

    assert restored.username == "alice"
    assert restored.task_id == "t1"
    assert restored.failed_agent_id == "agent-a"
    assert restored.reason == "task_failure"


def test_reassign_task_command_defaults():
    cmd = ReassignTaskCommand(
        username="bob",
        pipeline_id="pipe-2",
        pipeline_run_id="run-2",
        plan_id="plan-2",
        task_id="t2",
        failed_agent_id="agent-x",
    )
    assert cmd.reason == "task_failure"


def test_task_reassigned_event_roundtrip():
    from datetime import datetime, timezone

    evt = TaskReassignedEvent(
        username="alice",
        pipeline_id="pipe-1",
        pipeline_run_id="run-1",
        plan_id="plan-1",
        task_id="t1",
        failed_agent_id="agent-a",
        new_agent_id="agent-b",
        new_bid_score=0.75,
        sequence_index=1,
        timestamp=datetime.now(timezone.utc),
    )
    data = evt.model_dump_json()
    restored = TaskReassignedEvent.model_validate_json(data)

    assert restored.failed_agent_id == "agent-a"
    assert restored.new_agent_id == "agent-b"
    assert restored.new_bid_score == 0.75
    assert restored.sequence_index == 1


# ---------------------------------------------------------------------------
# Consumer handler tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handle_reassign_task_happy_path():
    from agentcy.orchestrator_core.consumers.cnp_lifecycle import handle_reassign_task

    cmd = ReassignTaskCommand(
        username="alice",
        pipeline_id="pipe-1",
        pipeline_run_id="run-1",
        plan_id="plan-1",
        task_id="t1",
        failed_agent_id="agent-a",
    )

    store = MagicMock()
    store.get_evaluation_sequence.return_value = {
        "candidates": [
            {"bidder_id": "agent-a", "bid_score": 0.9},
            {"bidder_id": "agent-b", "bid_score": 0.7},
        ],
        "current_index": 1,
    }

    rm = MagicMock()
    rm.graph_marker_store = store
    rm.agent_registry_store = None

    publish = AsyncMock()
    evt = await handle_reassign_task(cmd, rm, publish)

    assert evt is not None
    assert isinstance(evt, TaskReassignedEvent)
    assert evt.new_agent_id == "agent-b"
    assert evt.new_bid_score == 0.7
    assert evt.sequence_index == 1
    publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_reassign_task_no_sequence():
    from agentcy.orchestrator_core.consumers.cnp_lifecycle import handle_reassign_task

    cmd = ReassignTaskCommand(
        username="alice",
        pipeline_id="pipe-1",
        pipeline_run_id="run-1",
        plan_id="plan-1",
        task_id="t1",
        failed_agent_id="agent-a",
    )

    store = MagicMock()
    store.get_evaluation_sequence.return_value = None

    rm = MagicMock()
    rm.graph_marker_store = store

    publish = AsyncMock()
    evt = await handle_reassign_task(cmd, rm, publish)

    assert evt is None
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_reassign_task_no_store():
    from agentcy.orchestrator_core.consumers.cnp_lifecycle import handle_reassign_task

    cmd = ReassignTaskCommand(
        username="alice",
        pipeline_id="pipe-1",
        pipeline_run_id="run-1",
        plan_id="plan-1",
        task_id="t1",
        failed_agent_id="agent-a",
    )

    rm = MagicMock()
    rm.graph_marker_store = None

    publish = AsyncMock()
    evt = await handle_reassign_task(cmd, rm, publish)

    assert evt is None
    publish.assert_not_awaited()
