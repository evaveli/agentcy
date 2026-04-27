"""Tests for the fire-and-forget execution recorder service."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


class _FakeGraphMarkerStore:
    """Minimal stand-in for GraphMarkerStore with upsert_raw."""

    def __init__(self):
        self._data = {}

    def upsert_raw(self, key: str, doc: dict) -> str:
        self._data[key] = doc
        return key

    def get_raw(self, key: str):
        return self._data.get(key)


# ── record_execution Tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_execution_disabled(monkeypatch):
    """Returns False when EXECUTION_RECORDER_ENABLE=0."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "0")
    monkeypatch.delenv("FUSEKI_ENABLE", raising=False)
    monkeypatch.delenv("FUSEKI_URL", raising=False)

    from src.agentcy.semantic.execution_recorder import record_execution

    result = await record_execution(
        task_id="t1",
        agent_id="a1",
        plan_id="p1",
        pipeline_run_id="run-1",
        username="alice",
        status="completed",
    )
    assert result is False


@pytest.mark.asyncio
async def test_record_execution_success(monkeypatch):
    """Succeeds and writes both RDF and Couchbase when enabled."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "1")

    mock_ingest = AsyncMock(return_value=True)
    store = _FakeGraphMarkerStore()

    with patch("src.agentcy.semantic.execution_recorder.ingest_turtle", mock_ingest):
        from src.agentcy.semantic.execution_recorder import record_execution

        result = await record_execution(
            task_id="t1",
            agent_id="agent-a",
            plan_id="plan-123",
            pipeline_run_id="run-001",
            username="alice",
            status="completed",
            attempt_number=1,
            duration_seconds=5.0,
            graph_marker_store=store,
        )

    assert result is True
    mock_ingest.assert_called_once()

    # Check Couchbase document
    key = "execution::alice::run-001::t1::1"
    doc = store.get_raw(key)
    assert doc is not None
    assert doc["task_id"] == "t1"
    assert doc["agent_id"] == "agent-a"
    assert doc["status"] == "completed"
    assert doc["duration_seconds"] == 5.0
    assert doc["_meta"]["type"] == "execution_record"


@pytest.mark.asyncio
async def test_record_execution_fuseki_failure_does_not_raise(monkeypatch):
    """Returns False (does not raise) when Fuseki ingestion fails."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "1")

    mock_ingest = AsyncMock(side_effect=Exception("Fuseki down"))

    with patch("src.agentcy.semantic.execution_recorder.ingest_turtle", mock_ingest):
        from src.agentcy.semantic.execution_recorder import record_execution

        result = await record_execution(
            task_id="t1",
            agent_id="a1",
            plan_id="p1",
            pipeline_run_id="run-1",
            username="alice",
            status="failed",
            error="task error",
        )

    assert result is False


@pytest.mark.asyncio
async def test_record_execution_couchbase_failure_still_succeeds(monkeypatch):
    """Fuseki succeeds even when Couchbase store fails."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "1")

    mock_ingest = AsyncMock(return_value=True)
    bad_store = MagicMock()
    bad_store.upsert_raw.side_effect = Exception("CB down")

    with patch("src.agentcy.semantic.execution_recorder.ingest_turtle", mock_ingest):
        from src.agentcy.semantic.execution_recorder import record_execution

        result = await record_execution(
            task_id="t1",
            agent_id="a1",
            plan_id="p1",
            pipeline_run_id="run-1",
            username="alice",
            status="completed",
            graph_marker_store=bad_store,
        )

    # Fuseki succeeded, so overall result is True
    assert result is True


@pytest.mark.asyncio
async def test_record_execution_no_store(monkeypatch):
    """Works fine when graph_marker_store is None (no Couchbase)."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "1")

    mock_ingest = AsyncMock(return_value=True)

    with patch("src.agentcy.semantic.execution_recorder.ingest_turtle", mock_ingest):
        from src.agentcy.semantic.execution_recorder import record_execution

        result = await record_execution(
            task_id="t1",
            agent_id="a1",
            plan_id="p1",
            pipeline_run_id="run-1",
            username="alice",
            status="completed",
            graph_marker_store=None,
        )

    assert result is True


# ── record_data_flow Tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_data_flow_disabled(monkeypatch):
    """Returns False when disabled."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "0")
    monkeypatch.delenv("FUSEKI_ENABLE", raising=False)
    monkeypatch.delenv("FUSEKI_URL", raising=False)

    from src.agentcy.semantic.execution_recorder import record_data_flow

    result = await record_data_flow(
        from_task="a",
        to_task="b",
        plan_id="p1",
        pipeline_run_id="run-1",
        username="alice",
    )
    assert result is False


@pytest.mark.asyncio
async def test_record_data_flow_success(monkeypatch):
    """Succeeds and writes both RDF and Couchbase."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "1")

    mock_ingest = AsyncMock(return_value=True)
    store = _FakeGraphMarkerStore()

    with patch("src.agentcy.semantic.execution_recorder.ingest_turtle", mock_ingest):
        from src.agentcy.semantic.execution_recorder import record_data_flow

        result = await record_data_flow(
            from_task="extract",
            to_task="transform",
            plan_id="plan-123",
            pipeline_run_id="run-001",
            username="alice",
            payload_size_bytes=2048,
            payload_fields=["orders", "products"],
            graph_marker_store=store,
        )

    assert result is True
    mock_ingest.assert_called_once()

    key = "dataflow::alice::run-001::extract::transform"
    doc = store.get_raw(key)
    assert doc is not None
    assert doc["from_task"] == "extract"
    assert doc["to_task"] == "transform"
    assert doc["payload_size_bytes"] == 2048
    assert doc["payload_fields"] == ["orders", "products"]
    assert doc["_meta"]["type"] == "data_flow_record"


@pytest.mark.asyncio
async def test_record_data_flow_fuseki_failure_does_not_raise(monkeypatch):
    """Returns False (does not raise) when Fuseki fails."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "1")

    mock_ingest = AsyncMock(side_effect=Exception("Fuseki down"))

    with patch("src.agentcy.semantic.execution_recorder.ingest_turtle", mock_ingest):
        from src.agentcy.semantic.execution_recorder import record_data_flow

        result = await record_data_flow(
            from_task="a",
            to_task="b",
            plan_id="p1",
            pipeline_run_id="run-1",
            username="alice",
        )

    assert result is False


@pytest.mark.asyncio
async def test_record_data_flow_no_payload_fields(monkeypatch):
    """Works when payload metadata is omitted."""
    monkeypatch.setenv("EXECUTION_RECORDER_ENABLE", "1")

    mock_ingest = AsyncMock(return_value=True)

    with patch("src.agentcy.semantic.execution_recorder.ingest_turtle", mock_ingest):
        from src.agentcy.semantic.execution_recorder import record_data_flow

        result = await record_data_flow(
            from_task="a",
            to_task="b",
            plan_id="p1",
            pipeline_run_id="run-1",
            username="alice",
        )

    assert result is True
