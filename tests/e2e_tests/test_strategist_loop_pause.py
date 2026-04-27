import uuid

import pytest

from src.agentcy.agent_runtime.services.llm_strategist_loop import handle_task_event
from src.agentcy.orchestrator_core.utils import seed_initial_run
from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft


@pytest.mark.asyncio
async def test_strategist_loop_pauses_and_resumes(
    http_client,
    resource_manager_fixture,
    monkeypatch,
):
    rm = resource_manager_fixture
    store = rm.graph_marker_store
    assert store is not None
    assert rm.ephemeral_store is not None

    monkeypatch.setenv("LLM_STRATEGIST_LOOP", "1")
    monkeypatch.setenv("LLM_STRATEGIST_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("LLM_STRATEGIST_AUTO_APPLY", "0")
    monkeypatch.setenv("LLM_STRATEGIST_REQUIRE_HUMAN", "1")
    monkeypatch.setenv("SEMANTIC_RDF_EXPORT", "0")

    username = f"loop_{uuid.uuid4().hex[:6]}"
    pipeline_id = f"pipe_{uuid.uuid4().hex[:6]}"
    run_id = str(uuid.uuid4())

    final_cfg = {
        "task_dict": {
            "t1": {"available_services": "graph_builder", "is_final_task": False},
            "t2": {"available_services": "system_executor", "is_final_task": True},
        }
    }
    pipeline_run = seed_initial_run(
        username=username,
        pipeline_id=pipeline_id,
        run_id=run_id,
        cfg=final_cfg,
        pipeline_config_id="e2e_run_cfg",
    )
    rm.ephemeral_store.create_run(username, pipeline_id, run_id, pipeline_run.model_dump())

    plan_id = str(uuid.uuid4())
    graph_spec = {
        "tasks": [
            {
                "task_id": "t1",
                "assigned_agent": "agent-a",
                "required_capabilities": ["plan"],
                "tags": ["core"],
                "task_type": "plan",
            },
            {
                "task_id": "t2",
                "assigned_agent": "agent-b",
                "required_capabilities": ["execute"],
                "tags": ["core"],
                "task_type": "execute",
            },
        ],
        "edges": [{"from": "t1", "to": "t2"}],
    }
    draft = PlanDraft(
        plan_id=plan_id,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=run_id,
        revision=1,
        graph_spec=graph_spec,
        is_valid=True,
    )
    store.save_plan_draft(username=username, draft=draft)

    suggestion = await handle_task_event(
        rm,
        {
            "event": "task_state_changed",
            "username": username,
            "pipeline_id": pipeline_id,
            "pipeline_run_id": run_id,
            "task_id": "t1",
            "status": "COMPLETED",
        },
    )
    assert suggestion is not None

    run_doc = rm.ephemeral_store.read_run(username, pipeline_id, run_id)
    assert run_doc.get("paused") is True

    resp = await http_client.get(f"/pipelines/{username}/{pipeline_id}/{run_id}")
    resp.raise_for_status()
    run_payload = resp.json()
    assert run_payload.get("pause_suggestion")
    assert run_payload.get("pause_suggestion", {}).get("suggestion_id") == suggestion.suggestion_id

    approval = {
        "plan_id": plan_id,
        "username": username,
        "approver": "tester",
        "approved": True,
        "suggestion_id": suggestion.suggestion_id,
    }
    resp = await http_client.post(f"/graph-store/{username}/human-approvals", json=approval)
    resp.raise_for_status()

    run_doc = rm.ephemeral_store.read_run(username, pipeline_id, run_id)
    assert run_doc.get("paused") is False

    updated = store.get_plan_draft(username=username, plan_id=plan_id)
    assert updated["revision"] == 2
