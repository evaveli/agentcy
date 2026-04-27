from __future__ import annotations
import os
import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import trace, propagate

# We test internal helpers too for exporter choice
from src.agentcy.observability import bootstrap as bs

@pytest.mark.asyncio
async def test_start_observability_idempotent(otel_pipeline, monkeypatch):

    monkeypatch.setattr(bs, "INSTRUMENTATIONS", {
        "fastapi": True, "aio-pika": False, "dbapi": False,
        "requests": True, "httpx": True, "logging": True
    }, raising=True )