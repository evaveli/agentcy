from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import EscalationNotice, RiskLevel

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


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
        raise ValueError("failure_escalation requires username")

    draft = load_plan_draft(store, username=username, pipeline_id=pipeline_id, plan_id=plan_id)
    payload = _payload_from_message(message)

    max_retries = _int(payload.get("max_retries"), _int(os.getenv("FAILURE_ESCALATION_MAX_RETRIES", 2), 2))
    attempts = _int(payload.get("attempts"), _int(getattr(message, "attempts", 0), 0))

    _exec_reports, _ = store.list_execution_reports(username=username, plan_id=draft.plan_id)
    latest_report = _latest_by_timestamp(
        _exec_reports,
        "created_at",
    )
    failure_count = 0
    if latest_report and isinstance(latest_report.get("outcomes"), list):
        failure_count = sum(1 for outcome in latest_report["outcomes"] if outcome.get("success") is False)

    escalated = failure_count > 0 and attempts >= max_retries
    reason = "retries_exhausted" if escalated else "pending"
    severity = RiskLevel.HIGH if escalated else RiskLevel.MEDIUM

    notice = EscalationNotice(
        pipeline_run_id=pipeline_run_id or "",
        reason=reason,
        severity=severity,
        retries_exhausted=escalated,
    )
    store.save_escalation_notice(username=username, notice=notice)

    logger.info("Failure escalation recorded for %s (escalated=%s)", username, escalated)
    return {
        "plan_id": draft.plan_id,
        "attempts": attempts,
        "max_retries": max_retries,
        "failure_count": failure_count,
        "escalated": escalated,
        "reason": reason,
    }
