"""
Fuseki HTTP client for RDF ingestion and SPARQL queries.

This module provides async HTTP access to Apache Jena Fuseki for:
- Ingesting Turtle RDF data (POST)
- Executing SPARQL SELECT queries
- Executing SPARQL ASK queries

All operations are feature-flagged via FUSEKI_ENABLE environment variable.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    raw = os.getenv("FUSEKI_ENABLE", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(os.getenv("FUSEKI_URL"))


def _base_url() -> str:
    return os.getenv("FUSEKI_URL", "http://fuseki:3030").rstrip("/")


def _dataset() -> str:
    return os.getenv("FUSEKI_DATASET", "agentcy")


def _timeout() -> float:
    raw = os.getenv("FUSEKI_TIMEOUT", "5")
    try:
        return float(raw)
    except ValueError:
        return 5.0


def _auth() -> Optional[tuple[str, str]]:
    user = os.getenv("FUSEKI_USER", "").strip()
    password = os.getenv("FUSEKI_PASSWORD", "").strip()
    if user and password:
        return user, password
    return None


def _endpoint_url(base_url: str, dataset: str) -> str:
    return f"{base_url}/{dataset}/data"


def _headers() -> dict:
    return {"Content-Type": "text/turtle"}


async def ingest_turtle(
    turtle: str,
    *,
    graph_uri: Optional[str] = None,
    dataset: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
) -> bool:
    if not turtle:
        return False
    if not _enabled():
        return False

    base_url = (base_url or _base_url()).rstrip("/")
    dataset = dataset or _dataset()
    url = _endpoint_url(base_url, dataset)
    params = {"graph": graph_uri} if graph_uri else None
    timeout = timeout if timeout is not None else _timeout()
    auth = _auth()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                content=turtle.encode("utf-8"),
                params=params,
                headers=_headers(),
                auth=auth,
            )
            resp.raise_for_status()
        return True
    except Exception:
        logger.exception("Fuseki ingest failed (dataset=%s, graph=%s)", dataset, graph_uri)
        return False


async def sparql_query(
    query: str,
    *,
    dataset: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Optional[List[Dict[str, Any]]]:
    """
    Execute a SPARQL SELECT query and return results.

    Args:
        query: SPARQL SELECT query string
        dataset: Override dataset name (default: FUSEKI_DATASET env)
        base_url: Override Fuseki URL (default: FUSEKI_URL env)
        timeout: Request timeout in seconds

    Returns:
        List of result bindings as dicts with simplified values,
        or None if Fuseki is disabled or query fails.

    Example:
        results = await sparql_query("SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 10")
        # Returns: [{"s": "http://...", "p": "http://...", "o": "value"}, ...]
    """
    if not _enabled():
        return None

    base_url = (base_url or _base_url()).rstrip("/")
    dataset = dataset or _dataset()
    url = f"{base_url}/{dataset}/sparql"
    timeout = timeout if timeout is not None else _timeout()
    auth = _auth()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                auth=auth,
            )
            resp.raise_for_status()
            data = resp.json()
            bindings = data.get("results", {}).get("bindings", [])
            # Simplify bindings: extract just the "value" from each binding
            return [
                {k: v.get("value") for k, v in binding.items()}
                for binding in bindings
            ]
    except Exception:
        logger.exception("SPARQL query failed (dataset=%s)", dataset)
        return None


async def sparql_ask(
    query: str,
    *,
    dataset: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Optional[bool]:
    """
    Execute a SPARQL ASK query and return boolean result.

    Args:
        query: SPARQL ASK query string
        dataset: Override dataset name
        base_url: Override Fuseki URL
        timeout: Request timeout in seconds

    Returns:
        True/False for ASK result, or None if disabled/error.

    Example:
        exists = await sparql_ask("ASK { ?s a <http://example/Person> }")
    """
    if not _enabled():
        return None

    base_url = (base_url or _base_url()).rstrip("/")
    dataset = dataset or _dataset()
    url = f"{base_url}/{dataset}/sparql"
    timeout = timeout if timeout is not None else _timeout()
    auth = _auth()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                auth=auth,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("boolean", False)
    except Exception:
        logger.exception("SPARQL ASK query failed (dataset=%s)", dataset)
        return None
