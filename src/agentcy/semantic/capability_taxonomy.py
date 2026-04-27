"""
Capability hierarchy for semantic matching.

Defines parent–child relationships between capabilities so that an agent
with a specialized capability (e.g. ``file_read``) is recognised as
satisfying a broader requirement (e.g. ``data_read``).

The default hierarchy is a simple ``child → parent`` mapping.  It can be
overridden entirely by pointing ``CAPABILITY_HIERARCHY_PATH`` to a JSON
file with the same structure.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

_MAX_DEPTH = 10

# ── Default Hierarchy ────────────────────────────────────────────────

_DEFAULT_HIERARCHY: Dict[str, str] = {
    # I/O capabilities
    "data_read": "io_capability",
    "data_write": "io_capability",
    "api_call": "io_capability",
    "file_read": "data_read",
    "file_write": "data_write",
    "db_read": "data_read",
    "db_write": "data_write",
    # Processing capabilities
    "transform": "processing",
    "validate": "processing",
    "filter": "processing",
    "aggregate": "processing",
    "parse": "transform",
    "normalize": "transform",
    # Analysis capabilities
    "analyze": "analysis",
    "ml_inference": "analysis",
    "statistics": "analysis",
    # Integration capabilities
    "http_request": "integration",
    "webhook": "integration",
    "queue_publish": "integration",
    "queue_consume": "integration",
}


# ── Public API ───────────────────────────────────────────────────────


def load_hierarchy() -> Dict[str, str]:
    """Return the capability hierarchy (child → parent mapping).

    Loads from ``CAPABILITY_HIERARCHY_PATH`` env var if set (JSON file),
    otherwise returns the built-in default hierarchy.
    """
    path = os.getenv("CAPABILITY_HIERARCHY_PATH")
    if path:
        try:
            with open(path) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            logger.warning("Failed to load hierarchy from %s, using defaults", path, exc_info=True)
    return dict(_DEFAULT_HIERARCHY)


def expand_capabilities(
    capabilities: Set[str],
    hierarchy: Optional[Dict[str, str]] = None,
) -> Set[str]:
    """Expand a set of capabilities to include all ancestors.

    Example::

        >>> expand_capabilities({"file_read"})
        {"file_read", "data_read", "io_capability"}

    Unknown capabilities are kept as-is (no ancestors added).
    """
    if hierarchy is None:
        hierarchy = load_hierarchy()
    result: Set[str] = set()
    for cap in capabilities:
        key = cap.lower().strip()
        result.add(key)
        visited: Set[str] = {key}
        current = key
        for _ in range(_MAX_DEPTH):
            parent = hierarchy.get(current)
            if not parent or parent in visited:
                break
            result.add(parent)
            visited.add(parent)
            current = parent
    return result


def get_children(
    capability: str,
    hierarchy: Optional[Dict[str, str]] = None,
) -> Set[str]:
    """Get all descendants of a capability (recursive).

    Example::

        >>> get_children("data_read")
        {"file_read", "db_read"}
    """
    if hierarchy is None:
        hierarchy = load_hierarchy()
    target = capability.lower().strip()
    # Build reverse index: parent → children
    children_map: Dict[str, Set[str]] = {}
    for child, parent in hierarchy.items():
        children_map.setdefault(parent, set()).add(child)

    result: Set[str] = set()
    queue = list(children_map.get(target, []))
    visited: Set[str] = {target}
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        result.add(current)
        queue.extend(children_map.get(current, []))
    return result
