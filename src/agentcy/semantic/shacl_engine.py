from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from rdflib import Graph, RDF

from agentcy.semantic.namespaces import SH
from agentcy.semantic.plan_graph import build_plan_graph, serialize_graph

try:
    from pyshacl import validate as shacl_validate
except Exception:  # pragma: no cover - guard for environments missing pyshacl
    shacl_validate = None


DEFAULT_SHAPES_PATH = "schemas/plan_draft_shapes.ttl"


def _load_shapes_graph(path: Optional[str]) -> Optional[Graph]:
    if not path:
        return None
    if not os.path.exists(path):
        return None
    graph = Graph()
    try:
        graph.parse(path)
    except Exception:
        return None
    return graph


def _extract_results(results_graph: Graph) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for result in results_graph.subjects(RDF.type, SH.ValidationResult):
        entry: Dict[str, Any] = {}
        focus_nodes = list(results_graph.objects(result, SH.focusNode))
        if focus_nodes:
            entry["focus_node"] = str(focus_nodes[0])
        paths = list(results_graph.objects(result, SH.resultPath))
        if paths:
            entry["path"] = str(paths[0])
        messages = list(results_graph.objects(result, SH.resultMessage))
        if messages:
            entry["message"] = str(messages[0])
        severities = list(results_graph.objects(result, SH.resultSeverity))
        if severities:
            entry["severity"] = str(severities[0])
        sources = list(results_graph.objects(result, SH.sourceShape))
        if sources:
            entry["source_shape"] = str(sources[0])
        values = list(results_graph.objects(result, SH.value))
        if values:
            entry["value"] = str(values[0])
        results.append(entry)
    return results


def validate_graph_spec(
    graph_spec: Dict[str, Any],
    *,
    plan_id: str,
    pipeline_id: Optional[str],
    username: Optional[str],
    shapes_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    shacl_enabled = os.getenv("SHACL_ENABLE")
    if shacl_enabled is not None and shacl_enabled.strip().lower() in {"", "0", "false", "no", "off"}:
        return None
    if shacl_validate is None:
        return None

    shapes_path = shapes_path or os.getenv("SHACL_SHAPES_PATH", DEFAULT_SHAPES_PATH)
    shapes_graph = _load_shapes_graph(shapes_path)
    if shapes_graph is None:
        return None

    data_graph = build_plan_graph(
        graph_spec,
        plan_id=plan_id,
        pipeline_id=pipeline_id,
        username=username,
        include_prov=False,
    )

    conforms, report_graph, report_text = shacl_validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="rdfs",
        serialize_report_graph=False,
    )

    return {
        "engine": "pyshacl",
        "conforms": bool(conforms),
        "results": _extract_results(report_graph),
        "report_text": str(report_text or ""),
        "report_ttl": serialize_graph(report_graph, fmt="turtle"),
        "shapes_path": shapes_path,
    }
