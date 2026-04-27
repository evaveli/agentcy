import uuid

from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
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


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


def test_graph_marker_store_roundtrip():
    pool = _pool()
    store = GraphMarkerStore(pool)
    username = f"graph_integration_{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"
    plan_id = str(uuid.uuid4())

    try:
        spec = TaskSpec(
            task_id=task_id,
            username=username,
            description="integration task",
            required_capabilities=["plan"],
        )
        store.upsert_task_spec(username=username, spec=spec)
        assert store.get_task_spec(username=username, task_id=task_id)
        assert store.list_task_specs(username=username)

        store.add_affordance_marker(
            username=username,
            marker=AffordanceMarker(task_id=task_id, agent_id="agent-a"),
        )
        assert store.list_affordance_markers(username=username, task_id=task_id)

        store.add_reservation_marker(
            username=username,
            marker=ReservationMarker(task_id=task_id, agent_id="agent-a"),
        )
        assert store.list_reservation_markers(username=username, task_id=task_id)

        bid_id = store.add_bid(
            username=username,
            bid=BlueprintBid(task_id=task_id, bidder_id="agent-a", bid_score=0.8),
        )
        bids = store.list_bids(username=username, task_id=task_id)
        assert any(b.get("bid_id") == bid_id for b in bids)

        draft = PlanDraft(
            plan_id=plan_id,
            username=username,
            pipeline_id="pipeline-1",
            graph_spec={"nodes": [task_id], "edges": []},
        )
        store.save_plan_draft(username=username, draft=draft)
        assert store.get_plan_draft(username=username, plan_id=plan_id)
        assert store.list_plan_drafts(username=username, pipeline_id="pipeline-1")

        cfp = CallForProposal(task_id=task_id)
        store.add_cfp(username=username, cfp=cfp)
        assert store.list_cfps(username=username, task_id=task_id)

        award = ContractAward(task_id=task_id, bidder_id="agent-a", cfp_id=cfp.cfp_id)
        store.add_contract_award(username=username, award=award)
        assert store.list_contract_awards(username=username, task_id=task_id)

        approval = HumanApproval(plan_id=plan_id, username=username, approver="tester", approved=True)
        store.save_human_approval(username=username, approval=approval)
        assert store.list_human_approvals(username=username, plan_id=plan_id)

        ethics = EthicsCheckResult(plan_id=plan_id, approved=True)
        store.save_ethics_check(username=username, check=ethics)
        assert store.list_ethics_checks(username=username, plan_id=plan_id)

        strategy = StrategyPlan(plan_id=plan_id, summary="do it")
        store.save_strategy_plan(username=username, strategy=strategy)
        assert store.list_strategy_plans(username=username, plan_id=plan_id)

        report = ExecutionReport(plan_id=plan_id, success_rate=1.0)
        store.save_execution_report(username=username, report=report)
        assert store.list_execution_reports(username=username, plan_id=plan_id)

        audit = AuditLogEntry(event_type="test", pipeline_run_id="run-1", actor="tester")
        store.add_audit_log(username=username, entry=audit)
        assert store.list_audit_logs(username=username, pipeline_run_id="run-1")

        escalation = EscalationNotice(pipeline_run_id="run-1", reason="fail")
        store.save_escalation_notice(username=username, notice=escalation)
        assert store.list_escalation_notices(username=username, pipeline_run_id="run-1")
    finally:
        pool.close_all()
