import pytest

from src.agentcy.agent_runtime.services.failure_escalation import run
from src.agentcy.pydantic_models.multi_agent_pipeline import ExecutionOutcome, ExecutionReport, PlanDraft


class _FakeStore:
    def __init__(self):
        self.drafts = {}
        self.reports = []
        self.escalations = []

    def list_plan_drafts(self, *, username, pipeline_id=None):
        drafts = list(self.drafts.values())
        if pipeline_id:
            drafts = [d for d in drafts if d.get("pipeline_id") == pipeline_id]
        return drafts, len(drafts)

    def get_plan_draft(self, *, username, plan_id):
        return self.drafts.get(plan_id)

    def save_plan_draft(self, *, username, draft):
        self.drafts[draft.plan_id] = draft.model_dump(mode="json")

    def list_execution_reports(self, *, username, plan_id=None):
        items = [report.model_dump(mode="json") for report in self.reports]
        return items, len(items)

    def save_escalation_notice(self, *, username, notice):
        self.escalations.append((username, notice))
        return "esc-1"


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store


@pytest.mark.asyncio
async def test_failure_escalation_flags_retries_exhausted():
    store = _FakeStore()
    rm = _FakeRM(store)
    username = "alice"
    pipeline_id = "pipe-1"

    draft = PlanDraft(
        plan_id="plan-1",
        username=username,
        pipeline_id=pipeline_id,
        graph_spec={"tasks": [], "edges": []},
    )
    store.save_plan_draft(username=username, draft=draft)

    report = ExecutionReport(
        plan_id="plan-1",
        outcomes=[ExecutionOutcome(task_id="t1", success=False)],
        success_rate=0.0,
    )
    store.reports.append(report)

    message = {
        "username": username,
        "pipeline_id": pipeline_id,
        "plan_id": "plan-1",
        "pipeline_run_id": "run-1",
        "data": {"attempts": 2, "max_retries": 2},
    }
    result = await run(rm, "run-1", "failure_escalation", None, message)

    assert result["escalated"] is True
    assert store.escalations
