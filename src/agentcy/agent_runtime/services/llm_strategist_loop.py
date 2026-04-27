from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.agent_runtime.services.plan_revision_utils import apply_delta, validate_candidate_graph
from agentcy.llm_utilities.llm_connector import LLM_Connector, Provider
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import RevisePlanCommand
from agentcy.pydantic_models.multi_agent_pipeline import PlanDraft, PlanRevision, PlanSuggestion
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskStatus

logger = logging.getLogger(__name__)

_run_locks: dict[str, asyncio.Lock] = {}
_last_suggested: dict[str, float] = {}

# ── Convergence tracking ──
_suggestion_counts: dict[str, int] = {}
_rejection_streak: dict[str, int] = {}


# ── Configuration helpers ──────────────────────────────────────────────────

def _loop_enabled() -> bool:
    return os.getenv("LLM_STRATEGIST_LOOP", "0").strip().lower() in ("1", "true", "yes", "on")


def _auto_apply() -> bool:
    return os.getenv("LLM_STRATEGIST_AUTO_APPLY", "0").strip().lower() in ("1", "true", "yes", "on")


def _require_human() -> bool:
    return os.getenv("LLM_STRATEGIST_REQUIRE_HUMAN", "1").strip().lower() not in ("0", "false", "no", "off")


def _min_interval_seconds() -> float:
    return float(os.getenv("LLM_STRATEGIST_MIN_INTERVAL_SECONDS", "5"))


def _max_suggestions_per_run() -> int:
    return int(os.getenv("LLM_STRATEGIST_MAX_SUGGESTIONS", "10"))


def _rejection_window() -> int:
    """Number of consecutive rejections before the loop stops suggesting."""
    return int(os.getenv("LLM_STRATEGIST_REJECTION_WINDOW", "3"))


def _is_stub_mode() -> bool:
    """Check if stub mode is enabled (allows operation without LLM)."""
    return os.getenv("LLM_STUB_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def _provider_from_env() -> Optional[Provider]:
    raw = os.getenv("LLM_STRATEGIST_LOOP_PROVIDER", "").strip().lower()
    if not raw:
        raw = os.getenv("LLM_STRATEGIST_PROVIDER", "").strip().lower()
    if raw in ("openai", "gpt"):
        return Provider.OPENAI
    if raw in ("llama", "ollama"):
        return Provider.LLAMA
    return None


# ── LLM prompt ─────────────────────────────────────────────────────────────

def _build_prompt(
    *,
    graph_spec: Dict[str, Any],
    run_state: Dict[str, Any],
    last_event: Dict[str, Any],
) -> list[Dict[str, str]]:
    system = "You are a plan delta strategist. Return ONLY valid JSON. No markdown."
    schema = {
        "rationale": "string",
        "task_overrides": {"task_id": {"field": "value"}},
        "add_tasks": [{"task_id": "string"}],
        "remove_tasks": ["task_id"],
        "add_edges": [{"from": "task_id", "to": "task_id"}],
        "remove_edges": [{"from": "task_id", "to": "task_id"}],
    }
    context = {
        "graph_spec": {
            "tasks": graph_spec.get("tasks") or [],
            "edges": graph_spec.get("edges") or [],
            "ontology": graph_spec.get("ontology"),
        },
        "run_state": run_state,
        "last_event": last_event,
    }
    user = (
        "Propose a delta to improve execution based on the latest event and run state. "
        "Only use task_ids already present unless adding new tasks is necessary. "
        "Return JSON that matches the schema.\n"
        f"Schema example: {json.dumps(schema, separators=(',', ':'))}\n"
        f"Context: {json.dumps(context, separators=(',', ':'))}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ── Delta parsing ──────────────────────────────────────────────────────────

def _extract_json(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return stripped[start : end + 1]


def _parse_delta(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not text or text == "Error":
        return None
    payload = _extract_json(text)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _delta_has_changes(delta: Dict[str, Any]) -> bool:
    for key in ("task_overrides", "add_tasks", "remove_tasks", "add_edges", "remove_edges"):
        value = delta.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _event_is_relevant(event: Dict[str, Any]) -> bool:
    status = str(event.get("status", "")).upper()
    return status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value)


# ── Run-level helpers ──────────────────────────────────────────────────────

def _run_lock(run_id: str) -> asyncio.Lock:
    lock = _run_locks.get(run_id)
    if lock is None:
        lock = asyncio.Lock()
        _run_locks[run_id] = lock
    return lock


async def _set_run_pause_state(
    rm: ResourceManager,
    *,
    username: str,
    pipeline_id: str,
    run_id: str,
    paused: bool,
    reason: Optional[str] = None,
    suggestion_id: Optional[str] = None,
) -> None:
    store = rm.ephemeral_store
    if store is None:
        return
    run_doc = store.read_run(username, pipeline_id, run_id)
    if not isinstance(run_doc, dict):
        return
    run_doc["paused"] = paused
    run_doc["pause_reason"] = reason if paused else None
    run_doc["pause_context"] = {"suggestion_id": suggestion_id} if paused else {}
    now = datetime_now_iso()
    if paused:
        run_doc["paused_at"] = now
    else:
        run_doc["resumed_at"] = now
    store.update_run(username, pipeline_id, run_id, run_doc)


def datetime_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _run_state_snapshot(run_doc: Dict[str, Any]) -> Dict[str, Any]:
    tasks = run_doc.get("tasks") or {}
    if not isinstance(tasks, dict):
        return {"tasks": {}}
    snapshot: Dict[str, Any] = {"tasks": {}}
    for task_id, entry in tasks.items():
        if isinstance(entry, dict):
            snapshot["tasks"][task_id] = {"status": entry.get("status")}
    return snapshot


# ── Stub mode delta generation ─────────────────────────────────────────────

def _stub_generate_delta(
    *,
    graph_spec: Dict[str, Any],
    run_state: Dict[str, Any],
    last_event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Rule-based delta generation when LLM is not available.

    Rules:
    1. If a task failed → suggest removing downstream edges to unblock successors,
       or tag the task for retry if no downstream edges exist.
    2. If a task completed and there's a bottleneck (multiple PENDING, none RUNNING)
       → tag pending tasks as parallel candidates.
    3. Otherwise → return None (no suggestion).
    """
    task_id = last_event.get("task_id")
    status = str(last_event.get("status", "")).upper()
    edges = list(graph_spec.get("edges") or [])
    run_tasks = run_state.get("tasks") or {}

    if status == "FAILED" and task_id:
        downstream_edges = [e for e in edges if e.get("from") == task_id]
        if downstream_edges:
            return {
                "rationale": (
                    f"Task '{task_id}' failed; removing {len(downstream_edges)} "
                    f"downstream edge(s) to unblock successors"
                ),
                "remove_edges": downstream_edges,
            }
        return {
            "rationale": f"Task '{task_id}' failed; tagging for retry",
            "task_overrides": {task_id: {"tags": ["retry_candidate", "failed"]}},
        }

    if status == "COMPLETED" and task_id:
        pending = [
            tid for tid, entry in run_tasks.items()
            if isinstance(entry, dict)
            and str(entry.get("status", "")).upper() == "PENDING"
        ]
        running = [
            tid for tid, entry in run_tasks.items()
            if isinstance(entry, dict)
            and str(entry.get("status", "")).upper() == "RUNNING"
        ]
        if len(pending) > 1 and len(running) == 0:
            overrides = {}
            for pid in pending[:3]:
                overrides[pid] = {"tags": ["parallel_candidate"]}
            return {
                "rationale": (
                    f"Task '{task_id}' completed; {len(pending)} tasks pending, "
                    f"none running. Tagging for parallelism."
                ),
                "task_overrides": overrides,
            }

    return None


# ── Delta generation (LLM + stub fallback) ─────────────────────────────────

async def _build_delta_with_llm(
    *,
    graph_spec: Dict[str, Any],
    run_state: Dict[str, Any],
    last_event: Dict[str, Any],
    request_id: str,
) -> Optional[Dict[str, Any]]:
    provider = _provider_from_env()
    if not provider:
        if _is_stub_mode():
            logger.info("Strategist loop using stub mode (LLM_STUB_MODE=1)")
            return _stub_generate_delta(
                graph_spec=graph_spec, run_state=run_state, last_event=last_event,
            )
        return None
    try:
        connector = LLM_Connector(provider=provider)
    except Exception as exc:
        if _is_stub_mode():
            logger.warning("Strategist loop LLM init failed, falling back to stub: %s", exc)
            return _stub_generate_delta(
                graph_spec=graph_spec, run_state=run_state, last_event=last_event,
            )
        logger.warning("Strategist loop LLM disabled (init failed): %s", exc)
        return None
    prompt = _build_prompt(graph_spec=graph_spec, run_state=run_state, last_event=last_event)
    await connector.start()
    try:
        responses = await connector.handle_incoming_requests([(request_id, prompt)])
    finally:
        await connector.stop()
    result = _parse_delta(responses.get(request_id))
    if result is None and _is_stub_mode():
        logger.warning("Strategist loop LLM response invalid, falling back to stub")
        return _stub_generate_delta(
            graph_spec=graph_spec, run_state=run_state, last_event=last_event,
        )
    return result


# ── Convergence detection ──────────────────────────────────────────────────

def _check_convergence(run_id: str) -> Optional[str]:
    """
    Return a skip reason if the loop should stop generating suggestions,
    or ``None`` if it should proceed.
    """
    count = _suggestion_counts.get(run_id, 0)
    max_allowed = _max_suggestions_per_run()
    if count >= max_allowed:
        return f"max_suggestions_reached ({count}/{max_allowed})"

    streak = _rejection_streak.get(run_id, 0)
    window = _rejection_window()
    if streak >= window:
        return f"consecutive_rejections ({streak}/{window})"

    return None


# ── Apply revision (publish to bus) ────────────────────────────────────────

async def _apply_revision(
    rm: ResourceManager,
    *,
    draft: PlanDraft,
    candidate_graph: Dict[str, Any],
    delta: Dict[str, Any],
    validation: Dict[str, Any],
    reason: str,
    suggestion_id: Optional[str],
    created_by: str,
) -> None:
    """Store candidate revision in Couchbase and publish ``RevisePlanCommand``."""
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    # 1. Store candidate in Couchbase (payload_ref pattern)
    next_revision = int(getattr(draft, "revision", 1) or 1) + 1
    ref_key = f"revision_candidate::{draft.username}::{draft.plan_id}::{next_revision}"
    candidate_doc = {
        "candidate_graph": candidate_graph,
        "delta": delta,
        "validation": validation,
        "base_revision": int(getattr(draft, "revision", 1) or 1),
        "next_revision": next_revision,
        "plan_id": draft.plan_id,
        "pipeline_id": draft.pipeline_id,
        "pipeline_run_id": draft.pipeline_run_id,
    }
    store.upsert_raw(ref_key, candidate_doc)

    # 2. Publish RevisePlanCommand to bus
    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        raise RuntimeError("RabbitMQ manager not configured")

    from agentcy.api_service.dependecies import CommandPublisher
    pub = CommandPublisher(rabbit_mgr)

    cmd = RevisePlanCommand(
        username=draft.username,
        pipeline_id=draft.pipeline_id,
        plan_id=draft.plan_id,
        pipeline_run_id=draft.pipeline_run_id,
        payload_ref=ref_key,
        suggestion_id=suggestion_id,
        created_by=created_by,
        reason=reason,
    )
    await pub.publish("commands.revise_plan", cmd)
    logger.info(
        "Published RevisePlanCommand: plan_id=%s ref=%s",
        draft.plan_id, ref_key,
    )


# ── Main entry point: task event handler ───────────────────────────────────

async def handle_task_event(rm: ResourceManager, event: Dict[str, Any]) -> Optional[PlanSuggestion]:
    if not _loop_enabled():
        return None
    if not _event_is_relevant(event):
        return None

    username = event.get("username")
    pipeline_id = event.get("pipeline_id")
    run_id = event.get("pipeline_run_id")
    if not (isinstance(username, str) and isinstance(pipeline_id, str) and isinstance(run_id, str)):
        return None

    lock = _run_lock(run_id)
    if lock.locked():
        return None

    async with lock:
        now = time.monotonic()
        last = _last_suggested.get(run_id)
        if last is not None and (now - last) < _min_interval_seconds():
            return None

        store = rm.graph_marker_store
        if store is None:
            return None

        run_doc = None
        if getattr(rm, "ephemeral_store", None) is not None:
            run_doc = rm.ephemeral_store.read_run(username, pipeline_id, run_id)
        if not isinstance(run_doc, dict):
            return None
        if run_doc.get("paused"):
            return None
        if run_doc.get("status") not in (None, "RUNNING"):
            return None

        # Convergence check
        skip_reason = _check_convergence(run_id)
        if skip_reason:
            logger.info("Strategist loop skipping for run %s: %s", run_id, skip_reason)
            return None

        try:
            draft = load_plan_draft(
                store,
                username=username,
                pipeline_id=pipeline_id,
                pipeline_run_id=run_id,
            )
        except Exception:
            logger.debug("Strategist loop could not load plan draft for %s", run_id, exc_info=True)
            return None
        base_graph = draft.graph_spec or {}
        run_state = _run_state_snapshot(run_doc)

        delta = await _build_delta_with_llm(
            graph_spec=base_graph,
            run_state=run_state,
            last_event=event,
            request_id=f"{draft.plan_id}:{run_id}:delta",
        )
        if not delta or not _delta_has_changes(delta):
            return None

        candidate_graph, applied = apply_delta(base_graph, delta)
        if applied == 0:
            return None

        validation = validate_candidate_graph(
            candidate_graph=candidate_graph,
            base_graph=base_graph,
            run_doc=run_doc,
            plan_id=draft.plan_id,
            pipeline_id=pipeline_id,
            username=username,
        )

        base_revision = int(getattr(draft, "revision", 1) or 1)
        suggestion = PlanSuggestion(
            plan_id=draft.plan_id,
            username=username,
            pipeline_id=pipeline_id,
            pipeline_run_id=run_id,
            base_revision=base_revision,
            candidate_revision=base_revision + 1,
            delta=delta,
            graph_spec=candidate_graph,
            status="PENDING_REVIEW",
            created_by="llm_strategist_loop",
            reason=delta.get("rationale") if isinstance(delta, dict) else None,
            validation=validation,
        )

        try:
            pending_revision = PlanRevision(
                plan_id=draft.plan_id,
                username=username,
                pipeline_id=pipeline_id,
                pipeline_run_id=run_id,
                revision=base_revision + 1,
                parent_revision=base_revision,
                graph_spec=candidate_graph,
                delta=delta,
                status="PENDING_REVIEW",
                created_by="llm_strategist_loop",
                reason=suggestion.reason,
                validation=validation,
            )
            store.save_plan_revision(username=username, revision=pending_revision)
        except Exception:
            logger.debug("Strategist loop failed to save pending plan revision", exc_info=True)

        # Track suggestion count for convergence
        _suggestion_counts[run_id] = _suggestion_counts.get(run_id, 0) + 1

        if not validation.get("conforms"):
            suggestion = suggestion.model_copy(update={"status": "REJECTED"})
            store.save_plan_suggestion(username=username, suggestion=suggestion)
            try:
                rejected = PlanRevision(
                    plan_id=draft.plan_id,
                    username=username,
                    pipeline_id=pipeline_id,
                    pipeline_run_id=run_id,
                    revision=base_revision + 1,
                    parent_revision=base_revision,
                    graph_spec=candidate_graph,
                    delta=delta,
                    status="REJECTED",
                    created_by="llm_strategist_loop",
                    reason="validation_failed",
                    validation=validation,
                )
                store.save_plan_revision(username=username, revision=rejected)
            except Exception:
                logger.debug("Failed to mark pending revision rejected", exc_info=True)
            return suggestion

        if _auto_apply() or not _require_human():
            store.save_plan_suggestion(username=username, suggestion=suggestion.model_copy(update={"status": "APPLIED"}))
            await _apply_revision(
                rm,
                draft=draft,
                candidate_graph=candidate_graph,
                delta=delta,
                validation=validation,
                reason="llm_auto_apply",
                suggestion_id=suggestion.suggestion_id,
                created_by="llm_strategist_loop",
            )
            _last_suggested[run_id] = now
            return suggestion

        store.save_plan_suggestion(username=username, suggestion=suggestion)
        await _set_run_pause_state(
            rm,
            username=username,
            pipeline_id=pipeline_id,
            run_id=run_id,
            paused=True,
            reason="llm_suggestion_pending",
            suggestion_id=suggestion.suggestion_id,
        )
        _last_suggested[run_id] = now
        return suggestion


# ── Suggestion decision handler ────────────────────────────────────────────

async def apply_suggestion_decision(
    rm: ResourceManager,
    *,
    username: str,
    suggestion_id: str,
    approved: bool,
    approver: Optional[str] = None,
) -> Optional[PlanDraft]:
    store = rm.graph_marker_store
    if store is None:
        return None
    raw = store.get_plan_suggestion(username=username, suggestion_id=suggestion_id)
    if not raw:
        return None
    suggestion = PlanSuggestion.model_validate(raw)
    if suggestion.status in ("APPLIED", "REJECTED"):
        return None

    pipeline_id = suggestion.pipeline_id
    run_id = suggestion.pipeline_run_id

    if not approved:
        store.save_plan_suggestion(
            username=username,
            suggestion=suggestion.model_copy(update={"status": "REJECTED"}),
        )
        # Track rejection streak for convergence
        if run_id:
            _rejection_streak[run_id] = _rejection_streak.get(run_id, 0) + 1
        try:
            rejected = PlanRevision(
                plan_id=suggestion.plan_id,
                username=username,
                pipeline_id=suggestion.pipeline_id,
                pipeline_run_id=suggestion.pipeline_run_id,
                revision=suggestion.candidate_revision,
                parent_revision=suggestion.base_revision,
                graph_spec=suggestion.graph_spec,
                delta=suggestion.delta,
                status="REJECTED",
                created_by=approver or "human",
                reason="human_rejected",
                validation=suggestion.validation,
            )
            store.save_plan_revision(username=username, revision=rejected)
        except Exception:
            logger.debug("Failed to mark plan revision rejected", exc_info=True)
        if run_id:
            await _set_run_pause_state(
                rm,
                username=username,
                pipeline_id=pipeline_id,
                run_id=run_id,
                paused=False,
                reason=None,
                suggestion_id=None,
            )
        return None

    # Reset rejection streak on approval
    if run_id:
        _rejection_streak[run_id] = 0

    try:
        draft = load_plan_draft(
            store,
            username=username,
            pipeline_id=pipeline_id,
            pipeline_run_id=run_id,
            plan_id=suggestion.plan_id,
        )
    except Exception:
        logger.debug("Failed to load plan draft for suggestion %s", suggestion_id, exc_info=True)
        return None
    base_graph = draft.graph_spec or {}

    rebase_info = None
    if int(getattr(draft, "revision", 1) or 1) != int(suggestion.base_revision):
        rebase_info = {
            "from": suggestion.base_revision,
            "to": int(getattr(draft, "revision", 1) or 1),
            "policy": "rebase_on_latest",
        }
    candidate_graph, _ = apply_delta(base_graph, suggestion.delta)
    validation = validate_candidate_graph(
        candidate_graph=candidate_graph,
        base_graph=base_graph,
        run_doc=(rm.ephemeral_store.read_run(username, pipeline_id, run_id) if run_id and rm.ephemeral_store else {}),
        plan_id=draft.plan_id,
        pipeline_id=pipeline_id,
        username=username,
    )
    if rebase_info:
        validation = dict(validation)
        validation["merge_policy"] = rebase_info
    if not validation.get("conforms"):
        store.save_plan_suggestion(
            username=username,
            suggestion=suggestion.model_copy(update={"status": "REJECTED", "validation": validation}),
        )
        try:
            rejected = PlanRevision(
                plan_id=suggestion.plan_id,
                username=username,
                pipeline_id=suggestion.pipeline_id,
                pipeline_run_id=suggestion.pipeline_run_id,
                revision=suggestion.candidate_revision,
                parent_revision=suggestion.base_revision,
                graph_spec=candidate_graph,
                delta=suggestion.delta,
                status="REJECTED",
                created_by=approver or "human",
                reason="validation_failed",
                validation=validation,
            )
            store.save_plan_revision(username=username, revision=rejected)
        except Exception:
            logger.debug("Failed to mark plan revision rejected after validation", exc_info=True)
        if run_id:
            await _set_run_pause_state(
                rm,
                username=username,
                pipeline_id=pipeline_id,
                run_id=run_id,
                paused=False,
                reason=None,
                suggestion_id=None,
            )
        return None

    await _apply_revision(
        rm,
        draft=draft,
        candidate_graph=candidate_graph,
        delta=suggestion.delta,
        validation=validation,
        reason="human_approved",
        suggestion_id=suggestion.suggestion_id,
        created_by=approver or "human",
    )
    store.save_plan_suggestion(
        username=username,
        suggestion=suggestion.model_copy(update={"status": "APPLIED", "validation": validation}),
    )
    if run_id:
        await _set_run_pause_state(
            rm,
            username=username,
            pipeline_id=pipeline_id,
            run_id=run_id,
            paused=False,
            reason=None,
            suggestion_id=None,
        )
    return draft
