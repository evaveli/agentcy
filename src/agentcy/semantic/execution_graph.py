"""
RDF graph builder for task execution records.

Produces triples capturing what actually happened during task execution:
agent, status, duration, errors, and links back to the planned task.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import XSD

from agentcy.semantic.namespaces import ONTOLOGY, RESOURCE
from agentcy.semantic.plan_graph import _slug, _task_uri, _agent_uri


def _execution_uri(pipeline_run_id: str, task_id: str, attempt: int) -> URIRef:
    return URIRef(
        f"{RESOURCE}execution/{_slug(pipeline_run_id)}/{_slug(task_id)}/{attempt}"
    )


def _run_uri(pipeline_run_id: str) -> URIRef:
    return URIRef(f"{RESOURCE}run/{_slug(pipeline_run_id)}")


def build_execution_graph(
    *,
    task_id: str,
    agent_id: str,
    plan_id: str,
    pipeline_run_id: str,
    status: str,
    attempt_number: int = 1,
    duration_seconds: Optional[float] = None,
    error: Optional[str] = None,
    executed_at: Optional[str] = None,
) -> Graph:
    """Build an RDF graph for a single task execution record.

    Args:
        task_id: The task that was executed.
        agent_id: The agent/service that executed the task.
        plan_id: The plan this task belongs to.
        pipeline_run_id: The pipeline run this execution is part of.
        status: ``"completed"`` or ``"failed"``.
        attempt_number: Which attempt this was (1-based).
        duration_seconds: How long the execution took.
        error: Error message if the task failed.
        executed_at: ISO-8601 timestamp; defaults to now.

    Returns:
        An rdflib Graph with the execution triples.
    """
    graph = Graph()
    graph.bind("ac", ONTOLOGY)
    graph.bind("res", RESOURCE)

    exec_uri = _execution_uri(pipeline_run_id, task_id, attempt_number)
    graph.add((exec_uri, RDF.type, ONTOLOGY.Execution))

    # Link to task
    graph.add((exec_uri, ONTOLOGY.executionOf, _task_uri(plan_id, task_id)))

    # Link to agent
    graph.add((exec_uri, ONTOLOGY.executedBy, _agent_uri(agent_id)))

    # Status
    graph.add((exec_uri, ONTOLOGY.executionStatus, Literal(status, datatype=XSD.string)))

    # Attempt number
    graph.add((exec_uri, ONTOLOGY.attemptNumber, Literal(attempt_number, datatype=XSD.integer)))

    # Timestamp
    ts = executed_at or datetime.now(timezone.utc).isoformat()
    graph.add((exec_uri, ONTOLOGY.executedAt, Literal(ts, datatype=XSD.dateTime)))

    # Duration (optional)
    if duration_seconds is not None:
        graph.add((exec_uri, ONTOLOGY.durationSeconds, Literal(duration_seconds, datatype=XSD.decimal)))

    # Error (optional)
    if error:
        graph.add((exec_uri, ONTOLOGY.errorMessage, Literal(error, datatype=XSD.string)))

    # Link to pipeline run
    graph.add((exec_uri, ONTOLOGY.inRun, _run_uri(pipeline_run_id)))

    return graph
