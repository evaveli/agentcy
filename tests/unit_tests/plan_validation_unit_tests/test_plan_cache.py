import pytest

from src.agentcy.agent_runtime.services.plan_cache import cache_plan_draft
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


@pytest.mark.asyncio
async def test_plan_cache_sets_cached_flag():
    store = _FakeStore()
    rm = _FakeRM(store)
    draft = PlanDraft(
        plan_id="plan-1",
        username="alice",
        pipeline_id="pipeline-1",
        graph_spec={"tasks": [], "edges": []},
        is_valid=True,
    )
    store.save_plan_draft(username="alice", draft=draft)

    result = await cache_plan_draft(rm, username="alice", pipeline_id="pipeline-1")
    assert result["cached"] is True
    stored = store.get_plan_draft(username="alice", plan_id="plan-1")
    assert stored["cached"] is True
    assert "cache" in stored["graph_spec"]


@pytest.mark.asyncio
async def test_plan_cache_skips_invalid_plan():
    store = _FakeStore()
    rm = _FakeRM(store)
    draft = PlanDraft(
        plan_id="plan-2",
        username="alice",
        pipeline_id="pipeline-2",
        graph_spec={"tasks": [], "edges": []},
        is_valid=False,
    )
    store.save_plan_draft(username="alice", draft=draft)

    result = await cache_plan_draft(rm, username="alice", pipeline_id="pipeline-2")
    assert result["cached"] is False
    stored = store.get_plan_draft(username="alice", plan_id="plan-2")
    assert stored["cached"] is False
