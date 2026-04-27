"""Tests for the execution record RDF graph builder."""
from __future__ import annotations

from rdflib import RDF, Literal, URIRef
from rdflib.namespace import XSD

from src.agentcy.semantic.execution_graph import (
    build_execution_graph,
    _execution_uri,
    _run_uri,
)
from src.agentcy.semantic.namespaces import ONTOLOGY, RESOURCE


def test_build_execution_graph_completed():
    """A successful execution produces the expected triples."""
    graph = build_execution_graph(
        task_id="t1",
        agent_id="agent-a",
        plan_id="plan-123",
        pipeline_run_id="run-001",
        status="completed",
        attempt_number=1,
        duration_seconds=12.5,
        executed_at="2026-03-02T10:00:00+00:00",
    )

    exec_uri = _execution_uri("run-001", "t1", 1)
    assert (exec_uri, RDF.type, ONTOLOGY.Execution) in graph
    assert (exec_uri, ONTOLOGY.executionStatus, Literal("completed", datatype=XSD.string)) in graph
    assert (exec_uri, ONTOLOGY.attemptNumber, Literal(1, datatype=XSD.integer)) in graph
    assert (exec_uri, ONTOLOGY.durationSeconds, Literal(12.5, datatype=XSD.decimal)) in graph
    assert (exec_uri, ONTOLOGY.executedAt, Literal("2026-03-02T10:00:00+00:00", datatype=XSD.dateTime)) in graph

    # Links to task and agent
    task_uri = next(graph.objects(exec_uri, ONTOLOGY.executionOf))
    assert (task_uri, RDF.type, ONTOLOGY.Task) not in graph  # task type not added by execution builder
    assert "plan-123" in str(task_uri)
    assert "t1" in str(task_uri)

    agent_uri = next(graph.objects(exec_uri, ONTOLOGY.executedBy))
    assert "agent-a" in str(agent_uri)

    # Link to run
    run_uri = next(graph.objects(exec_uri, ONTOLOGY.inRun))
    assert "run-001" in str(run_uri)


def test_build_execution_graph_failed_with_error():
    """A failed execution includes the error message."""
    graph = build_execution_graph(
        task_id="t2",
        agent_id="agent-b",
        plan_id="plan-456",
        pipeline_run_id="run-002",
        status="failed",
        attempt_number=2,
        error="Connection timeout",
        executed_at="2026-03-02T11:00:00+00:00",
    )

    exec_uri = _execution_uri("run-002", "t2", 2)
    assert (exec_uri, ONTOLOGY.executionStatus, Literal("failed", datatype=XSD.string)) in graph
    assert (exec_uri, ONTOLOGY.errorMessage, Literal("Connection timeout", datatype=XSD.string)) in graph
    assert (exec_uri, ONTOLOGY.attemptNumber, Literal(2, datatype=XSD.integer)) in graph


def test_optional_fields_omitted():
    """Duration and error are omitted when not provided."""
    graph = build_execution_graph(
        task_id="t3",
        agent_id="agent-c",
        plan_id="plan-789",
        pipeline_run_id="run-003",
        status="completed",
    )

    exec_uri = _execution_uri("run-003", "t3", 1)
    # Duration and error should not be in the graph
    duration_triples = list(graph.objects(exec_uri, ONTOLOGY.durationSeconds))
    error_triples = list(graph.objects(exec_uri, ONTOLOGY.errorMessage))
    assert len(duration_triples) == 0
    assert len(error_triples) == 0

    # But required fields should still be present
    assert (exec_uri, RDF.type, ONTOLOGY.Execution) in graph
    assert (exec_uri, ONTOLOGY.executionStatus, Literal("completed", datatype=XSD.string)) in graph


def test_execution_uri_stability():
    """Same inputs produce the same URI."""
    uri1 = _execution_uri("run-001", "t1", 1)
    uri2 = _execution_uri("run-001", "t1", 1)
    assert uri1 == uri2

    # Different attempt produces different URI
    uri3 = _execution_uri("run-001", "t1", 2)
    assert uri1 != uri3


def test_run_uri_format():
    """Run URIs follow the expected pattern."""
    uri = _run_uri("run-abc-123")
    assert str(uri) == f"{RESOURCE}run/run-abc-123"


def test_default_timestamp():
    """When executed_at is not provided, a timestamp is still generated."""
    graph = build_execution_graph(
        task_id="t4",
        agent_id="agent-d",
        plan_id="plan-000",
        pipeline_run_id="run-004",
        status="completed",
    )

    exec_uri = _execution_uri("run-004", "t4", 1)
    timestamps = list(graph.objects(exec_uri, ONTOLOGY.executedAt))
    assert len(timestamps) == 1


def test_graph_triple_count_completed():
    """A completed execution with all fields has the expected triple count."""
    graph = build_execution_graph(
        task_id="t5",
        agent_id="agent-e",
        plan_id="plan-111",
        pipeline_run_id="run-005",
        status="completed",
        attempt_number=1,
        duration_seconds=5.0,
        executed_at="2026-03-02T12:00:00+00:00",
    )
    # Expected: type + executionOf + executedBy + status + attempt + executedAt + duration + inRun = 8
    assert len(graph) == 8


def test_graph_triple_count_failed_with_error():
    """A failed execution with error has one extra triple for errorMessage."""
    graph = build_execution_graph(
        task_id="t6",
        agent_id="agent-f",
        plan_id="plan-222",
        pipeline_run_id="run-006",
        status="failed",
        attempt_number=1,
        duration_seconds=1.0,
        error="Boom",
        executed_at="2026-03-02T12:00:00+00:00",
    )
    # Expected: 8 (from completed) + 1 (errorMessage) = 9
    assert len(graph) == 9
