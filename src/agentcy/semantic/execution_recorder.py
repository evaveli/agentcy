"""
Fire-and-forget execution recorder for the knowledge graph.

Coordinates writing execution records and data flow edges to both
Apache Jena Fuseki (RDF triples) and Couchbase (fast KV lookup).

All public functions are async, catch all exceptions internally,
and return ``bool`` indicating success.  They never block the caller.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, List, Optional

from agentcy.semantic.execution_graph import build_execution_graph
from agentcy.semantic.dataflow_graph import build_dataflow_graph
from agentcy.semantic.plan_graph import serialize_graph
from agentcy.semantic.fuseki_client import ingest_turtle

logger = logging.getLogger(__name__)


def _recorder_enabled() -> bool:
    """Check if execution recording is enabled."""
    raw = os.getenv("EXECUTION_RECORDER_ENABLE", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    # Default: enabled when Fuseki is enabled
    fuseki_raw = os.getenv("FUSEKI_ENABLE", "").strip().lower()
    return fuseki_raw in {"1", "true", "yes", "on"} or bool(os.getenv("FUSEKI_URL"))


async def record_execution(
    *,
    task_id: str,
    agent_id: str,
    plan_id: str,
    pipeline_run_id: str,
    username: str,
    status: str,
    attempt_number: int = 1,
    duration_seconds: Optional[float] = None,
    error: Optional[str] = None,
    graph_marker_store: Optional[Any] = None,
) -> bool:
    """Record a task execution in the knowledge graph and Couchbase.

    This is fire-and-forget: never raises, returns False on any error.

    Args:
        task_id: The task that was executed.
        agent_id: The agent/service that ran the task.
        plan_id: The plan this task belongs to.
        pipeline_run_id: The pipeline run identifier.
        username: Owner of the pipeline.
        status: ``"completed"`` or ``"failed"``.
        attempt_number: Which attempt (1-based).
        duration_seconds: Execution duration.
        error: Error message on failure.
        graph_marker_store: Optional GraphMarkerStore for Couchbase persistence.

    Returns:
        True if RDF ingestion succeeded, False otherwise.
    """
    if not _recorder_enabled():
        return False

    try:
        now = datetime.now(timezone.utc).isoformat()

        # Build and ingest RDF
        graph = build_execution_graph(
            task_id=task_id,
            agent_id=agent_id,
            plan_id=plan_id,
            pipeline_run_id=pipeline_run_id,
            status=status,
            attempt_number=attempt_number,
            duration_seconds=duration_seconds,
            error=error,
            executed_at=now,
        )
        turtle = serialize_graph(graph)
        await ingest_turtle(turtle)

        # Persist to Couchbase for fast KV lookup
        if graph_marker_store is not None:
            try:
                key = f"execution::{username}::{pipeline_run_id}::{task_id}::{attempt_number}"
                doc = {
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "plan_id": plan_id,
                    "pipeline_run_id": pipeline_run_id,
                    "username": username,
                    "status": status,
                    "attempt_number": attempt_number,
                    "duration_seconds": duration_seconds,
                    "error": error,
                    "executed_at": now,
                    "_meta": {"type": "execution_record", "updated_at": now},
                }
                graph_marker_store.upsert_raw(key, doc)
            except Exception:
                logger.warning("Failed to persist execution record to Couchbase", exc_info=True)

        return True
    except Exception:
        logger.warning("Failed to record execution for task %s", task_id, exc_info=True)
        return False


async def record_data_flow(
    *,
    from_task: str,
    to_task: str,
    plan_id: str,
    pipeline_run_id: str,
    username: str,
    payload_size_bytes: Optional[int] = None,
    payload_fields: Optional[List[str]] = None,
    graph_marker_store: Optional[Any] = None,
) -> bool:
    """Record a data flow edge in the knowledge graph and Couchbase.

    This is fire-and-forget: never raises, returns False on any error.

    Args:
        from_task: Source task that produced the data.
        to_task: Destination task that consumed the data.
        plan_id: The plan these tasks belong to.
        pipeline_run_id: The pipeline run identifier.
        username: Owner of the pipeline.
        payload_size_bytes: Approximate payload size.
        payload_fields: Top-level field names in the payload.
        graph_marker_store: Optional GraphMarkerStore for Couchbase persistence.

    Returns:
        True if RDF ingestion succeeded, False otherwise.
    """
    if not _recorder_enabled():
        return False

    try:
        now = datetime.now(timezone.utc).isoformat()

        graph = build_dataflow_graph(
            from_task=from_task,
            to_task=to_task,
            plan_id=plan_id,
            pipeline_run_id=pipeline_run_id,
            flow_timestamp=now,
            payload_size_bytes=payload_size_bytes,
            payload_fields=payload_fields,
        )
        turtle = serialize_graph(graph)
        await ingest_turtle(turtle)

        # Persist to Couchbase for fast KV lookup
        if graph_marker_store is not None:
            try:
                key = f"dataflow::{username}::{pipeline_run_id}::{from_task}::{to_task}"
                doc = {
                    "from_task": from_task,
                    "to_task": to_task,
                    "plan_id": plan_id,
                    "pipeline_run_id": pipeline_run_id,
                    "username": username,
                    "payload_size_bytes": payload_size_bytes,
                    "payload_fields": payload_fields,
                    "flow_timestamp": now,
                    "_meta": {"type": "data_flow_record", "updated_at": now},
                }
                graph_marker_store.upsert_raw(key, doc)
            except Exception:
                logger.warning("Failed to persist data flow record to Couchbase", exc_info=True)

        return True
    except Exception:
        logger.warning(
            "Failed to record data flow %s -> %s", from_task, to_task, exc_info=True
        )
        return False
