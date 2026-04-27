from agentcy.semantic.namespaces import BASE_URI, ONTOLOGY, RESOURCE, PROV, SH
from agentcy.semantic.plan_graph import build_plan_graph, serialize_graph
from agentcy.semantic.shacl_engine import validate_graph_spec
from agentcy.semantic.prov_graph import build_audit_prov_graph
from agentcy.semantic.fuseki_client import ingest_turtle, sparql_query, sparql_ask
from agentcy.semantic.fuseki_init import (
    initialize_fuseki,
    register_shapes,
    register_ontology,
    SHAPES_GRAPH_URI,
    ONTOLOGY_GRAPH_URI,
)
from agentcy.semantic.ontology_manager import OntologyManager
from agentcy.semantic.execution_graph import build_execution_graph
from agentcy.semantic.dataflow_graph import build_dataflow_graph
from agentcy.semantic.execution_recorder import record_execution, record_data_flow
from agentcy.semantic.template_graph import build_template_graph
from agentcy.semantic.capability_taxonomy import expand_capabilities, load_hierarchy
from agentcy.semantic.plan_recommender import get_plan_context
from agentcy.semantic.domain_extractor import extract_domain_knowledge
from agentcy.semantic.domain_graph import build_domain_graph

__all__ = [
    # Namespaces
    "BASE_URI",
    "ONTOLOGY",
    "RESOURCE",
    "PROV",
    "SH",
    # Graph building
    "build_plan_graph",
    "serialize_graph",
    "build_audit_prov_graph",
    "build_execution_graph",
    "build_dataflow_graph",
    "build_template_graph",
    # Validation
    "validate_graph_spec",
    # Fuseki client
    "ingest_turtle",
    "sparql_query",
    "sparql_ask",
    # Fuseki initialization
    "initialize_fuseki",
    "register_shapes",
    "register_ontology",
    "SHAPES_GRAPH_URI",
    "ONTOLOGY_GRAPH_URI",
    # Ontology management
    "OntologyManager",
    # Execution recording
    "record_execution",
    "record_data_flow",
    # Capability taxonomy
    "expand_capabilities",
    "load_hierarchy",
    # Cross-plan learning
    "get_plan_context",
    # Domain knowledge
    "extract_domain_knowledge",
    "build_domain_graph",
]
