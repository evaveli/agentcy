"""Tests for CNP failure re-forwarding in the tracker."""
import os
import asyncio
import contextlib
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import (
    PipelineRun, TaskState, TaskStatus, PipelineStatus,
)
from agentcy.agent_runtime.tracker import ReforwardInfo


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


def _make_pipeline_run(tasks=None, plan_id="plan-1", status=PipelineStatus.RUNNING):
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


def _make_tracker(
    *,
    advance_result=None,
    run_doc=None,
    plan_id="plan-1",
):
    """Build a PipelineRunTracker with mocked dependencies."""
    from agentcy.agent_runtime.tracker import PipelineRunTracker

    # Mock resource_manager
    rm = MagicMock()

    # graph_marker_store
    store = MagicMock()
    store.advance_evaluation_sequence = MagicMock(return_value=advance_result)
    store.add_contract_award = MagicMock()
    rm.graph_marker_store = store

    # ephemeral_store / pipeline_doc_manager
    doc_mgr = MagicMock()
    read_doc = run_doc or {"plan_id": plan_id, "status": "RUNNING", "tasks": {}}
    doc_mgr.read_run.return_value = read_doc
    doc_mgr.update_run = MagicMock()

    rm.rabbit_mgr = None  # Disable async publish in tests

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
# Tests
# ---------------------------------------------------------------------------
def test_reforward_picks_next_candidate():
    """When a candidate exists in the eval sequence, task is re-assigned."""
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
            username="alice",
            pipeline_id="pipe-1",
            run_id="run-1",
            task_id="t1",
            pipeline_run=pipeline_run,
        )

    assert result is not None
    assert isinstance(result, ReforwardInfo)
    assert result.new_agent_id == "agent-b"
    assert result.bid_score == 0.7
    assert result.sequence_index == 1
    assert result.task_id == "t1"

    # Task should be reset to PENDING with new service
    updated_task = pipeline_run.tasks["t1"]
    assert updated_task.status == TaskStatus.PENDING
    assert updated_task.error is None

    # Award should be created
    store.add_contract_award.assert_called_once()
    award_call = store.add_contract_award.call_args
    assert award_call.kwargs["award"].bidder_id == "agent-b"


def test_reforward_exhausted_fails_run():
    """When no candidates remain, re-forwarding returns False."""
    tracker, rm, store = _make_tracker(advance_result=None)

    pipeline_run = _make_pipeline_run()
    with _env(CNP_FAILURE_REFORWARD="1"):
        result = tracker._try_reforward_sync(
            username="alice",
            pipeline_id="pipe-1",
            run_id="run-1",
            task_id="t1",
            pipeline_run=pipeline_run,
        )

    assert result is None
    store.add_contract_award.assert_not_called()


def test_reforward_disabled_by_env():
    """When CNP_FAILURE_REFORWARD=0, re-forwarding is skipped."""
    next_candidate = {
        "bidder_id": "agent-b", "bid_score": 0.7,
        "sequence_index": 1,
    }
    tracker, rm, store = _make_tracker(advance_result=next_candidate)

    pipeline_run = _make_pipeline_run()
    with _env(CNP_FAILURE_REFORWARD="0"):
        result = tracker._try_reforward_sync(
            username="alice",
            pipeline_id="pipe-1",
            run_id="run-1",
            task_id="t1",
            pipeline_run=pipeline_run,
        )

    assert result is None
    store.advance_evaluation_sequence.assert_not_called()


def test_reforward_missing_plan_id_skips():
    """When plan_id is not in run doc, re-forwarding is skipped."""
    tracker, rm, store = _make_tracker(
        advance_result={"bidder_id": "agent-b", "bid_score": 0.7, "sequence_index": 1},
        plan_id=None,
        run_doc={"status": "RUNNING", "tasks": {}},  # No plan_id
    )

    pipeline_run = _make_pipeline_run()
    with _env(CNP_FAILURE_REFORWARD="1"):
        result = tracker._try_reforward_sync(
            username="alice",
            pipeline_id="pipe-1",
            run_id="run-1",
            task_id="t1",
            pipeline_run=pipeline_run,
        )

    assert result is None


def test_reforward_creates_new_award():
    """Re-forwarding creates a ContractAward for the new agent."""
    next_candidate = {
        "bidder_id": "agent-c",
        "bid_score": 0.6,
        "bid_id": "bid-3",
        "cfp_id": "cfp-1",
        "sequence_index": 2,
    }
    tracker, rm, store = _make_tracker(advance_result=next_candidate)

    pipeline_run = _make_pipeline_run()
    with _env(CNP_FAILURE_REFORWARD="1"):
        tracker._try_reforward_sync(
            username="alice",
            pipeline_id="pipe-1",
            run_id="run-1",
            task_id="t1",
            pipeline_run=pipeline_run,
        )

    store.add_contract_award.assert_called_once()
    award = store.add_contract_award.call_args.kwargs["award"]
    assert award.task_id == "t1"
    assert award.bidder_id == "agent-c"
    assert award.bid_id == "bid-3"
    assert award.cfp_id == "cfp-1"


def test_reforward_no_store_returns_false():
    """When graph_marker_store is None, re-forwarding returns False."""
    from agentcy.agent_runtime.tracker import PipelineRunTracker

    rm = MagicMock()
    rm.graph_marker_store = None
    doc_mgr = MagicMock()
    doc_mgr.read_run.return_value = {"plan_id": "plan-1"}

    tracker = PipelineRunTracker(rm)
    tracker.pipeline_doc_manager = doc_mgr

    pipeline_run = _make_pipeline_run()
    with _env(CNP_FAILURE_REFORWARD="1"):
        result = tracker._try_reforward_sync(
            username="alice",
            pipeline_id="pipe-1",
            run_id="run-1",
            task_id="t1",
            pipeline_run=pipeline_run,
        )

    assert result is None
