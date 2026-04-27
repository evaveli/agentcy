from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import aio_pika

from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import ExecutionOutcome, ExecutionReport

logger = logging.getLogger(__name__)


def _payload_from_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message.get("data") or message)
    return dict(getattr(message, "data", {}) or {})


def _latest_by_timestamp(items: List[Dict[str, Any]], field: str) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    def _key(item: Dict[str, Any]) -> str:
        return str(item.get(field) or "")
    return max(items, key=_key)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _resolve_mode(rm: ResourceManager) -> str:
    raw = os.getenv("SYSTEM_EXECUTION_MODE", "auto").strip().lower()
    if raw in {"simulate", "stub"}:
        return "simulate"
    if raw in {"launch", "real"}:
        return "launch"
    if rm.rabbit_mgr is None or rm.service_store is None:
        return "simulate"
    return "launch"


def _start_task_queue() -> str:
    return os.getenv("START_TASK_QUEUE", "commands.start_task")


def _resolve_service_name(
    task: Dict[str, Any],
    *,
    username: str,
    registry_store: Any,
) -> Optional[str]:
    service_name = task.get("service_name")
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    service_name = service_name or (metadata.get("service_name") if isinstance(metadata, dict) else None)
    if service_name:
        return str(service_name)
    agent_id = task.get("assigned_agent")
    if agent_id and registry_store is not None:
        entry = registry_store.get(username=username, agent_id=str(agent_id))
        if entry and entry.get("service_name"):
            return str(entry.get("service_name"))
    return None


def _resolve_runtime_and_artifact(
    task: Dict[str, Any],
    service_doc: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    runtime = (
        task.get("runtime")
        or (metadata.get("runtime") if isinstance(metadata, dict) else None)
        or (service_doc or {}).get("runtime")
    )
    artifact = (
        task.get("artifact")
        or (metadata.get("artifact") if isinstance(metadata, dict) else None)
        or (service_doc or {}).get("artifact")
    )
    image_tag = (service_doc or {}).get("image_tag") or (metadata.get("image_tag") if isinstance(metadata, dict) else None)

    if artifact is None and runtime == "container" and image_tag:
        artifact = str(image_tag)

    if isinstance(artifact, str):
        if runtime == "container":
            if ":" in artifact:
                repo, tag = artifact.rsplit(":", 1)
                artifact = {"repo": repo, "tag": tag}
            else:
                return runtime, None, "container_artifact_missing_tag"
        else:
            artifact = {"kind": "entry", "entry": artifact}

    if isinstance(artifact, dict):
        return runtime, dict(artifact), None
    return runtime, None, "artifact_unavailable"


def _service_lookup_map(service_store: Any, username: str) -> Dict[str, str]:
    if service_store is None:
        return {}
    try:
        entries = service_store.list_all(username)
    except Exception:
        logger.debug("Service store list_all failed", exc_info=True)
        return {}
    mapping = {}
    for item in entries:
        name = item.get("service_name")
        service_id = item.get("service_id")
        if name and service_id:
            mapping[str(name)] = str(service_id)
    return mapping


async def _publish_start_tasks(
    rm: ResourceManager,
    payloads: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], Optional[str]]]:
    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        return [(payload, "rabbit_not_configured") for payload in payloads]
    errors: List[Tuple[Dict[str, Any], Optional[str]]] = []
    async with rabbit_mgr.get_channel() as channel:
        for payload in payloads:
            try:
                body = json.dumps(payload).encode("utf-8")
                await channel.default_exchange.publish(
                    aio_pika.Message(body=body),
                    routing_key=_start_task_queue(),
                )
                errors.append((payload, None))
            except Exception as exc:
                errors.append((payload, f"start_task_publish_failed:{exc}"))
    return errors


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    pipeline_id = getattr(message, "pipeline_id", None) or (message.get("pipeline_id") if isinstance(message, dict) else None)
    plan_id = getattr(message, "plan_id", None)
    pipeline_run_id = getattr(message, "pipeline_run_id", None)
    if isinstance(message, dict):
        plan_id = message.get("plan_id", plan_id)
        pipeline_run_id = message.get("pipeline_run_id", pipeline_run_id)

    if not username:
        raise ValueError("system_executor requires username")

    draft = load_plan_draft(store, username=username, pipeline_id=pipeline_id, plan_id=plan_id)
    graph_spec = draft.graph_spec or {}
    tasks = list(graph_spec.get("tasks") or [])

    payload = _payload_from_message(message)
    fail_task_ids = set(payload.get("fail_task_ids") or payload.get("failed_tasks") or [])
    overrides = payload.get("execution_overrides") if isinstance(payload.get("execution_overrides"), dict) else {}

    require_ethics = os.getenv("EXECUTION_REQUIRE_ETHICS", "1") != "0"
    require_human = os.getenv("EXECUTION_REQUIRE_HUMAN", "0") == "1"

    if require_ethics:
        _ethics_items, _ = store.list_ethics_checks(username=username, plan_id=draft.plan_id)
        latest_check = _latest_by_timestamp(_ethics_items, "checked_at")
        if latest_check and not latest_check.get("approved"):
            logger.warning("System execution blocked by ethics check for %s", username)
            report = ExecutionReport(plan_id=draft.plan_id, pipeline_run_id=pipeline_run_id, outcomes=[], success_rate=0.0)
            store.save_execution_report(username=username, report=report)
            return {
                "plan_id": draft.plan_id,
                "execution_report_id": report.report_id,
                "blocked": True,
                "block_reason": "ethics_rejected",
                "task_outcomes": [],
            }

    if require_human:
        approvals, _ = store.list_human_approvals(username=username, plan_id=draft.plan_id)
        latest = _latest_by_timestamp(approvals, "decided_at")
        if latest and not latest.get("approved"):
            logger.warning("System execution blocked by human approval for %s", username)
            report = ExecutionReport(plan_id=draft.plan_id, pipeline_run_id=pipeline_run_id, outcomes=[], success_rate=0.0)
            store.save_execution_report(username=username, report=report)
            return {
                "plan_id": draft.plan_id,
                "execution_report_id": report.report_id,
                "blocked": True,
                "block_reason": "human_rejected",
                "task_outcomes": [],
            }

    outcomes: List[ExecutionOutcome] = []
    mode = _resolve_mode(rm)

    if mode == "launch":
        registry_store = getattr(rm, "agent_registry_store", None)
        service_store = getattr(rm, "service_store", None)
        service_map = _service_lookup_map(service_store, username)
        start_payloads: List[Dict[str, Any]] = []
        task_payloads: Dict[str, Dict[str, Any]] = {}

        for task in tasks:
            task_id = task.get("task_id")
            if not task_id:
                continue
            task_override = overrides.get(task_id, {}) if isinstance(overrides, dict) else {}
            force_fail = task_override.get("success") is False
            if not force_fail and _truthy(task_override.get("force_fail")):
                force_fail = True
            if not force_fail:
                meta = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                if _truthy(task.get("simulate_failure")) or _truthy(meta.get("force_fail")):
                    force_fail = True
            if task_id in fail_task_ids:
                force_fail = True

            service_name = _resolve_service_name(task, username=username, registry_store=registry_store)
            if not service_name:
                outcomes.append(
                    ExecutionOutcome(
                        task_id=str(task_id),
                        agent_id=task.get("assigned_agent"),
                        success=False,
                        duration_seconds=float(task_override.get("duration_seconds", 0.1)),
                        error="service_name_unresolved",
                        metadata={"capability": (task.get("required_capabilities") or [None])[0]},
                    )
                )
                continue

            service_doc = None
            if service_store is not None and service_name in service_map:
                service_doc = service_store.get(username, service_map[service_name])

            runtime, artifact, artifact_error = _resolve_runtime_and_artifact(task, service_doc)
            if artifact is None:
                outcomes.append(
                    ExecutionOutcome(
                        task_id=str(task_id),
                        agent_id=task.get("assigned_agent"),
                        success=False,
                        duration_seconds=float(task_override.get("duration_seconds", 0.1)),
                        error=artifact_error or "artifact_unresolved",
                        metadata={
                            "service_name": service_name,
                            "runtime": runtime,
                            "capability": (task.get("required_capabilities") or [None])[0],
                        },
                    )
                )
                continue

            env_payload = {
                "PIPELINE_ID": pipeline_id or "",
                "PIPELINE_RUN_ID": pipeline_run_id or "",
                "PLAN_ID": draft.plan_id,
                "TASK_ID": str(task_id),
            }
            task_payload = task.get("payload") or (task.get("metadata") or {}).get("payload")
            if task_payload is not None:
                env_payload["TASK_PAYLOAD"] = json.dumps(task_payload)

            if force_fail:
                outcomes.append(
                    ExecutionOutcome(
                        task_id=str(task_id),
                        agent_id=task.get("assigned_agent"),
                        success=False,
                        duration_seconds=float(task_override.get("duration_seconds", 0.1)),
                        error="forced_failure",
                        metadata={
                            "service_name": service_name,
                            "runtime": runtime,
                            "capability": (task.get("required_capabilities") or [None])[0],
                        },
                    )
                )
                continue

            payload = {
                "runtime": runtime or "python_plugin",
                "service_name": service_name,
                "artifact": artifact,
                "task_environ": env_payload,
            }
            task_payloads[str(task_id)] = payload
            start_payloads.append(payload)

        publish_results = await _publish_start_tasks(rm, start_payloads)
        publish_errors = {
            item[0].get("task_environ", {}).get("TASK_ID"): item[1]
            for item in publish_results
            if item[1]
        }

        for task in tasks:
            task_id = str(task.get("task_id"))
            if any(outcome.task_id == task_id for outcome in outcomes):
                continue
            payload = task_payloads.get(task_id)
            publish_error = publish_errors.get(task_id)
            launched = publish_error is None
            duration = float(overrides.get(task_id, {}).get("duration_seconds", 0.2)) if isinstance(overrides, dict) else 0.2
            outcomes.append(
                ExecutionOutcome(
                    task_id=task_id,
                    agent_id=task.get("assigned_agent"),
                    success=launched,
                    duration_seconds=duration,
                    error=None if launched else publish_error,
                    metadata={
                        "service_name": payload.get("service_name") if payload else None,
                        "runtime": payload.get("runtime") if payload else None,
                        "capability": (task.get("required_capabilities") or [None])[0],
                        "launch_status": "started" if launched else "failed",
                    },
                )
            )
    else:
        for task in tasks:
            task_id = task.get("task_id")
            if not task_id:
                continue
            task_override = overrides.get(task_id, {}) if isinstance(overrides, dict) else {}
            force_fail = task_override.get("success") is False
            if not force_fail and _truthy(task_override.get("force_fail")):
                force_fail = True
            if not force_fail:
                meta = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                if _truthy(task.get("simulate_failure")) or _truthy(meta.get("force_fail")):
                    force_fail = True
            if task_id in fail_task_ids:
                force_fail = True

            success = not force_fail
            outcomes.append(
                ExecutionOutcome(
                    task_id=str(task_id),
                    agent_id=task.get("assigned_agent"),
                    success=success,
                    duration_seconds=float(task_override.get("duration_seconds", 1.0)),
                    error=None if success else "simulated_failure",
                    metadata={"capability": (task.get("required_capabilities") or [None])[0]},
                )
            )

    success_count = sum(1 for outcome in outcomes if outcome.success)
    success_rate = (success_count / len(outcomes)) if outcomes else 0.0

    report = ExecutionReport(
        plan_id=draft.plan_id,
        pipeline_run_id=pipeline_run_id,
        outcomes=outcomes,
        success_rate=success_rate,
    )
    store.save_execution_report(username=username, report=report)

    task_outcomes = [
        {
            "task_id": outcome.task_id,
            "agent_id": outcome.agent_id,
            "success": outcome.success,
            "duration_seconds": outcome.duration_seconds,
            "error": outcome.error,
            "capability": outcome.metadata.get("capability"),
            "service_name": outcome.metadata.get("service_name"),
            "runtime": outcome.metadata.get("runtime"),
            "launch_status": outcome.metadata.get("launch_status"),
        }
        for outcome in outcomes
    ]

    logger.info("System executor stored execution report %s for %s", report.report_id, username)
    return {
        "plan_id": draft.plan_id,
        "execution_report_id": report.report_id,
        "success_rate": success_rate,
        "task_outcomes": task_outcomes,
        "blocked": False,
        "mode": mode,
    }
