"""Tests for the cross-plan recommendation service."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agentcy.semantic.plan_recommender import get_plan_context


@pytest.fixture(autouse=True)
def _enable_fuseki(monkeypatch):
    """Enable Fuseki for all tests."""
    monkeypatch.setenv("FUSEKI_ENABLE", "1")


@pytest.mark.asyncio
async def test_returns_none_when_fuseki_disabled(monkeypatch):
    """Returns None when Fuseki is not enabled."""
    monkeypatch.delenv("FUSEKI_ENABLE", raising=False)
    monkeypatch.delenv("FUSEKI_URL", raising=False)
    result = await get_plan_context(capabilities=["data_read"])
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_for_empty_capabilities():
    """Returns None when no capabilities provided."""
    result = await get_plan_context(capabilities=[])
    assert result is None


@pytest.mark.asyncio
async def test_returns_structured_context():
    """Returns structured dict with similar_plans, capability_stats, recommended_templates."""
    mock_similar = [{"planId": "plan-xyz", "sharedCaps": "3"}]
    mock_outcomes = [{"total": "10", "successes": "8", "avgDuration": "5.2"}]
    mock_duration = [{"avgDuration": "3.1", "sampleCount": "20"}]
    mock_templates = [{"templateId": "tmpl-001", "templateName": "checker", "total": "10", "successes": "9"}]

    with patch("agentcy.semantic.queries.find_plans_by_capabilities", new_callable=AsyncMock, return_value=mock_similar), \
         patch("agentcy.semantic.queries.find_similar_plans", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.get_plan_execution_outcomes", new_callable=AsyncMock, return_value=mock_outcomes), \
         patch("agentcy.semantic.queries.get_task_avg_duration", new_callable=AsyncMock, return_value=mock_duration), \
         patch("agentcy.semantic.queries.get_best_template_for_capability", new_callable=AsyncMock, return_value=mock_templates):
        result = await get_plan_context(capabilities=["data_read"])

    assert result is not None
    assert "similar_plans" in result
    assert "capability_stats" in result
    assert "recommended_templates" in result
    assert len(result["similar_plans"]) == 1
    assert result["similar_plans"][0]["plan_id"] == "plan-xyz"
    assert result["similar_plans"][0]["shared_capabilities"] == 3
    assert result["similar_plans"][0]["execution_summary"]["total"] == 10
    assert result["similar_plans"][0]["execution_summary"]["successes"] == 8


@pytest.mark.asyncio
async def test_handles_empty_kg():
    """Handles empty KG gracefully (no similar plans found)."""
    with patch("agentcy.semantic.queries.find_plans_by_capabilities", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.find_similar_plans", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.get_task_avg_duration", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.get_best_template_for_capability", new_callable=AsyncMock, return_value=None):
        result = await get_plan_context(capabilities=["data_read"])

    assert result is not None
    assert result["similar_plans"] == []
    assert result["capability_stats"] == {}
    assert result["recommended_templates"] == []


@pytest.mark.asyncio
async def test_capability_stats_formatted():
    """Capability stats are formatted correctly with avg_duration and sample_count."""
    mock_duration = [{"avgDuration": "4.5", "sampleCount": "15"}]

    with patch("agentcy.semantic.queries.find_plans_by_capabilities", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.find_similar_plans", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.get_task_avg_duration", new_callable=AsyncMock, return_value=mock_duration), \
         patch("agentcy.semantic.queries.get_best_template_for_capability", new_callable=AsyncMock, return_value=None):
        result = await get_plan_context(capabilities=["validate"])

    assert result is not None
    assert "validate" in result["capability_stats"]
    assert result["capability_stats"]["validate"]["avg_duration"] == 4.5
    assert result["capability_stats"]["validate"]["sample_count"] == 15


@pytest.mark.asyncio
async def test_uses_plan_id_when_provided():
    """Uses find_similar_plans when plan_id is provided."""
    mock_similar = [{"otherPlanId": "plan-abc", "sharedCaps": "5"}]
    mock_outcomes = [{"total": "5", "successes": "4", "avgDuration": "2.0"}]

    with patch("agentcy.semantic.queries.find_similar_plans", new_callable=AsyncMock, return_value=mock_similar) as find_sim, \
         patch("agentcy.semantic.queries.find_plans_by_capabilities", new_callable=AsyncMock) as find_cap, \
         patch("agentcy.semantic.queries.get_plan_execution_outcomes", new_callable=AsyncMock, return_value=mock_outcomes), \
         patch("agentcy.semantic.queries.get_task_avg_duration", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.get_best_template_for_capability", new_callable=AsyncMock, return_value=None):
        result = await get_plan_context(capabilities=["data_read"], plan_id="plan-source")

    find_sim.assert_called_once_with("plan-source", limit=3)
    find_cap.assert_not_called()
    assert result["similar_plans"][0]["plan_id"] == "plan-abc"


@pytest.mark.asyncio
async def test_recommended_templates_deduped():
    """Recommended templates are deduplicated across capabilities."""
    mock_tmpl = [
        {"templateId": "tmpl-001", "templateName": "a", "total": "10", "successes": "9"},
    ]

    with patch("agentcy.semantic.queries.find_plans_by_capabilities", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.find_similar_plans", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.get_task_avg_duration", new_callable=AsyncMock, return_value=None), \
         patch("agentcy.semantic.queries.get_best_template_for_capability", new_callable=AsyncMock, return_value=mock_tmpl):
        # Two capabilities returning the same template
        result = await get_plan_context(capabilities=["data_read", "validate"])

    assert result is not None
    # Should be deduped to 1 template
    assert len(result["recommended_templates"]) == 1
    assert result["recommended_templates"][0]["template_id"] == "tmpl-001"
    assert result["recommended_templates"][0]["success_rate"] == 0.9


@pytest.mark.asyncio
async def test_exception_returns_none():
    """Exception during query returns None gracefully."""
    with patch("agentcy.semantic.queries.find_plans_by_capabilities", new_callable=AsyncMock, side_effect=Exception("boom")), \
         patch("agentcy.semantic.queries.find_similar_plans", new_callable=AsyncMock, side_effect=Exception("boom")):
        result = await get_plan_context(capabilities=["data_read"])

    assert result is None
