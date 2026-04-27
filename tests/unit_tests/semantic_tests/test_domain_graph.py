"""Tests for the domain knowledge RDF graph builder."""
from __future__ import annotations

from rdflib import RDF, RDFS, Literal
from rdflib.namespace import XSD

from src.agentcy.semantic.domain_graph import (
    build_domain_graph,
    _entity_uri,
    _relationship_uri,
    _process_uri,
)
from src.agentcy.semantic.namespaces import ONTOLOGY, RESOURCE


def test_entity_triples():
    """Entity produces correct core triples."""
    entities = [{"name": "OrderDB", "type": "data_source", "description": "Main order database"}]
    graph = build_domain_graph(entities, [], [])
    uri = _entity_uri("OrderDB")

    assert (uri, RDF.type, ONTOLOGY.DomainEntity) in graph
    assert (uri, ONTOLOGY.entityName, Literal("OrderDB", datatype=XSD.string)) in graph
    assert (uri, ONTOLOGY.entityType, Literal("data_source", datatype=XSD.string)) in graph
    assert (uri, ONTOLOGY.entityDescription, Literal("Main order database", datatype=XSD.string)) in graph
    assert (uri, RDFS.label, Literal("OrderDB")) in graph


def test_relationship_links_two_entities():
    """Relationship links from and to entities."""
    rels = [{"from": "OrderDB", "to": "WarehouseAPI", "type": "feeds_data_to"}]
    graph = build_domain_graph([], rels, [])
    rel_uri = _relationship_uri("OrderDB", "WarehouseAPI", "feeds_data_to")

    assert (rel_uri, RDF.type, ONTOLOGY.DomainRelationship) in graph
    assert (rel_uri, ONTOLOGY.fromEntity, _entity_uri("OrderDB")) in graph
    assert (rel_uri, ONTOLOGY.toEntity, _entity_uri("WarehouseAPI")) in graph
    assert (rel_uri, ONTOLOGY.relationshipType, Literal("feeds_data_to", datatype=XSD.string)) in graph


def test_process_with_involved_entities():
    """Business process links to involved entities."""
    procs = [{"name": "Order Fulfillment", "description": "End-to-end order process", "involves": ["OrderDB", "WarehouseAPI"]}]
    graph = build_domain_graph([], [], procs)
    proc_uri = _process_uri("Order Fulfillment")

    assert (proc_uri, RDF.type, ONTOLOGY.BusinessProcess) in graph
    assert (proc_uri, ONTOLOGY.processName, Literal("Order Fulfillment", datatype=XSD.string)) in graph
    assert (proc_uri, ONTOLOGY.processDescription, Literal("End-to-end order process", datatype=XSD.string)) in graph
    assert (proc_uri, RDFS.label, Literal("Order Fulfillment")) in graph
    assert (proc_uri, ONTOLOGY.involvesEntity, _entity_uri("OrderDB")) in graph
    assert (proc_uri, ONTOLOGY.involvesEntity, _entity_uri("WarehouseAPI")) in graph


def test_uri_stability():
    """Same entity name produces the same URI."""
    uri1 = _entity_uri("OrderDB")
    uri2 = _entity_uri("OrderDB")
    assert uri1 == uri2

    uri3 = _entity_uri("Different")
    assert uri1 != uri3


def test_plan_provenance_link():
    """Entities are linked to source plan via extractedFrom."""
    entities = [{"name": "UserService", "type": "service"}]
    graph = build_domain_graph(entities, [], [], plan_id="plan-abc")
    uri = _entity_uri("UserService")

    plan_uri = graph.value(uri, ONTOLOGY.extractedFrom)
    assert plan_uri is not None
    assert "plan-abc" in str(plan_uri)


def test_username_provenance():
    """Username is attached to entities when provided."""
    entities = [{"name": "MetricDB", "type": "data_source"}]
    graph = build_domain_graph(entities, [], [], username="alice")
    uri = _entity_uri("MetricDB")

    assert (uri, ONTOLOGY.username, Literal("alice", datatype=XSD.string)) in graph


def test_triple_count_full():
    """Full domain graph with 2 entities + 1 relationship + 1 process has expected count."""
    entities = [
        {"name": "A", "type": "service", "description": "Svc A"},
        {"name": "B", "type": "data_source", "description": "DB B"},
    ]
    rels = [{"from": "A", "to": "B", "type": "uses"}]
    procs = [{"name": "ETL", "description": "Extract-Transform-Load", "involves": ["A", "B"]}]
    graph = build_domain_graph(entities, rels, procs, plan_id="plan-x")

    # Entities: 2 × (type + name + entityType + description + label + extractedFrom) = 12
    # Rels: 1 × (type + fromEntity + toEntity + relationshipType + extractedFrom) = 5
    # Procs: 1 × (type + processName + label + processDescription + 2×involvesEntity + extractedFrom) = 7
    # Total = 24
    assert len(graph) == 24


def test_empty_inputs():
    """Empty entities/relationships/processes produces empty graph."""
    graph = build_domain_graph([], [], [])
    assert len(graph) == 0


def test_skips_empty_names():
    """Entities/rels/processes with empty names are skipped."""
    entities = [{"name": "", "type": "service"}, {"name": "Valid", "type": "service"}]
    rels = [{"from": "", "to": "Valid", "type": "uses"}]
    procs = [{"name": "", "description": "empty"}]
    graph = build_domain_graph(entities, rels, procs)

    # Only the "Valid" entity should produce triples
    entity_count = len(list(graph.subjects(RDF.type, ONTOLOGY.DomainEntity)))
    assert entity_count == 1

    rel_count = len(list(graph.subjects(RDF.type, ONTOLOGY.DomainRelationship)))
    assert rel_count == 0

    proc_count = len(list(graph.subjects(RDF.type, ONTOLOGY.BusinessProcess)))
    assert proc_count == 0
