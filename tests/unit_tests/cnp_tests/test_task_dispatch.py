"""Tests for CNP task dispatch: tracker return types, forwarder inline re-dispatch,
and Pydantic model roundtrips."""
import os
import contextlib
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import (
    PipelineRun, TaskState, TaskStatus, PipelineStatus,
)
from agentcy.agent_runtime.tracker import ReforwardInfo, PipelineRunTracker
from agentcy.pydantic_models.commands import (
    DispatchTaskCommand,
    TaskDispatchedEvent,
    SchemaVersion,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_task_state(task_id="t1", status=TaskStatus.FAILED, **kwargs):
    return TaskState(
        task_id=task_id,
        pipeline_run_id="run-1",
        username="alice",
        pipeline_id="pipe-1",
        status=status,
        service_name=kwargs.get("service_name", "svc-a"),
    )


def _make_pipeline_run(tasks=None, status=PipelineStatus.RUNNING):
    if tasks is None:
        tasks = {"t1": _make_task_state(status=TaskStatus.RUNNING)}
    return PipelineRun(
        pipeline_run_id="run-1",
        run_id="run-1",
        pipeline_id="pipe-1",
        username="alice",
        status=status,
        tasks=tasks,
        triggered_by="test",
    )


def _make_tracker(*, advance_result=None, run_doc=None, plan_id="plan-1"):
    rm = MagicMock()
    store = MagicMock()
    store.advance_evaluation_sequence = MagicMock(return_value=advance_result)
    store.add_contract_award = MagicMock()
    rm.graph_marker_store = store

    doc_mgr = MagicMock()
    read_doc = run_doc or {"plan_id": plan_id, "status": "RUNNING", "tasks": {}}
    doc_mgr.read_run.return_value = read_doc
    doc_mgr.update_run = MagicMock()

    rm.rabbit_mgr = None

    tracker = PipelineRunTracker(rm)
    tracker.pipeline_doc_manager = doc_mgr

    return tracker, rm, store


@contextlib.contextmanager
def _env(**kwargs):
    old = {}
    for k, v in kwargs.items():
        old[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# 1) _try_reforward_sync returns ReforwardInfo when candidate exists
# ---------------------------------------------------------------------------
def test_try_reforward_returns_reforward_info():
    """_try_reforward_sync returns ReforwardInfo (not bool) on success."""
    next_candidate = {
        "bidder_id": "agent-b",
        "bid_score": 0.7,
        "bid_id": "bid-2",
        "cfp_id": "cfp-1",
        "sequence_index": 1,
    }
    tracker, rm, store = _make_tracker(advance_result=next_candidate)
    pipeline_run = _make_pipeline_run()

    with _env(CNP_FAILURE_REFORWARD="1"):
        result = tracker._try_reforward_sync(
            username="alice", pipeline_id="pipe-1",
            run_id="run-1", task_id="t1", pipeline_run=pipeline_run,
        )

    assert result is not None
    assert isinstance(result, ReforwardInfo)
    assert result.new_agent_id == "agent-b"
    assert result.bid_score == 0.7
    assert result.sequence_index == 1
    assert result.task_id == "t1"


# ---------------------------------------------------------------------------
# 2) _try_reforward_sync returns None on success (no re-forward needed)
# ---------------------------------------------------------------------------
def test_try_reforward_returns_none_when_disabled():
    """When CNP_FAILURE_REFORWARD=0, returns None."""
    tracker, rm, store = _make_tracker(
        advance_result={"bidder_id": "agent-b", "bid_score": 0.7, "sequence_index": 1},
    )
    pipeline_run = _make_pipeline_run()

    with _env(CNP_FAILURE_REFORWARD="0"):
        result = tracker._try_reforward_sync(
            username="alice", pipeline_id="pipe-1",
            run_id="run-1", task_id="t1", pipeline_run=pipeline_run,
        )

    assert result is None


# ---------------------------------------------------------------------------
# 3) _try_reforward_sync returns None when exhausted
# ---------------------------------------------------------------------------
def test_try_reforward_returns_none_when_exhausted():
    """No candidates left → returns None."""
    tracker, rm, store = _make_tracker(advance_result=None)
    pipeline_run = _make_pipeline_run()

    with _env(CNP_FAILURE_REFORWARD="1"):
        result = tracker._try_reforward_sync(
            username="alice", pipeline_id="pipe-1",
            run_id="run-1", task_id="t1", pipeline_run=pipeline_run,
        )

    assert result is None


# ---------------------------------------------------------------------------
# 4) Forwarder inline re-dispatch: fail once → re-forward → succeed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_forwarder_inline_redispatch():
    """Microservice fails once, tracker returns ReforwardInfo, retry succeeds."""
    from agentcy.agent_runtime.forwarder import DefaultForwarder

    call_count = 0

    async def mock_microservice_logic(rm, run_id, task_name, triggered_by, msg):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated failure")
        return {"raw_output": "ok"}

    rm = MagicMock()
    rm.ephemeral_store = None

    reforward = ReforwardInfo(
        task_id="t1", new_service="svc-b",
        new_agent_id="agent-b", sequence_index=1, bid_score=0.7,
    )

    tracker = MagicMock()
    tracker.on_task_done = MagicMock(side_effect=[reforward, None])

    with patch("agentcy.agent_runtime.forwarder.PipelineRunTracker", return_value=tracker):
        forwarder = DefaultForwarder(rm, microservice_logic=mock_microservice_logic)

    task_state = _make_task_state(status=TaskStatus.RUNNING)

    # Passthrough for call_microservice_logic_with_retry (skip tenacity retries)
    async def passthrough(func, msg):
        return await func(msg)

    with _env(CNP_MAX_REFORWARDS="3"):
        with patch("agentcy.agent_runtime.forwarder.publish_message", new_callable=AsyncMock):
            with patch("agentcy.agent_runtime.forwarder.call_persistence_with_retry", new_callable=AsyncMock) as mock_persist:
                mock_persist.side_effect = [
                    task_state.model_copy(update={"status": TaskStatus.FAILED}),
                    task_state.model_copy(update={"status": TaskStatus.COMPLETED}),
                ]
                with patch("agentcy.agent_runtime.forwarder.call_microservice_logic_with_retry", side_effect=passthrough):
                    with patch("agentcy.agent_runtime.forwarder.get_registry_client", return_value=None):
                        # Patch TaskState in forwarder to match our import
                        with patch("agentcy.agent_runtime.forwarder.TaskState", TaskState):
                            await forwarder.forward(
                                message_data=task_state,
                                to_task="t1",
                                triggered_by="test",
                            )

    assert call_count == 2, f"Expected 2 calls to microservice logic, got {call_count}"


# ---------------------------------------------------------------------------
# 5) Forwarder re-dispatch cap exceeded
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_forwarder_redispatch_cap_exceeded():
    """Always fails → caps at CNP_MAX_REFORWARDS → publishes FAILED downstream."""
    from agentcy.agent_runtime.forwarder import DefaultForwarder

    call_count = 0

    async def mock_microservice_logic(rm, run_id, task_name, triggered_by, msg):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("always fails")

    rm = MagicMock()
    rm.ephemeral_store = None

    reforward = ReforwardInfo(
        task_id="t1", new_service="svc-b",
        new_agent_id="agent-b", sequence_index=1, bid_score=0.5,
    )

    tracker = MagicMock()
    tracker.on_task_done = MagicMock(return_value=reforward)

    with patch("agentcy.agent_runtime.forwarder.PipelineRunTracker", return_value=tracker):
        forwarder = DefaultForwarder(rm, microservice_logic=mock_microservice_logic)

    task_state = _make_task_state(status=TaskStatus.RUNNING)

    async def passthrough(func, msg):
        return await func(msg)

    with _env(CNP_MAX_REFORWARDS="2"):
        with patch("agentcy.agent_runtime.forwarder.publish_message", new_callable=AsyncMock) as mock_publish:
            with patch("agentcy.agent_runtime.forwarder.call_persistence_with_retry", new_callable=AsyncMock) as mock_persist:
                mock_persist.return_value = task_state.model_copy(update={"status": TaskStatus.FAILED})
                with patch("agentcy.agent_runtime.forwarder.call_microservice_logic_with_retry", side_effect=passthrough):
                    with patch("agentcy.agent_runtime.forwarder.get_registry_client", return_value=None):
                        with patch("agentcy.agent_runtime.forwarder.TaskState", TaskState):
                            await forwarder.forward(
                                message_data=task_state,
                                to_task="t1",
                                triggered_by="test",
                            )

    # 1 initial + 2 re-forwards = 3 total calls
    assert call_count == 3, f"Expected 3 calls (1 + cap 2), got {call_count}"
    mock_publish.assert_called_once()


# ---------------------------------------------------------------------------
# 6) Forwarder no re-dispatch on success
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_forwarder_no_redispatch_on_success():
    """Success path: no re-dispatch, single microservice call."""
    from agentcy.agent_runtime.forwarder import DefaultForwarder

    call_count = 0

    async def mock_microservice_logic(rm, run_id, task_name, triggered_by, msg):
        nonlocal call_count
        call_count += 1
        return {"raw_output": "ok"}

    rm = MagicMock()
    rm.ephemeral_store = None

    tracker = MagicMock()
    tracker.on_task_done = MagicMock(return_value=None)

    with patch("agentcy.agent_runtime.forwarder.PipelineRunTracker", return_value=tracker):
        forwarder = DefaultForwarder(rm, microservice_logic=mock_microservice_logic)

    task_state = _make_task_state(status=TaskStatus.RUNNING)

    async def passthrough(func, msg):
        return await func(msg)

    with patch("agentcy.agent_runtime.forwarder.publish_message", new_callable=AsyncMock) as mock_publish:
        with patch("agentcy.agent_runtime.forwarder.call_persistence_with_retry", new_callable=AsyncMock) as mock_persist:
            mock_persist.return_value = task_state.model_copy(update={"status": TaskStatus.COMPLETED})
            with patch("agentcy.agent_runtime.forwarder.call_microservice_logic_with_retry", side_effect=passthrough):
                with patch("agentcy.agent_runtime.forwarder.get_registry_client", return_value=None):
                    with patch("agentcy.agent_runtime.forwarder.TaskState", TaskState):
                        await forwarder.forward(
                            message_data=task_state,
                            to_task="t1",
                            triggered_by="test",
                        )

    assert call_count == 1
    mock_publish.assert_called_once()


# ---------------------------------------------------------------------------
# 7) DispatchTaskCommand model roundtrip
# ---------------------------------------------------------------------------
def test_dispatch_task_command_model():
    """DispatchTaskCommand serializes and deserializes correctly."""
    cmd = DispatchTaskCommand(
        username="alice",
        pipeline_id="pipe-1",
        pipeline_run_id="run-1",
        plan_id="plan-1",
        task_id="t1",
        new_agent_id="agent-b",
        new_service="svc-b",
        task_input_ref="ephemeral::alice::input_t1::run-1",
        reforward_count=2,
    )

    raw = cmd.model_dump_json()
    restored = DispatchTaskCommand.model_validate_json(raw)

    assert restored.username == "alice"
    assert restored.task_id == "t1"
    assert restored.new_agent_id == "agent-b"
    assert restored.new_service == "svc-b"
    assert restored.reforward_count == 2
    assert restored.reason == "cnp_cross_service_dispatch"


# ---------------------------------------------------------------------------
# 8) TaskDispatchedEvent model roundtrip
# ---------------------------------------------------------------------------
def test_dispatched_event_model():
    """TaskDispatchedEvent serializes and deserializes correctly."""
    evt = TaskDispatchedEvent(
        username="alice",
        pipeline_id="pipe-1",
        pipeline_run_id="run-1",
        task_id="t1",
        agent_id="agent-b",
        service_name="svc-b",
        dispatch_type="cross_service",
        reforward_count=1,
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    raw = evt.model_dump_json()
    restored = TaskDispatchedEvent.model_validate_json(raw)

    assert restored.schema_version == SchemaVersion.V1
    assert restored.agent_id == "agent-b"
    assert restored.dispatch_type == "cross_service"
    assert restored.reforward_count == 1
