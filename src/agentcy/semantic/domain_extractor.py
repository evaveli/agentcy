"""
LLM-powered domain knowledge extraction from NL descriptions.

Extracts business entities, relationships, and processes from pipeline
task descriptions using an LLM, then ingests the extracted knowledge
into the KG (Fuseki) and optionally into Couchbase.

This module is fire-and-forget: ``extract_domain_knowledge`` never raises
and returns ``False`` on any error. It is designed to be called via
``asyncio.ensure_future()`` so it never blocks the caller.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ENTITY_TYPES = frozenset({
    "data_source", "system", "service", "business_unit",
    "product", "metric", "workflow", "policy", "role",
})

# Simple keyword patterns for stub-mode extraction (no LLM)
_STUB_PATTERNS: Dict[str, str] = {
    "database": "data_source",
    "api": "service",
    "service": "service",
    "warehouse": "data_source",
    "pipeline": "workflow",
    "etl": "workflow",
    "dashboard": "product",
    "report": "product",
    "user": "role",
    "admin": "role",
    "metric": "metric",
    "kpi": "metric",
    "team": "business_unit",
    "department": "business_unit",
    "policy": "policy",
    "system": "system",
    "server": "system",
    "queue": "system",
    "kafka": "system",
    "redis": "system",
    "s3": "data_source",
    "bucket": "data_source",
}


def _recorder_enabled() -> bool:
    raw = os.getenv("EXECUTION_RECORDER_ENABLE", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    fuseki_raw = os.getenv("FUSEKI_ENABLE", "").strip().lower()
    return fuseki_raw in {"1", "true", "yes", "on"} or bool(os.getenv("FUSEKI_URL"))


def _is_stub_mode() -> bool:
    return os.getenv("LLM_STUB_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def _domain_provider_name() -> Optional[str]:
    """Get the LLM provider name for domain extraction."""
    raw = os.getenv("LLM_DOMAIN_PROVIDER", "").strip().lower()
    if not raw:
        raw = os.getenv("LLM_SUPERVISOR_PROVIDER", "").strip().lower()
    return raw if raw else None


def _extract_json(text: Optional[str]) -> Optional[str]:
    """Extract the first JSON object from text."""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return stripped[start: end + 1]


def _stub_extract(text: str) -> Dict[str, Any]:
    """Keyword-based entity extraction (no LLM required)."""
    words = set(re.findall(r"[a-zA-Z_]{3,}", text.lower()))
    entities: List[Dict[str, str]] = []
    seen: set = set()
    for word in words:
        etype = _STUB_PATTERNS.get(word)
        if etype and word not in seen:
            seen.add(word)
            entities.append({
                "name": word,
                "type": etype,
                "description": f"Extracted from task descriptions (keyword: {word})",
            })
    return {"entities": entities, "relationships": [], "processes": []}


async def _call_llm(text: str) -> Optional[str]:
    """Call LLM for domain knowledge extraction. Returns raw response."""
    try:
        from agentcy.llm_utilities.llm_connector import LLM_Connector, Provider

        provider_name = _domain_provider_name()
        if not provider_name:
            return None

        if provider_name in ("openai", "gpt"):
            provider = Provider.OPENAI
        elif provider_name in ("llama", "ollama"):
            provider = Provider.LLAMA
        else:
            return None

        connector = LLM_Connector(provider=provider)
        await connector.start()
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a domain knowledge extraction engine. "
                        "Extract business entities, relationships, and processes "
                        "from the text. Return ONLY valid JSON, no markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f'Extract domain knowledge from: "{text}"\n'
                        "Return JSON with:\n"
                        "{\n"
                        '  "entities": [{"name": "...", "type": "...", "description": "..."}],\n'
                        '  "relationships": [{"from": "...", "to": "...", "type": "..."}],\n'
                        '  "processes": [{"name": "...", "description": "...", "involves": ["..."]}]\n'
                        "}\n"
                        f"Valid entity types: {', '.join(sorted(_ENTITY_TYPES))}"
                    ),
                },
            ]
            results = await connector.handle_incoming_requests(
                [("domain_extraction", messages)]
            )
            return results.get("domain_extraction")
        finally:
            await connector.stop()
    except Exception:
        logger.debug("LLM call for domain extraction failed", exc_info=True)
        return None


async def extract_domain_knowledge(
    *,
    text: str,
    plan_id: Optional[str] = None,
    username: Optional[str] = None,
    graph_marker_store: Optional[Any] = None,
) -> bool:
    """Extract domain entities and relationships from NL text.

    Fire-and-forget: never raises, returns ``False`` on any error.
    Ingests extracted knowledge to Fuseki and optionally Couchbase.

    Args:
        text: Natural language text (task descriptions).
        plan_id: Source plan for provenance.
        username: Owner for provenance.
        graph_marker_store: Optional Couchbase store.

    Returns:
        ``True`` if extraction and ingestion succeeded.
    """
    if not _recorder_enabled():
        return False

    if not text or not text.strip():
        return False

    try:
        knowledge: Optional[Dict[str, Any]] = None

        # Try LLM extraction first, fall back to stub
        if _is_stub_mode() or not _domain_provider_name():
            knowledge = _stub_extract(text)
        else:
            raw = await _call_llm(text)
            if raw and raw != "Error":
                json_str = _extract_json(raw)
                if json_str:
                    try:
                        knowledge = json.loads(json_str)
                    except json.JSONDecodeError:
                        logger.debug("Failed to parse LLM domain extraction response")
            # Fall back to stub if LLM failed
            if not knowledge:
                knowledge = _stub_extract(text)

        if not knowledge:
            return False

        entities = knowledge.get("entities") or []
        relationships = knowledge.get("relationships") or []
        processes = knowledge.get("processes") or []

        if not entities and not relationships and not processes:
            return False

        # Build RDF and ingest
        from agentcy.semantic.domain_graph import build_domain_graph
        from agentcy.semantic.plan_graph import serialize_graph
        from agentcy.semantic.fuseki_client import ingest_turtle

        graph = build_domain_graph(
            entities,
            relationships,
            processes,
            plan_id=plan_id,
            username=username,
        )
        turtle = serialize_graph(graph)
        await ingest_turtle(turtle)

        # Persist to Couchbase for fast KV lookup
        if graph_marker_store is not None:
            try:
                from datetime import datetime, timezone

                now = datetime.now(timezone.utc).isoformat()
                key = f"domain_knowledge::{username or 'system'}::{plan_id or 'adhoc'}"
                doc = {
                    "plan_id": plan_id,
                    "username": username,
                    "entities": entities,
                    "relationships": relationships,
                    "processes": processes,
                    "extracted_at": now,
                    "_meta": {"type": "domain_knowledge", "updated_at": now},
                }
                graph_marker_store.upsert_raw(key, doc)
            except Exception:
                logger.warning("Failed to persist domain knowledge to Couchbase", exc_info=True)

        return True
    except Exception:
        logger.warning("Domain knowledge extraction failed", exc_info=True)
        return False
