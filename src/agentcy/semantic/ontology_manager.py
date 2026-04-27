"""
Ontology version management - tracks and syncs ontology/shapes changes.

This module provides version tracking for SHACL shapes and OWL ontology files.
Version metadata is stored in Couchbase, enabling:
- Change detection via checksum comparison
- Version history tracking
- Automatic re-upload to Fuseki when files change
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, ContextManager, cast

from couchbase.exceptions import DocumentNotFoundException

from agentcy.shared_lib.kv.backoff import with_backoff
from agentcy.shared_lib.kv.protocols import KVCollection
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.orchestrator_core.couch.config import CNames
from agentcy.semantic.fuseki_init import (
    register_ontology,
    register_shapes,
    _load_turtle_file,
    _resolve_path,
    DEFAULT_ONTOLOGY_PATH,
    DEFAULT_SHAPES_PATH,
    ONTOLOGY_GRAPH_URI,
    SHAPES_GRAPH_URI,
)

logger = logging.getLogger(__name__)

# Document keys for version tracking
ONTOLOGY_VERSION_KEY = "semantic::ontology_version"
SHAPES_VERSION_KEY = "semantic::shapes_version"


def _compute_checksum(content: str) -> str:
    """Compute MD5 checksum of content."""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class OntologyManager:
    """
    Manages ontology and SHACL shapes versioning.

    Stores version metadata in Couchbase (graph_markers collection) and
    synchronizes with Fuseki when changes are detected.

    Usage:
        manager = OntologyManager(cb_pool)
        status = await manager.sync_all()
        # Returns: {"ontology": {...}, "shapes": {...}}
    """

    def __init__(self, pool: Optional[DynamicCouchbaseConnectionPool]):
        self._pool = pool

    def _get_version_doc(self, key: str) -> Optional[Dict[str, Any]]:
        """Get a version document from Couchbase."""
        if self._pool is None:
            logger.debug("No Couchbase pool, cannot get version doc")
            return None
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                res = col.get(key)
                return res.content_as[dict] if res else None
        except DocumentNotFoundException:
            return None
        except Exception:
            logger.exception("Failed to get version doc %s", key)
            return None

    def _save_version_doc(self, key: str, doc: Dict[str, Any]) -> bool:
        """Save a version document to Couchbase."""
        if self._pool is None:
            logger.debug("No Couchbase pool, cannot save version doc")
            return False
        try:
            with cast(ContextManager[KVCollection], self._pool.collections(CNames.GRAPH_MARKERS)) as col:
                col.upsert(key, doc)
            return True
        except Exception:
            logger.exception("Failed to save version doc %s", key)
            return False

    def get_ontology_version(self) -> Optional[Dict[str, Any]]:
        """
        Get current ontology version info from Couchbase.

        Returns:
            Dict with version metadata, or None if not tracked.
            Example: {"checksum": "abc123", "version": 1, "path": "...", "updated_at": "..."}
        """
        return self._get_version_doc(ONTOLOGY_VERSION_KEY)

    def get_shapes_version(self) -> Optional[Dict[str, Any]]:
        """
        Get current SHACL shapes version info from Couchbase.

        Returns:
            Dict with version metadata, or None if not tracked.
        """
        return self._get_version_doc(SHAPES_VERSION_KEY)

    async def check_and_sync_ontology(
        self,
        ontology_path: Optional[str] = None,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Check if ontology has changed and sync to Fuseki if needed.

        Args:
            ontology_path: Path to ontology TTL file (default: ONTOLOGY_PATH env)
            force: Force re-upload even if no changes detected

        Returns:
            Dict with sync status and version info.
        """
        ontology_path = ontology_path or os.getenv("ONTOLOGY_PATH", DEFAULT_ONTOLOGY_PATH)
        content = _load_turtle_file(ontology_path)

        if not content:
            return {
                "synced": False,
                "reason": "file_not_found",
                "path": ontology_path,
            }

        current_checksum = _compute_checksum(content)
        stored_version = self.get_ontology_version()
        stored_checksum = stored_version.get("checksum") if stored_version else None

        needs_sync = force or stored_checksum != current_checksum

        if not needs_sync:
            return {
                "synced": False,
                "reason": "no_changes",
                "checksum": current_checksum,
                "version": stored_version.get("version") if stored_version else None,
            }

        # Sync to Fuseki
        success = await register_ontology(ontology_path, force=True)

        if success:
            # Update version in Couchbase
            new_version = (stored_version.get("version", 0) if stored_version else 0) + 1
            version_doc = {
                "checksum": current_checksum,
                "version": new_version,
                "path": ontology_path,
                "graph_uri": ONTOLOGY_GRAPH_URI,
                "updated_at": _now_iso(),
                "_meta": {"type": "ontology_version"},
            }
            self._save_version_doc(ONTOLOGY_VERSION_KEY, version_doc)

            return {
                "synced": True,
                "checksum": current_checksum,
                "version": new_version,
                "previous_version": stored_version.get("version") if stored_version else None,
            }

        return {"synced": False, "reason": "fuseki_upload_failed"}

    async def check_and_sync_shapes(
        self,
        shapes_path: Optional[str] = None,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Check if SHACL shapes have changed and sync to Fuseki if needed.

        Args:
            shapes_path: Path to shapes TTL file (default: SHACL_SHAPES_PATH env)
            force: Force re-upload even if no changes detected

        Returns:
            Dict with sync status and version info.
        """
        shapes_path = shapes_path or os.getenv("SHACL_SHAPES_PATH", DEFAULT_SHAPES_PATH)
        content = _load_turtle_file(shapes_path)

        if not content:
            return {
                "synced": False,
                "reason": "file_not_found",
                "path": shapes_path,
            }

        current_checksum = _compute_checksum(content)
        stored_version = self.get_shapes_version()
        stored_checksum = stored_version.get("checksum") if stored_version else None

        needs_sync = force or stored_checksum != current_checksum

        if not needs_sync:
            return {
                "synced": False,
                "reason": "no_changes",
                "checksum": current_checksum,
                "version": stored_version.get("version") if stored_version else None,
            }

        # Sync to Fuseki
        success = await register_shapes(shapes_path, force=True)

        if success:
            # Update version in Couchbase
            new_version = (stored_version.get("version", 0) if stored_version else 0) + 1
            version_doc = {
                "checksum": current_checksum,
                "version": new_version,
                "path": shapes_path,
                "graph_uri": SHAPES_GRAPH_URI,
                "updated_at": _now_iso(),
                "_meta": {"type": "shapes_version"},
            }
            self._save_version_doc(SHAPES_VERSION_KEY, version_doc)

            return {
                "synced": True,
                "checksum": current_checksum,
                "version": new_version,
                "previous_version": stored_version.get("version") if stored_version else None,
            }

        return {"synced": False, "reason": "fuseki_upload_failed"}

    async def sync_all(self, *, force: bool = False) -> Dict[str, Any]:
        """
        Sync both ontology and SHACL shapes if needed.

        Args:
            force: Force re-upload even if no changes detected

        Returns:
            Dict with results for both ontology and shapes.
        """
        ontology_result = await self.check_and_sync_ontology(force=force)
        shapes_result = await self.check_and_sync_shapes(force=force)

        return {
            "ontology": ontology_result,
            "shapes": shapes_result,
        }
