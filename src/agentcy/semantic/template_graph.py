"""
RDF graph builder for AgentTemplate instances.

Makes templates first-class KG citizens, linked to capabilities, tags,
and metadata so that SPARQL queries can join templates to execution
history for performance-based template selection.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from agentcy.semantic.namespaces import ONTOLOGY, RESOURCE
from agentcy.semantic.plan_graph import _slug, _capability_uri, _tag_uri


def _template_uri(template_id: str) -> URIRef:
    return URIRef(f"{RESOURCE}template/{_slug(template_id)}")


def build_template_graph(
    template: Dict[str, Any],
    *,
    username: Optional[str] = None,
) -> Graph:
    """Build an RDF graph for an AgentTemplate.

    Args:
        template: Template dict (or ``AgentTemplate.model_dump()``).
        username: Optional owner for provenance.

    Returns:
        An rdflib Graph with the template triples.
    """
    graph = Graph()
    graph.bind("ac", ONTOLOGY)
    graph.bind("res", RESOURCE)

    template_id = template.get("template_id", "")
    tmpl_uri = _template_uri(template_id)

    graph.add((tmpl_uri, RDF.type, ONTOLOGY.Template))
    graph.add((tmpl_uri, ONTOLOGY.templateId, Literal(template_id, datatype=XSD.string)))

    name = template.get("name", "")
    if name:
        graph.add((tmpl_uri, ONTOLOGY.templateName, Literal(name, datatype=XSD.string)))

    display_name = template.get("display_name", "")
    if display_name:
        graph.add((tmpl_uri, RDFS.label, Literal(display_name)))

    category = template.get("category", "")
    if category:
        graph.add((tmpl_uri, ONTOLOGY.templateCategory, Literal(str(category), datatype=XSD.string)))

    version = template.get("version", "")
    if version:
        graph.add((tmpl_uri, ONTOLOGY.templateVersion, Literal(version, datatype=XSD.string)))

    enabled = template.get("enabled")
    if enabled is not None:
        graph.add((tmpl_uri, ONTOLOGY.templateEnabled, Literal(bool(enabled), datatype=XSD.boolean)))

    # Link to capabilities
    for cap in template.get("capabilities") or []:
        if not cap:
            continue
        cap_uri = _capability_uri(str(cap))
        graph.add((cap_uri, RDF.type, ONTOLOGY.Capability))
        graph.add((cap_uri, RDFS.label, Literal(str(cap))))
        graph.add((tmpl_uri, ONTOLOGY.requiresCapability, cap_uri))

    # Link to tags
    for tag in template.get("tags") or []:
        if not tag:
            continue
        tag_uri = _tag_uri(str(tag))
        graph.add((tag_uri, RDF.type, ONTOLOGY.Tag))
        graph.add((tag_uri, RDFS.label, Literal(str(tag))))
        graph.add((tmpl_uri, ONTOLOGY.hasTag, tag_uri))

    if username:
        graph.add((tmpl_uri, ONTOLOGY.username, Literal(username, datatype=XSD.string)))

    return graph
