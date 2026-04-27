import pytest

from src.agentcy.agent_runtime.services.system_executor import run
from src.agentcy.pydantic_models.multi_agent_pipeline import PlanDraft


class _FakeStore:
    def __init__(self):
        self.drafts = {}
        self.ethics = []
        self.approvals = []
        self.execution_reports = []

    def list_plan_drafts(self, *, username, pipeline_id=None):
        drafts = list(self.drafts.values())
        if pipeline_id:
            drafts = [d for d in drafts if d.get("pipeline_id") == pipeline_id]
        return drafts, len(drafts)

    def get_plan_draft(self, *, username, plan_id):
        return self.drafts.get(plan_id)

    def save_plan_draft(self, *, username, draft):
        self.drafts[draft.plan_id] = draft.model_dump(mode="json")

    def list_ethics_checks(self, *, username, plan_id=None):
        items = list(self.ethics)
        return items, len(items)

    def list_human_approvals(self, *, username, plan_id=None):
        items = list(self.approvals)
        return items, len(items)

    def save_execution_report(self, *, username, report):
        self.execution_reports.append((username, report))
        return report.report_id


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store
        self.rabbit_mgr = None
        self.service_store = None
        self.agent_registry_store = None


@pytest.mark.asyncio
async def test_system_executor_records_outcomes():
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
            "edges": [],
        },
    )
    store.save_plan_draft(username=username, draft=draft)

    message = {
        "username": username,
        "pipeline_id": pipeline_id,
        "plan_id": "plan-1",
        "data": {"fail_task_ids": ["t2"]},
    }
    result = await run(rm, "run-1", "system_executor", None, message)

    assert result["execution_report_id"]
    assert result["success_rate"] == 0.5
    assert len(result["task_outcomes"]) == 2
    assert store.execution_reports
