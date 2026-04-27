"""Tests for the LLM-powered domain knowledge extractor."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agentcy.semantic.domain_extractor import (
    extract_domain_knowledge,
    _stub_extract,
    _extract_json,
)


@pytest.fixture(autouse=True)
def _enable_recorder(monkeypatch):
    """Enable execution recorder for all tests."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "1")
    monkeypatch.setenv("FUSEKI_ENABLE", "1")


@pytest.mark.asyncio
async def test_returns_false_when_disabled(monkeypatch):
    """Returns False when execution recorder is disabled."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "0")
    monkeypatch.delenv("FUSEKI_URL", raising=False)
    result = await extract_domain_knowledge(text="some text")
    assert result is False


@pytest.mark.asyncio
async def test_returns_false_for_empty_text():
    """Returns False for empty/blank text."""
    result = await extract_domain_knowledge(text="")
    assert result is False

    result = await extract_domain_knowledge(text="   ")
    assert result is False


@pytest.mark.asyncio
async def test_stub_mode_extracts_keywords(monkeypatch):
    """Stub mode extracts keyword-based entities."""
    monkeypatch.setenv("LLM_STUB_MODE", "1")

    with patch("agentcy.semantic.domain_graph.build_domain_graph") as mock_build, \
         patch("agentcy.semantic.plan_graph.serialize_graph", return_value="<turtle>"), \
         patch("agentcy.semantic.fuseki_client.ingest_turtle", new_callable=AsyncMock):
        mock_build.return_value = MagicMock()

        result = await extract_domain_knowledge(
            text="Read from database and call the API service",
        )

    assert result is True
    mock_build.assert_called_once()
    entities = mock_build.call_args[0][0]
    entity_names = {e["name"] for e in entities}
    assert "database" in entity_names
    assert "api" in entity_names or "service" in entity_names


@pytest.mark.asyncio
async def test_llm_extraction_success(monkeypatch):
    """Extracts entities via LLM when provider is configured."""
    monkeypatch.setenv("LLM_DOMAIN_PROVIDER", "openai")
    monkeypatch.delenv("LLM_STUB_MODE", raising=False)

    llm_response = json.dumps({
        "entities": [{"name": "OrderDB", "type": "data_source", "description": "Order database"}],
        "relationships": [{"from": "OrderDB", "to": "API", "type": "feeds"}],
        "processes": [{"name": "ETL", "description": "Load data", "involves": ["OrderDB"]}],
    })

    with patch("src.agentcy.semantic.domain_extractor._call_llm", new_callable=AsyncMock, return_value=llm_response), \
         patch("agentcy.semantic.domain_graph.build_domain_graph") as mock_build, \
         patch("agentcy.semantic.plan_graph.serialize_graph", return_value="<turtle>"), \
         patch("agentcy.semantic.fuseki_client.ingest_turtle", new_callable=AsyncMock):
        mock_build.return_value = MagicMock()
        result = await extract_domain_knowledge(
            text="Process orders from OrderDB via API",
            plan_id="plan-001",
            username="alice",
        )

    assert result is True
    mock_build.assert_called_once()
    call_args = mock_build.call_args
    assert call_args[0][0] == [{"name": "OrderDB", "type": "data_source", "description": "Order database"}]
    assert call_args[1]["plan_id"] == "plan-001"
    assert call_args[1]["username"] == "alice"


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_stub(monkeypatch):
    """Falls back to stub extraction when LLM returns Error."""
    monkeypatch.setenv("LLM_DOMAIN_PROVIDER", "openai")
    monkeypatch.delenv("LLM_STUB_MODE", raising=False)

    with patch("src.agentcy.semantic.domain_extractor._call_llm", new_callable=AsyncMock, return_value="Error"), \
         patch("agentcy.semantic.domain_graph.build_domain_graph") as mock_build, \
         patch("agentcy.semantic.plan_graph.serialize_graph", return_value="<turtle>"), \
         patch("agentcy.semantic.fuseki_client.ingest_turtle", new_callable=AsyncMock):
        mock_build.return_value = MagicMock()
        result = await extract_domain_knowledge(
            text="Read from the database service",
        )

    assert result is True
    mock_build.assert_called_once()
    entities = mock_build.call_args[0][0]
    assert len(entities) > 0


@pytest.mark.asyncio
async def test_malformed_llm_response_falls_back(monkeypatch):
    """Malformed LLM JSON response falls back to stub extraction."""
    monkeypatch.setenv("LLM_DOMAIN_PROVIDER", "openai")
    monkeypatch.delenv("LLM_STUB_MODE", raising=False)

    with patch("src.agentcy.semantic.domain_extractor._call_llm", new_callable=AsyncMock, return_value="not json at all"), \
         patch("agentcy.semantic.domain_graph.build_domain_graph") as mock_build, \
         patch("agentcy.semantic.plan_graph.serialize_graph", return_value="<turtle>"), \
         patch("agentcy.semantic.fuseki_client.ingest_turtle", new_callable=AsyncMock):
        mock_build.return_value = MagicMock()
        result = await extract_domain_knowledge(
            text="Process data in the pipeline system",
        )

    assert result is True


@pytest.mark.asyncio
async def test_couchbase_persistence(monkeypatch):
    """Domain knowledge is persisted to Couchbase store."""
    monkeypatch.setenv("LLM_STUB_MODE", "1")

    mock_store = MagicMock()

    with patch("agentcy.semantic.domain_graph.build_domain_graph") as mock_build, \
         patch("agentcy.semantic.plan_graph.serialize_graph", return_value="<turtle>"), \
         patch("agentcy.semantic.fuseki_client.ingest_turtle", new_callable=AsyncMock):
        mock_build.return_value = MagicMock()
        result = await extract_domain_knowledge(
            text="Query the database service",
            plan_id="plan-001",
            username="bob",
            graph_marker_store=mock_store,
        )

    assert result is True
    mock_store.upsert_raw.assert_called_once()
    key, doc = mock_store.upsert_raw.call_args[0]
    assert key == "domain_knowledge::bob::plan-001"
    assert doc["plan_id"] == "plan-001"
    assert doc["username"] == "bob"
    assert "entities" in doc


@pytest.mark.asyncio
async def test_exception_returns_false(monkeypatch):
    """Any exception in the extraction pipeline returns False."""
    monkeypatch.setenv("LLM_STUB_MODE", "1")

    with patch("agentcy.semantic.domain_graph.build_domain_graph", side_effect=Exception("boom")):
        result = await extract_domain_knowledge(text="something with database")

    assert result is False


# ── Unit tests for helper functions ──────────────────────────────────


def test_stub_extract_keywords():
    """Stub extractor finds keyword-based entities."""
    result = _stub_extract("Connect to the database and call the API")
    names = {e["name"] for e in result["entities"]}
    assert "database" in names
    assert "api" in names


def test_stub_extract_empty():
    """Stub extractor returns empty for text with no keywords."""
    result = _stub_extract("hello world xyz")
    assert result["entities"] == []


def test_extract_json_valid():
    """Extract JSON from a clean string."""
    assert _extract_json('{"key": "value"}') == '{"key": "value"}'


def test_extract_json_with_prefix():
    """Extract JSON from a string with surrounding text."""
    assert _extract_json('Here is the result: {"key": "value"} done') == '{"key": "value"}'


def test_extract_json_none():
    """Returns None for non-JSON strings."""
    assert _extract_json("no json here") is None
    assert _extract_json(None) is None
    assert _extract_json("") is None
