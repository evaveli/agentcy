"""
RDF graph builder for domain knowledge entities, relationships, and
business processes extracted from NL descriptions.

Makes domain knowledge first-class KG citizens so SPARQL can join them
to plans, capabilities, and execution history for richer context.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from agentcy.semantic.namespaces import ONTOLOGY, RESOURCE
from agentcy.semantic.plan_graph import _slug


# ── URI helpers ──────────────────────────────────────────────────────


def _entity_uri(name: str) -> URIRef:
    return URIRef(f"{RESOURCE}domain/entity/{_slug(name)}")


def _relationship_uri(from_name: str, to_name: str, rel_type: str) -> URIRef:
    return URIRef(
        f"{RESOURCE}domain/rel/{_slug(from_name)}/{_slug(to_name)}/{_slug(rel_type)}"
    )


def _process_uri(name: str) -> URIRef:
    return URIRef(f"{RESOURCE}domain/process/{_slug(name)}")


def _plan_uri(plan_id: str) -> URIRef:
    return URIRef(f"{RESOURCE}plan/{plan_id}")


# ── Public API ───────────────────────────────────────────────────────


def build_domain_graph(
    entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
    processes: List[Dict[str, Any]],
    *,
    plan_id: Optional[str] = None,
    username: Optional[str] = None,
) -> Graph:
    """Build an RDF graph for domain knowledge extracted from NL.

    Args:
        entities: List of entity dicts with ``name``, ``type``, ``description``.
        relationships: List of relationship dicts with ``from``, ``to``, ``type``.
        processes: List of process dicts with ``name``, ``description``, ``involves``.
        plan_id: Optional source plan for provenance.
        username: Optional owner for provenance.

    Returns:
        An rdflib Graph with domain knowledge triples.
    """
    graph = Graph()
    graph.bind("ac", ONTOLOGY)
    graph.bind("res", RESOURCE)

    # ── Entities ─────────────────────────────────────────────────────
    for ent in entities:
        name = ent.get("name", "").strip()
        if not name:
            continue
        uri = _entity_uri(name)
        graph.add((uri, RDF.type, ONTOLOGY.DomainEntity))
        graph.add((uri, ONTOLOGY.entityName, Literal(name, datatype=XSD.string)))

        etype = ent.get("type", "").strip()
        if etype:
            graph.add((uri, ONTOLOGY.entityType, Literal(etype, datatype=XSD.string)))

        desc = ent.get("description", "").strip()
        if desc:
            graph.add((uri, ONTOLOGY.entityDescription, Literal(desc, datatype=XSD.string)))

        graph.add((uri, RDFS.label, Literal(name)))

        if plan_id:
            graph.add((uri, ONTOLOGY.extractedFrom, _plan_uri(plan_id)))

    # ── Relationships ────────────────────────────────────────────────
    for rel in relationships:
        from_name = rel.get("from", "").strip()
        to_name = rel.get("to", "").strip()
        rel_type = rel.get("type", "").strip()
        if not from_name or not to_name:
            continue
        rel_uri = _relationship_uri(from_name, to_name, rel_type or "related")
        graph.add((rel_uri, RDF.type, ONTOLOGY.DomainRelationship))

        from_uri = _entity_uri(from_name)
        to_uri = _entity_uri(to_name)
        graph.add((rel_uri, ONTOLOGY.fromEntity, from_uri))
        graph.add((rel_uri, ONTOLOGY.toEntity, to_uri))

        if rel_type:
            graph.add((rel_uri, ONTOLOGY.relationshipType, Literal(rel_type, datatype=XSD.string)))

        if plan_id:
            graph.add((rel_uri, ONTOLOGY.extractedFrom, _plan_uri(plan_id)))

    # ── Business Processes ───────────────────────────────────────────
    for proc in processes:
        proc_name = proc.get("name", "").strip()
        if not proc_name:
            continue
        proc_uri = _process_uri(proc_name)
        graph.add((proc_uri, RDF.type, ONTOLOGY.BusinessProcess))
        graph.add((proc_uri, ONTOLOGY.processName, Literal(proc_name, datatype=XSD.string)))
        graph.add((proc_uri, RDFS.label, Literal(proc_name)))

        proc_desc = proc.get("description", "").strip()
        if proc_desc:
            graph.add((proc_uri, ONTOLOGY.processDescription, Literal(proc_desc, datatype=XSD.string)))

        for involved in proc.get("involves") or []:
            involved_name = involved.strip() if isinstance(involved, str) else ""
            if involved_name:
                graph.add((proc_uri, ONTOLOGY.involvesEntity, _entity_uri(involved_name)))

        if plan_id:
            graph.add((proc_uri, ONTOLOGY.extractedFrom, _plan_uri(plan_id)))

    # ── Provenance ───────────────────────────────────────────────────
    if username:
        for ent in entities:
            name = ent.get("name", "").strip()
            if name:
                graph.add((_entity_uri(name), ONTOLOGY.username, Literal(username, datatype=XSD.string)))

    return graph
