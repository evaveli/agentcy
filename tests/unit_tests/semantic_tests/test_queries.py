"""Unit tests for SPARQL query helpers."""
from __future__ import annotations

import pytest

from src.agentcy.semantic import queries
from src.agentcy.semantic import fuseki_client


@pytest.mark.asyncio
async def test_find_plans_by_capability_disabled(monkeypatch):
    """Should return None when Fuseki is disabled."""
    monkeypatch.delenv("FUSEKI_ENABLE", raising=False)
    monkeypatch.delenv("FUSEKI_URL", raising=False)
    result = await queries.find_plans_by_capability("execute")
    assert result is None


@pytest.mark.asyncio
async def test_find_plans_by_capability_success(monkeypatch):
    """Should return plans with mocked query."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return [
            {"plan": "http://example/plan/1", "planId": "plan-1", "taskCount": "3"},
            {"plan": "http://example/plan/2", "planId": "plan-2", "taskCount": "1"},
        ]

    monkeypatch.setattr(fuseki_client, "sparql_query", mock_query)
    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.find_plans_by_capability("execute", limit=10)
    assert result is not None
    assert len(result) == 2
    assert result[0]["planId"] == "plan-1"


@pytest.mark.asyncio
async def test_find_plans_by_agent_success(monkeypatch):
    """Should return plans for an agent."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return [{"plan": "http://example/plan/1", "planId": "plan-1", "taskCount": "2"}]

    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.find_plans_by_agent("agent-123")
    assert result is not None
    assert len(result) == 1


@pytest.mark.asyncio
async def test_find_similar_plans_success(monkeypatch):
    """Should return similar plans based on shared capabilities."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return [
            {"otherPlan": "http://example/plan/2", "otherPlanId": "plan-2", "sharedCaps": "3"},
            {"otherPlan": "http://example/plan/3", "otherPlanId": "plan-3", "sharedCaps": "1"},
        ]

    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.find_similar_plans("plan-1", limit=5)
    assert result is not None
    assert len(result) == 2
    assert result[0]["sharedCaps"] == "3"


@pytest.mark.asyncio
async def test_get_plan_task_graph_success(monkeypatch):
    """Should return task dependency edges."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return [
            {
                "fromTask": "http://example/task/1",
                "fromTaskId": "task-1",
                "toTask": "http://example/task/2",
                "toTaskId": "task-2",
            }
        ]

    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.get_plan_task_graph("plan-1")
    assert result is not None
    assert len(result) == 1
    assert result[0]["fromTaskId"] == "task-1"
    assert result[0]["toTaskId"] == "task-2"


@pytest.mark.asyncio
async def test_get_capability_usage_stats_success(monkeypatch):
    """Should return capability statistics."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return [
            {"cap": "http://example/cap/execute", "taskCount": "25", "planCount": "10"},
            {"cap": "http://example/cap/validate", "taskCount": "15", "planCount": "8"},
        ]

    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.get_capability_usage_stats()
    assert result is not None
    assert len(result) == 2


@pytest.mark.asyncio
async def test_search_by_tag_success(monkeypatch):
    """Should find plans and tasks by tag."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return [
            {
                "plan": "http://example/plan/1",
                "planId": "plan-1",
                "task": "http://example/task/1",
                "taskId": "task-1",
            }
        ]

    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.search_by_tag("gpu", limit=50)
    assert result is not None
    assert len(result) == 1


@pytest.mark.asyncio
async def test_count_all_triples_success(monkeypatch):
    """Should return triple count."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return [{"count": "12345"}]

    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.count_all_triples()
    assert result == 12345


@pytest.mark.asyncio
async def test_count_all_triples_empty(monkeypatch):
    """Should return None when no results."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return []

    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.count_all_triples()
    assert result is None


@pytest.mark.asyncio
async def test_get_graph_summary_success(monkeypatch):
    """Should return graph summary."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")

    async def mock_query(query, **kwargs):
        return [
            {
                "planCount": "10",
                "taskCount": "50",
                "agentCount": "5",
                "capabilityCount": "8",
            }
        ]

    monkeypatch.setattr(queries, "sparql_query", mock_query)

    result = await queries.get_graph_summary()
    assert result is not None
    assert result["planCount"] == "10"


def test_escape_sparql_string():
    """Should escape special characters."""
    assert queries._escape_sparql_string('test"quote') == 'test\\"quote'
    assert queries._escape_sparql_string("test\\slash") == "test\\\\slash"
    assert queries._escape_sparql_string("test'single") == "test\\'single"


def test_make_uri():
    """Should create proper URIs."""
    uri = queries._make_uri("capability", "execute")
    assert "capability/execute" in uri
