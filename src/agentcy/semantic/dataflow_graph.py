"""
RDF graph builder for data flow edges between tasks.

Produces triples capturing data lineage: which task produced data
consumed by another task during a pipeline run.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import XSD

from agentcy.semantic.namespaces import ONTOLOGY, RESOURCE
from agentcy.semantic.plan_graph import _slug, _task_uri


def _dataflow_uri(pipeline_run_id: str, from_task: str, to_task: str) -> URIRef:
    return URIRef(
        f"{RESOURCE}dataflow/{_slug(pipeline_run_id)}/{_slug(from_task)}/{_slug(to_task)}"
    )


def _run_uri(pipeline_run_id: str) -> URIRef:
    return URIRef(f"{RESOURCE}run/{_slug(pipeline_run_id)}")


def build_dataflow_graph(
    *,
    from_task: str,
    to_task: str,
    plan_id: str,
    pipeline_run_id: str,
    flow_timestamp: Optional[str] = None,
    payload_size_bytes: Optional[int] = None,
    payload_fields: Optional[List[str]] = None,
) -> Graph:
    """Build an RDF graph for a data flow edge between two tasks.

    Args:
        from_task: The task that produced the data.
        to_task: The task that consumed the data.
        plan_id: The plan these tasks belong to.
        pipeline_run_id: The pipeline run this flow occurred in.
        flow_timestamp: ISO-8601 timestamp; defaults to now.
        payload_size_bytes: Approximate size of the payload in bytes.
        payload_fields: Top-level field names in the payload.

    Returns:
        An rdflib Graph with the data flow triples.
    """
    graph = Graph()
    graph.bind("ac", ONTOLOGY)
    graph.bind("res", RESOURCE)

    flow_uri = _dataflow_uri(pipeline_run_id, from_task, to_task)
    graph.add((flow_uri, RDF.type, ONTOLOGY.DataFlow))

    # Link from/to tasks
    graph.add((flow_uri, ONTOLOGY.fromTask, _task_uri(plan_id, from_task)))
    graph.add((flow_uri, ONTOLOGY.toTask, _task_uri(plan_id, to_task)))

    # Link to pipeline run
    graph.add((flow_uri, ONTOLOGY.inRun, _run_uri(pipeline_run_id)))

    # Timestamp
    ts = flow_timestamp or datetime.now(timezone.utc).isoformat()
    graph.add((flow_uri, ONTOLOGY.flowTimestamp, Literal(ts, datatype=XSD.dateTime)))

    # Payload size (optional)
    if payload_size_bytes is not None:
        graph.add((flow_uri, ONTOLOGY.payloadSizeBytes, Literal(payload_size_bytes, datatype=XSD.integer)))

    # Payload fields (optional, comma-separated)
    if payload_fields:
        fields_str = ",".join(payload_fields)
        graph.add((flow_uri, ONTOLOGY.payloadFields, Literal(fields_str, datatype=XSD.string)))

    return graph
