from __future__ import annotations

from rdflib import RDF, Literal, URIRef

from src.agentcy.semantic.plan_graph import build_plan_graph
from src.agentcy.semantic.namespaces import ONTOLOGY, PROV, RESOURCE


def _plan_uri(plan_id: str) -> URIRef:
    return URIRef(f"{RESOURCE}plan/{plan_id}")


def _task_uri(graph, task_id: str) -> URIRef:
    for subj, obj in graph.subject_objects(ONTOLOGY.taskId):
        if str(obj) == task_id:
            return subj
    raise AssertionError(f"task_id {task_id} not found in graph")


def test_build_plan_graph_basic():
    plan_id = "plan-123"
    graph_spec = {
        "tasks": [
            {
                "task_id": "t1",
                "assigned_agent": "agent-a",
                "required_capabilities": ["cap-a"],
                "tags": ["tag-a"],
                "task_type": "type-a",
            },
            {
                "task_id": "t2",
                "assigned_agent": "agent-b",
                "required_capabilities": ["cap-b"],
                "tags": [],
                "task_type": "type-b",
            },
        ],
        "edges": [{"from": "t1", "to": "t2"}],
    }

    graph = build_plan_graph(
        graph_spec,
        plan_id=plan_id,
        pipeline_id="pipe-1",
        username="alice",
        include_prov=False,
    )

    plan_uri = _plan_uri(plan_id)
    assert (plan_uri, RDF.type, ONTOLOGY.Plan) in graph

    task1_uri = _task_uri(graph, "t1")
    task2_uri = _task_uri(graph, "t2")
    assert (task2_uri, ONTOLOGY.dependsOn, task1_uri) in graph

    agent_uri = next(graph.objects(task1_uri, ONTOLOGY.assignedAgent))
    assert (agent_uri, RDF.type, ONTOLOGY.Agent) in graph

    cap_uri = next(graph.objects(task1_uri, ONTOLOGY.requiresCapability))
    assert (cap_uri, RDF.type, ONTOLOGY.Capability) in graph

    tag_uri = next(graph.objects(task1_uri, ONTOLOGY.hasTag))
    assert (tag_uri, RDF.type, ONTOLOGY.Tag) in graph

    task_type_uri = next(graph.objects(task1_uri, ONTOLOGY.taskType))
    assert (task_type_uri, RDF.type, ONTOLOGY.TaskType) in graph


def test_build_plan_graph_includes_prov_types():
    graph_spec = {
        "tasks": [
            {
                "task_id": "t1",
                "assigned_agent": "agent-a",
                "required_capabilities": ["cap-a"],
            }
        ],
        "edges": [],
    }

    graph = build_plan_graph(
        graph_spec,
        plan_id="plan-prov",
        pipeline_id=None,
        username=None,
        include_prov=True,
    )

    plan_uri = _plan_uri("plan-prov")
    task_uri = _task_uri(graph, "t1")

    assert (plan_uri, RDF.type, PROV.Entity) in graph
    assert (task_uri, RDF.type, PROV.Entity) in graph
