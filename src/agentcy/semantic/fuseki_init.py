"""
Fuseki initialization module - registers SHACL shapes and OWL ontology on startup.

This module provides automatic registration of semantic layer resources
to Apache Jena Fuseki when FUSEKI_ENABLE=1. The initialization is:
- Idempotent: checks if graphs exist before uploading
- Feature-flagged: respects FUSEKI_ENABLE environment variable
- Non-blocking: failures are logged but don't crash the application
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_SHAPES_PATH = "schemas/plan_draft_shapes.ttl"
DEFAULT_ONTOLOGY_PATH = "schemas/agentcy_ontology.ttl"
SHAPES_GRAPH_URI = "http://agentcy.ai/graphs/shapes"
ONTOLOGY_GRAPH_URI = "http://agentcy.ai/graphs/ontology"


def _enabled() -> bool:
    """Check if Fuseki integration is enabled."""
    raw = os.getenv("FUSEKI_ENABLE", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(os.getenv("FUSEKI_URL"))


def _base_url() -> str:
    """Get Fuseki base URL."""
    return os.getenv("FUSEKI_URL", "http://fuseki:3030").rstrip("/")


def _dataset() -> str:
    """Get Fuseki dataset name."""
    return os.getenv("FUSEKI_DATASET", "agentcy")


def _timeout() -> float:
    """Get HTTP timeout in seconds."""
    raw = os.getenv("FUSEKI_TIMEOUT", "10")
    try:
        return float(raw)
    except ValueError:
        return 10.0


def _auth() -> Optional[tuple[str, str]]:
    """Get optional basic auth credentials."""
    user = os.getenv("FUSEKI_USER", "").strip()
    password = os.getenv("FUSEKI_PASSWORD", "").strip()
    if user and password:
        return user, password
    return None


def _resolve_path(path: str) -> Optional[Path]:
    """Resolve a file path, checking common locations."""
    p = Path(path)
    if p.is_absolute() and p.exists():
        return p

    # Try relative to current working directory
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path

    # Try relative to this module's parent directories
    module_dir = Path(__file__).parent
    for parent in [module_dir, module_dir.parent, module_dir.parent.parent]:
        candidate = parent / path
        if candidate.exists():
            return candidate

    return None


def _load_turtle_file(path: str) -> Optional[str]:
    """Load a Turtle file from disk."""
    resolved = _resolve_path(path)
    if not resolved:
        logger.warning("Turtle file not found: %s", path)
        return None
    try:
        return resolved.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


async def _check_graph_exists(
    graph_uri: str,
    *,
    base_url: Optional[str] = None,
    dataset: Optional[str] = None,
) -> bool:
    """Check if a named graph exists in Fuseki using SPARQL ASK."""
    base_url = (base_url or _base_url()).rstrip("/")
    dataset = dataset or _dataset()
    url = f"{base_url}/{dataset}/sparql"
    query = f"ASK {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
    timeout = _timeout()
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
        logger.debug("Graph existence check failed for %s (may not exist)", graph_uri)
        return False


async def _upload_graph(
    turtle: str,
    graph_uri: str,
    *,
    base_url: Optional[str] = None,
    dataset: Optional[str] = None,
) -> bool:
    """Upload a Turtle graph to Fuseki using PUT (replace entire graph)."""
    base_url = (base_url or _base_url()).rstrip("/")
    dataset = dataset or _dataset()
    url = f"{base_url}/{dataset}/data"
    timeout = _timeout()
    auth = _auth()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.put(
                url,
                content=turtle.encode("utf-8"),
                params={"graph": graph_uri},
                headers={"Content-Type": "text/turtle"},
                auth=auth,
            )
            resp.raise_for_status()
        logger.info("Uploaded graph to %s", graph_uri)
        return True
    except Exception:
        logger.exception("Failed to upload graph to %s", graph_uri)
        return False


async def register_shapes(
    shapes_path: Optional[str] = None,
    *,
    force: bool = False,
) -> bool:
    """
    Register SHACL shapes in Fuseki. Idempotent unless force=True.

    Args:
        shapes_path: Path to shapes TTL file. Defaults to SHACL_SHAPES_PATH env
                     or schemas/plan_draft_shapes.ttl.
        force: If True, re-upload even if graph already exists.

    Returns:
        True if shapes were uploaded or already exist, False on error.
    """
    if not _enabled():
        logger.debug("Fuseki not enabled, skipping shapes registration")
        return False

    shapes_path = shapes_path or os.getenv("SHACL_SHAPES_PATH", DEFAULT_SHAPES_PATH)
    turtle = _load_turtle_file(shapes_path)
    if not turtle:
        logger.warning("No shapes file found at %s", shapes_path)
        return False

    if not force:
        exists = await _check_graph_exists(SHAPES_GRAPH_URI)
        if exists:
            logger.info("SHACL shapes graph already exists, skipping upload")
            return True

    success = await _upload_graph(turtle, SHAPES_GRAPH_URI)
    if success:
        logger.info("SHACL shapes registered at <%s>", SHAPES_GRAPH_URI)
    return success


async def register_ontology(
    ontology_path: Optional[str] = None,
    *,
    force: bool = False,
) -> bool:
    """
    Register OWL ontology in Fuseki. Idempotent unless force=True.

    Args:
        ontology_path: Path to ontology TTL file. Defaults to ONTOLOGY_PATH env
                       or schemas/agentcy_ontology.ttl.
        force: If True, re-upload even if graph already exists.

    Returns:
        True if ontology was uploaded or already exists, False on error.
    """
    if not _enabled():
        logger.debug("Fuseki not enabled, skipping ontology registration")
        return False

    ontology_path = ontology_path or os.getenv("ONTOLOGY_PATH", DEFAULT_ONTOLOGY_PATH)
    turtle = _load_turtle_file(ontology_path)
    if not turtle:
        logger.warning("No ontology file found at %s", ontology_path)
        return False

    if not force:
        exists = await _check_graph_exists(ONTOLOGY_GRAPH_URI)
        if exists:
            logger.info("Ontology graph already exists, skipping upload")
            return True

    success = await _upload_graph(turtle, ONTOLOGY_GRAPH_URI)
    if success:
        logger.info("Ontology registered at <%s>", ONTOLOGY_GRAPH_URI)
    return success


async def initialize_fuseki() -> dict:
    """
    Initialize Fuseki with SHACL shapes and OWL ontology.

    Call this on application startup. The function is safe to call
    multiple times (idempotent) and handles errors gracefully.

    Returns:
        Dict with status of each registration:
        {
            "enabled": bool,      # Whether Fuseki is enabled
            "shapes": bool,       # Whether shapes were registered/exist
            "ontology": bool,     # Whether ontology was registered/exist
        }
    """
    if not _enabled():
        logger.debug("Fuseki not enabled, skipping initialization")
        return {"enabled": False, "shapes": False, "ontology": False}

    logger.info("Initializing Fuseki semantic layer...")
    shapes_ok = await register_shapes()
    ontology_ok = await register_ontology()

    return {
        "enabled": True,
        "shapes": shapes_ok,
        "ontology": ontology_ok,
    }
