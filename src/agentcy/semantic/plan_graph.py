from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

from rdflib import Graph, Literal, RDF, RDFS, URIRef
from rdflib.namespace import XSD

from agentcy.semantic.namespaces import ONTOLOGY, PROV, RESOURCE
from agentcy.semantic.capability_taxonomy import load_hierarchy


_slug_pattern = re.compile(r"[^a-zA-Z0-9\-_.]+")


def _slug(value: str) -> str:
    if not value:
        return "unknown"
    cleaned = _slug_pattern.sub("-", value.strip())
    cleaned = cleaned.strip("-_.")
    return cleaned or "unknown"


def _task_uri(plan_id: str, task_id: str) -> URIRef:
    return URIRef(f"{RESOURCE}task/{plan_id}/{task_id}")


def _agent_uri(agent_id: str) -> URIRef:
    return URIRef(f"{RESOURCE}agent/{_slug(agent_id)}")


def _capability_uri(capability: str) -> URIRef:
    return URIRef(f"{RESOURCE}capability/{_slug(capability)}")


def _tag_uri(tag: str) -> URIRef:
    return URIRef(f"{RESOURCE}tag/{_slug(tag)}")


def _task_type_uri(task_type: str) -> URIRef:
    return URIRef(f"{RESOURCE}task-type/{_slug(task_type)}")


def build_plan_graph(
    graph_spec: Dict[str, Any],
    *,
    plan_id: str,
    pipeline_id: Optional[str],
    username: Optional[str],
    include_prov: bool = True,
) -> Graph:
    graph = Graph()
    graph.bind("ac", ONTOLOGY)
    graph.bind("res", RESOURCE)
    graph.bind("prov", PROV)

    plan_uri = URIRef(f"{RESOURCE}plan/{plan_id}")
    graph.add((plan_uri, RDF.type, ONTOLOGY.Plan))
    if include_prov:
        graph.add((plan_uri, RDF.type, PROV.Entity))

    graph.add((plan_uri, ONTOLOGY.planId, Literal(plan_id)))
    if pipeline_id:
        graph.add((plan_uri, ONTOLOGY.pipelineId, Literal(pipeline_id)))
    if username:
        graph.add((plan_uri, ONTOLOGY.username, Literal(username)))

    tasks = list(graph_spec.get("tasks") or [])
    edges = list(graph_spec.get("edges") or [])

    task_index: Dict[str, URIRef] = {}
    hierarchy = load_hierarchy()

    for task in tasks:
        task_id = task.get("task_id")
        if not task_id:
            continue
        task_uri = _task_uri(plan_id, str(task_id))
        task_index[str(task_id)] = task_uri
        graph.add((task_uri, RDF.type, ONTOLOGY.Task))
        if include_prov:
            graph.add((task_uri, RDF.type, PROV.Entity))
        graph.add((task_uri, ONTOLOGY.taskId, Literal(str(task_id), datatype=XSD.string)))
        graph.add((plan_uri, ONTOLOGY.hasTask, task_uri))

        assigned_agent = task.get("assigned_agent")
        if assigned_agent:
            agent_uri = _agent_uri(str(assigned_agent))
            graph.add((agent_uri, RDF.type, ONTOLOGY.Agent))
            if include_prov:
                graph.add((agent_uri, RDF.type, PROV.Agent))
            graph.add((agent_uri, ONTOLOGY.agentId, Literal(str(assigned_agent), datatype=XSD.string)))
            graph.add((task_uri, ONTOLOGY.assignedAgent, agent_uri))

        for cap in task.get("required_capabilities") or []:
            if not cap:
                continue
            cap_uri = _capability_uri(str(cap))
            graph.add((cap_uri, RDF.type, ONTOLOGY.Capability))
            graph.add((cap_uri, RDFS.label, Literal(str(cap))))
            graph.add((task_uri, ONTOLOGY.requiresCapability, cap_uri))
            # Emit hierarchy edge if capability has a parent
            parent = hierarchy.get(str(cap).lower().strip())
            if parent:
                parent_uri = _capability_uri(parent)
                graph.add((parent_uri, RDF.type, ONTOLOGY.Capability))
                graph.add((parent_uri, RDFS.label, Literal(parent)))
                graph.add((cap_uri, ONTOLOGY.subCapabilityOf, parent_uri))

        for tag in task.get("tags") or []:
            if not tag:
                continue
            tag_uri = _tag_uri(str(tag))
            graph.add((tag_uri, RDF.type, ONTOLOGY.Tag))
            graph.add((tag_uri, RDFS.label, Literal(str(tag))))
            graph.add((task_uri, ONTOLOGY.hasTag, tag_uri))

        task_type = task.get("task_type")
        if task_type:
            task_type_uri = _task_type_uri(str(task_type))
            graph.add((task_type_uri, RDF.type, ONTOLOGY.TaskType))
            graph.add((task_type_uri, RDFS.label, Literal(str(task_type))))
            graph.add((task_uri, ONTOLOGY.taskType, task_type_uri))

        # ── Domain-specific RDF types for SHACL shape targeting ────────
        # Emit additional rdf:type triples based on task metadata so that
        # the domain SHACL shapes (HazmatApprovalShape, CriticalDealRiskShape,
        # LeaseTermShape, ColdStorageSpecShape, ComplianceDependencyShape)
        # can fire during live plan validation.
        task_meta = task.get("metadata") or {}
        task_desc = str(task.get("description") or "").lower()
        task_special = str(task_meta.get("special_requirements") or "").lower()

        # HazmatTask: task involves hazmat storage
        if "hazmat" in task_desc or "hazmat" in task_special:
            graph.add((task_uri, RDF.type, ONTOLOGY.HazmatTask))
            # Track human approval status
            if task_meta.get("requires_human_approval"):
                approval_uri = URIRef(f"{RESOURCE}approval/{plan_id}/{task_id}")
                graph.add((approval_uri, RDF.type, ONTOLOGY.HumanApproval))
                graph.add((task_uri, ONTOLOGY.approvedBy, approval_uri))

        # ColdStorageTask: task involves cold storage
        if "cold" in task_desc or "cold storage" in task_special:
            graph.add((task_uri, RDF.type, ONTOLOGY.ColdStorageTask))
            temp_range = task_meta.get("temperature_range")
            if temp_range:
                graph.add((task_uri, ONTOLOGY.requiredTemperatureRange,
                           Literal(str(temp_range), datatype=XSD.string)))

        # CriticalDealTask: task has critical priority
        risk_level = task_meta.get("risk_level") or task.get("risk_level")
        priority = task_meta.get("priority")
        if str(priority) == "1" or str(risk_level).lower() == "high":
            graph.add((task_uri, RDF.type, ONTOLOGY.CriticalDealTask))
            if risk_level:
                graph.add((task_uri, ONTOLOGY.riskLevel,
                           Literal(str(risk_level).lower(), datatype=XSD.string)))
            if task_meta.get("security_level"):
                graph.add((task_uri, ONTOLOGY.securityLevel,
                           Literal(str(task_meta["security_level"]), datatype=XSD.string)))

        # WarehouseAssignment: warehouse-related tasks
        caps = [str(c).lower() for c in (task.get("required_capabilities") or [])]
        if "warehouse_matching" in caps or "warehouse" in task_desc:
            graph.add((task_uri, RDF.type, ONTOLOGY.WarehouseAssignment))
            lease_term = task_meta.get("lease_term_months")
            if lease_term is not None:
                graph.add((task_uri, ONTOLOGY.leaseTermMonths,
                           Literal(int(lease_term), datatype=XSD.integer)))
            # Mark compliance dependency if compliance-check is in the DAG
            has_compliance_dep = any(
                str(e.get("to")) == str(task_id) and "compliance" in str(e.get("from", "")).lower()
                for e in edges
            ) or any(
                "compliance" in str(dep).lower()
                for dep in (task.get("dependencies") or [])
            )
            graph.add((task_uri, ONTOLOGY.requiresComplianceCheck,
                       Literal(has_compliance_dep)))

    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if not src or not dst:
            continue
        src_uri = task_index.get(str(src))
        dst_uri = task_index.get(str(dst))
        if not src_uri or not dst_uri:
            continue
        # "to" depends on "from" in the plan graph.
        graph.add((dst_uri, ONTOLOGY.dependsOn, src_uri))

    ontology = graph_spec.get("ontology") if isinstance(graph_spec, dict) else None
    if isinstance(ontology, dict):
        for cap in ontology.get("capabilities") or []:
            cap_uri = _capability_uri(str(cap))
            graph.add((cap_uri, RDF.type, ONTOLOGY.Capability))
            graph.add((cap_uri, RDFS.label, Literal(str(cap))))
        for tag in ontology.get("tags") or []:
            tag_uri = _tag_uri(str(tag))
            graph.add((tag_uri, RDF.type, ONTOLOGY.Tag))
            graph.add((tag_uri, RDFS.label, Literal(str(tag))))
        for task_type in ontology.get("task_types") or []:
            task_type_uri = _task_type_uri(str(task_type))
            graph.add((task_type_uri, RDF.type, ONTOLOGY.TaskType))
            graph.add((task_type_uri, RDFS.label, Literal(str(task_type))))

    return graph


def serialize_graph(graph: Graph, *, fmt: str = "turtle") -> str:
    return graph.serialize(format=fmt)
