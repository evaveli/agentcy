"""Tests for the template RDF graph builder."""
from __future__ import annotations

from rdflib import RDF, RDFS, Literal
from rdflib.namespace import XSD

from src.agentcy.semantic.template_graph import (
    build_template_graph,
    _template_uri,
)
from src.agentcy.semantic.namespaces import ONTOLOGY, RESOURCE


def _sample_template(**overrides):
    base = {
        "template_id": "tmpl-001",
        "name": "inventory_checker",
        "display_name": "Inventory Checker",
        "category": "data_processing",
        "capabilities": ["data_read", "validate"],
        "tags": ["inventory", "warehouse"],
        "version": "1.0.0",
        "enabled": True,
    }
    base.update(overrides)
    return base


def test_basic_triples():
    """A template produces the expected core triples."""
    graph = build_template_graph(_sample_template())
    uri = _template_uri("tmpl-001")

    assert (uri, RDF.type, ONTOLOGY.Template) in graph
    assert (uri, ONTOLOGY.templateId, Literal("tmpl-001", datatype=XSD.string)) in graph
    assert (uri, ONTOLOGY.templateName, Literal("inventory_checker", datatype=XSD.string)) in graph
    assert (uri, RDFS.label, Literal("Inventory Checker")) in graph
    assert (uri, ONTOLOGY.templateCategory, Literal("data_processing", datatype=XSD.string)) in graph
    assert (uri, ONTOLOGY.templateVersion, Literal("1.0.0", datatype=XSD.string)) in graph
    assert (uri, ONTOLOGY.templateEnabled, Literal(True, datatype=XSD.boolean)) in graph


def test_capabilities_linked():
    """Capabilities are linked via requiresCapability."""
    graph = build_template_graph(_sample_template())
    uri = _template_uri("tmpl-001")

    cap_uris = list(graph.objects(uri, ONTOLOGY.requiresCapability))
    assert len(cap_uris) == 2
    cap_strs = {str(c) for c in cap_uris}
    assert any("data_read" in c for c in cap_strs)
    assert any("validate" in c for c in cap_strs)


def test_tags_linked():
    """Tags are linked via hasTag."""
    graph = build_template_graph(_sample_template())
    uri = _template_uri("tmpl-001")

    tag_uris = list(graph.objects(uri, ONTOLOGY.hasTag))
    assert len(tag_uris) == 2
    tag_strs = {str(t) for t in tag_uris}
    assert any("inventory" in t for t in tag_strs)
    assert any("warehouse" in t for t in tag_strs)


def test_disabled_template():
    """Disabled template has enabled=false."""
    graph = build_template_graph(_sample_template(enabled=False))
    uri = _template_uri("tmpl-001")
    assert (uri, ONTOLOGY.templateEnabled, Literal(False, datatype=XSD.boolean)) in graph


def test_no_capabilities_or_tags():
    """Template with empty capabilities/tags has no link triples."""
    graph = build_template_graph(_sample_template(capabilities=[], tags=[]))
    uri = _template_uri("tmpl-001")

    cap_uris = list(graph.objects(uri, ONTOLOGY.requiresCapability))
    tag_uris = list(graph.objects(uri, ONTOLOGY.hasTag))
    assert len(cap_uris) == 0
    assert len(tag_uris) == 0


def test_username_added():
    """Username triple is added when provided."""
    graph = build_template_graph(_sample_template(), username="alice")
    uri = _template_uri("tmpl-001")
    assert (uri, ONTOLOGY.username, Literal("alice", datatype=XSD.string)) in graph


def test_uri_stability():
    """Same template_id produces the same URI."""
    uri1 = _template_uri("tmpl-001")
    uri2 = _template_uri("tmpl-001")
    assert uri1 == uri2

    uri3 = _template_uri("tmpl-002")
    assert uri1 != uri3


def test_triple_count_full():
    """Full template with 2 caps + 2 tags has expected triple count."""
    graph = build_template_graph(_sample_template())
    # Core: type + templateId + templateName + label + category + version + enabled = 7
    # Caps: 2 × (type + label + requiresCapability) = 6
    # Tags: 2 × (type + label + hasTag) = 6
    # Total = 19
    assert len(graph) == 19


def test_triple_count_minimal():
    """Minimal template (no caps/tags/optional fields) has base triples only."""
    graph = build_template_graph({
        "template_id": "tmpl-min",
        "name": "minimal",
        "display_name": "Minimal",
        "category": "custom",
        "version": "1.0.0",
        "enabled": True,
    })
    # type + templateId + templateName + label + category + version + enabled = 7
    assert len(graph) == 7
