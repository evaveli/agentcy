"""Tests for RevisePlanCommand and PlanRevisedEvent models."""
import pytest
from datetime import datetime, timezone


def test_revise_plan_command_round_trip():
    from src.agentcy.pydantic_models.commands import RevisePlanCommand

    cmd = RevisePlanCommand(
        username="alice",
        pipeline_id="pipe-1",
        plan_id="plan-1",
        pipeline_run_id="run-1",
        payload_ref="revision_candidate::alice::plan-1::2",
        suggestion_id="sug-123",
        created_by="llm_strategist_loop",
        reason="llm_auto_apply",
    )
    data = cmd.model_dump_json()
    restored = RevisePlanCommand.model_validate_json(data)

    assert restored.username == "alice"
    assert restored.pipeline_id == "pipe-1"
    assert restored.plan_id == "plan-1"
    assert restored.pipeline_run_id == "run-1"
    assert restored.payload_ref == "revision_candidate::alice::plan-1::2"
    assert restored.suggestion_id == "sug-123"
    assert restored.created_by == "llm_strategist_loop"
    assert restored.reason == "llm_auto_apply"


def test_revise_plan_command_defaults():
    from src.agentcy.pydantic_models.commands import RevisePlanCommand

    cmd = RevisePlanCommand(
        username="bob",
        pipeline_id="pipe-2",
        plan_id="plan-2",
        payload_ref="revision_candidate::bob::plan-2::1",
    )
    assert cmd.pipeline_run_id is None
    assert cmd.suggestion_id is None
    assert cmd.created_by == "system"
    assert cmd.reason == "revision"


def test_plan_revised_event_round_trip():
    from src.agentcy.pydantic_models.commands import PlanRevisedEvent

    evt = PlanRevisedEvent(
        username="alice",
        pipeline_id="pipe-1",
        plan_id="plan-1",
        revision=3,
        pipeline_run_id="run-1",
        created_by="human",
        reason="human_approved",
        timestamp=datetime.now(timezone.utc),
    )
    data = evt.model_dump_json()
    restored = PlanRevisedEvent.model_validate_json(data)

    assert restored.username == "alice"
    assert restored.revision == 3
    assert restored.created_by == "human"
    assert restored.reason == "human_approved"


def test_plan_revised_event_optional_run_id():
    from src.agentcy.pydantic_models.commands import PlanRevisedEvent

    evt = PlanRevisedEvent(
        username="bob",
        pipeline_id="pipe-2",
        plan_id="plan-2",
        revision=1,
        created_by="system",
        reason="manual_revision",
        timestamp=datetime.now(timezone.utc),
    )
    assert evt.pipeline_run_id is None
