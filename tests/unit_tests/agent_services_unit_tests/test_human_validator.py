import pytest

from src.agentcy.agent_runtime.services.human_validator import run
from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft, TaskSpec


class _FakeStore:
    def __init__(self):
        self.specs = []
        self.drafts = {}
        self.approvals = []

    def list_task_specs(self, *, username):
        items = list(self.specs)
        return items, len(items)

    def list_plan_drafts(self, *, username, pipeline_id=None):
        drafts = list(self.drafts.values())
        if pipeline_id:
            drafts = [d for d in drafts if d.get("pipeline_id") == pipeline_id]
        return drafts, len(drafts)

    def get_plan_draft(self, *, username, plan_id):
        return self.drafts.get(plan_id)

    def save_plan_draft(self, *, username, draft):
        self.drafts[draft.plan_id] = draft.model_dump(mode="json")

    def save_human_approval(self, *, username, approval):
        self.approvals.append((username, approval))
        return "approval-1"


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store


@pytest.mark.asyncio
async def test_human_validator_applies_modifications():
    store = _FakeStore()
    rm = _FakeRM(store)
    username = "alice"
    pipeline_id = "pipe-1"

    spec = TaskSpec(
        task_id="task-1",
        username=username,
        description="plan",
        required_capabilities=["plan"],
        metadata={"pipeline_id": pipeline_id},
    )
    store.specs.append(spec.model_dump(mode="json"))

    draft = PlanDraft(
        plan_id="plan-1",
        username=username,
        pipeline_id=pipeline_id,
        graph_spec={
            "tasks": [{"task_id": "task-1", "assigned_agent": "agent-a", "required_capabilities": ["plan"]}],
            "edges": [],
        },
    )
    store.save_plan_draft(username=username, draft=draft)

    message = {
        "username": username,
        "pipeline_id": pipeline_id,
        "plan_id": "plan-1",
        "data": {
            "approved": True,
            "modifications": {"task_overrides": {"task-1": {"assigned_agent": "agent-b"}}},
        },
    }

    result = await run(rm, "run-1", "human_validator", None, message)
    assert result["approved"] is True
    assert result["modifications_applied"] >= 1
    saved = store.get_plan_draft(username=username, plan_id="plan-1")
    task = saved["graph_spec"]["tasks"][0]
    assert task["assigned_agent"] == "agent-b"
    assert store.approvals
