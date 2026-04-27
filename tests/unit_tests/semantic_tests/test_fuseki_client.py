from __future__ import annotations

import pytest

from src.agentcy.semantic import fuseki_client


@pytest.mark.asyncio
async def test_ingest_disabled(monkeypatch):
    monkeypatch.delenv("FUSEKI_ENABLE", raising=False)
    monkeypatch.delenv("FUSEKI_URL", raising=False)
    result = await fuseki_client.ingest_turtle("@prefix a: <x> .")
    assert result is False


@pytest.mark.asyncio
async def test_ingest_enabled_success(monkeypatch):
    posts = []

    class _FakeResp:
        status_code = 204

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, content=None, params=None, headers=None, auth=None):
            posts.append({"url": url, "params": params, "headers": headers, "content": content})
            return _FakeResp()

    monkeypatch.setenv("FUSEKI_ENABLE", "1")
    monkeypatch.setenv("FUSEKI_URL", "http://fuseki:3030")
    monkeypatch.setenv("FUSEKI_DATASET", "kg")
    monkeypatch.setattr(fuseki_client.httpx, "AsyncClient", _FakeClient)

    ok = await fuseki_client.ingest_turtle("@prefix a: <x> .", graph_uri="http://example/graph")
    assert ok is True
    assert posts
    assert posts[0]["url"].endswith("/kg/data")
    assert posts[0]["params"]["graph"] == "http://example/graph"
