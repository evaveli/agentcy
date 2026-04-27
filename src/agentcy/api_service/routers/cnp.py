from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from agentcy.agent_runtime.services import blueprint_bidder
from agentcy.api_service.dependecies import get_rm, get_publisher, pagination_params, sort_params, CommandPublisher
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import RunCNPCycleCommand
from agentcy.pydantic_models.pagination import (
    PaginatedDict,
    PaginatedResponse,
    PaginationParams,
    SortParams,
)

router = APIRouter(tags=["cnp"])


def _store(rm: ResourceManager):
    store = rm.graph_marker_store
    if store is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "graph_marker_store not configured")
    return store


# ─────────────────────────────────────────────────────────────────────────────
# Existing endpoint
# ─────────────────────────────────────────────────────────────────────────────
class CnpBidRequest(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[str] = None
    to_task: Optional[str] = None
    triggered_by: Optional[Any] = None


@router.post("/cnp/{username}/bid", status_code=status.HTTP_200_OK)
async def cnp_bid(
    username: str,
    payload: CnpBidRequest,
    rm: ResourceManager = Depends(get_rm),
):
    message = {"username": username, "data": dict(payload.data)}
    message["data"]["cnp_force_local"] = True
    return await blueprint_bidder.run(
        rm,
        payload.run_id or "cnp-api",
        payload.to_task or "blueprint_bidder",
        payload.triggered_by,
        message,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Call for Proposals
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/cnp/{username}/cfps")
async def list_cfps(
    username: str,
    task_id: Optional[str] = None,
    cfp_status: Optional[str] = Query(None, alias="status"),
    use_pagination: bool = Query(False),
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


# ─────────────────────────────────────────────────────────────────────────────
# Contract Awards
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/cnp/{username}/awards")
async def list_awards(
    username: str,
    task_id: Optional[str] = None,
    use_pagination: bool = Query(False),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    store = _store(rm)
    items, total = store.list_contract_awards(
        username=username,
        task_id=task_id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value,
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation Sequences
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/cnp/{username}/eval-sequences/{task_id}/{plan_id}")
async def get_eval_sequence(
    username: str,
    task_id: str,
    plan_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    doc = store.get_evaluation_sequence(username=username, task_id=task_id, plan_id=plan_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation sequence not found")
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# CNP Statistics
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/cnp/{username}/stats")
async def cnp_stats(
    username: str,
    task_id: Optional[str] = None,
    rm: ResourceManager = Depends(get_rm),
):
    store = _store(rm)
    bid_stats = store.get_bid_score_stats(username=username, task_id=task_id)
    cfp_counts = store.get_status_counts(username=username, entity_type="cfp")
    return {
        "bid_score_stats": bid_stats,
        "cfp_status_counts": cfp_counts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch History
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/cnp/{username}/dispatch-history/{pipeline_run_id}/{task_id}")
async def get_dispatch_history(
    username: str,
    pipeline_run_id: str,
    task_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    """Return the dispatch/re-dispatch chain for a task in a specific run."""
    store = _store(rm)

    awards, _ = store.list_contract_awards(username=username, task_id=task_id)

    eval_seq = None
    ephemeral = getattr(rm, "ephemeral_store", None)
    plan_id = None
    task_state = None
    if ephemeral is not None:
        try:
            run_doc = ephemeral.read_run(username, "", pipeline_run_id)
            if run_doc:
                plan_id = run_doc.get("plan_id")
                task_state = (run_doc.get("tasks") or {}).get(task_id)
        except Exception:
            pass

    if plan_id:
        eval_seq = store.get_evaluation_sequence(
            username=username, task_id=task_id, plan_id=plan_id,
        )

    return {
        "task_id": task_id,
        "pipeline_run_id": pipeline_run_id,
        "current_status": task_state.get("status") if isinstance(task_state, dict) else None,
        "current_agent": task_state.get("service_name") if isinstance(task_state, dict) else None,
        "awards": awards,
        "evaluation_sequence": eval_seq,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CNP Cycle Management (improved Contract Net Protocol)
# ─────────────────────────────────────────────────────────────────────────────
class RunCNPCycleRequest(BaseModel):
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    task_ids: List[str] = Field(default_factory=list)
    max_rounds: Optional[int] = None
    bid_timeout_seconds: Optional[int] = None


@router.post("/cnp/{username}/cycle", status_code=status.HTTP_202_ACCEPTED)
async def trigger_cnp_cycle(
    username: str,
    payload: RunCNPCycleRequest,
    publisher: CommandPublisher = Depends(get_publisher),
):
    """Trigger a full CNP Announce → Bid → Award cycle for a pipeline.

    Requires ``CNP_MANAGER_ENABLE=1``.  Returns 202 Accepted with the
    request_id for tracking.
    """
    if os.getenv("CNP_MANAGER_ENABLE", "0") != "1":
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "CNP Manager is disabled (set CNP_MANAGER_ENABLE=1)",
        )

    cmd = RunCNPCycleCommand(
        username=username,
        pipeline_id=payload.pipeline_id,
        pipeline_run_id=payload.pipeline_run_id,
        task_ids=payload.task_ids,
        max_rounds=payload.max_rounds,
        bid_timeout_seconds=payload.bid_timeout_seconds,
    )
    await publisher.publish("commands.run_cnp_cycle", cmd)
    return {"status": "accepted", "request_id": cmd.request_id}


@router.get("/cnp/{username}/cycles")
async def list_cnp_cycles(
    username: str,
    pipeline_id: Optional[str] = None,
    cycle_status: Optional[str] = Query(None, alias="status"),
    use_pagination: bool = Query(False),
    pagination: PaginationParams = Depends(pagination_params),
    sorting: SortParams = Depends(sort_params),
    rm: ResourceManager = Depends(get_rm),
) -> Union[List[dict], PaginatedDict]:
    """List CNP cycles for a user, with optional filtering and pagination."""
    store = _store(rm)
    items, total = store.list_cnp_cycles(
        username=username,
        pipeline_id=pipeline_id,
        status=cycle_status,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sorting.sort_by,
        sort_order=sorting.sort_order.value if sorting.sort_order else "DESC",
    )
    if use_pagination or pagination.limit is not None:
        return PaginatedResponse.from_items(items, total, pagination.limit, pagination.offset)
    return items


@router.get("/cnp/{username}/cycles/{cycle_id}")
async def get_cnp_cycle(
    username: str,
    cycle_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    """Get a single CNP cycle state by ID."""
    store = _store(rm)
    doc = store.get_cnp_cycle(username=username, cycle_id=cycle_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "CNP cycle not found")
    return doc
