"""Tests for the handle_revise_plan handler (consumer logic without RabbitMQ)."""
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

from agentcy.pydantic_models.commands import (
    PlanRevisedEvent,
    RevisePlanCommand,
)
from agentcy.pydantic_models.multi_agent_pipeline import PlanDraft


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_draft(username="alice", plan_id="plan-1", pipeline_id="pipe-1", revision=1):
    """Return a PlanDraft as a dict (mimics what store.get_plan_draft returns)."""
    return PlanDraft(
        plan_id=plan_id,
        username=username,
        pipeline_id=pipeline_id,
        revision=revision,
        graph_spec={"tasks": [{"id": "t1"}], "edges": []},
        is_valid=True,
    ).model_dump()


def _make_candidate(base_revision=1, next_revision=2):
    """Return a candidate document (mimics what store.get_raw returns)."""
    return {
        "candidate_graph": {"tasks": [{"id": "t1"}, {"id": "t2"}], "edges": [{"from": "t1", "to": "t2"}]},
        "delta": {"added_tasks": ["t2"], "added_edges": [{"from": "t1", "to": "t2"}]},
        "validation": {"conforms": True},
        "base_revision": base_revision,
        "next_revision": next_revision,
    }


def _make_cmd(username="alice", plan_id="plan-1", pipeline_id="pipe-1",
              payload_ref="revision_candidate::alice::plan-1::2",
              pipeline_run_id=None, created_by="system", reason="revision"):
    return RevisePlanCommand(
        username=username,
        pipeline_id=pipeline_id,
        plan_id=plan_id,
        pipeline_run_id=pipeline_run_id,
        payload_ref=payload_ref,
        created_by=created_by,
        reason=reason,
    )


def _make_rm(candidate_doc=None, draft_doc=None, ephemeral_store=None):
    """Build a mock ResourceManager with a mock graph_marker_store."""
    store = MagicMock()
    store.get_raw.return_value = candidate_doc
    store.get_plan_draft.return_value = draft_doc
    store.save_plan_draft = MagicMock()
    store.save_plan_revision = MagicMock()

    rm = MagicMock()
    rm.graph_marker_store = store
    rm.ephemeral_store = ephemeral_store
    return rm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_applies_revision():
    """Handler fetches candidate, updates draft, saves revision, emits event."""
    from agentcy.orchestrator_core.consumers.plan_revision import handle_revise_plan

    cmd = _make_cmd()
    candidate = _make_candidate()
    draft = _make_draft()
    rm = _make_rm(candidate_doc=candidate, draft_doc=draft)
    publish = AsyncMock()

    with _disable_rdf():
        evt = await handle_revise_plan(cmd, rm, publish)

    # Event was returned and published
    assert evt is not None
    assert isinstance(evt, PlanRevisedEvent)
    assert evt.revision == 2
    assert evt.plan_id == "plan-1"
    assert evt.username == "alice"
    assert evt.created_by == "system"
    publish.assert_awaited_once()

    # Store was called correctly
    rm.graph_marker_store.get_raw.assert_called_once_with(cmd.payload_ref)
    rm.graph_marker_store.get_plan_draft.assert_called_once_with(
        username="alice", plan_id="plan-1",
    )
    rm.graph_marker_store.save_plan_draft.assert_called_once()
    rm.graph_marker_store.save_plan_revision.assert_called_once()

    # Check the saved draft has updated revision
    saved_draft = rm.graph_marker_store.save_plan_draft.call_args
    assert saved_draft.kwargs["draft"].revision == 2
    assert saved_draft.kwargs["draft"].is_valid is True

    # Check the saved revision record
    saved_rev = rm.graph_marker_store.save_plan_revision.call_args
    assert saved_rev.kwargs["revision"].revision == 2
    assert saved_rev.kwargs["revision"].parent_revision == 1
    assert saved_rev.kwargs["revision"].status == "APPLIED"


@pytest.mark.asyncio
async def test_missing_candidate_returns_none():
    """Handler returns None when payload_ref not found in store."""
    from agentcy.orchestrator_core.consumers.plan_revision import handle_revise_plan

    cmd = _make_cmd()
    rm = _make_rm(candidate_doc=None, draft_doc=_make_draft())
    publish = AsyncMock()

    with _disable_rdf():
        evt = await handle_revise_plan(cmd, rm, publish)

    assert evt is None
    publish.assert_not_awaited()
    rm.graph_marker_store.save_plan_draft.assert_not_called()


@pytest.mark.asyncio
async def test_missing_draft_returns_none():
    """Handler returns None when plan draft not found."""
    from agentcy.orchestrator_core.consumers.plan_revision import handle_revise_plan

    cmd = _make_cmd()
    rm = _make_rm(candidate_doc=_make_candidate(), draft_doc=None)
    publish = AsyncMock()

    with _disable_rdf():
        evt = await handle_revise_plan(cmd, rm, publish)

    assert evt is None
    publish.assert_not_awaited()
    rm.graph_marker_store.save_plan_draft.assert_not_called()


@pytest.mark.asyncio
async def test_updates_run_doc_when_run_id_present():
    """Handler updates ephemeral run doc when pipeline_run_id is set."""
    from agentcy.orchestrator_core.consumers.plan_revision import handle_revise_plan

    eph = MagicMock()
    eph.read_run.return_value = {"status": "RUNNING", "plan_id": "old"}
    eph.update_run = MagicMock()

    cmd = _make_cmd(pipeline_run_id="run-1")
    rm = _make_rm(
        candidate_doc=_make_candidate(),
        draft_doc=_make_draft(),
        ephemeral_store=eph,
    )
    publish = AsyncMock()

    with _disable_rdf():
        evt = await handle_revise_plan(cmd, rm, publish)

    assert evt is not None
    eph.read_run.assert_called_once_with("alice", "pipe-1", "run-1")
    eph.update_run.assert_called_once()
    updated_doc = eph.update_run.call_args[0][3]
    assert updated_doc["plan_id"] == "plan-1"
    assert updated_doc["plan_revision"] == 2


@pytest.mark.asyncio
async def test_skips_run_doc_when_no_run_id():
    """Handler does NOT touch ephemeral store when pipeline_run_id is None."""
    from agentcy.orchestrator_core.consumers.plan_revision import handle_revise_plan

    eph = MagicMock()
    cmd = _make_cmd(pipeline_run_id=None)
    rm = _make_rm(
        candidate_doc=_make_candidate(),
        draft_doc=_make_draft(),
        ephemeral_store=eph,
    )
    publish = AsyncMock()

    with _disable_rdf():
        evt = await handle_revise_plan(cmd, rm, publish)

    assert evt is not None
    eph.read_run.assert_not_called()


@pytest.mark.asyncio
async def test_event_carries_command_metadata():
    """The emitted event reflects created_by and reason from the command."""
    from agentcy.orchestrator_core.consumers.plan_revision import handle_revise_plan

    cmd = _make_cmd(created_by="llm_strategist_loop", reason="llm_auto_apply")
    rm = _make_rm(candidate_doc=_make_candidate(), draft_doc=_make_draft())
    publish = AsyncMock()

    with _disable_rdf():
        evt = await handle_revise_plan(cmd, rm, publish)

    assert evt.created_by == "llm_strategist_loop"
    assert evt.reason == "llm_auto_apply"


@pytest.mark.asyncio
async def test_validation_not_conforming_sets_is_valid_false():
    """When validation.conforms is false, draft.is_valid should be False."""
    from agentcy.orchestrator_core.consumers.plan_revision import handle_revise_plan

    candidate = _make_candidate()
    candidate["validation"] = {"conforms": False, "errors": ["shape violation"]}

    cmd = _make_cmd()
    rm = _make_rm(candidate_doc=candidate, draft_doc=_make_draft())
    publish = AsyncMock()

    with _disable_rdf():
        evt = await handle_revise_plan(cmd, rm, publish)

    assert evt is not None
    saved_draft = rm.graph_marker_store.save_plan_draft.call_args.kwargs["draft"]
    assert saved_draft.is_valid is False
    assert saved_draft.shacl_report == {"conforms": False, "errors": ["shape violation"]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import contextlib

@contextlib.contextmanager
def _disable_rdf():
    """Disable semantic RDF export during tests."""
    old = os.environ.get("SEMANTIC_RDF_EXPORT")
    os.environ["SEMANTIC_RDF_EXPORT"] = "0"
    try:
        yield
    finally:
        if old is None:
            del os.environ["SEMANTIC_RDF_EXPORT"]
        else:
            os.environ["SEMANTIC_RDF_EXPORT"] = old
