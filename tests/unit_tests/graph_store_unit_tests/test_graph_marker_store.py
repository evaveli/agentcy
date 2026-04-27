import re
from contextlib import contextmanager
from uuid import uuid4

from couchbase.exceptions import DocumentNotFoundException

from src.agentcy.orchestrator_core.stores.graph_marker_store import GraphMarkerStore
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    AffordanceMarker,
    BlueprintBid,
    CallForProposal,
    ContractAward,
    ExecutionReport,
    EscalationNotice,
    EthicsCheckResult,
    HumanApproval,
    PlanDraft,
    ReservationMarker,
    StrategyPlan,
    TaskSpec,
    AuditLogEntry,
)


class _FakeResult:
    def __init__(self, value):
        self.content_as = {dict: value}


class _FakeCollection:
    def __init__(self):
        self._data = {}

    def upsert(self, key, value, **_kw):
        self._data[key] = value
        return _FakeResult(value)

    def get(self, key, **_kw):
        if key not in self._data:
            raise DocumentNotFoundException()
        return _FakeResult(self._data[key])


class _FakeCluster:
    def __init__(self, data):
        self._data = data

    def query(self, statement):
        match = re.search(r"LIKE '([^']+)%'", statement)
        prefix = match.group(1) if match else ""
        rows = []
        for key, doc in self._data.items():
            if key.startswith(prefix):
                row = {"id": key}
                row.update(doc)
                rows.append(row)
        return rows


class _FakeBundle:
    def __init__(self, collection):
        self.cluster = _FakeCluster(collection._data)
        self._collection = collection

    def collection(self, _logical):
        return self._collection


class _FakePool:
    def __init__(self):
        self._collection = _FakeCollection()

    @contextmanager
    def collections(self, *_keys, **_kw):
        yield self._collection

    def acquire(self, *_a, **_kw):
        return _FakeBundle(self._collection)

    def release(self, _bundle):
        return None


def test_task_spec_roundtrip():
    store = GraphMarkerStore(_FakePool())
    spec = TaskSpec(
        task_id="task-1",
        username="alice",
        description="Do something",
        required_capabilities=["plan"],
        tags=["core"],
    )
    store.upsert_task_spec(username="alice", spec=spec)
    doc = store.get_task_spec(username="alice", task_id="task-1")
    assert doc is not None
    assert doc["task_id"] == "task-1"
    assert doc["required_capabilities"] == ["plan"]
    listed, total = store.list_task_specs(username="alice")
    assert len(listed) == 1


def test_markers_bids_and_plan_drafts():
    store = GraphMarkerStore(_FakePool())
    username = "alice"

    affordance = AffordanceMarker(task_id="task-2", agent_id="agent-a")
    store.add_affordance_marker(username=username, marker=affordance)
    aff_list, aff_total = store.list_affordance_markers(username=username, task_id="task-2")
    assert len(aff_list) == 1
    assert aff_list[0]["agent_id"] == "agent-a"

    reservation = ReservationMarker(task_id="task-2", agent_id="agent-a")
    store.add_reservation_marker(username=username, marker=reservation)
    res_list, res_total = store.list_reservation_markers(username=username, task_id="task-2")
    assert len(res_list) == 1
    assert res_list[0]["agent_id"] == "agent-a"

    bid = BlueprintBid(task_id="task-2", bidder_id="agent-a", bid_score=0.9)
    bid_id = store.add_bid(username=username, bid=bid)
    bids, bids_total = store.list_bids(username=username, task_id="task-2")
    assert any(b.get("bid_id") == bid_id for b in bids)

    plan_id = str(uuid4())
    draft = PlanDraft(
        plan_id=plan_id,
        username=username,
        pipeline_id="pipeline-1",
        graph_spec={"nodes": ["task-1"], "edges": []},
    )
    store.save_plan_draft(username=username, draft=draft)
    loaded = store.get_plan_draft(username=username, plan_id=plan_id)
    assert loaded is not None
    plans, plans_total = store.list_plan_drafts(username=username, pipeline_id="pipeline-1")
    assert any(p.get("plan_id") == plan_id for p in plans)

    cfp = CallForProposal(task_id="task-2")
    store.add_cfp(username=username, cfp=cfp)
    cfps, cfps_total = store.list_cfps(username=username, task_id="task-2")
    assert any(c.get("cfp_id") == cfp.cfp_id for c in cfps)

    award = ContractAward(task_id="task-2", bidder_id="agent-a", cfp_id=cfp.cfp_id)
    store.add_contract_award(username=username, award=award)
    awards, awards_total = store.list_contract_awards(username=username, task_id="task-2")
    assert any(a.get("award_id") == award.award_id for a in awards)

    approval = HumanApproval(plan_id=plan_id, username=username, approver="tester", approved=True)
    store.save_human_approval(username=username, approval=approval)
    approvals, approvals_total = store.list_human_approvals(username=username, plan_id=plan_id)
    assert approvals

    ethics = EthicsCheckResult(plan_id=plan_id, approved=True)
    store.save_ethics_check(username=username, check=ethics)
    ethics_checks, ethics_total = store.list_ethics_checks(username=username, plan_id=plan_id)
    assert ethics_checks

    strategy = StrategyPlan(plan_id=plan_id, summary="do it")
    store.save_strategy_plan(username=username, strategy=strategy)
    strategies, strategies_total = store.list_strategy_plans(username=username, plan_id=plan_id)
    assert any(s.get("strategy_id") == strategy.strategy_id for s in strategies)

    report = ExecutionReport(plan_id=plan_id, success_rate=1.0)
    store.save_execution_report(username=username, report=report)
    reports, reports_total = store.list_execution_reports(username=username, plan_id=plan_id)
    assert any(r.get("report_id") == report.report_id for r in reports)

    audit = AuditLogEntry(event_type="test", pipeline_run_id="run-1", actor="tester")
    store.add_audit_log(username=username, entry=audit)
    audits, audits_total = store.list_audit_logs(username=username, pipeline_run_id="run-1")
    assert audits

    escalation = EscalationNotice(pipeline_run_id="run-1", reason="fail")
    store.save_escalation_notice(username=username, notice=escalation)
    escalations, escalations_total = store.list_escalation_notices(username=username, pipeline_run_id="run-1")
    assert escalations
