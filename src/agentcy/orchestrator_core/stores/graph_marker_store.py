from __future__ import annotations

import uuid
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, Optional, ContextManager, cast

from couchbase.exceptions import DocumentNotFoundException
from couchbase.options import QueryOptions
from couchbase.cluster import QueryScanConsistency

from agentcy.shared_lib.kv.backoff import with_backoff
from agentcy.shared_lib.kv.protocols import KVCollection
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS, CB_SCOPE, CNames
from agentcy.pydantic_models.multi_agent_pipeline import (
    AffordanceMarker,
    AuditLogEntry,
    BlueprintBid,
    CallForProposal,
    CoalitionContract,
    CoalitionOutcome,
    CoalitionSignal,
    ContractAward,
    DecisionRecord,
    EscalationNotice,
    EthicsCheckResult,
    EthicsPolicy,
    ExecutionOutcomeBandit,
    ExecutionReport,
    HumanApproval,
    LinUCBModelState,
    PlanDraft,
    PlanRevision,
    PlanSuggestion,
    ReservationMarker,
    StrategyPlan,
    TaskSpec,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry(ttl_seconds: Optional[int]) -> Optional[timedelta]:
    if ttl_seconds is None:
        return None
    if ttl_seconds <= 0:
        return None
    return timedelta(seconds=ttl_seconds)


def _timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _query_options() -> Optional[QueryOptions]:
    raw = os.getenv("CB_QUERY_CONSISTENCY", "").strip().lower()
    if raw == "request_plus":
        return QueryOptions(scan_consistency=QueryScanConsistency.REQUEST_PLUS)
    return None


class GraphMarkerStore:
    """
    Shared stigmergic storage for task specs, markers, bids, and plan drafts.
    All documents live in the GRAPH_MARKERS collection with distinct key prefixes.
    """

    def __init__(self, pool: DynamicCouchbaseConnectionPool):
        self._pool = pool

    # ──────────────────────────────────────────────────────────────────────
    # Generic raw KV (used by payload_ref pattern for revision candidates)
    # ──────────────────────────────────────────────────────────────────────
    @with_backoff(msg="graph_store.upsert_raw")
    def upsert_raw(self, key: str, doc: Dict[str, Any]) -> str:
        doc.setdefault("_meta", {})["updated_at"] = _now_iso()
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, doc)
        return key

    @with_backoff(msg="graph_store.get_raw")
    def get_raw(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    # ──────────────────────────────────────────────────────────────────────
    # Task specs
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _task_spec_key(username: str, task_id: str) -> str:
        return f"task_spec::{username}::{task_id}"

    @with_backoff(msg="graph_store.upsert_task_spec")
    def upsert_task_spec(self, *, username: str, spec: TaskSpec) -> str:
        payload = spec.model_copy(update={"username": username}).model_dump(mode="json")
        payload["_meta"] = {"type": "task_spec", "updated_at": _now_iso()}
        key = self._task_spec_key(username, spec.task_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_task_spec")
    def get_task_spec(self, *, username: str, task_id: str) -> Optional[Dict[str, Any]]:
        key = self._task_spec_key(username, task_id)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    def list_task_specs(
        self,
        *,
        username: str,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List task specs with optional pagination and sorting."""
        prefix = self._task_spec_key(username, "")
        return self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Affordance markers
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _affordance_key(username: str, task_id: str, marker_id: str) -> str:
        return f"marker::affordance::{username}::{task_id}::{marker_id}"

    @with_backoff(msg="graph_store.add_affordance_marker")
    def add_affordance_marker(
        self,
        *,
        username: str,
        marker: AffordanceMarker,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        payload = marker.model_dump(mode="json")
        payload["_meta"] = {"type": "affordance_marker", "updated_at": _now_iso()}
        expiry = _expiry(ttl_seconds if ttl_seconds is not None else marker.ttl_seconds)
        if expiry:
            payload["expires_at"] = (
                datetime.now(timezone.utc) + expiry
            ).isoformat()
        key = self._affordance_key(username, marker.task_id, marker.marker_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            if expiry:
                col.upsert(key, payload, expiry=expiry)
            else:
                col.upsert(key, payload)
        return key

    def list_affordance_markers(
        self,
        *,
        username: str,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        pipeline_run_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List affordance markers with optional filtering, pagination and sorting."""
        prefix = f"marker::affordance::{username}::"
        if task_id:
            prefix = f"{prefix}{task_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )
        # Post-query filtering for agent_id
        if agent_id:
            items = [item for item in items if item.get("agent_id") == agent_id]
        if pipeline_id:
            items = [item for item in items if item.get("pipeline_id") == pipeline_id]
        if pipeline_run_id:
            items = [item for item in items if item.get("pipeline_run_id") == pipeline_run_id]
        if agent_id or pipeline_id or pipeline_run_id:
            total = len(items)
        return items, total

    def list_failure_markers(
        self,
        *,
        username: str,
        agent_id: str,
        task_type: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """Return affordance markers that carry a failure_context for *agent_id*.

        Optionally narrow to a specific *task_type* within the failure context.
        Only non-expired markers with ``failure_context`` present are returned,
        sorted by most-recent first.
        """
        items, _ = self.list_affordance_markers(
            username=username, agent_id=agent_id
        )
        results: list[Dict[str, Any]] = []
        for item in items:
            fc = item.get("failure_context")
            if not fc or not isinstance(fc, dict):
                continue
            if task_type and fc.get("task_type") != task_type:
                continue
            results.append(item)
        results.sort(key=lambda m: _timestamp(m.get("created_at")), reverse=True)
        return results

    # ──────────────────────────────────────────────────────────────────────
    # Reservation markers
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _reservation_key(username: str, task_id: str, marker_id: str) -> str:
        return f"marker::reservation::{username}::{task_id}::{marker_id}"

    @with_backoff(msg="graph_store.add_reservation_marker")
    def add_reservation_marker(
        self,
        *,
        username: str,
        marker: ReservationMarker,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        payload = marker.model_dump(mode="json")
        payload["_meta"] = {"type": "reservation_marker", "updated_at": _now_iso()}
        expiry = _expiry(ttl_seconds if ttl_seconds is not None else marker.ttl_seconds)
        if expiry:
            payload["expires_at"] = (
                datetime.now(timezone.utc) + expiry
            ).isoformat()
        key = self._reservation_key(username, marker.task_id, marker.marker_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            if expiry:
                col.upsert(key, payload, expiry=expiry)
            else:
                col.upsert(key, payload)
        return key

    def list_reservation_markers(
        self,
        *,
        username: str,
        task_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List reservation markers with optional filtering, pagination and sorting."""
        prefix = f"marker::reservation::{username}::"
        if task_id:
            prefix = f"{prefix}{task_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )
        # Post-query filtering for agent_id
        if agent_id:
            items = [item for item in items if item.get("agent_id") == agent_id]
            total = len(items)  # Adjust total for post-filter
        return items, total

    # ──────────────────────────────────────────────────────────────────────
    # Bids
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _bid_key(username: str, task_id: str, bidder_id: str, bid_id: str) -> str:
        return f"bid::{username}::{task_id}::{bidder_id}::{bid_id}"

    @with_backoff(msg="graph_store.add_bid")
    def add_bid(self, *, username: str, bid: BlueprintBid) -> str:
        bid_id = str(uuid.uuid4())
        payload = bid.model_dump(mode="json")
        payload["bid_id"] = bid_id
        payload["_meta"] = {"type": "blueprint_bid", "updated_at": _now_iso()}
        expiry = _expiry(bid.ttl_seconds)
        if expiry:
            payload["expires_at"] = (
                datetime.now(timezone.utc) + expiry
            ).isoformat()
        key = self._bid_key(username, bid.task_id, bid.bidder_id, bid_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            if expiry:
                col.upsert(key, payload, expiry=expiry)
            else:
                col.upsert(key, payload)
        return bid_id

    def list_bids(
        self,
        *,
        username: str,
        task_id: Optional[str] = None,
        bidder_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        pipeline_run_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List bids with optional filtering, pagination and sorting."""
        prefix = f"bid::{username}::"
        if task_id:
            prefix = f"{prefix}{task_id}::"
        if bidder_id:
            prefix = f"{prefix}{bidder_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "bid_score",
            sort_order=sort_order,
        )
        # Post-query filtering
        if task_id and not bidder_id:
            items = [item for item in items if item.get("task_id") == task_id]
        if bidder_id and not task_id:
            items = [item for item in items if item.get("bidder_id") == bidder_id]
        if pipeline_id:
            items = [item for item in items if item.get("pipeline_id") == pipeline_id]
        if pipeline_run_id:
            items = [item for item in items if item.get("pipeline_run_id") == pipeline_run_id]
        if task_id or bidder_id or pipeline_id or pipeline_run_id:
            total = len(items)
        return items, total

    # ──────────────────────────────────────────────────────────────────────
    # Plan drafts
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _plan_key(username: str, plan_id: str) -> str:
        return f"plan_draft::{username}::{plan_id}"

    @staticmethod
    def _plan_revision_key(username: str, plan_id: str, revision: int) -> str:
        return f"plan_revision::{username}::{plan_id}::{revision}"

    @staticmethod
    def _plan_suggestion_key(username: str, suggestion_id: str) -> str:
        return f"plan_suggestion::{username}::{suggestion_id}"

    @with_backoff(msg="graph_store.save_plan_draft")
    def save_plan_draft(self, *, username: str, draft: PlanDraft) -> str:
        payload = draft.model_copy(update={"username": username}).model_dump(mode="json")
        payload["_meta"] = {"type": "plan_draft", "updated_at": _now_iso()}
        key = self._plan_key(username, draft.plan_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.save_plan_revision")
    def save_plan_revision(self, *, username: str, revision: PlanRevision) -> str:
        payload = revision.model_copy(update={"username": username}).model_dump(mode="json")
        payload["_meta"] = {"type": "plan_revision", "updated_at": _now_iso()}
        key = self._plan_revision_key(username, revision.plan_id, revision.revision)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_plan_revision")
    def get_plan_revision(
        self,
        *,
        username: str,
        plan_id: str,
        revision: int,
    ) -> Optional[Dict[str, Any]]:
        key = self._plan_revision_key(username, plan_id, revision)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    def list_plan_revisions(
        self,
        *,
        username: str,
        plan_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List plan revisions with optional pagination and sorting."""
        prefix = f"plan_revision::{username}::"
        if plan_id:
            prefix = f"{prefix}{plan_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "revision",
            sort_order=sort_order,
        )
        # Additional Python-level sorting if needed (legacy behavior)
        if not sort_by:
            items.sort(
                key=lambda item: int(item.get("revision", 0)),
                reverse=(sort_order.upper() == "DESC"),
            )
        return items, total

    @with_backoff(msg="graph_store.save_plan_suggestion")
    def save_plan_suggestion(self, *, username: str, suggestion: PlanSuggestion) -> str:
        payload = suggestion.model_copy(update={"username": username}).model_dump(mode="json")
        payload["_meta"] = {"type": "plan_suggestion", "updated_at": _now_iso()}
        key = self._plan_suggestion_key(username, suggestion.suggestion_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_plan_suggestion")
    def get_plan_suggestion(self, *, username: str, suggestion_id: str) -> Optional[Dict[str, Any]]:
        key = self._plan_suggestion_key(username, suggestion_id)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    def list_plan_suggestions(
        self,
        *,
        username: str,
        plan_id: Optional[str] = None,
        pipeline_run_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List plan suggestions with optional filtering, pagination and sorting."""
        prefix = f"plan_suggestion::{username}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )
        # Post-query filtering
        if plan_id:
            items = [item for item in items if item.get("plan_id") == plan_id]
        if pipeline_run_id:
            items = [item for item in items if item.get("pipeline_run_id") == pipeline_run_id]
        if status:
            items = [item for item in items if item.get("status") == status]
        # Adjust total after post-filtering
        if plan_id or pipeline_run_id or status:
            total = len(items)
        # Additional Python-level sorting if needed (legacy behavior)
        if not sort_by:
            items.sort(
                key=lambda item: _timestamp(
                    item.get("created_at")
                    or (item.get("_meta") or {}).get("updated_at")
                ),
                reverse=(sort_order.upper() == "DESC"),
            )
        return items, total

    @with_backoff(msg="graph_store.get_plan_draft")
    def get_plan_draft(self, *, username: str, plan_id: str) -> Optional[Dict[str, Any]]:
        key = self._plan_key(username, plan_id)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    def list_plan_drafts(
        self,
        *,
        username: str,
        pipeline_id: Optional[str] = None,
        pipeline_run_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List plan drafts with optional filtering, pagination and sorting."""
        prefix = self._plan_key(username, "")
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )
        # Post-query filtering
        if pipeline_id:
            items = [item for item in items if item.get("pipeline_id") == pipeline_id]
        if pipeline_run_id:
            items = [item for item in items if item.get("pipeline_run_id") == pipeline_run_id]
        if pipeline_id or pipeline_run_id:
            total = len(items)
        # Additional Python-level sorting if needed (legacy behavior)
        if not sort_by:
            items.sort(
                key=lambda item: _timestamp(
                    item.get("created_at")
                    or (item.get("_meta") or {}).get("updated_at")
                ),
                reverse=(sort_order.upper() == "DESC"),
            )
        return items, total

    # ──────────────────────────────────────────────────────────────────────
    # Contract Net Protocol: CFPs and awards
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _cfp_key(username: str, task_id: str, cfp_id: str) -> str:
        return f"cfp::{username}::{task_id}::{cfp_id}"

    @with_backoff(msg="graph_store.add_cfp")
    def add_cfp(self, *, username: str, cfp: CallForProposal) -> str:
        payload = cfp.model_dump(mode="json")
        payload["_meta"] = {"type": "cfp", "updated_at": _now_iso()}
        key = self._cfp_key(username, cfp.task_id, cfp.cfp_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    def list_cfps(
        self,
        *,
        username: str,
        task_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List CFPs with optional filtering, pagination and sorting."""
        prefix = f"cfp::{username}::"
        if task_id:
            prefix = f"{prefix}{task_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )
        # Post-query filtering
        if status:
            items = [item for item in items if item.get("status") == status]
            total = len(items)
        return items, total

    @staticmethod
    def _award_key(username: str, task_id: str, award_id: str) -> str:
        return f"award::{username}::{task_id}::{award_id}"

    @with_backoff(msg="graph_store.add_contract_award")
    def add_contract_award(self, *, username: str, award: ContractAward) -> str:
        payload = award.model_dump(mode="json")
        payload["_meta"] = {"type": "contract_award", "updated_at": _now_iso()}
        key = self._award_key(username, award.task_id, award.award_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    def list_contract_awards(
        self,
        *,
        username: str,
        task_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        pipeline_run_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List contract awards with optional pagination and sorting."""
        prefix = f"award::{username}::"
        if task_id:
            prefix = f"{prefix}{task_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "awarded_at",
            sort_order=sort_order,
        )
        if pipeline_id:
            items = [item for item in items if item.get("pipeline_id") == pipeline_id]
        if pipeline_run_id:
            items = [item for item in items if item.get("pipeline_run_id") == pipeline_run_id]
        if pipeline_id or pipeline_run_id:
            total = len(items)
        return items, total

    # ──────────────────────────────────────────────────────────────────────
    # Evaluation sequences (CNP failure re-forwarding)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _eval_seq_key(username: str, task_id: str, plan_id: str) -> str:
        return f"eval_seq::{username}::{task_id}::{plan_id}"

    @with_backoff(msg="graph_store.save_evaluation_sequence")
    def save_evaluation_sequence(self, *, username: str, seq) -> str:
        payload = seq.model_dump(mode="json")
        payload["_meta"] = {"type": "evaluation_sequence", "updated_at": _now_iso()}
        key = self._eval_seq_key(username, seq.task_id, seq.plan_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_evaluation_sequence")
    def get_evaluation_sequence(
        self, *, username: str, task_id: str, plan_id: str,
    ) -> Optional[Dict[str, Any]]:
        key = self._eval_seq_key(username, task_id, plan_id)
        return self.get_raw(key)

    @with_backoff(msg="graph_store.advance_evaluation_sequence")
    def advance_evaluation_sequence(
        self, username: str, task_id: str, plan_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Increment current_index and return the next candidate, or None if exhausted."""
        key = self._eval_seq_key(username, task_id, plan_id)
        doc = self.get_raw(key)
        if doc is None:
            return None
        candidates = doc.get("candidates") or []
        current_index = int(doc.get("current_index", 0))
        next_index = current_index + 1
        if next_index >= len(candidates):
            return None
        doc["current_index"] = next_index
        doc.setdefault("_meta", {})["updated_at"] = _now_iso()
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, doc)
        candidate = dict(candidates[next_index])
        candidate["sequence_index"] = next_index
        return candidate

    # ──────────────────────────────────────────────────────────────────────
    # Human approvals & ethics checks
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _approval_key(username: str, plan_id: str, approval_id: str) -> str:
        return f"human_approval::{username}::{plan_id}::{approval_id}"

    @staticmethod
    def _approval_latest_key(username: str, plan_id: str) -> str:
        return f"human_approval_latest::{username}::{plan_id}"

    @with_backoff(msg="graph_store.save_human_approval")
    def save_human_approval(self, *, username: str, approval: HumanApproval) -> str:
        payload = approval.model_dump(mode="json")
        payload["_meta"] = {"type": "human_approval", "updated_at": _now_iso()}
        key = self._approval_key(username, approval.plan_id, str(uuid.uuid4()))
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
            latest_key = self._approval_latest_key(username, approval.plan_id)
            latest_payload = dict(payload)
            latest_payload["_meta"] = {"type": "human_approval_latest", "updated_at": _now_iso()}
            col.upsert(latest_key, latest_payload)
        return key

    def list_human_approvals(
        self,
        *,
        username: str,
        plan_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List human approvals with optional pagination and sorting."""
        prefix = f"human_approval::{username}::"
        if plan_id:
            prefix = f"{prefix}{plan_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "decided_at",
            sort_order=sort_order,
        )
        if items or not plan_id:
            return items, total
        # Fallback: try to get latest approval
        latest_key = self._approval_latest_key(username, plan_id)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(latest_key)
        except DocumentNotFoundException:
            return [], 0
        result = [res.content_as[dict]] if res is not None else []
        return result, len(result)

    @staticmethod
    def _ethics_key(username: str, plan_id: str, check_id: str) -> str:
        return f"ethics_check::{username}::{plan_id}::{check_id}"

    @staticmethod
    def _ethics_latest_key(username: str, plan_id: str) -> str:
        return f"ethics_check_latest::{username}::{plan_id}"

    @with_backoff(msg="graph_store.save_ethics_check")
    def save_ethics_check(self, *, username: str, check: EthicsCheckResult) -> str:
        payload = check.model_dump(mode="json")
        payload["_meta"] = {"type": "ethics_check", "updated_at": _now_iso()}
        key = self._ethics_key(username, check.plan_id, str(uuid.uuid4()))
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
            latest_key = self._ethics_latest_key(username, check.plan_id)
            latest_payload = dict(payload)
            latest_payload["_meta"] = {"type": "ethics_check_latest", "updated_at": _now_iso()}
            col.upsert(latest_key, latest_payload)
        return key

    def list_ethics_checks(
        self,
        *,
        username: str,
        plan_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List ethics checks with optional pagination and sorting."""
        prefix = f"ethics_check::{username}::"
        if plan_id:
            prefix = f"{prefix}{plan_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "checked_at",
            sort_order=sort_order,
        )
        if items or not plan_id:
            return items, total
        # Fallback: try to get latest ethics check
        latest_key = self._ethics_latest_key(username, plan_id)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(latest_key)
        except DocumentNotFoundException:
            return [], 0
        result = [res.content_as[dict]] if res is not None else []
        return result, len(result)

    # ──────────────────────────────────────────────────────────────────────
    # Ethics policies (per-tenant company rules)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _ethics_policy_key(username: str, policy_id: str) -> str:
        return f"ethics_policy::{username}::{policy_id}"

    @staticmethod
    def _ethics_policy_active_key(username: str) -> str:
        return f"ethics_policy_active::{username}"

    @with_backoff(msg="graph_store.save_ethics_policy")
    def save_ethics_policy(self, *, username: str, policy: EthicsPolicy) -> str:
        payload = policy.model_dump(mode="json")
        payload["_meta"] = {"type": "ethics_policy", "updated_at": _now_iso()}
        key = self._ethics_policy_key(username, policy.policy_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
            active_payload = dict(payload)
            active_payload["_meta"] = {"type": "ethics_policy_active", "updated_at": _now_iso()}
            col.upsert(self._ethics_policy_active_key(username), active_payload)
        return key

    @with_backoff(msg="graph_store.get_ethics_policy")
    def get_ethics_policy(self, *, username: str, policy_id: str) -> Optional[Dict[str, Any]]:
        key = self._ethics_policy_key(username, policy_id)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    @with_backoff(msg="graph_store.get_active_ethics_policy")
    def get_active_ethics_policy(self, *, username: str) -> Optional[Dict[str, Any]]:
        key = self._ethics_policy_active_key(username)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    def list_ethics_policies(
        self,
        *,
        username: str,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        prefix = f"ethics_policy::{username}::"
        return self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )

    # ──────────────────────────────────────────────────────────────────────
    # CNP Cycle State
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _cnp_cycle_key(username: str, cycle_id: str) -> str:
        return f"cnp_cycle::{username}::{cycle_id}"

    @with_backoff(msg="graph_store.save_cnp_cycle")
    def save_cnp_cycle(self, *, username: str, cycle) -> str:
        payload = cycle.model_dump(mode="json")
        payload["_meta"] = {"type": "cnp_cycle", "updated_at": _now_iso()}
        key = self._cnp_cycle_key(username, cycle.cycle_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_cnp_cycle")
    def get_cnp_cycle(self, *, username: str, cycle_id: str) -> Optional[Dict[str, Any]]:
        key = self._cnp_cycle_key(username, cycle_id)
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    def list_cnp_cycles(
        self,
        *,
        username: str,
        pipeline_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        prefix = f"cnp_cycle::{username}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )
        if pipeline_id:
            items = [i for i in items if i.get("pipeline_id") == pipeline_id]
            total = len(items)
        if status:
            items = [i for i in items if i.get("status") == status]
            total = len(items)
        return items, total

    # ──────────────────────────────────────────────────────────────────────
    # Strategies, execution reports, audit logs
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _strategy_key(username: str, plan_id: str, strategy_id: str) -> str:
        return f"strategy::{username}::{plan_id}::{strategy_id}"

    @with_backoff(msg="graph_store.save_strategy_plan")
    def save_strategy_plan(self, *, username: str, strategy: StrategyPlan) -> str:
        payload = strategy.model_dump(mode="json")
        payload["_meta"] = {"type": "strategy_plan", "updated_at": _now_iso()}
        key = self._strategy_key(username, strategy.plan_id, strategy.strategy_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    def list_strategy_plans(
        self,
        *,
        username: str,
        plan_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List strategy plans with optional pagination and sorting."""
        prefix = f"strategy::{username}::"
        if plan_id:
            prefix = f"{prefix}{plan_id}::"
        return self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )

    @staticmethod
    def _execution_key(username: str, plan_id: str, report_id: str) -> str:
        return f"execution::{username}::{plan_id}::{report_id}"

    @with_backoff(msg="graph_store.save_execution_report")
    def save_execution_report(self, *, username: str, report: ExecutionReport) -> str:
        payload = report.model_dump(mode="json")
        payload["_meta"] = {"type": "execution_report", "updated_at": _now_iso()}
        key = self._execution_key(username, report.plan_id, report.report_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    def list_execution_reports(
        self,
        *,
        username: str,
        plan_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List execution reports with optional pagination and sorting."""
        prefix = f"execution::{username}::"
        if plan_id:
            prefix = f"{prefix}{plan_id}::"
        return self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Failure escalation notices
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _escalation_key(username: str, run_id: str, notice_id: str) -> str:
        return f"escalation::{username}::{run_id}::{notice_id}"

    @with_backoff(msg="graph_store.save_escalation_notice")
    def save_escalation_notice(self, *, username: str, notice: EscalationNotice) -> str:
        payload = notice.model_dump(mode="json")
        payload["_meta"] = {"type": "escalation_notice", "updated_at": _now_iso()}
        key = self._escalation_key(username, notice.pipeline_run_id, str(uuid.uuid4()))
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    def list_escalation_notices(
        self,
        *,
        username: str,
        pipeline_run_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List escalation notices with optional pagination and sorting."""
        prefix = f"escalation::{username}::"
        if pipeline_run_id:
            prefix = f"{prefix}{pipeline_run_id}::"
        return self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )

    @staticmethod
    def _audit_key(username: str, run_id: str, entry_id: str) -> str:
        return f"audit::{username}::{run_id}::{entry_id}"

    @with_backoff(msg="graph_store.add_audit_log")
    def add_audit_log(self, *, username: str, entry: AuditLogEntry) -> str:
        payload = entry.model_dump(mode="json")
        payload["_meta"] = {"type": "audit_log", "updated_at": _now_iso()}
        key = self._audit_key(username, entry.pipeline_run_id, str(uuid.uuid4()))
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    def list_audit_logs(
        self,
        *,
        username: str,
        pipeline_run_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """List audit logs with optional pagination and sorting."""
        prefix = f"audit::{username}::"
        if pipeline_run_id:
            prefix = f"{prefix}{pipeline_run_id}::"
        return self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Decision records (bandit learning)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _decision_key(username: str, task_id: str, decision_id: str) -> str:
        return f"decision::{username}::{task_id}::{decision_id}"

    @with_backoff(msg="graph_store.save_decision_record")
    def save_decision_record(self, *, username: str, record: DecisionRecord) -> str:
        payload = record.model_dump(mode="json")
        payload["_meta"] = {"type": "decision_record", "updated_at": _now_iso()}
        key = self._decision_key(username, record.task_id, record.decision_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_decision_record")
    def get_decision_record(self, *, username: str, task_id: str, decision_id: str) -> Optional[Dict[str, Any]]:
        key = self._decision_key(username, task_id, decision_id)
        return self.get_raw(key)

    def list_decision_records(
        self,
        *,
        username: str,
        task_id: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        prefix = f"decision::{username}::"
        if task_id:
            prefix = f"{prefix}{task_id}::"
        items, total = self._list_by_prefix(
            prefix,
            limit=limit,
            offset=offset,
            sort_by=sort_by or "created_at",
            sort_order=sort_order,
        )
        if task_type:
            items = [i for i in items if i.get("task_type") == task_type]
            total = len(items)
        return items, total

    def update_decision_outcome(
        self,
        *,
        username: str,
        task_id: str,
        decision_id: str,
        outcome: ExecutionOutcomeBandit,
        reward: float,
    ) -> bool:
        key = self._decision_key(username, task_id, decision_id)
        doc = self.get_raw(key)
        if doc is None:
            return False
        doc["outcome"] = outcome.model_dump(mode="json")
        doc["reward"] = reward
        doc["outcome_recorded_at"] = _now_iso()
        doc.setdefault("_meta", {})["updated_at"] = _now_iso()
        self.upsert_raw(key, doc)
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Bandit model state
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _bandit_model_key(username: str, task_type: str) -> str:
        return f"bandit_model::{username}::{task_type}"

    @with_backoff(msg="graph_store.save_bandit_model")
    def save_bandit_model(self, *, username: str, model_state: LinUCBModelState) -> str:
        payload = model_state.model_dump(mode="json")
        payload["_meta"] = {"type": "bandit_model", "updated_at": _now_iso()}
        key = self._bandit_model_key(username, model_state.task_type)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_bandit_model")
    def get_bandit_model(self, *, username: str, task_type: str) -> Optional[Dict[str, Any]]:
        key = self._bandit_model_key(username, task_type)
        return self.get_raw(key)

    def list_bandit_models(
        self, *, username: str,
    ) -> tuple[list[Dict[str, Any]], int]:
        prefix = f"bandit_model::{username}::"
        return self._list_by_prefix(prefix)

    # ──────────────────────────────────────────────────────────────────────
    # Topology outcomes (performance memory)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _topology_outcome_key(username: str, pipeline_run_id: str, outcome_id: str) -> str:
        return f"topology_outcome::{username}::{pipeline_run_id}::{outcome_id}"

    @with_backoff(msg="graph_store.save_topology_outcome")
    def save_topology_outcome(self, *, username: str, outcome: Any) -> str:
        payload = outcome.model_dump(mode="json") if hasattr(outcome, "model_dump") else dict(outcome)
        payload["_meta"] = {"type": "topology_outcome", "updated_at": _now_iso()}
        key = self._topology_outcome_key(
            username, payload.get("pipeline_run_id", ""), payload.get("outcome_id", ""),
        )
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    def list_topology_outcomes(
        self,
        *,
        username: str,
        topology_signature: Optional[str] = None,
        skeleton_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> tuple[list[Dict[str, Any]], int]:
        prefix = f"topology_outcome::{username}::"
        items, total = self._list_by_prefix(
            prefix, limit=limit, offset=offset,
            sort_by="created_at", sort_order="DESC",
        )
        if topology_signature:
            items = [i for i in items if i.get("topology_signature") == topology_signature]
            total = len(items)
        if skeleton_id:
            items = [i for i in items if i.get("skeleton_id") == skeleton_id]
            total = len(items)
        return items, total

    @staticmethod
    def _topology_perf_key(username: str, topology_signature: str) -> str:
        return f"topology_perf::{username}::{topology_signature}"

    @with_backoff(msg="graph_store.save_topology_performance")
    def save_topology_performance(self, *, username: str, perf: Any) -> str:
        payload = perf.model_dump(mode="json") if hasattr(perf, "model_dump") else dict(perf)
        payload["_meta"] = {"type": "topology_performance", "updated_at": _now_iso()}
        key = self._topology_perf_key(username, payload.get("topology_signature", ""))
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_topology_performance")
    def get_topology_performance(
        self, *, username: str, topology_signature: str,
    ) -> Optional[Dict[str, Any]]:
        key = self._topology_perf_key(username, topology_signature)
        return self.get_raw(key)

    # ──────────────────────────────────────────────────────────────────────
    # Coalition contracts
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _coalition_contract_key(username: str, task_id: str, coalition_id: str) -> str:
        return f"coalition_contract::{username}::{task_id}::{coalition_id}"

    @with_backoff(msg="graph_store.save_coalition_contract")
    def save_coalition_contract(self, *, username: str, contract: CoalitionContract) -> str:
        payload = contract.model_dump(mode="json")
        payload["_meta"] = {"type": "coalition_contract", "updated_at": _now_iso()}
        key = self._coalition_contract_key(username, contract.task_id, contract.coalition_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_coalition_contract")
    def get_coalition_contract(
        self, *, username: str, task_id: str, coalition_id: str,
    ) -> Optional[Dict[str, Any]]:
        key = self._coalition_contract_key(username, task_id, coalition_id)
        return self.get_raw(key)

    # ──────────────────────────────────────────────────────────────────────
    # Coalition outcomes
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _coalition_outcome_key(username: str, coalition_id: str, outcome_id: str) -> str:
        return f"coalition_outcome::{username}::{coalition_id}::{outcome_id}"

    @with_backoff(msg="graph_store.save_coalition_outcome")
    def save_coalition_outcome(self, *, username: str, outcome: CoalitionOutcome) -> str:
        payload = outcome.model_dump(mode="json")
        payload["_meta"] = {"type": "coalition_outcome", "updated_at": _now_iso()}
        key = self._coalition_outcome_key(username, outcome.coalition_id, outcome.outcome_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    def list_coalition_outcomes(
        self,
        *,
        username: str,
        coalition_signature: Optional[str] = None,
        task_signature: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> tuple[list[Dict[str, Any]], int]:
        prefix = f"coalition_outcome::{username}::"
        items, total = self._list_by_prefix(
            prefix, limit=limit, offset=offset,
            sort_by="created_at", sort_order="DESC",
        )
        if coalition_signature:
            items = [i for i in items if i.get("coalition_signature") == coalition_signature]
            total = len(items)
        if task_signature:
            items = [i for i in items if i.get("task_signature") == task_signature]
            total = len(items)
        return items, total

    # ──────────────────────────────────────────────────────────────────────
    # Coalition signals (joint_trust, handoff_friction, coalition_overhead)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _coalition_signal_key(
        username: str, signal_type: str, coalition_signature: str, task_signature: str,
    ) -> str:
        return f"coalition_signal::{username}::{signal_type}::{coalition_signature}::{task_signature}"

    @with_backoff(msg="graph_store.save_coalition_signal")
    def save_coalition_signal(self, *, username: str, signal: CoalitionSignal) -> str:
        payload = signal.model_dump(mode="json")
        payload["_meta"] = {"type": "coalition_signal", "updated_at": _now_iso()}
        key = self._coalition_signal_key(
            username, signal.signal_type, signal.coalition_signature, signal.task_signature,
        )
        with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
            col.upsert(key, payload)
        return key

    @with_backoff(msg="graph_store.get_coalition_signal")
    def get_coalition_signal(
        self,
        *,
        username: str,
        signal_type: str,
        coalition_signature: str,
        task_signature: str = "",
    ) -> Optional[Dict[str, Any]]:
        key = self._coalition_signal_key(username, signal_type, coalition_signature, task_signature)
        return self.get_raw(key)

    def list_coalition_signals(
        self, *, username: str, signal_type: Optional[str] = None,
    ) -> tuple[list[Dict[str, Any]], int]:
        prefix = f"coalition_signal::{username}::"
        if signal_type:
            prefix = f"{prefix}{signal_type}::"
        return self._list_by_prefix(prefix)

    # ──────────────────────────────────────────────────────────────────────
    # Aggregation methods
    # ──────────────────────────────────────────────────────────────────────
    def get_bid_score_stats(
        self,
        *,
        username: str,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get aggregated statistics for bid scores."""
        prefix = f"bid::{username}::"
        if task_id:
            prefix = f"{prefix}{task_id}::"

        bucket_name = CB_BUCKET
        scope_name = CB_SCOPE
        coll_name = CB_COLLECTIONS[CNames.GRAPH_MARKERS]

        query = f"""
            SELECT
                COUNT(*) AS count,
                AVG(g.bid_score) AS avg_score,
                MIN(g.bid_score) AS min_score,
                MAX(g.bid_score) AS max_score
            FROM `{bucket_name}`.`{scope_name}`.`{coll_name}` g
            WHERE META(g).id LIKE '{prefix}%'
        """

        bundle = self._pool.acquire()
        try:
            opts = _query_options()
            if opts is None:
                rows = list(bundle.cluster.query(query))
            else:
                try:
                    rows = list(bundle.cluster.query(query, opts))
                except TypeError:
                    rows = list(bundle.cluster.query(query))
            return dict(rows[0]) if rows else {"count": 0}
        finally:
            self._pool.release(bundle)

    def get_status_counts(
        self,
        *,
        username: str,
        entity_type: str,
    ) -> list[Dict[str, Any]]:
        """Get counts grouped by status for an entity type (cfp or plan_suggestion)."""
        prefix_map = {
            "cfp": f"cfp::{username}::",
            "plan_suggestion": f"plan_suggestion::{username}::",
        }
        prefix = prefix_map.get(entity_type)
        if not prefix:
            return []

        bucket_name = CB_BUCKET
        scope_name = CB_SCOPE
        coll_name = CB_COLLECTIONS[CNames.GRAPH_MARKERS]

        query = f"""
            SELECT g.status, COUNT(*) AS count
            FROM `{bucket_name}`.`{scope_name}`.`{coll_name}` g
            WHERE META(g).id LIKE '{prefix}%'
            GROUP BY g.status
            ORDER BY count DESC
        """

        bundle = self._pool.acquire()
        try:
            opts = _query_options()
            if opts is None:
                rows = bundle.cluster.query(query)
            else:
                try:
                    rows = bundle.cluster.query(query, opts)
                except TypeError:
                    rows = bundle.cluster.query(query)
            return [dict(row) for row in rows]
        finally:
            self._pool.release(bundle)

    def get_entity_counts(self, *, username: str) -> Dict[str, int]:
        """Get counts for all entity types for a user."""
        prefixes = {
            "task_specs": f"task_spec::{username}::",
            "bids": f"bid::{username}::",
            "plan_drafts": f"plan_draft::{username}::",
            "plan_revisions": f"plan_revision::{username}::",
            "plan_suggestions": f"plan_suggestion::{username}::",
            "cfps": f"cfp::{username}::",
            "awards": f"award::{username}::",
            "human_approvals": f"human_approval::{username}::",
            "ethics_checks": f"ethics_check::{username}::",
            "strategy_plans": f"strategy::{username}::",
            "execution_reports": f"execution::{username}::",
            "audit_logs": f"audit::{username}::",
            "escalations": f"escalation::{username}::",
            "affordance_markers": f"marker::affordance::{username}::",
            "reservation_markers": f"marker::reservation::{username}::",
            "ethics_policies": f"ethics_policy::{username}::",
            "cnp_cycles": f"cnp_cycle::{username}::",
        }

        bucket_name = CB_BUCKET
        scope_name = CB_SCOPE
        coll_name = CB_COLLECTIONS[CNames.GRAPH_MARKERS]

        # Build a UNION ALL query for efficiency
        union_parts = []
        for name, prefix in prefixes.items():
            union_parts.append(
                f"SELECT '{name}' AS entity_type, COUNT(*) AS count "
                f"FROM `{bucket_name}`.`{scope_name}`.`{coll_name}` "
                f"WHERE META().id LIKE '{prefix}%'"
            )

        query = " UNION ALL ".join(union_parts)

        bundle = self._pool.acquire()
        try:
            opts = _query_options()
            if opts is None:
                rows = bundle.cluster.query(query)
            else:
                try:
                    rows = bundle.cluster.query(query, opts)
                except TypeError:
                    rows = bundle.cluster.query(query)
            return {row["entity_type"]: row["count"] for row in rows}
        finally:
            self._pool.release(bundle)

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    # Allowed fields for sorting (whitelist to prevent injection)
    _ALLOWED_SORT_FIELDS = frozenset({
        "created_at", "updated_at", "bid_score", "revision", "status",
        "task_id", "plan_id", "agent_id", "decided_at", "checked_at",
        "awarded_at", "_meta.updated_at",
    })

    def _list_by_prefix(
        self,
        prefix: str,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "DESC",
    ) -> tuple[list[Dict[str, Any]], int]:
        """
        List documents by key prefix with optional pagination and sorting.

        Returns:
            Tuple of (items, total_count)
        """
        bucket_name = CB_BUCKET
        scope_name = CB_SCOPE
        coll_name = CB_COLLECTIONS[CNames.GRAPH_MARKERS]

        where_clause = f"META(g).id LIKE '{prefix}%'"

        # Count query for total
        count_query = (
            f"SELECT COUNT(*) AS total "
            f"FROM `{bucket_name}`.`{scope_name}`.`{coll_name}` g "
            f"WHERE {where_clause}"
        )

        # Data query with optional ORDER BY and LIMIT/OFFSET
        select_clause = "SELECT META(g).id AS id, g.*"
        from_clause = f"FROM `{bucket_name}`.`{scope_name}`.`{coll_name}` g"

        # Build ORDER BY clause (validate sort_by to prevent injection)
        order_clause = ""
        if sort_by and sort_by in self._ALLOWED_SORT_FIELDS:
            order_clause = f"ORDER BY g.{sort_by} {sort_order.upper()}"
        elif sort_by == "created_at":
            # Fallback to meta.updated_at if created_at not in allowed
            order_clause = f"ORDER BY g._meta.updated_at {sort_order.upper()}"

        # Build LIMIT/OFFSET clause
        pagination_clause = ""
        if limit is not None:
            pagination_clause = f"LIMIT {limit} OFFSET {offset}"

        data_query = (
            f"{select_clause} {from_clause} WHERE {where_clause} "
            f"{order_clause} {pagination_clause}"
        ).strip()

        bundle = self._pool.acquire()
        try:
            opts = _query_options()

            # Execute count query
            if opts is None:
                count_rows = list(bundle.cluster.query(count_query))
            else:
                try:
                    count_rows = list(bundle.cluster.query(count_query, opts))
                except TypeError:
                    count_rows = list(bundle.cluster.query(count_query))

            total = count_rows[0].get("total", 0) if count_rows else 0

            # Execute data query
            if opts is None:
                rows = bundle.cluster.query(data_query)
            else:
                try:
                    rows = bundle.cluster.query(data_query, opts)
                except TypeError:
                    rows = bundle.cluster.query(data_query)

            return [dict(row) for row in rows], total
        finally:
            self._pool.release(bundle)

    # Legacy helper for backward compatibility (returns list only)
    def _list_by_prefix_legacy(self, prefix: str) -> list[Dict[str, Any]]:
        """Legacy version that returns just the list (no total count)."""
        items, _ = self._list_by_prefix(prefix)
        return items
