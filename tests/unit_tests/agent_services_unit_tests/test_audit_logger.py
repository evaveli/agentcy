import pytest

from src.agentcy.agent_runtime.services.audit_logger import run
from src.agentcy.pydantic_models.multi_agent_pipeline import ExecutionReport, PlanDraft


class _FakeStore:
    def __init__(self):
        self.drafts = {}
        self.human = []
        self.ethics = []
        self.execution = []
        self.escalations = []
        self.audit_logs = []

    def list_plan_drafts(self, *, username, pipeline_id=None):
        drafts = list(self.drafts.values())
        if pipeline_id:
            drafts = [d for d in drafts if d.get("pipeline_id") == pipeline_id]
        return drafts, len(drafts)

    def get_plan_draft(self, *, username, plan_id):
        return self.drafts.get(plan_id)

    def save_plan_draft(self, *, username, draft):
        self.drafts[draft.plan_id] = draft.model_dump(mode="json")

    def list_human_approvals(self, *, username, plan_id=None):
        items = list(self.human)
        return items, len(items)

    def list_ethics_checks(self, *, username, plan_id=None):
        items = list(self.ethics)
        return items, len(items)

    def list_execution_reports(self, *, username, plan_id=None):
        items = [report.model_dump(mode="json") for report in self.execution]
        return items, len(items)

    def list_escalation_notices(self, *, username, pipeline_run_id=None):
        items = list(self.escalations)
        return items, len(items)

    def add_audit_log(self, *, username, entry):
        self.audit_logs.append((username, entry))
        return "audit-1"


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store


@pytest.mark.asyncio
async def test_audit_logger_records_summary():
    store = _FakeStore()
    rm = _FakeRM(store)
    username = "alice"
    pipeline_id = "pipe-1"

    draft = PlanDraft(
        plan_id="plan-1",
        username=username,
        pipeline_id=pipeline_id,
        graph_spec={"tasks": [], "edges": []},
        is_valid=True,
    )
    store.save_plan_draft(username=username, draft=draft)
    store.execution.append(ExecutionReport(plan_id="plan-1", success_rate=1.0))

    message = {
        "username": username,
        "pipeline_id": pipeline_id,
        "plan_id": "plan-1",
        "pipeline_run_id": "run-1",
        "data": {},
    }
    result = await run(rm, "run-1", "audit_logger", None, message)

    assert result["logged"] is True
    assert result["traceability_score"] > 0
    assert store.audit_logs
