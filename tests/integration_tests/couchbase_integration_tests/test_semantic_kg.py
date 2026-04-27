import uuid

import pytest
from rdflib import Graph

from src.agentcy.agent_runtime.services import audit_logger, graph_builder
from src.agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS
from src.agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pydantic_models.multi_agent_pipeline import BlueprintBid, ExecutionReport, PlanDraft, TaskSpec


def _pool():
    return DynamicCouchbaseConnectionPool(
        bucket_name=CB_BUCKET,
        collections_map=CB_COLLECTIONS,
        min_size=1,
        max_size=3,
        idle_timeout=5,
    )


@pytest.mark.asyncio
async def test_graph_builder_embeds_semantic_graph(monkeypatch):
    monkeypatch.setenv("SEMANTIC_RDF_EXPORT", "1")
    monkeypatch.setenv("FUSEKI_ENABLE", "0")
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"semantic_{uuid.uuid4().hex[:6]}"
    pipeline_id = f"pipe-{uuid.uuid4().hex[:6]}"

    try:
        spec = TaskSpec(
            task_id="task-1",
            username=username,
            description="semantic test",
            required_capabilities=["plan"],
            tags=["core"],
        )
        store.upsert_task_spec(username=username, spec=spec)
        bid = BlueprintBid(task_id="task-1", bidder_id="agent-a", bid_score=0.9)
        store.add_bid(username=username, bid=bid)

        draft = await graph_builder.build_plan_draft(
            rm,
            username=username,
            pipeline_id=pipeline_id,
        )
        semantic = draft.graph_spec.get("semantic_graph")
        assert semantic
        assert semantic.get("format") == "turtle"
        graph = Graph().parse(data=semantic.get("data", ""), format="turtle")
        assert len(graph) > 0
    finally:
        pool.close_all()


@pytest.mark.asyncio
async def test_audit_logger_attaches_prov_graph(monkeypatch):
    monkeypatch.setenv("FUSEKI_ENABLE", "0")
    pool = _pool()
    rm = ResourceManager(pool, None, None)
    store = rm.graph_marker_store
    assert store is not None

    username = f"audit_{uuid.uuid4().hex[:6]}"
    pipeline_id = f"pipe-{uuid.uuid4().hex[:6]}"
    plan_id = str(uuid.uuid4())

    try:
        draft = PlanDraft(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            graph_spec={"tasks": [], "edges": []},
            is_valid=True,
        )
        store.save_plan_draft(username=username, draft=draft)
        store.save_execution_report(username=username, report=ExecutionReport(plan_id=plan_id, success_rate=1.0))

        message = {
            "username": username,
            "pipeline_id": pipeline_id,
            "plan_id": plan_id,
            "pipeline_run_id": "run-1",
            "data": {},
        }
        result = await audit_logger.run(rm, "run-1", "audit_logger", None, message)
        assert result["logged"] is True

        audits = store.list_audit_logs(username=username, pipeline_run_id="run-1")
        assert audits
        payload = audits[0].get("payload") or {}
        assert payload.get("prov_rdf")
        assert payload.get("prov_format") == "turtle"
        graph = Graph().parse(data=payload.get("prov_rdf"), format="turtle")
        assert len(graph) > 0
    finally:
        pool.close_all()
