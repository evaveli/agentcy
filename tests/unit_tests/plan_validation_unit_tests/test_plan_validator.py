import json

import pytest

from src.agentcy.agent_runtime.services import plan_validator
from src.agentcy.agent_runtime.services.plan_validator import validate_plan_draft
from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft


class _FakeStore:
    def __init__(self):
        self.drafts = {}

    def list_plan_drafts(self, *, username, pipeline_id=None):
        drafts = list(self.drafts.values())
        if pipeline_id:
            drafts = [d for d in drafts if d.get("pipeline_id") == pipeline_id]
        return drafts, len(drafts)

    def get_plan_draft(self, *, username, plan_id):
        return self.drafts.get(plan_id)

    def save_plan_draft(self, *, username, draft):
        self.drafts[draft.plan_id] = draft.model_dump(mode="json")


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store


def _fake_llm(response_text: str):
    class _FakeLLM:
        def __init__(self, provider):
            self.provider = provider

        async def start(self):
            return None

        async def stop(self):
            return None

        async def handle_incoming_requests(self, requests):
            return {request_id: response_text for request_id, _ in requests}

    return _FakeLLM


@pytest.mark.asyncio
async def test_plan_validator_marks_valid():
    store = _FakeStore()
    rm = _FakeRM(store)
    draft = PlanDraft(
        plan_id="plan-1",
        username="alice",
        pipeline_id="pipeline-1",
        graph_spec={
            "tasks": [
                {"task_id": "t1", "assigned_agent": "agent-a", "required_capabilities": ["plan"]},
                {"task_id": "t2", "assigned_agent": "agent-b", "required_capabilities": ["execute"]},
            ],
            "edges": [{"from": "t1", "to": "t2"}],
        },
    )
    store.save_plan_draft(username="alice", draft=draft)

    updated = await validate_plan_draft(rm, username="alice", pipeline_id="pipeline-1")
    assert updated.is_valid is True
    assert updated.shacl_report
    assert updated.shacl_report["conforms"] is True
    assert store.get_plan_draft(username="alice", plan_id="plan-1")["is_valid"] is True


@pytest.mark.asyncio
async def test_plan_validator_detects_missing_task():
    store = _FakeStore()
    rm = _FakeRM(store)
    draft = PlanDraft(
        plan_id="plan-2",
        username="alice",
        pipeline_id="pipeline-2",
        graph_spec={
            "tasks": [{"task_id": "t1", "assigned_agent": "agent-a", "required_capabilities": ["plan"]}],
            "edges": [{"from": "t1", "to": "t2"}],
        },
    )
    store.save_plan_draft(username="alice", draft=draft)

    updated = await validate_plan_draft(rm, username="alice", pipeline_id="pipeline-2")
    assert updated.is_valid is False
    assert any(v["code"] == "edge_missing_task" for v in updated.shacl_report["violations"])


@pytest.mark.asyncio
async def test_plan_validator_flags_missing_capabilities():
    store = _FakeStore()
    rm = _FakeRM(store)
    draft = PlanDraft(
        plan_id="plan-3",
        username="alice",
        pipeline_id="pipeline-3",
        graph_spec={
            "tasks": [{"task_id": "t1", "assigned_agent": "agent-a", "required_capabilities": []}],
            "edges": [],
        },
    )
    store.save_plan_draft(username="alice", draft=draft)

    updated = await validate_plan_draft(rm, username="alice", pipeline_id="pipeline-3")
    assert updated.is_valid is False
    assert any(v["code"] == "missing_required_capabilities" for v in updated.shacl_report["violations"])


@pytest.mark.asyncio
async def test_plan_validator_enforces_ontology():
    store = _FakeStore()
    rm = _FakeRM(store)
    draft = PlanDraft(
        plan_id="plan-4",
        username="alice",
        pipeline_id="pipeline-4",
        graph_spec={
            "ontology": {"capabilities": ["plan"], "task_types": ["planning"]},
            "tasks": [
                {
                    "task_id": "t1",
                    "assigned_agent": "agent-a",
                    "required_capabilities": ["execute"],
                    "task_type": "execution",
                }
            ],
            "edges": [],
        },
    )
    store.save_plan_draft(username="alice", draft=draft)

    updated = await validate_plan_draft(rm, username="alice", pipeline_id="pipeline-4")
    assert updated.is_valid is False
    codes = {v["code"] for v in updated.shacl_report["violations"]}
    assert "unknown_capability" in codes
    assert "unknown_task_type" in codes


@pytest.mark.asyncio
async def test_plan_validator_attaches_llm_review(monkeypatch):
    store = _FakeStore()
    rm = _FakeRM(store)
    draft = PlanDraft(
        plan_id="plan-5",
        username="alice",
        pipeline_id="pipeline-5",
        graph_spec={
            "tasks": [
                {"task_id": "t1", "assigned_agent": "agent-a", "required_capabilities": ["plan"]},
                {"task_id": "t2", "assigned_agent": "agent-b", "required_capabilities": ["execute"]},
            ],
            "edges": [{"from": "t1", "to": "t2"}],
        },
    )
    store.save_plan_draft(username="alice", draft=draft)

    response = json.dumps(
        {
            "approved": True,
            "assessment": "looks ok",
            "risks": ["none"],
            "suggested_fixes": ["none"],
            "confidence": 0.8,
        }
    )
    monkeypatch.setenv("LLM_PLAN_VALIDATOR_PROVIDER", "openai")
    monkeypatch.setattr(plan_validator, "LLM_Connector", _fake_llm(response))

    updated = await validate_plan_draft(rm, username="alice", pipeline_id="pipeline-5")
    review = (updated.shacl_report or {}).get("llm_review")
    assert review
    assert review["assessment"] == "looks ok"


@pytest.mark.asyncio
async def test_plan_validator_includes_shacl_engine(monkeypatch):
    store = _FakeStore()
    rm = _FakeRM(store)
    draft = PlanDraft(
        plan_id="plan-6",
        username="alice",
        pipeline_id="pipeline-6",
        graph_spec={
            "tasks": [
                {"task_id": "t1", "assigned_agent": "agent-a", "required_capabilities": ["plan"]}
            ],
            "edges": [],
        },
    )
    store.save_plan_draft(username="alice", draft=draft)

    monkeypatch.delenv("LLM_PLAN_VALIDATOR_PROVIDER", raising=False)
    updated = await validate_plan_draft(rm, username="alice", pipeline_id="pipeline-6")
    report = updated.shacl_report or {}
    assert "shacl_engine" in report
    assert report.get("llm_required") is False
