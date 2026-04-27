from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.agent_runtime.services.cnp_utils import task_params, update_cnp_metadata
from agentcy.pydantic_models.multi_agent_pipeline import AffordanceMarker, ExecutionReport, FailureContext, TaskSpec

logger = logging.getLogger(__name__)


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _coerce_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_success(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"success", "succeeded", "completed", "ok", "true", "passed"}:
        return True
    if lowered in {"failed", "error", "false", "no", "rejected"}:
        return False
    return None


def _payload_from_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message.get("data") or message)
    return dict(getattr(message, "data", {}) or {})


def _as_dict(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            return None
    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            return None
    return None


def _candidate_payloads(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if payload:
        candidates.append(payload)

    direct_payload = _as_dict(payload.get("payload")) if isinstance(payload, dict) else None
    if direct_payload:
        candidates.append(direct_payload)

    nested_payload = _as_dict(direct_payload.get("payload")) if direct_payload else None
    if nested_payload:
        candidates.append(nested_payload)
        nested_result = _as_dict(nested_payload.get("result"))
        if nested_result:
            candidates.append(nested_result)

    result_payload = _as_dict(direct_payload.get("result")) if direct_payload else None
    if result_payload:
        candidates.append(result_payload)

    if isinstance(payload.get("upstreams"), dict):
        for entry in payload["upstreams"].values():
            entry_dict = _as_dict(entry)
            if entry_dict:
                candidates.append(entry_dict)
                entry_payload = _as_dict(entry_dict.get("payload"))
                if entry_payload:
                    candidates.append(entry_payload)
                    nested_result = _as_dict(entry_payload.get("result"))
                    if nested_result:
                        candidates.append(nested_result)

    if isinstance(payload.get("aggregated"), dict):
        for entry in payload["aggregated"].values():
            entry_dict = _as_dict(entry)
            if entry_dict:
                candidates.append(entry_dict)

    return candidates


def _collect_output_refs(payload: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for candidate in _candidate_payloads(payload):
        output_ref = candidate.get("output_ref")
        if isinstance(output_ref, str) and output_ref:
            refs.append(output_ref)
    return refs


def _extract_feedback(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not payload:
        return []
    for candidate in _candidate_payloads(payload):
        for key in ("feedback", "task_outcomes", "results", "events"):
            value = candidate.get(key)
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]
                if items:
                    return items
            if isinstance(value, dict):
                return [value]

        if "task_id" in candidate:
            return [candidate]

    return []


def _task_spec_lookup(store, username: str) -> Dict[str, TaskSpec]:
    raw, _ = store.list_task_specs(username=username)
    lookup: Dict[str, TaskSpec] = {}
    for item in raw:
        try:
            spec = TaskSpec.model_validate(item)
        except Exception:
            continue
        lookup[spec.task_id] = spec
    return lookup


def _latest_execution_report(reports: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not reports:
        return None
    def _key(item: Dict[str, Any]) -> str:
        return str(item.get("created_at") or "")
    return max(reports, key=_key)


def _feedback_from_execution_report(report_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        report = ExecutionReport.model_validate(report_doc)
    except Exception:
        return []
    feedback: List[Dict[str, Any]] = []
    for outcome in report.outcomes:
        entry = {
            "task_id": outcome.task_id,
            "agent_id": outcome.agent_id,
            "success": outcome.success,
            "duration_seconds": outcome.duration_seconds,
            "error": outcome.error,
        }
        if isinstance(outcome.metadata, dict):
            entry.update(outcome.metadata)
        feedback.append(entry)
    return feedback


_ERROR_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "timeout": ["timeout", "timed out", "deadline exceeded", "deadline_exceeded"],
    "validation": ["validation", "invalid", "schema", "pydantic", "value error", "valueerror"],
    "runtime": ["runtime", "assertion", "index error", "key error", "attribute error"],
    "connection": ["connection", "refused", "unreachable", "dns", "socket"],
    "resource": ["oom", "out of memory", "disk full", "quota", "resource exhausted"],
    "permission": ["permission", "forbidden", "unauthorized", "403", "401"],
}


def _classify_error(error: Optional[str]) -> str:
    """Classify a free-form error string into a normalised category."""
    if not error:
        return "unknown"
    lowered = error.lower()
    for category, keywords in _ERROR_CATEGORY_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return category
    return "unknown"


def _build_failure_context(
    entry: Dict[str, Any],
    *,
    task_type: Optional[str],
    existing_marker: Optional[AffordanceMarker],
) -> Optional[FailureContext]:
    """Build a FailureContext from a feedback entry on failure."""
    # Avoid `or` chain — False is falsy but is a valid explicit value
    raw_success = entry.get("success")
    if raw_success is None:
        raw_success = entry.get("status")
    if raw_success is None:
        raw_success = entry.get("outcome")
    success = _coerce_success(raw_success)
    if success is not False:
        return None

    error_raw = entry.get("error") or entry.get("error_message") or entry.get("reason") or ""
    error_category = _classify_error(str(error_raw) if error_raw else None)
    resolved_task_type = task_type or entry.get("task_type") or entry.get("capability") or "general"

    prev_count = 0
    if existing_marker and existing_marker.failure_context:
        prev = existing_marker.failure_context
        if prev.task_type == resolved_task_type and prev.error_category == error_category:
            prev_count = prev.count

    return FailureContext(
        task_type=str(resolved_task_type),
        error_category=error_category,
        count=prev_count + 1,
        last_error=str(error_raw)[:200] if error_raw else None,
    )


def _select_marker(markers: List[Dict[str, Any]]) -> Optional[AffordanceMarker]:
    if not markers:
        return None
    try:
        markers.sort(key=lambda item: float(item.get("intensity", 0.0)), reverse=True)
    except (TypeError, ValueError):
        pass
    try:
        return AffordanceMarker.model_validate(markers[0])
    except Exception:
        return None


def _apply_feedback(
    store,
    *,
    username: str,
    feedback: List[Dict[str, Any]],
    ttl_seconds: int,
    min_intensity: float,
    max_intensity: float,
    success_bonus: float,
    failure_penalty: float,
    spec_lookup: Optional[Dict[str, TaskSpec]] = None,
) -> Tuple[int, int]:
    updated = 0
    task_ids: set[str] = set()

    for entry in feedback:
        task_id = entry.get("task_id")
        agent_id = entry.get("agent_id") or entry.get("assigned_agent")
        if not task_id or not agent_id:
            continue

        task_ids.add(str(task_id))

        existing, _ = store.list_affordance_markers(
            username=username, task_id=str(task_id), agent_id=str(agent_id)
        )
        marker = _select_marker(existing)
        base_intensity = _coerce_float(entry.get("intensity"))
        if base_intensity is None and marker is not None:
            base_intensity = marker.intensity
        if base_intensity is None:
            base_intensity = 0.5

        delta = _coerce_float(entry.get("intensity_delta"), 0.0) or 0.0
        raw_s = entry.get("success")
        if raw_s is None:
            raw_s = entry.get("status")
        if raw_s is None:
            raw_s = entry.get("outcome")
        success = _coerce_success(raw_s)
        if success is True:
            delta += success_bonus
        elif success is False:
            delta -= failure_penalty

        intensity = min(max(base_intensity + delta, min_intensity), max_intensity)

        # Resolve task_type for failure context
        resolved_task_type: Optional[str] = entry.get("task_type") or entry.get("capability")
        if resolved_task_type is None and spec_lookup is not None:
            spec = spec_lookup.get(str(task_id))
            if spec and spec.required_capabilities:
                resolved_task_type = spec.required_capabilities[0]

        fc = _build_failure_context(
            entry,
            task_type=resolved_task_type,
            existing_marker=marker,
        )

        if marker is None:
            capability = entry.get("capability")
            if capability is None and resolved_task_type is not None:
                capability = resolved_task_type
            marker = AffordanceMarker(
                task_id=str(task_id),
                agent_id=str(agent_id),
                capability=capability,
                intensity=intensity,
                rationale="pheromone_feedback",
                failure_context=fc,
            )
        else:
            update_fields: Dict[str, Any] = {
                "intensity": intensity,
                "rationale": "pheromone_feedback",
            }
            # Attach failure_context on failure; clear it on success
            success = _coerce_success(entry.get("success") or entry.get("status") or entry.get("outcome"))
            if fc is not None:
                update_fields["failure_context"] = fc
            elif success is True and marker.failure_context is not None:
                update_fields["failure_context"] = None
            marker = marker.model_copy(update=update_fields)

        store.add_affordance_marker(
            username=username, marker=marker, ttl_seconds=ttl_seconds
        )
        updated += 1

    return updated, len(task_ids)


def _update_agent_registry(
    registry: Any,
    *,
    username: str,
    feedback: List[Dict[str, Any]],
    spec_lookup: Dict[str, TaskSpec],
) -> int:
    if registry is None or not hasattr(registry, "get") or not hasattr(registry, "heartbeat"):
        return 0
    updated = 0
    for entry in feedback:
        agent_id = entry.get("agent_id") or entry.get("assigned_agent")
        task_id = entry.get("task_id")
        if not agent_id or not task_id:
            continue
        spec = spec_lookup.get(str(task_id))
        if spec is None:
            continue
        success = _coerce_success(entry.get("success") or entry.get("status") or entry.get("outcome"))
        if success is None:
            continue
        try:
            doc = registry.get(username=username, agent_id=str(agent_id))
        except Exception:
            continue
        if not doc:
            continue
        params = task_params(spec)
        metadata = update_cnp_metadata(
            agent_doc=doc,
            task_type=params["task_type"],
            stimulus=params["stimulus"],
            priority=params["priority"],
            reward=params["reward"],
            success=success,
        )
        try:
            registry.heartbeat(username=username, agent_id=str(agent_id), metadata=metadata)
            updated += 1
        except Exception:
            continue
    return updated


def _decay_markers(
    store,
    *,
    username: str,
    decay_factor: float,
    min_intensity: float,
    max_updates: int,
) -> Tuple[int, int]:
    markers, _ = store.list_affordance_markers(username=username)
    if not markers:
        return 0, 0

    updated = 0
    for marker_doc in markers[:max_updates]:
        try:
            marker = AffordanceMarker.model_validate(marker_doc)
        except Exception:
            continue
        new_intensity = max(min_intensity, marker.intensity * decay_factor)
        if abs(new_intensity - marker.intensity) < 1e-6:
            continue
        updated_marker = marker.model_copy(update={"intensity": new_intensity})
        store.add_affordance_marker(
            username=username,
            marker=updated_marker,
            ttl_seconds=marker_doc.get("ttl_seconds") or marker.ttl_seconds,
        )
        updated += 1

    return updated, len(markers)


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    """
    Pheromone engine updates affordance markers using execution feedback or decay.
    """
    if os.getenv("PHEROMONE_ENABLE", "1").strip().lower() in {"", "0", "false", "no", "off"}:
        return {"markers_updated": 0, "task_count": 0, "mode": "disabled"}
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    if not username:
        raise ValueError("pheromone_engine requires username")

    payload = _payload_from_message(message)
    feedback = _extract_feedback(payload)
    if not feedback and getattr(rm, "doc_store", None) is not None:
        refs = _collect_output_refs(payload)
        for ref in refs:
            try:
                raw = await rm.doc_store.load(ref)
            except Exception:
                continue
            feedback = _extract_feedback(raw)
            if feedback:
                break

    ttl_seconds = _get_env_int("PHEROMONE_TTL_SECONDS", 300)
    min_intensity = _get_env_float("PHEROMONE_INTENSITY_MIN", 0.0)
    max_intensity = _get_env_float("PHEROMONE_INTENSITY_MAX", 1.0)
    decay_factor = _get_env_float("PHEROMONE_DECAY_FACTOR", 0.9)
    decay_limit = _get_env_int("PHEROMONE_DECAY_LIMIT", 250)
    success_bonus = _get_env_float("PHEROMONE_SUCCESS_BONUS", 0.1)
    failure_penalty = _get_env_float("PHEROMONE_FAILURE_PENALTY", 0.2)

    if not feedback:
        plan_id = payload.get("plan_id")
        if plan_id:
            reports, _ = store.list_execution_reports(username=username, plan_id=str(plan_id))
            latest = _latest_execution_report(reports)
            if latest:
                feedback = _feedback_from_execution_report(latest)

    if feedback:
        spec_lookup = _task_spec_lookup(store, username)
        updated, task_count = _apply_feedback(
            store,
            username=username,
            feedback=feedback,
            ttl_seconds=ttl_seconds,
            min_intensity=min_intensity,
            max_intensity=max_intensity,
            success_bonus=success_bonus,
            failure_penalty=failure_penalty,
            spec_lookup=spec_lookup,
        )
        registry = getattr(rm, "agent_registry_store", None)
        registry_updates = _update_agent_registry(
            registry,
            username=username,
            feedback=feedback,
            spec_lookup=spec_lookup,
        )
        logger.info(
            "Pheromone feedback updated %d markers for %s (registry_updates=%d)",
            updated,
            username,
            registry_updates,
        )
        return {
            "markers_updated": updated,
            "task_count": task_count,
            "mode": "feedback",
            "registry_updates": registry_updates,
        }

    updated, total = _decay_markers(
        store,
        username=username,
        decay_factor=decay_factor,
        min_intensity=min_intensity,
        max_updates=decay_limit,
    )
    logger.info("Pheromone decay updated %d/%d markers for %s", updated, total, username)
    return {"markers_updated": updated, "marker_count": total, "mode": "decay"}
