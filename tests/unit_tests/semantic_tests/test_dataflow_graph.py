"""Tests for the data flow RDF graph builder."""
from __future__ import annotations

from rdflib import RDF, Literal
from rdflib.namespace import XSD

from src.agentcy.semantic.dataflow_graph import (
    build_dataflow_graph,
    _dataflow_uri,
    _run_uri,
)
from src.agentcy.semantic.namespaces import ONTOLOGY, RESOURCE


def test_build_dataflow_graph_basic():
    """A data flow edge produces the expected triples."""
    graph = build_dataflow_graph(
        from_task="extract",
        to_task="transform",
        plan_id="plan-123",
        pipeline_run_id="run-001",
        flow_timestamp="2026-03-02T10:00:00+00:00",
    )

    flow_uri = _dataflow_uri("run-001", "extract", "transform")
    assert (flow_uri, RDF.type, ONTOLOGY.DataFlow) in graph

    # from/to task links
    from_uri = next(graph.objects(flow_uri, ONTOLOGY.fromTask))
    to_uri = next(graph.objects(flow_uri, ONTOLOGY.toTask))
    assert "extract" in str(from_uri)
    assert "transform" in str(to_uri)

    # Run link
    run_uri = next(graph.objects(flow_uri, ONTOLOGY.inRun))
    assert "run-001" in str(run_uri)

    # Timestamp
    assert (
        flow_uri,
        ONTOLOGY.flowTimestamp,
        Literal("2026-03-02T10:00:00+00:00", datatype=XSD.dateTime),
    ) in graph


def test_dataflow_with_payload_metadata():
    """Payload size and fields are recorded when provided."""
    graph = build_dataflow_graph(
        from_task="fetch",
        to_task="parse",
        plan_id="plan-456",
        pipeline_run_id="run-002",
        flow_timestamp="2026-03-02T11:00:00+00:00",
        payload_size_bytes=4096,
        payload_fields=["orders", "customers", "products"],
    )

    flow_uri = _dataflow_uri("run-002", "fetch", "parse")
    assert (flow_uri, ONTOLOGY.payloadSizeBytes, Literal(4096, datatype=XSD.integer)) in graph
    assert (
        flow_uri,
        ONTOLOGY.payloadFields,
        Literal("orders,customers,products", datatype=XSD.string),
    ) in graph


def test_optional_fields_omitted():
    """Payload size and fields are omitted when not provided."""
    graph = build_dataflow_graph(
        from_task="a",
        to_task="b",
        plan_id="plan-789",
        pipeline_run_id="run-003",
    )

    flow_uri = _dataflow_uri("run-003", "a", "b")
    size_triples = list(graph.objects(flow_uri, ONTOLOGY.payloadSizeBytes))
    field_triples = list(graph.objects(flow_uri, ONTOLOGY.payloadFields))
    assert len(size_triples) == 0
    assert len(field_triples) == 0


def test_dataflow_uri_stability():
    """Same inputs produce the same URI."""
    uri1 = _dataflow_uri("run-001", "a", "b")
    uri2 = _dataflow_uri("run-001", "a", "b")
    assert uri1 == uri2

    # Different tasks produce different URIs
    uri3 = _dataflow_uri("run-001", "a", "c")
    assert uri1 != uri3


def test_default_timestamp():
    """When flow_timestamp is not provided, one is auto-generated."""
    graph = build_dataflow_graph(
        from_task="x",
        to_task="y",
        plan_id="plan-000",
        pipeline_run_id="run-004",
    )

    flow_uri = _dataflow_uri("run-004", "x", "y")
    timestamps = list(graph.objects(flow_uri, ONTOLOGY.flowTimestamp))
    assert len(timestamps) == 1


def test_triple_count_minimal():
    """Minimal data flow has the expected triple count."""
    graph = build_dataflow_graph(
        from_task="a",
        to_task="b",
        plan_id="plan-min",
        pipeline_run_id="run-005",
        flow_timestamp="2026-03-02T12:00:00+00:00",
    )
    # Expected: type + fromTask + toTask + inRun + flowTimestamp = 5
    assert len(graph) == 5


def test_triple_count_with_payload():
    """Data flow with payload metadata has extra triples."""
    graph = build_dataflow_graph(
        from_task="a",
        to_task="b",
        plan_id="plan-full",
        pipeline_run_id="run-006",
        flow_timestamp="2026-03-02T12:00:00+00:00",
        payload_size_bytes=1024,
        payload_fields=["field_a", "field_b"],
    )
    # Expected: 5 (minimal) + 2 (size + fields) = 7
    assert len(graph) == 7
