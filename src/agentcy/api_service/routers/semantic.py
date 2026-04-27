"""
API endpoints for semantic search and SPARQL queries.

These endpoints provide access to the Fuseki triplestore for semantic
queries over the RDF knowledge graph. All endpoints require FUSEKI_ENABLE=1.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agentcy.api_service.dependecies import get_rm
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.semantic.fuseki_client import sparql_query, sparql_ask
from agentcy.semantic import queries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/semantic", tags=["semantic"])


def _semantic_enabled() -> bool:
    """Check if semantic layer (Fuseki) is enabled."""
    raw = os.getenv("FUSEKI_ENABLE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"} or bool(os.getenv("FUSEKI_URL"))


def _require_semantic():
    """Dependency that raises 503 if semantic layer is not enabled."""
    if not _semantic_enabled():
        raise HTTPException(
            status_code=503,
            detail="Semantic layer not enabled. Set FUSEKI_ENABLE=1 to enable.",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Request/Response Models
# ──────────────────────────────────────────────────────────────────────────────


class SparqlRequest(BaseModel):
    """Request body for custom SPARQL queries."""

    query: str = Field(..., description="SPARQL SELECT or ASK query")


class SparqlResponse(BaseModel):
    """Response for SPARQL queries."""

    results: Optional[List[Dict[str, Any]]] = Field(
        None, description="Query results (for SELECT)"
    )
    boolean: Optional[bool] = Field(None, description="Boolean result (for ASK)")
    error: Optional[str] = Field(None, description="Error message if query failed")


class PlansResponse(BaseModel):
    """Response for plan search queries."""

    plans: List[Dict[str, Any]] = Field(default_factory=list)


class SimilarPlansResponse(BaseModel):
    """Response for similar plans query."""

    plan_id: str
    similar_plans: List[Dict[str, Any]] = Field(default_factory=list)


class TaskGraphResponse(BaseModel):
    """Response for task dependency graph."""

    plan_id: str
    edges: List[Dict[str, Any]] = Field(default_factory=list)


class CapabilityStatsResponse(BaseModel):
    """Response for capability statistics."""

    capabilities: List[Dict[str, Any]] = Field(default_factory=list)


class GraphSummaryResponse(BaseModel):
    """Response for graph summary."""

    total_triples: Optional[int] = None
    summary: Optional[Dict[str, Any]] = None


class AgentSuccessRateResponse(BaseModel):
    """Response for agent execution success rate."""

    agent_id: str
    total: Optional[int] = None
    successes: Optional[int] = None
    success_rate: Optional[float] = None


class TaskDurationResponse(BaseModel):
    """Response for task duration statistics."""

    capability: str
    avg_duration: Optional[float] = None
    min_duration: Optional[float] = None
    max_duration: Optional[float] = None
    sample_count: Optional[int] = None


class FailurePatternsResponse(BaseModel):
    """Response for capability failure patterns."""

    capability: str
    patterns: List[Dict[str, Any]] = Field(default_factory=list)


class DataLineageResponse(BaseModel):
    """Response for data lineage tracing."""

    run_id: str
    task_id: str
    upstream_tasks: List[Dict[str, Any]] = Field(default_factory=list)


class DownstreamImpactResponse(BaseModel):
    """Response for downstream impact tracing."""

    run_id: str
    task_id: str
    downstream_tasks: List[Dict[str, Any]] = Field(default_factory=list)


class TemplateExecutionSummaryResponse(BaseModel):
    """Response for template execution summary."""

    template_id: str
    total_executions: int = 0
    successes: int = 0
    avg_duration: Optional[float] = None
    capability_count: int = 0


class BestTemplateResponse(BaseModel):
    """Response for best template for a capability."""

    capability: str
    templates: List[Dict[str, Any]] = Field(default_factory=list)


class PlanRecommendationResponse(BaseModel):
    """Response for cross-plan learning recommendations."""

    similar_plans: List[Dict[str, Any]] = Field(default_factory=list)
    capability_stats: Dict[str, Any] = Field(default_factory=dict)
    recommended_templates: List[Dict[str, Any]] = Field(default_factory=list)


class DomainEntitiesResponse(BaseModel):
    """Response for domain entity listing."""

    entities: List[Dict[str, Any]] = Field(default_factory=list)


class DomainContextResponse(BaseModel):
    """Response for domain context related to capabilities."""

    capabilities: List[str] = Field(default_factory=list)
    entities: List[Dict[str, Any]] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# SPARQL Endpoints
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/sparql", response_model=SparqlResponse)
async def execute_sparql(
    request: SparqlRequest,
    _: None = Depends(_require_semantic),
):
    """
    Execute a custom SPARQL query.

    Only SELECT and ASK queries are allowed for safety.
    Modifying queries (INSERT, DELETE, UPDATE) are not permitted.
    """
    query_upper = request.query.strip().upper()

    # Safety check: only allow read-only queries
    if query_upper.startswith("SELECT"):
        results = await sparql_query(request.query)
        if results is None:
            return SparqlResponse(error="Query failed or Fuseki unavailable")
        return SparqlResponse(results=results)

    elif query_upper.startswith("ASK"):
        result = await sparql_ask(request.query)
        if result is None:
            return SparqlResponse(error="Query failed or Fuseki unavailable")
        return SparqlResponse(boolean=result)

    else:
        raise HTTPException(
            status_code=400,
            detail="Only SELECT and ASK queries are allowed. "
            "INSERT, DELETE, and UPDATE queries are not permitted.",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Search by Capability
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/plans/by-capability/{capability}", response_model=PlansResponse)
async def find_plans_by_capability(
    capability: str,
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    _: None = Depends(_require_semantic),
):
    """
    Find plans with tasks requiring a specific capability.

    Returns plans sorted by the number of tasks with that capability.
    """
    results = await queries.find_plans_by_capability(capability, limit=limit)
    return PlansResponse(plans=results or [])


# ──────────────────────────────────────────────────────────────────────────────
# Search by Agent
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/plans/by-agent/{agent_id}", response_model=PlansResponse)
async def find_plans_by_agent(
    agent_id: str,
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    _: None = Depends(_require_semantic),
):
    """
    Find plans with tasks assigned to a specific agent.

    Returns plans sorted by the number of tasks assigned to that agent.
    """
    results = await queries.find_plans_by_agent(agent_id, limit=limit)
    return PlansResponse(plans=results or [])


# ──────────────────────────────────────────────────────────────────────────────
# Similar Plans
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/plans/{plan_id}/similar", response_model=SimilarPlansResponse)
async def find_similar_plans(
    plan_id: str,
    limit: int = Query(10, ge=1, le=100, description="Max results"),
    _: None = Depends(_require_semantic),
):
    """
    Find plans similar to a given plan based on shared capabilities.

    Similarity is computed by counting shared capabilities between plans.
    Plans with more shared capabilities rank higher.
    """
    results = await queries.find_similar_plans(plan_id, limit=limit)
    return SimilarPlansResponse(plan_id=plan_id, similar_plans=results or [])


# ──────────────────────────────────────────────────────────────────────────────
# Task Graph
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/plans/{plan_id}/task-graph", response_model=TaskGraphResponse)
async def get_plan_task_graph(
    plan_id: str,
    _: None = Depends(_require_semantic),
):
    """
    Get the task dependency graph for a plan.

    Returns edges representing task dependencies (from_task -> to_task).
    """
    results = await queries.get_plan_task_graph(plan_id)
    return TaskGraphResponse(plan_id=plan_id, edges=results or [])


@router.get("/plans/{plan_id}/details")
async def get_plan_details(
    plan_id: str,
    _: None = Depends(_require_semantic),
):
    """
    Get detailed information about a plan including all tasks.
    """
    results = await queries.get_plan_details(plan_id)
    return {"plan_id": plan_id, "tasks": results or []}


# ──────────────────────────────────────────────────────────────────────────────
# Statistics
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/capabilities/stats", response_model=CapabilityStatsResponse)
async def get_capability_stats(
    _: None = Depends(_require_semantic),
):
    """
    Get usage statistics for all capabilities.

    Returns a list of capabilities with their task and plan counts.
    """
    results = await queries.get_capability_usage_stats()
    return CapabilityStatsResponse(capabilities=results or [])


@router.get("/graph/summary", response_model=GraphSummaryResponse)
async def get_graph_summary(
    _: None = Depends(_require_semantic),
):
    """
    Get a summary of the RDF knowledge graph.

    Returns total triple count and entity counts by type.
    """
    total = await queries.count_all_triples()
    summary = await queries.get_graph_summary()
    return GraphSummaryResponse(total_triples=total, summary=summary)


# ──────────────────────────────────────────────────────────────────────────────
# Search by Tag
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/search/by-tag/{tag}")
async def search_by_tag(
    tag: str,
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    _: None = Depends(_require_semantic),
):
    """
    Search for tasks and plans by tag.
    """
    results = await queries.search_by_tag(tag, limit=limit)
    return {"tag": tag, "results": results or []}


# ──────────────────────────────────────────────────────────────────────────────
# Execution History
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/agents/{agent_id}/success-rate",
    response_model=AgentSuccessRateResponse,
    summary="Agent success rate",
)
async def get_agent_success_rate(
    agent_id: str,
    _: None = Depends(_require_semantic),
):
    """Get task success rate for an agent from execution history."""
    results = await queries.get_agent_success_rate(agent_id)
    if not results:
        return AgentSuccessRateResponse(agent_id=agent_id)
    row = results[0]
    total = int(row.get("total", 0))
    successes = int(row.get("successes", 0))
    rate = successes / total if total > 0 else None
    return AgentSuccessRateResponse(
        agent_id=agent_id, total=total, successes=successes, success_rate=rate,
    )


@router.get(
    "/capabilities/{capability}/avg-duration",
    response_model=TaskDurationResponse,
    summary="Average task duration by capability",
)
async def get_task_avg_duration(
    capability: str,
    _: None = Depends(_require_semantic),
):
    """Get average execution duration for tasks with a given capability."""
    results = await queries.get_task_avg_duration(capability)
    if not results:
        return TaskDurationResponse(capability=capability)
    row = results[0]
    return TaskDurationResponse(
        capability=capability,
        avg_duration=float(row["avgDuration"]) if row.get("avgDuration") else None,
        min_duration=float(row["minDuration"]) if row.get("minDuration") else None,
        max_duration=float(row["maxDuration"]) if row.get("maxDuration") else None,
        sample_count=int(row["sampleCount"]) if row.get("sampleCount") else None,
    )


@router.get(
    "/capabilities/{capability}/failure-patterns",
    response_model=FailurePatternsResponse,
    summary="Failure patterns by capability",
)
async def get_failure_patterns(
    capability: str,
    limit: int = Query(20, ge=1, le=100),
    _: None = Depends(_require_semantic),
):
    """Get agent+capability failure frequency."""
    results = await queries.get_failure_patterns(capability, limit=limit)
    return FailurePatternsResponse(capability=capability, patterns=results or [])


# ──────────────────────────────────────────────────────────────────────────────
# Data Flow Lineage
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/runs/{run_id}/tasks/{task_id}/lineage",
    response_model=DataLineageResponse,
    summary="Upstream data lineage",
)
async def get_data_lineage(
    run_id: str,
    task_id: str,
    _: None = Depends(_require_semantic),
):
    """Trace upstream data sources for a task in a specific run."""
    results = await queries.get_data_lineage(run_id, task_id)
    return DataLineageResponse(run_id=run_id, task_id=task_id, upstream_tasks=results or [])


@router.get(
    "/runs/{run_id}/tasks/{task_id}/impact",
    response_model=DownstreamImpactResponse,
    summary="Downstream impact",
)
async def get_downstream_impact(
    run_id: str,
    task_id: str,
    _: None = Depends(_require_semantic),
):
    """Trace downstream consumers of a task's output in a specific run."""
    results = await queries.get_downstream_impact(run_id, task_id)
    return DownstreamImpactResponse(
        run_id=run_id, task_id=task_id, downstream_tasks=results or [],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Status
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/status")
async def semantic_status():
    """
    Get the status of the semantic layer.

    Returns whether Fuseki is enabled and connection details.
    """
    enabled = _semantic_enabled()
    fuseki_url = os.getenv("FUSEKI_URL", "http://fuseki:3030") if enabled else None
    fuseki_dataset = os.getenv("FUSEKI_DATASET", "agentcy") if enabled else None

    return {
        "enabled": enabled,
        "fuseki_url": fuseki_url,
        "fuseki_dataset": fuseki_dataset,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Ontology Version Management
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/ontology/version")
async def get_ontology_version(rm: ResourceManager = Depends(get_rm)):
    """
    Get current ontology version info.

    Returns version metadata including checksum, version number, and last update time.
    """
    if rm.ontology_manager is None:
        raise HTTPException(503, "Ontology manager not available")

    version = rm.ontology_manager.get_ontology_version()
    return version or {"version": None, "message": "No version tracked yet"}


@router.get("/shapes/version")
async def get_shapes_version(rm: ResourceManager = Depends(get_rm)):
    """
    Get current SHACL shapes version info.

    Returns version metadata including checksum, version number, and last update time.
    """
    if rm.ontology_manager is None:
        raise HTTPException(503, "Ontology manager not available")

    version = rm.ontology_manager.get_shapes_version()
    return version or {"version": None, "message": "No version tracked yet"}


@router.get(
    "/templates/{template_id}/execution-summary",
    response_model=TemplateExecutionSummaryResponse,
)
async def template_execution_summary(
    template_id: str,
    _: None = Depends(_require_semantic),
):
    """Get aggregate execution stats for tasks matching a template's capabilities."""
    results = await queries.get_template_execution_summary(template_id)
    if not results:
        return TemplateExecutionSummaryResponse(template_id=template_id)
    row = results[0]
    return TemplateExecutionSummaryResponse(
        template_id=template_id,
        total_executions=int(row.get("totalExecs", 0)),
        successes=int(row.get("successes", 0)),
        avg_duration=float(row["avgDuration"]) if row.get("avgDuration") else None,
        capability_count=int(row.get("capabilityCount", 0)),
    )


@router.get(
    "/capabilities/{capability}/best-template",
    response_model=BestTemplateResponse,
)
async def best_template_for_capability(
    capability: str,
    limit: int = Query(5, ge=1, le=50),
    _: None = Depends(_require_semantic),
):
    """Rank templates by execution success rate for a capability."""
    results = await queries.get_best_template_for_capability(capability, limit=limit)
    templates = []
    for row in results or []:
        total = int(row.get("total", 0))
        successes = int(row.get("successes", 0))
        templates.append({
            "template_id": row.get("templateId"),
            "template_name": row.get("templateName"),
            "total": total,
            "successes": successes,
            "success_rate": round(successes / total, 4) if total > 0 else 0.0,
        })
    return BestTemplateResponse(capability=capability, templates=templates)


# ──────────────────────────────────────────────────────────────────────────────
# Cross-Plan Learning
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/plans/recommend",
    response_model=PlanRecommendationResponse,
    summary="Cross-plan recommendations",
)
async def recommend_plans(
    capabilities: str = Query(..., description="Comma-separated capabilities"),
    limit: int = Query(3, ge=1, le=20),
    _: None = Depends(_require_semantic),
):
    """Get recommendations from similar past plans based on capabilities."""
    from agentcy.semantic.plan_recommender import get_plan_context

    cap_list = [c.strip() for c in capabilities.split(",") if c.strip()]
    context = await get_plan_context(capabilities=cap_list, limit=limit)
    if not context:
        return PlanRecommendationResponse()
    return PlanRecommendationResponse(
        similar_plans=context.get("similar_plans", []),
        capability_stats=context.get("capability_stats", {}),
        recommended_templates=context.get("recommended_templates", []),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Domain Knowledge
# ──────────────────────────────────────────────────────────────────────────────


@router.get(
    "/domain/entities",
    response_model=DomainEntitiesResponse,
    summary="List domain entities",
)
async def list_domain_entities(
    entity_type: Optional[str] = Query(None, alias="type", description="Filter by entity type"),
    limit: int = Query(50, ge=1, le=500),
    _: None = Depends(_require_semantic),
):
    """List extracted domain entities, optionally filtered by type."""
    results = await queries.get_domain_entities(entity_type, limit=limit)
    return DomainEntitiesResponse(entities=results or [])


@router.get(
    "/domain/context",
    response_model=DomainContextResponse,
    summary="Domain context for capabilities",
)
async def domain_context_for_capabilities(
    capabilities: str = Query(..., description="Comma-separated capabilities"),
    limit: int = Query(10, ge=1, le=100),
    _: None = Depends(_require_semantic),
):
    """Find domain entities related to given capabilities."""
    cap_list = [c.strip() for c in capabilities.split(",") if c.strip()]
    results = await queries.get_domain_context_for_capabilities(cap_list, limit=limit)
    return DomainContextResponse(capabilities=cap_list, entities=results or [])


# ──────────────────────────────────────────────────────────────────────────────
# Sync
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/sync")
async def sync_semantic_layer(
    force: bool = Query(False, description="Force re-upload even if no changes"),
    rm: ResourceManager = Depends(get_rm),
    _: None = Depends(_require_semantic),
):
    """
    Sync ontology and SHACL shapes to Fuseki.

    Checks for changes and uploads if files have been modified.
    Use force=true to re-upload regardless of changes.
    """
    if rm.ontology_manager is None:
        raise HTTPException(503, "Ontology manager not available")

    result = await rm.ontology_manager.sync_all(force=force)
    return result
