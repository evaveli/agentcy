import pytest

from src.agentcy.agent_runtime.services import llm_strategist
from src.agentcy.agent_runtime.services.llm_strategist import run
from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft


class _FakeStore:
    def __init__(self):
        self.drafts = {}
        self.strategies = []

    def list_plan_drafts(self, *, username, pipeline_id=None):
        drafts = list(self.drafts.values())
        if pipeline_id:
            drafts = [d for d in drafts if d.get("pipeline_id") == pipeline_id]
        return drafts, len(drafts)

    def get_plan_draft(self, *, username, plan_id):
        return self.drafts.get(plan_id)

    def save_plan_draft(self, *, username, draft):
        self.drafts[draft.plan_id] = draft.model_dump(mode="json")

    def save_strategy_plan(self, *, username, strategy):
        self.strategies.append((username, strategy))
        return strategy.strategy_id


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store


@pytest.mark.asyncio
async def test_llm_strategist_creates_strategy():
    store = _FakeStore()
    rm = _FakeRM(store)
    username = "alice"
    pipeline_id = "pipe-1"

    draft = PlanDraft(
        plan_id="plan-1",
        username=username,
        pipeline_id=pipeline_id,
        graph_spec={
            "tasks": [
                {"task_id": "t1", "assigned_agent": "a", "required_capabilities": ["plan"]},
                {"task_id": "t2", "assigned_agent": "b", "required_capabilities": ["execute"]},
            ],
            "edges": [{"from": "t1", "to": "t2"}],
        },
    )
    store.save_plan_draft(username=username, draft=draft)

    message = {"username": username, "pipeline_id": pipeline_id, "plan_id": "plan-1", "data": {}}
    result = await run(rm, "run-1", "llm_strategist", None, message)

    assert result["strategy_id"]
    assert result["phase_count"] >= 1
    assert store.strategies


def test_llm_strategist_parses_structured_response():
    response = (
        '{"summary":"ok","phases":[{"phase":1,"tasks":["t1"]},{"phase":2,"tasks":["t2"]}],'
        '"critical_path":["t1","t2"]}'
    )
    parsed = llm_strategist._parse_strategy_response(
        response,
        task_ids=["t1", "t2"],
        edges=[{"from": "t1", "to": "t2"}],
    )
    assert parsed is not None
    assert parsed["summary"] == "ok"
    assert parsed["phases"][0]["tasks"] == ["t1"]
