from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import AuditLogEntry
from agentcy.semantic.namespaces import RESOURCE
from agentcy.semantic.plan_graph import serialize_graph
from agentcy.semantic.prov_graph import build_audit_prov_graph
from agentcy.semantic.fuseki_client import ingest_turtle

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)
_meter = metrics.get_meter(__name__)
_audit_count = _meter.create_counter(
    "agentcy.audit.log.count",
    unit="1",
    description="Number of audit log entries emitted",
)
_traceability_histogram = _meter.create_histogram(
    "agentcy.audit.traceability.score",
    unit="1",
    description="Traceability scores for audit summaries",
)


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


def _traceability_score(
    *,
    plan_valid: bool,
    has_human: bool,
    has_ethics: bool,
    has_execution: bool,
    has_escalation: bool,
) -> int:
    score = 0
    if plan_valid:
        score += 25
    if has_human:
        score += 20
    if has_ethics:
        score += 20
    if has_execution:
        score += 25
    if has_escalation:
        score += 10
    return min(score, 100)


def _span_ids() -> tuple[Optional[str], Optional[str]]:
    span = trace.get_current_span()
    if span is None:
        return None, None
    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid:
        return None, None
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"


def _emit_audit_log(entry: AuditLogEntry, payload: Dict[str, Any]) -> None:
    slim = {
        "event": "audit_log",
        "event_type": entry.event_type,
        "pipeline_run_id": entry.pipeline_run_id,
        "plan_id": payload.get("plan_id"),
        "traceability_score": payload.get("traceability_score"),
        "provenance": entry.provenance,
        "trace_id": entry.trace_id,
        "span_id": entry.span_id,
    }
    logger.info("AUDIT_EVENT %s", json.dumps(slim, separators=(",", ":")))


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    with _tracer.start_as_current_span("audit_summary") as span:
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
            raise ValueError("audit_logger requires username")

        draft = load_plan_draft(store, username=username, pipeline_id=pipeline_id, plan_id=plan_id)

        span.set_attribute("agentcy.username", username)
        span.set_attribute("agentcy.pipeline_id", pipeline_id or "")
        span.set_attribute("agentcy.plan_id", draft.plan_id)
        span.set_attribute("agentcy.pipeline_run_id", str(pipeline_run_id or ""))

        human_approvals, _ = store.list_human_approvals(username=username, plan_id=draft.plan_id)
        ethics_checks, _ = store.list_ethics_checks(username=username, plan_id=draft.plan_id)
        execution_reports, _ = store.list_execution_reports(username=username, plan_id=draft.plan_id)
        escalation_notices, _ = store.list_escalation_notices(username=username, pipeline_run_id=pipeline_run_id)

        latest_approval = _latest_by_timestamp(human_approvals, "decided_at")
        latest_ethics = _latest_by_timestamp(ethics_checks, "checked_at")
        latest_execution = _latest_by_timestamp(execution_reports, "created_at")
        latest_escalation = _latest_by_timestamp(escalation_notices, "created_at")

        score = _traceability_score(
            plan_valid=bool(draft.is_valid),
            has_human=latest_approval is not None,
            has_ethics=latest_ethics is not None,
            has_execution=latest_execution is not None,
            has_escalation=latest_escalation is not None,
        )
        span.set_attribute("agentcy.traceability_score", score)

        payload = {
            "plan_id": draft.plan_id,
            "plan_valid": draft.is_valid,
            "human_approval": latest_approval,
            "ethics_check": latest_ethics,
            "execution_report": latest_execution,
            "escalation_notice": latest_escalation,
            "traceability_score": score,
        }
        provenance_uri = f"{RESOURCE}audit/{pipeline_run_id or 'run'}/{draft.plan_id}"
        payload["provenance_uri"] = provenance_uri
        try:
            prov_graph = build_audit_prov_graph(
                plan_id=draft.plan_id,
                pipeline_run_id=str(pipeline_run_id or ""),
                username=username,
                payload=payload,
            )
            turtle = serialize_graph(prov_graph, fmt="turtle")
            payload["prov_rdf"] = turtle
            payload["prov_format"] = "turtle"
            await ingest_turtle(turtle, graph_uri=provenance_uri)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            logger.exception("Audit logger failed to build provenance graph for %s", username)

        trace_id, span_id = _span_ids()
        if trace_id or span_id:
            payload["trace"] = {"trace_id": trace_id, "span_id": span_id}

        entry = AuditLogEntry(
            event_type="audit_summary",
            pipeline_run_id=str(pipeline_run_id or ""),
            actor=str(payload.get("actor") or "audit_logger"),
            rationale=payload.get("rationale"),
            provenance=provenance_uri,
            trace_id=trace_id,
            span_id=span_id,
            payload=payload,
        )
        key = store.add_audit_log(username=username, entry=entry)

        _audit_count.add(1, {"event_type": entry.event_type})
        _traceability_histogram.record(score, {"event_type": entry.event_type})
        _emit_audit_log(entry, payload)

        logger.info("Audit log stored for %s (score=%s)", username, score)
        return {
            "plan_id": draft.plan_id,
            "audit_key": key,
            "traceability_score": score,
            "logged": True,
        }
