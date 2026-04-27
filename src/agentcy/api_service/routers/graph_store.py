# src/agentcy/api_service/routers/graph_store.py

from __future__ import annotations

from typing import List, Optional, Dict, Any, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from agentcy.api_service.dependecies import get_rm, get_publisher, pagination_params, sort_params, CommandPublisher
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.agent_runtime.services.graph_builder import build_plan_draft
from agentcy.agent_runtime.services.llm_strategist_loop import apply_suggestion_decision
from agentcy.pydantic_models.pagination import (
    PaginationParams,
    SortParams,
    PaginatedResponse,
    PaginatedDict,
)
from agentcy.pydantic_models.aggregations import (
    BidScoreStats,
    EntityCounts,
    GraphStoreStats,
    StatusCount,
)
from agentcy.pydantic_models.multi_agent_pipeline import (
    AffordanceMarker,
    BlueprintBid,
    CallForProposal,
    ContractAward,
    ExecutionReport,
    EscalationNotice,
    EthicsCheckResult,
    EthicsPolicy,
    HumanApproval,
    StrategyPlan,
    AuditLogEntry,
    PlanDraft,
    ReservationMarker,
    TaskSpec,
)

router = APIRouter()


def _store(rm: ResourceManager):
    store = rm.graph_marker_store
    if store is None:
        raise HTTPException(500, "Graph marker store is not configured")
    return store


class BuildPlanDraftRequest(BaseModel):
    pipeline_id: str = Field(..., description="Pipeline that owns the plan draft.")
    task_ids: Optional[List[str]] = Field(default=None, description="Optional task subset.")


class PlanSuggestionDecision(BaseModel):
    approved: bool
    approver: str = Field("admin", description="Reviewer applying the suggestion.")
    rationale: Optional[str] = Field(default=None, description="Decision rationale.")


# ─────────────────────────────────────────────────────────────────────────────
# Task specs
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/task-specs",
    status_code=status.HTTP_201_CREATED,
)
async def upsert_task_spec(
    username: str,
    payload: TaskSpec,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    store.upsert_task_spec(username=username, spec=payload)
    doc = store.get_task_spec(username=username, task_id=payload.task_id)
    if doc is None:
        raise HTTPException(500, "Task spec write failed")
    return doc


@router.get("/graph-store/{username}/task-specs/{task_id}")
async def get_task_spec(
    username: str,
    task_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    doc = store.get_task_spec(username=username, task_id=task_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task spec not found")
    return doc


@router.get("/graph-store/{username}/task-specs")
async def list_task_specs(
    username: str,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_task_specs(
        username=username,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Markers
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/markers/affordance",
    status_code=status.HTTP_201_CREATED,
)
async def add_affordance_marker(
    username: str,
    payload: AffordanceMarker,
    ttl_seconds: Optional[int] = Query(default=None, ge=0),
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.add_affordance_marker(username=username, marker=payload, ttl_seconds=ttl_seconds)
    return {"key": key}


@router.get("/graph-store/{username}/markers/affordance")
async def list_affordance_markers(
    username: str,
    task_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_affordance_markers(
        username=username,
        task_id=task_id,
        agent_id=agent_id,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.post(
    "/graph-store/{username}/markers/reservation",
    status_code=status.HTTP_201_CREATED,
)
async def add_reservation_marker(
    username: str,
    payload: ReservationMarker,
    ttl_seconds: Optional[int] = Query(default=None, ge=0),
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.add_reservation_marker(username=username, marker=payload, ttl_seconds=ttl_seconds)
    return {"key": key}


@router.get("/graph-store/{username}/markers/reservation")
async def list_reservation_markers(
    username: str,
    task_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_reservation_markers(
        username=username,
        task_id=task_id,
        agent_id=agent_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Bids
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/bids",
    status_code=status.HTTP_201_CREATED,
)
async def add_bid(
    username: str,
    payload: BlueprintBid,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    bid_id = store.add_bid(username=username, bid=payload)
    return {"bid_id": bid_id}


@router.get("/graph-store/{username}/bids")
async def list_bids(
    username: str,
    task_id: Optional[str] = None,
    bidder_id: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_bids(
        username=username,
        task_id=task_id,
        bidder_id=bidder_id,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Plan drafts
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/plan-drafts",
    status_code=status.HTTP_201_CREATED,
)
async def save_plan_draft(
    username: str,
    payload: PlanDraft,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    store.save_plan_draft(username=username, draft=payload)
    doc = store.get_plan_draft(username=username, plan_id=payload.plan_id)
    if doc is None:
        raise HTTPException(500, "Plan draft write failed")
    return doc


@router.get("/graph-store/{username}/plan-drafts/{plan_id}")
async def get_plan_draft(
    username: str,
    plan_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    doc = store.get_plan_draft(username=username, plan_id=plan_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan draft not found")
    return doc


@router.get("/graph-store/{username}/plan-drafts")
async def list_plan_drafts(
    username: str,
    pipeline_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_plan_drafts(
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.get("/graph-store/{username}/plan-revisions")
async def list_plan_revisions(
    username: str,
    plan_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_plan_revisions(
        username=username,
        plan_id=plan_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ── Plan revision diff (must be declared before {revision} to avoid path conflict)
@router.get("/graph-store/{username}/plan-revisions/{plan_id}/diff")
async def diff_plan_revisions(
    username: str,
    plan_id: str,
    from_rev: int = Query(..., alias="from", description="Base revision number."),
    to_rev: int = Query(..., alias="to", description="Target revision number."),
    rm: ResourceManager = Depends(get_rm),
):
    """Compare two plan revisions and return the diff."""
    store = _store(rm)
    rev_from = store.get_plan_revision(username=username, plan_id=plan_id, revision=from_rev)
    if rev_from is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Revision {from_rev} not found")
    rev_to = store.get_plan_revision(username=username, plan_id=plan_id, revision=to_rev)
    if rev_to is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Revision {to_rev} not found")

    graph_from = rev_from.get("graph_spec") or {}
    graph_to = rev_to.get("graph_spec") or {}

    tasks_from = {t.get("task_id"): t for t in (graph_from.get("tasks") or []) if t.get("task_id")}
    tasks_to = {t.get("task_id"): t for t in (graph_to.get("tasks") or []) if t.get("task_id")}

    added_tasks = [tid for tid in tasks_to if tid not in tasks_from]
    removed_tasks = [tid for tid in tasks_from if tid not in tasks_to]
    modified_tasks: Dict[str, Any] = {}
    for tid in set(tasks_from) & set(tasks_to):
        if tasks_from[tid] != tasks_to[tid]:
            modified_tasks[tid] = {"from": tasks_from[tid], "to": tasks_to[tid]}

    edges_from = {(e.get("from"), e.get("to")) for e in (graph_from.get("edges") or [])}
    edges_to = {(e.get("from"), e.get("to")) for e in (graph_to.get("edges") or [])}

    added_edges = [{"from": f, "to": t} for f, t in (edges_to - edges_from)]
    removed_edges = [{"from": f, "to": t} for f, t in (edges_from - edges_to)]

    return {
        "plan_id": plan_id,
        "from_revision": from_rev,
        "to_revision": to_rev,
        "tasks": {
            "added": added_tasks,
            "removed": removed_tasks,
            "modified": modified_tasks,
        },
        "edges": {
            "added": added_edges,
            "removed": removed_edges,
        },
        "stored_delta": rev_to.get("delta"),
        "summary": (
            f"{len(added_tasks)} task(s) added, "
            f"{len(removed_tasks)} task(s) removed, "
            f"{len(modified_tasks)} task(s) modified, "
            f"{len(added_edges)} edge(s) added, "
            f"{len(removed_edges)} edge(s) removed"
        ),
    }


# ── Single plan revision GET
@router.get("/graph-store/{username}/plan-revisions/{plan_id}/{revision}")
async def get_plan_revision(
    username: str,
    plan_id: str,
    revision: int,
    rm: ResourceManager = Depends(get_rm),
):
    """Retrieve a specific plan revision by plan_id and revision number."""
    store = _store(rm)
    doc = store.get_plan_revision(username=username, plan_id=plan_id, revision=revision)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan revision not found")
    return doc


# ── Manual plan revision (via message bus)
class ManualPlanRevisionRequest(BaseModel):
    plan_id: str = Field(..., description="Plan to revise.")
    delta: Dict[str, Any] = Field(..., description="Delta to apply (task_overrides, add_tasks, etc.).")
    reason: Optional[str] = Field(default=None, description="Rationale for the revision.")
    approver: str = Field(default="user", description="Who is submitting this revision.")


@router.post(
    "/graph-store/{username}/plan-revisions",
    status_code=status.HTTP_202_ACCEPTED,
)
async def apply_manual_plan_revision(
    username: str,
    payload: ManualPlanRevisionRequest,
    rm: ResourceManager = Depends(get_rm),
    pub: CommandPublisher = Depends(get_publisher),
):
    """
    Apply a user-submitted delta to a plan draft.

    Validates the delta upfront, stores the candidate in Couchbase, then
    publishes a ``RevisePlanCommand`` to the message bus for async application.
    """
    from agentcy.agent_runtime.services.plan_revision_utils import (
        apply_delta,
        validate_candidate_graph,
    )
    from agentcy.pydantic_models.commands import RevisePlanCommand
    from agentcy.pydantic_models.multi_agent_pipeline import PlanDraft

    store = _store(rm)

    draft_doc = store.get_plan_draft(username=username, plan_id=payload.plan_id)
    if draft_doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan draft not found")
    draft = PlanDraft.model_validate(draft_doc)
    base_graph = draft.graph_spec or {}

    candidate_graph, applied = apply_delta(base_graph, payload.delta)
    if applied == 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Delta produced no changes")

    run_doc: Dict[str, Any] = {}
    if draft.pipeline_run_id and getattr(rm, "ephemeral_store", None) is not None:
        try:
            run_doc = rm.ephemeral_store.read_run(
                username, draft.pipeline_id, draft.pipeline_run_id,
            ) or {}
        except Exception:
            pass

    validation = validate_candidate_graph(
        candidate_graph=candidate_graph,
        base_graph=base_graph,
        run_doc=run_doc if isinstance(run_doc, dict) else {},
        plan_id=draft.plan_id,
        pipeline_id=draft.pipeline_id,
        username=username,
    )
    if not validation.get("conforms"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Validation failed", "validation": validation},
        )

    # Store candidate in Couchbase (payload_ref pattern)
    next_revision = int(getattr(draft, "revision", 1) or 1) + 1
    ref_key = f"revision_candidate::{username}::{draft.plan_id}::{next_revision}"
    candidate_doc = {
        "candidate_graph": candidate_graph,
        "delta": payload.delta,
        "validation": validation,
        "base_revision": int(getattr(draft, "revision", 1) or 1),
        "next_revision": next_revision,
        "plan_id": draft.plan_id,
        "pipeline_id": draft.pipeline_id,
        "pipeline_run_id": draft.pipeline_run_id,
    }
    store.upsert_raw(ref_key, candidate_doc)

    # Publish RevisePlanCommand to message bus
    cmd = RevisePlanCommand(
        username=username,
        pipeline_id=draft.pipeline_id,
        plan_id=draft.plan_id,
        pipeline_run_id=draft.pipeline_run_id,
        payload_ref=ref_key,
        suggestion_id=None,
        created_by=payload.approver,
        reason=payload.reason or "manual_revision",
    )
    await pub.publish("commands.revise_plan", cmd)

    return {
        "plan_id": draft.plan_id,
        "payload_ref": ref_key,
        "candidate_revision": next_revision,
        "applied_changes": applied,
        "validation": validation,
    }


@router.get("/graph-store/{username}/plan-suggestions")
async def list_plan_suggestions(
    username: str,
    plan_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    suggestion_status: Optional[str] = Query(None, alias="status"),
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_plan_suggestions(
        username=username,
        plan_id=plan_id,
        pipeline_run_id=pipeline_run_id,
        status=suggestion_status,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.get("/graph-store/{username}/plan-suggestions/{suggestion_id}")
async def get_plan_suggestion(
    username: str,
    suggestion_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    doc = store.get_plan_suggestion(username=username, suggestion_id=suggestion_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan suggestion not found")
    return doc


@router.post(
    "/graph-store/{username}/plan-suggestions/{suggestion_id}/decision",
    status_code=status.HTTP_200_OK,
)
async def decide_plan_suggestion(
    username: str,
    suggestion_id: str,
    payload: PlanSuggestionDecision,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    raw = store.get_plan_suggestion(username=username, suggestion_id=suggestion_id)
    if raw is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan suggestion not found")

    updated = await apply_suggestion_decision(
        rm,
        username=username,
        suggestion_id=suggestion_id,
        approved=bool(payload.approved),
        approver=payload.approver,
    )

    # Persist a human approval record for audit/UI consistency.
    try:
        approval_payload: Dict[str, Any] = {
            "plan_id": raw.get("plan_id"),
            "username": username,
            "approver": payload.approver,
            "approved": bool(payload.approved),
            "rationale": payload.rationale,
            "suggestion_id": suggestion_id,
            "plan_revision": raw.get("candidate_revision"),
        }
        store.save_human_approval(username=username, approval=HumanApproval.model_validate(approval_payload))
    except Exception:
        pass

    return {
        "suggestion_id": suggestion_id,
        "approved": bool(payload.approved),
        "plan_id": raw.get("plan_id"),
        "plan_revision": raw.get("candidate_revision"),
        "applied": updated is not None,
    }


@router.post(
    "/graph-store/{username}/plan-drafts/build",
    response_model=PlanDraft,
    status_code=status.HTTP_201_CREATED,
)
async def build_plan_draft_endpoint(
    username: str,
    payload: BuildPlanDraftRequest,
    rm: ResourceManager = Depends(get_rm),
):
    _store(rm)
    draft = await build_plan_draft(
        rm,
        username=username,
        pipeline_id=payload.pipeline_id,
        task_ids=payload.task_ids,
    )
    return draft


# ─────────────────────────────────────────────────────────────────────────────
# Contract Net Protocol: CFPs + Awards
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/cfps",
    status_code=status.HTTP_201_CREATED,
)
async def add_cfp(
    username: str,
    payload: CallForProposal,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.add_cfp(username=username, cfp=payload)
    return {"key": key}


@router.get("/graph-store/{username}/cfps")
async def list_cfps(
    username: str,
    task_id: Optional[str] = None,
    cfp_status: Optional[str] = Query(None, alias="status"),
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_cfps(
        username=username,
        task_id=task_id,
        status=cfp_status,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.post(
    "/graph-store/{username}/awards",
    status_code=status.HTTP_201_CREATED,
)
async def add_contract_award(
    username: str,
    payload: ContractAward,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.add_contract_award(username=username, award=payload)
    return {"key": key}


@router.get("/graph-store/{username}/awards")
async def list_contract_awards(
    username: str,
    task_id: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_contract_awards(
        username=username,
        task_id=task_id,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Human approvals + ethics checks
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/human-approvals",
    status_code=status.HTTP_201_CREATED,
)
async def save_human_approval(
    username: str,
    payload: HumanApproval,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.save_human_approval(username=username, approval=payload)
    if payload.suggestion_id:
        await apply_suggestion_decision(
            rm,
            username=username,
            suggestion_id=payload.suggestion_id,
            approved=bool(payload.approved),
            approver=payload.approver,
        )
    return {"key": key}


@router.get("/graph-store/{username}/human-approvals")
async def list_human_approvals(
    username: str,
    plan_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_human_approvals(
        username=username,
        plan_id=plan_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.post(
    "/graph-store/{username}/ethics-checks",
    status_code=status.HTTP_201_CREATED,
)
async def save_ethics_check(
    username: str,
    payload: EthicsCheckResult,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.save_ethics_check(username=username, check=payload)
    return {"key": key}


@router.get("/graph-store/{username}/ethics-checks")
async def list_ethics_checks(
    username: str,
    plan_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_ethics_checks(
        username=username,
        plan_id=plan_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Ethics policies (per-tenant company rules)
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/ethics-policies",
    status_code=status.HTTP_201_CREATED,
)
async def create_ethics_policy(
    username: str,
    policy: EthicsPolicy,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    policy_updated = policy.model_copy(update={"username": username})
    key = store.save_ethics_policy(username=username, policy=policy_updated)
    return {"key": key, "policy_id": policy.policy_id}


@router.get("/graph-store/{username}/ethics-policies")
async def list_ethics_policies(
    username: str,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_ethics_policies(
        username=username,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.get("/graph-store/{username}/ethics-policies/active")
async def get_active_ethics_policy(
    username: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    doc = store.get_active_ethics_policy(username=username)
    if doc is None:
        raise HTTPException(404, "No active ethics policy found")
    return doc


@router.get("/graph-store/{username}/ethics-policies/{policy_id}")
async def get_ethics_policy(
    username: str,
    policy_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    doc = store.get_ethics_policy(username=username, policy_id=policy_id)
    if doc is None:
        raise HTTPException(404, "Ethics policy not found")
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Strategy plans + execution reports
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/strategy-plans",
    status_code=status.HTTP_201_CREATED,
)
async def save_strategy_plan(
    username: str,
    payload: StrategyPlan,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.save_strategy_plan(username=username, strategy=payload)
    return {"key": key}


@router.get("/graph-store/{username}/strategy-plans")
async def list_strategy_plans(
    username: str,
    plan_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_strategy_plans(
        username=username,
        plan_id=plan_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.post(
    "/graph-store/{username}/execution-reports",
    status_code=status.HTTP_201_CREATED,
)
async def save_execution_report(
    username: str,
    payload: ExecutionReport,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.save_execution_report(username=username, report=payload)
    return {"key": key}


@router.get("/graph-store/{username}/execution-reports")
async def list_execution_reports(
    username: str,
    plan_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_execution_reports(
        username=username,
        plan_id=plan_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Audit logs + escalation notices
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/graph-store/{username}/audit-logs",
    status_code=status.HTTP_201_CREATED,
)
async def add_audit_log(
    username: str,
    payload: AuditLogEntry,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.add_audit_log(username=username, entry=payload)
    return {"key": key}


@router.get("/graph-store/{username}/audit-logs")
async def list_audit_logs(
    username: str,
    pipeline_run_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_audit_logs(
        username=username,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.post(
    "/graph-store/{username}/escalations",
    status_code=status.HTTP_201_CREATED,
)
async def save_escalation_notice(
    username: str,
    payload: EscalationNotice,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    key = store.save_escalation_notice(username=username, notice=payload)
    return {"key": key}


@router.get("/graph-store/{username}/escalations")
async def list_escalation_notices(
    username: str,
    pipeline_run_id: Optional[str] = None,
    use_pagination: bool = Query(False, description="Return paginated response format."),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_escalation_notices(
        username=username,
        pipeline_run_id=pipeline_run_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Statistics & Aggregations
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/graph-store/{username}/stats",
    response_model=GraphStoreStats,
    summary="Get aggregated statistics for user's graph store data",
)
async def get_graph_store_stats(
    username: str,
    include_bid_stats: bool = Query(True, description="Include bid score statistics."),
    include_status_counts: bool = Query(True, description="Include status distribution counts."),
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)

    # Entity counts
    counts_dict = store.get_entity_counts(username=username)
    entity_counts = EntityCounts(**counts_dict)

    # Bid stats (optional)
    bid_stats = None
    if include_bid_stats:
        bid_stats_dict = store.get_bid_score_stats(username=username)
        if bid_stats_dict.get("count", 0) > 0:
            bid_stats = BidScoreStats(**bid_stats_dict)

    # Status counts (optional)
    cfp_status_counts: List[StatusCount] = []
    plan_suggestion_status_counts: List[StatusCount] = []
    if include_status_counts:
        cfp_status_counts = [
            StatusCount(**r)
            for r in store.get_status_counts(username=username, entity_type="cfp")
        ]
        plan_suggestion_status_counts = [
            StatusCount(**r)
            for r in store.get_status_counts(username=username, entity_type="plan_suggestion")
        ]

    return GraphStoreStats(
        username=username,
        entity_counts=entity_counts,
        bid_stats=bid_stats,
        cfp_status_counts=cfp_status_counts,
        plan_suggestion_status_counts=plan_suggestion_status_counts,
    )


@router.get(
    "/graph-store/{username}/bids/stats",
    response_model=BidScoreStats,
    summary="Get bid score statistics",
)
async def get_bid_stats(
    username: str,
    task_id: Optional[str] = None,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    stats = store.get_bid_score_stats(username=username, task_id=task_id)
    return BidScoreStats(**stats)
