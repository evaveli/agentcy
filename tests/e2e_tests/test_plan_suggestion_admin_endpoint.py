import uuid

import pytest

from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft, PlanSuggestion


@pytest.mark.asyncio
async def test_admin_plan_suggestion_decision_endpoint(
    http_client,
    resource_manager_fixture,
):
    rm = resource_manager_fixture
    store = rm.graph_marker_store
    assert store is not None

    username = f"admin_{uuid.uuid4().hex[:6]}"
    pipeline_id = f"pipe_{uuid.uuid4().hex[:6]}"
    plan_id = str(uuid.uuid4())

    graph_spec = {
        "tasks": [
            {
                "task_id": "t1",
                "assigned_agent": "agent-a",
                "required_capabilities": ["plan"],
                "tags": ["core"],
                "task_type": "plan",
            }
        ],
        "edges": [],
    }
    draft = PlanDraft(
        plan_id=plan_id,
        username=username,
        pipeline_id=pipeline_id,
        revision=1,
        graph_spec=graph_spec,
        is_valid=True,
    )
    store.save_plan_draft(username=username, draft=draft)

    suggestion = PlanSuggestion(
        plan_id=plan_id,
        username=username,
        pipeline_id=pipeline_id,
        base_revision=1,
        candidate_revision=2,
        delta={"task_overrides": {"t1": {"tags": ["approved"]}}},
        graph_spec=graph_spec,
    )
    store.save_plan_suggestion(username=username, suggestion=suggestion)

    decision = {"approved": True, "approver": "admin", "rationale": "ok"}
    resp = await http_client.post(
        f"/graph-store/{username}/plan-suggestions/{suggestion.suggestion_id}/decision",
        json=decision,
    )
    resp.raise_for_status()
    body = resp.json()
    assert body["applied"] is True
    assert body["plan_revision"] == 2
