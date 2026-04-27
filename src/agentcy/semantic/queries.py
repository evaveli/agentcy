"""
Pre-built SPARQL query helpers for common semantic searches.

This module provides high-level functions for querying the Fuseki triplestore
without requiring users to write raw SPARQL. All functions are async and
return None if Fuseki is disabled or the query fails.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agentcy.semantic.fuseki_client import sparql_query
from agentcy.semantic.namespaces import BASE_URI, ONTOLOGY, RESOURCE

logger = logging.getLogger(__name__)


def _escape_sparql_string(value: str) -> str:
    """Escape special characters for SPARQL string literals."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def _make_uri(resource_type: str, identifier: str) -> str:
    """Create a full URI for a resource."""
    safe_id = _escape_sparql_string(identifier)
    return f"{RESOURCE}{resource_type}/{safe_id}"


async def find_plans_by_capability(
    capability: str,
    *,
    limit: int = 50,
) -> Optional[List[Dict[str, Any]]]:
    """
    Find plans that have tasks requiring a specific capability.

    Args:
        capability: The capability to search for (e.g., "plan", "execute", "validate")
        limit: Maximum number of results (default: 50)

    Returns:
        List of plans with their IDs and task counts, sorted by task count descending.
        Example: [{"plan": "http://...", "planId": "plan-123", "taskCount": "5"}, ...]
    """
    cap_uri = _make_uri("capability", capability)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT DISTINCT ?plan ?planId (COUNT(?task) AS ?taskCount)
        WHERE {{
            ?plan a ac:Plan ;
                  ac:hasTask ?task ;
                  ac:planId ?planId .
            ?task ac:requiresCapability <{cap_uri}> .
        }}
        GROUP BY ?plan ?planId
        ORDER BY DESC(?taskCount)
        LIMIT {limit}
    """
    return await sparql_query(query)


async def find_plans_by_agent(
    agent_id: str,
    *,
    limit: int = 50,
) -> Optional[List[Dict[str, Any]]]:
    """
    Find plans with tasks assigned to a specific agent.

    Args:
        agent_id: The agent identifier
        limit: Maximum number of results

    Returns:
        List of plans with their IDs and task counts.
    """
    agent_uri = _make_uri("agent", agent_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT DISTINCT ?plan ?planId (COUNT(?task) AS ?taskCount)
        WHERE {{
            ?plan a ac:Plan ;
                  ac:hasTask ?task ;
                  ac:planId ?planId .
            ?task ac:assignedAgent <{agent_uri}> .
        }}
        GROUP BY ?plan ?planId
        ORDER BY DESC(?taskCount)
        LIMIT {limit}
    """
    return await sparql_query(query)


async def find_similar_plans(
    plan_id: str,
    *,
    limit: int = 10,
) -> Optional[List[Dict[str, Any]]]:
    """
    Find plans similar to a given plan based on shared capabilities.

    Uses a Jaccard-like similarity metric: counts shared capabilities between plans.
    Plans with more shared capabilities rank higher.

    Args:
        plan_id: The source plan ID to find similar plans for
        limit: Maximum number of results

    Returns:
        List of similar plans with shared capability count.
        Example: [{"otherPlan": "http://...", "otherPlanId": "plan-456", "sharedCaps": "3"}, ...]
    """
    plan_uri = _make_uri("plan", plan_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT ?otherPlan ?otherPlanId (COUNT(DISTINCT ?cap) AS ?sharedCaps)
        WHERE {{
            # Get capabilities of the source plan
            <{plan_uri}> ac:hasTask ?srcTask .
            ?srcTask ac:requiresCapability ?cap .

            # Find other plans with same capabilities
            ?otherPlan a ac:Plan ;
                       ac:planId ?otherPlanId ;
                       ac:hasTask ?otherTask .
            ?otherTask ac:requiresCapability ?cap .

            # Exclude self
            FILTER(?otherPlan != <{plan_uri}>)
        }}
        GROUP BY ?otherPlan ?otherPlanId
        ORDER BY DESC(?sharedCaps)
        LIMIT {limit}
    """
    return await sparql_query(query)


async def get_plan_task_graph(
    plan_id: str,
) -> Optional[List[Dict[str, Any]]]:
    """
    Get the task dependency graph for a plan.

    Args:
        plan_id: The plan ID

    Returns:
        List of dependency edges with from_task and to_task info.
        Example: [{"fromTask": "...", "fromTaskId": "task-1", "toTask": "...", "toTaskId": "task-2"}, ...]
    """
    plan_uri = _make_uri("plan", plan_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT ?fromTask ?fromTaskId ?toTask ?toTaskId
        WHERE {{
            <{plan_uri}> ac:hasTask ?toTask .
            ?toTask ac:taskId ?toTaskId ;
                    ac:dependsOn ?fromTask .
            ?fromTask ac:taskId ?fromTaskId .
        }}
    """
    return await sparql_query(query)


async def get_plan_details(
    plan_id: str,
) -> Optional[List[Dict[str, Any]]]:
    """
    Get detailed information about a plan including all tasks.

    Args:
        plan_id: The plan ID

    Returns:
        List of task details for the plan.
    """
    plan_uri = _make_uri("plan", plan_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT ?task ?taskId ?agent ?capability ?taskType
        WHERE {{
            <{plan_uri}> ac:hasTask ?task .
            ?task ac:taskId ?taskId .
            OPTIONAL {{ ?task ac:assignedAgent ?agent }}
            OPTIONAL {{ ?task ac:requiresCapability ?capability }}
            OPTIONAL {{ ?task ac:taskType ?taskType }}
        }}
        ORDER BY ?taskId
    """
    return await sparql_query(query)


async def get_capability_usage_stats() -> Optional[List[Dict[str, Any]]]:
    """
    Get usage statistics for all capabilities across plans.

    Returns:
        List of capabilities with task and plan counts.
        Example: [{"cap": "http://.../execute", "taskCount": "25", "planCount": "10"}, ...]
    """
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

        SELECT ?cap (COUNT(DISTINCT ?task) AS ?taskCount) (COUNT(DISTINCT ?plan) AS ?planCount)
        WHERE {{
            ?task ac:requiresCapability ?cap .
            ?plan ac:hasTask ?task .
        }}
        GROUP BY ?cap
        ORDER BY DESC(?taskCount)
    """
    return await sparql_query(query)


async def search_by_tag(
    tag: str,
    *,
    limit: int = 50,
) -> Optional[List[Dict[str, Any]]]:
    """
    Find tasks and plans by tag.

    Args:
        tag: The tag to search for
        limit: Maximum number of results

    Returns:
        List of plans and tasks that have the specified tag.
    """
    tag_uri = _make_uri("tag", tag)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT ?plan ?planId ?task ?taskId
        WHERE {{
            ?plan a ac:Plan ;
                  ac:planId ?planId ;
                  ac:hasTask ?task .
            ?task ac:taskId ?taskId ;
                  ac:hasTag <{tag_uri}> .
        }}
        LIMIT {limit}
    """
    return await sparql_query(query)


async def count_all_triples() -> Optional[int]:
    """
    Count total number of triples in the default graph.

    Returns:
        Total triple count, or None if query fails.
    """
    query = """
        SELECT (COUNT(*) AS ?count)
        WHERE { ?s ?p ?o }
    """
    results = await sparql_query(query)
    if results and len(results) > 0:
        try:
            return int(results[0].get("count", 0))
        except (ValueError, TypeError):
            return None
    return None


async def get_graph_summary() -> Optional[Dict[str, Any]]:
    """
    Get a summary of the RDF graph including class and property counts.

    Returns:
        Dict with summary statistics.
    """
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

        SELECT
            (COUNT(DISTINCT ?plan) AS ?planCount)
            (COUNT(DISTINCT ?task) AS ?taskCount)
            (COUNT(DISTINCT ?agent) AS ?agentCount)
            (COUNT(DISTINCT ?cap) AS ?capabilityCount)
            (COUNT(DISTINCT ?exec) AS ?executionCount)
            (COUNT(DISTINCT ?flow) AS ?dataFlowCount)
        WHERE {{
            OPTIONAL {{ ?plan a ac:Plan }}
            OPTIONAL {{ ?task a ac:Task }}
            OPTIONAL {{ ?agent a ac:Agent }}
            OPTIONAL {{ ?cap a ac:Capability }}
            OPTIONAL {{ ?exec a ac:Execution }}
            OPTIONAL {{ ?flow a ac:DataFlow }}
        }}
    """
    results = await sparql_query(query)
    if results and len(results) > 0:
        return results[0]
    return None


# ──────────────────────────────────────────────────────────────────────
# Execution History Queries
# ──────────────────────────────────────────────────────────────────────


async def get_agent_success_rate(
    agent_id: str,
) -> Optional[List[Dict[str, Any]]]:
    """Get task success rate for an agent from execution history.

    Returns a single row with ``total``, ``successes`` counts.
    """
    agent_uri = _make_uri("agent", agent_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>

        SELECT
            (COUNT(?exec) AS ?total)
            (SUM(IF(?status = "completed", 1, 0)) AS ?successes)
        WHERE {{
            ?exec a ac:Execution ;
                  ac:executedBy <{agent_uri}> ;
                  ac:executionStatus ?status .
        }}
    """
    return await sparql_query(query)


async def get_task_avg_duration(
    capability: str,
) -> Optional[List[Dict[str, Any]]]:
    """Get average execution duration for tasks with a given capability.

    Returns a single row with ``avgDuration``, ``minDuration``,
    ``maxDuration``, and ``sampleCount``.
    """
    cap_uri = _make_uri("capability", capability)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>

        SELECT
            (AVG(?dur) AS ?avgDuration)
            (MIN(?dur) AS ?minDuration)
            (MAX(?dur) AS ?maxDuration)
            (COUNT(?exec) AS ?sampleCount)
        WHERE {{
            ?exec a ac:Execution ;
                  ac:executionOf ?task ;
                  ac:durationSeconds ?dur ;
                  ac:executionStatus "completed" .
            ?task ac:requiresCapability <{cap_uri}> .
        }}
    """
    return await sparql_query(query)


async def get_failure_patterns(
    capability: str,
    *,
    limit: int = 20,
) -> Optional[List[Dict[str, Any]]]:
    """Get agent+capability failure frequency for a given capability.

    Returns rows of ``agent`` URI and ``failures`` count, ordered by
    descending failure count.
    """
    cap_uri = _make_uri("capability", capability)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>

        SELECT ?agent (COUNT(?exec) AS ?failures)
        WHERE {{
            ?exec a ac:Execution ;
                  ac:executedBy ?agent ;
                  ac:executionOf ?task ;
                  ac:executionStatus "failed" .
            ?task ac:requiresCapability <{cap_uri}> .
        }}
        GROUP BY ?agent
        ORDER BY DESC(?failures)
        LIMIT {limit}
    """
    return await sparql_query(query)


# ──────────────────────────────────────────────────────────────────────
# Data Flow Lineage Queries
# ──────────────────────────────────────────────────────────────────────


async def get_data_lineage(
    run_id: str,
    task_id: str,
) -> Optional[List[Dict[str, Any]]]:
    """Trace upstream data sources for a task in a specific run.

    Returns rows with ``upstreamTask`` URI and ``upstreamTaskId``.
    """
    safe_run = _escape_sparql_string(run_id)
    safe_task = _escape_sparql_string(task_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT ?upstreamTask ?upstreamTaskId
        WHERE {{
            ?flow a ac:DataFlow ;
                  ac:inRun ?run ;
                  ac:toTask ?current ;
                  ac:fromTask ?upstreamTask .
            ?upstreamTask ac:taskId ?upstreamTaskId .
            FILTER(CONTAINS(STR(?run), "{safe_run}"))
            FILTER(CONTAINS(STR(?current), "{safe_task}"))
        }}
    """
    return await sparql_query(query)


async def get_downstream_impact(
    run_id: str,
    task_id: str,
) -> Optional[List[Dict[str, Any]]]:
    """Trace downstream consumers of a task's output in a specific run.

    Returns rows with ``downstreamTask`` URI and ``downstreamTaskId``.
    """
    safe_run = _escape_sparql_string(run_id)
    safe_task = _escape_sparql_string(task_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT ?downstreamTask ?downstreamTaskId
        WHERE {{
            ?flow a ac:DataFlow ;
                  ac:inRun ?run ;
                  ac:fromTask ?source ;
                  ac:toTask ?downstreamTask .
            ?downstreamTask ac:taskId ?downstreamTaskId .
            FILTER(CONTAINS(STR(?run), "{safe_run}"))
            FILTER(CONTAINS(STR(?source), "{safe_task}"))
        }}
    """
    return await sparql_query(query)


async def get_common_data_paths(
    pipeline_id: str,
    *,
    limit: int = 20,
) -> Optional[List[Dict[str, Any]]]:
    """Get most frequent data flow patterns across runs.

    Returns rows with ``fromTaskId``, ``toTaskId``, and ``frequency``.
    """
    query = f"""
        PREFIX ac: <{ONTOLOGY}>

        SELECT ?fromTaskId ?toTaskId (COUNT(?flow) AS ?frequency)
        WHERE {{
            ?flow a ac:DataFlow ;
                  ac:fromTask ?from ;
                  ac:toTask ?to .
            ?from ac:taskId ?fromTaskId .
            ?to ac:taskId ?toTaskId .
        }}
        GROUP BY ?fromTaskId ?toTaskId
        ORDER BY DESC(?frequency)
        LIMIT {limit}
    """
    return await sparql_query(query)


# ──────────────────────────────────────────────────────────────────────
# Cross-Plan Learning Queries
# ──────────────────────────────────────────────────────────────────────


async def get_plan_execution_outcomes(
    plan_id: str,
) -> Optional[List[Dict[str, Any]]]:
    """Get aggregate execution outcomes for all tasks in a plan.

    Returns a single row with ``total``, ``successes``, and ``avgDuration``.
    """
    plan_uri = _make_uri("plan", plan_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>

        SELECT
            (COUNT(?exec) AS ?total)
            (SUM(IF(?status = "completed", 1, 0)) AS ?successes)
            (AVG(?dur) AS ?avgDuration)
        WHERE {{
            <{plan_uri}> ac:hasTask ?task .
            ?exec a ac:Execution ;
                  ac:executionOf ?task ;
                  ac:executionStatus ?status .
            OPTIONAL {{ ?exec ac:durationSeconds ?dur }}
        }}
    """
    return await sparql_query(query)


async def find_plans_by_capabilities(
    capabilities: List[str],
    *,
    limit: int = 5,
) -> Optional[List[Dict[str, Any]]]:
    """Find plans that share the most capabilities with a given set.

    Unlike ``find_similar_plans`` which requires a source plan_id, this
    takes capabilities directly so it can be used before a plan exists.

    Returns rows with ``planId`` and ``sharedCaps``.
    """
    if not capabilities:
        return []
    cap_uris = " ".join(f"<{_make_uri('capability', c)}>" for c in capabilities)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>

        SELECT ?planId (COUNT(DISTINCT ?cap) AS ?sharedCaps)
        WHERE {{
            VALUES ?cap {{ {cap_uris} }}
            ?plan a ac:Plan ;
                  ac:planId ?planId ;
                  ac:hasTask ?task .
            ?task ac:requiresCapability ?cap .
        }}
        GROUP BY ?planId
        ORDER BY DESC(?sharedCaps)
        LIMIT {limit}
    """
    return await sparql_query(query)


# ──────────────────────────────────────────────────────────────────────
# Template KG Queries
# ──────────────────────────────────────────────────────────────────────


async def get_templates_by_capability(
    capability: str,
    *,
    limit: int = 20,
) -> Optional[List[Dict[str, Any]]]:
    """Find templates linked to a capability (including via hierarchy).

    Returns rows with ``template``, ``templateId``, ``templateName``.
    """
    cap_uri = _make_uri("capability", capability)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT DISTINCT ?template ?templateId ?templateName
        WHERE {{
            {{
                ?template a ac:Template ;
                          ac:templateId ?templateId ;
                          ac:requiresCapability <{cap_uri}> .
            }} UNION {{
                ?childCap ac:subCapabilityOf <{cap_uri}> .
                ?template a ac:Template ;
                          ac:templateId ?templateId ;
                          ac:requiresCapability ?childCap .
            }}
            OPTIONAL {{ ?template ac:templateName ?templateName }}
        }}
        LIMIT {limit}
    """
    return await sparql_query(query)


async def get_best_template_for_capability(
    capability: str,
    *,
    limit: int = 5,
) -> Optional[List[Dict[str, Any]]]:
    """Rank templates by execution success rate for a given capability.

    Joins templates → capability → tasks → executions to find which
    template's capabilities have the highest completion rate.

    Returns rows with ``templateId``, ``templateName``, ``total``,
    ``successes``, and ``successRate``.
    """
    cap_uri = _make_uri("capability", capability)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT ?templateId ?templateName
               (COUNT(?exec) AS ?total)
               (SUM(IF(?status = "completed", 1, 0)) AS ?successes)
        WHERE {{
            ?template a ac:Template ;
                      ac:templateId ?templateId ;
                      ac:requiresCapability <{cap_uri}> .
            OPTIONAL {{ ?template ac:templateName ?templateName }}
            ?task ac:requiresCapability <{cap_uri}> .
            ?exec a ac:Execution ;
                  ac:executionOf ?task ;
                  ac:executionStatus ?status .
        }}
        GROUP BY ?templateId ?templateName
        ORDER BY DESC(xsd:float(?successes) / xsd:float(?total))
        LIMIT {limit}
    """
    return await sparql_query(query)


async def get_template_execution_summary(
    template_id: str,
) -> Optional[List[Dict[str, Any]]]:
    """Aggregate execution stats for tasks matching a template's capabilities.

    Returns a single row with ``totalExecs``, ``successes``,
    ``avgDuration``, and ``capabilityCount``.
    """
    tmpl_uri = _make_uri("template", template_id)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>
        PREFIX res: <{RESOURCE}>

        SELECT
            (COUNT(DISTINCT ?exec) AS ?totalExecs)
            (SUM(IF(?status = "completed", 1, 0)) AS ?successes)
            (AVG(?dur) AS ?avgDuration)
            (COUNT(DISTINCT ?cap) AS ?capabilityCount)
        WHERE {{
            <{tmpl_uri}> a ac:Template ;
                         ac:requiresCapability ?cap .
            ?task ac:requiresCapability ?cap .
            ?exec a ac:Execution ;
                  ac:executionOf ?task ;
                  ac:executionStatus ?status .
            OPTIONAL {{ ?exec ac:durationSeconds ?dur }}
        }}
    """
    return await sparql_query(query)


# ──────────────────────────────────────────────────────────────────────
# Domain Knowledge Queries
# ──────────────────────────────────────────────────────────────────────


async def get_domain_entities(
    entity_type: Optional[str] = None,
    *,
    limit: int = 50,
) -> Optional[List[Dict[str, Any]]]:
    """List domain entities, optionally filtered by type.

    Returns rows with ``name``, ``entityType``, ``description``.
    """
    type_filter = ""
    if entity_type:
        safe_type = _escape_sparql_string(entity_type)
        type_filter = f'FILTER(?entityType = "{safe_type}")'

    query = f"""
        PREFIX ac: <{ONTOLOGY}>

        SELECT ?name ?entityType ?description
        WHERE {{
            ?entity a ac:DomainEntity ;
                    ac:entityName ?name .
            OPTIONAL {{ ?entity ac:entityType ?entityType }}
            OPTIONAL {{ ?entity ac:entityDescription ?description }}
            {type_filter}
        }}
        ORDER BY ?name
        LIMIT {limit}
    """
    return await sparql_query(query)


async def get_domain_context_for_capabilities(
    capabilities: List[str],
    *,
    limit: int = 10,
) -> Optional[List[Dict[str, Any]]]:
    """Find domain entities related to given capabilities.

    Joins DomainEntity -> extractedFrom -> Plan -> Task -> requiresCapability.
    Returns entities that appear in plans using these capabilities.
    """
    if not capabilities:
        return []
    cap_uris = " ".join(f"<{_make_uri('capability', c)}>" for c in capabilities)
    query = f"""
        PREFIX ac: <{ONTOLOGY}>

        SELECT DISTINCT ?name ?entityType ?description ?planId
        WHERE {{
            VALUES ?cap {{ {cap_uris} }}
            ?entity a ac:DomainEntity ;
                    ac:entityName ?name ;
                    ac:extractedFrom ?plan .
            ?plan a ac:Plan ;
                  ac:planId ?planId ;
                  ac:hasTask ?task .
            ?task ac:requiresCapability ?cap .
            OPTIONAL {{ ?entity ac:entityType ?entityType }}
            OPTIONAL {{ ?entity ac:entityDescription ?description }}
        }}
        ORDER BY ?name
        LIMIT {limit}
    """
    return await sparql_query(query)
