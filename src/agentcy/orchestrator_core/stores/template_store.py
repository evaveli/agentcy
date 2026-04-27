"""
Couchbase-backed CRUD store for AgentTemplate documents.

Key format:  ``agent_template::{username}::{template_id}``
Collection:  ``agent_templates`` (logical name ``AGENT_TEMPLATES``)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from couchbase.exceptions import DocumentNotFoundException

from agentcy.shared_lib.kv.backoff import with_backoff
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.orchestrator_core.couch.config import CNames

logger = logging.getLogger(__name__)

# Fields that callers may sort by.  Anything else is rejected.
_SORTABLE_FIELDS = frozenset({
    "name", "display_name", "category", "version",
    "created_at", "updated_at", "enabled",
})

# Alphanumeric + hyphens/underscores only – rejects N1QL metacharacters.
_SAFE_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_identifier(value: str, label: str) -> str:
    """Ensure a string is safe to embed in a document key (no injection)."""
    if not value or not _SAFE_ID.match(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


class TemplateStore:
    """CRUD operations for agent templates stored in Couchbase."""

    KEY_FMT = "agent_template::{username}::{template_id}"

    def __init__(self, pool: DynamicCouchbaseConnectionPool):
        self._pool = pool

    def _col(self):
        return self._pool.collection(CNames.AGENT_TEMPLATES)

    def _key(self, username: str, template_id: str) -> str:
        return self.KEY_FMT.format(username=username, template_id=template_id)

    # ──────────────────────────────────────────────────────────────────
    # Upsert
    # ──────────────────────────────────────────────────────────────────
    @with_backoff(msg="template_store.upsert")
    def upsert(self, *, username: str, template: Dict[str, Any]) -> str:
        template_id = template.get("template_id", "")
        if not template_id:
            raise ValueError("template must include 'template_id'")
        _validate_identifier(username, "username")
        _validate_identifier(template_id, "template_id")

        key = self._key(username, template_id)

        # Preserve created_at from the existing document if this is an update.
        now = _now_iso()
        created_at = now
        try:
            existing = self._col().get(key)
            existing_doc = existing.content_as[dict]
            created_at = (
                existing_doc.get("_meta", {}).get("created_at")
                or existing_doc.get("created_at")
                or now
            )
        except DocumentNotFoundException:
            pass

        doc = {
            **template,
            "username": username,
            "_meta": {"created_at": created_at, "updated_at": now},
        }
        self._col().upsert(key, doc)
        logger.debug("template_store.upsert key=%s", key)
        return template_id

    # ──────────────────────────────────────────────────────────────────
    # Get
    # ──────────────────────────────────────────────────────────────────
    @with_backoff(msg="template_store.get")
    def get(self, *, username: str, template_id: str) -> Optional[Dict[str, Any]]:
        key = self._key(username, template_id)
        try:
            result = self._col().get(key)
            return result.content_as[dict]
        except DocumentNotFoundException:
            return None

    # ──────────────────────────────────────────────────────────────────
    # Delete
    # ──────────────────────────────────────────────────────────────────
    @with_backoff(msg="template_store.delete")
    def delete(self, *, username: str, template_id: str) -> bool:
        key = self._key(username, template_id)
        try:
            self._col().remove(key)
            return True
        except DocumentNotFoundException:
            return False

    # ──────────────────────────────────────────────────────────────────
    # List (with optional filters)
    # ──────────────────────────────────────────────────────────────────
    @with_backoff(msg="template_store.list")
    def list(
        self,
        *,
        username: str,
        category: Optional[str] = None,
        capability: Optional[str] = None,
        enabled: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
    ) -> List[Dict[str, Any]]:
        _validate_identifier(username, "username")
        prefix = f"agent_template::{username}::"
        results = self._list_by_prefix(
            prefix=prefix,
            category=category,
            capability=capability,
            enabled=enabled,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return results

    def _list_by_prefix(
        self,
        *,
        prefix: str,
        category: Optional[str] = None,
        capability: Optional[str] = None,
        enabled: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
    ) -> List[Dict[str, Any]]:
        """Scan by key prefix with optional filters using parameterized N1QL."""
        from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_SCOPE, get_collection_name

        collection_name = get_collection_name(CNames.AGENT_TEMPLATES)

        # Build parameterized query to prevent N1QL injection.
        conditions = ["META().id LIKE $prefix"]
        params: Dict[str, Any] = {"prefix": f"{prefix}%"}

        if category is not None:
            conditions.append("t.category = $category")
            params["category"] = category
        if capability is not None:
            conditions.append(
                "ANY c IN t.capabilities SATISFIES c = $capability END"
            )
            params["capability"] = capability
        if enabled is not None:
            conditions.append("t.enabled = $enabled")
            params["enabled"] = enabled

        where_clause = " AND ".join(conditions)

        order_clause = ""
        if sort_by:
            if sort_by not in _SORTABLE_FIELDS:
                raise ValueError(
                    f"Invalid sort_by field: {sort_by!r}. "
                    f"Allowed: {', '.join(sorted(_SORTABLE_FIELDS))}"
                )
            direction = "ASC" if sort_order.lower() == "asc" else "DESC"
            order_clause = f"ORDER BY t.`{sort_by}` {direction}"

        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT $limit"
            params["limit"] = limit

        offset_clause = ""
        if offset:
            offset_clause = "OFFSET $offset"
            params["offset"] = offset

        statement = (
            f"SELECT t.*, META().id AS _doc_key "
            f"FROM `{CB_BUCKET}`.`{CB_SCOPE}`.`{collection_name}` t "
            f"WHERE {where_clause} "
            f"{order_clause} {limit_clause} {offset_clause}"
        )

        with self._pool.cluster() as cluster:
            rows = cluster.query(statement, named_parameters=params)
            return [dict(row) for row in rows]

    # ──────────────────────────────────────────────────────────────────
    # Count
    # ──────────────────────────────────────────────────────────────────
    @with_backoff(msg="template_store.count")
    def count(self, *, username: str, enabled: Optional[bool] = None) -> int:
        from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_SCOPE, get_collection_name

        _validate_identifier(username, "username")
        collection_name = get_collection_name(CNames.AGENT_TEMPLATES)
        prefix = f"agent_template::{username}::"

        conditions = ["META().id LIKE $prefix"]
        params: Dict[str, Any] = {"prefix": f"{prefix}%"}

        if enabled is not None:
            conditions.append("t.enabled = $enabled")
            params["enabled"] = enabled

        where_clause = " AND ".join(conditions)
        statement = (
            f"SELECT COUNT(*) AS cnt "
            f"FROM `{CB_BUCKET}`.`{CB_SCOPE}`.`{collection_name}` t "
            f"WHERE {where_clause}"
        )

        with self._pool.cluster() as cluster:
            for row in cluster.query(statement, named_parameters=params):
                return int(row.get("cnt", 0))
        return 0
