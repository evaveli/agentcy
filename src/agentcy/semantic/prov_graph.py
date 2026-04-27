from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from rdflib import Graph, Literal, RDF, URIRef
from rdflib.namespace import XSD

from agentcy.semantic.namespaces import PROV, RESOURCE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_audit_prov_graph(
    *,
    plan_id: str,
    pipeline_run_id: Optional[str],
    username: Optional[str],
    payload: Dict[str, Any],
) -> Graph:
    graph = Graph()
    graph.bind("prov", PROV)
    graph.bind("res", RESOURCE)

    plan_uri = URIRef(f"{RESOURCE}plan/{plan_id}")
    graph.add((plan_uri, RDF.type, PROV.Entity))

    audit_uri = URIRef(
        f"{RESOURCE}audit/{pipeline_run_id or 'run'}/{plan_id}/{_now_iso()}"
    )
    graph.add((audit_uri, RDF.type, PROV.Activity))
    graph.add((audit_uri, PROV.startedAtTime, Literal(_now_iso(), datatype=XSD.dateTime)))
    graph.add((audit_uri, PROV.used, plan_uri))

    if username:
        actor_uri = URIRef(f"{RESOURCE}actor/{username}")
        graph.add((actor_uri, RDF.type, PROV.Agent))
        graph.add((audit_uri, PROV.wasAssociatedWith, actor_uri))

    def _link_event(kind: str, event: Optional[Dict[str, Any]]) -> None:
        if not event:
            return
        event_uri = URIRef(
            f"{RESOURCE}event/{kind}/{plan_id}/{event.get('created_at') or event.get('checked_at') or _now_iso()}"
        )
        graph.add((event_uri, RDF.type, PROV.Entity))
        graph.add((event_uri, PROV.wasGeneratedBy, audit_uri))
        graph.add((audit_uri, PROV.generated, event_uri))

    _link_event("human_approval", payload.get("human_approval"))
    _link_event("ethics_check", payload.get("ethics_check"))
    _link_event("execution_report", payload.get("execution_report"))
    _link_event("escalation_notice", payload.get("escalation_notice"))

    score = payload.get("traceability_score")
    if score is not None:
        score_uri = URIRef(f"{RESOURCE}metric/traceability/{plan_id}/{_now_iso()}")
        graph.add((score_uri, RDF.type, PROV.Entity))
        graph.add((score_uri, PROV.value, Literal(score)))
        graph.add((audit_uri, PROV.generated, score_uri))

    return graph
