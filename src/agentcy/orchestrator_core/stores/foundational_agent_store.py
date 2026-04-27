"""
Persistent store for foundational agent state.

This replaces the in-memory state (REGISTRY, PLAN_CACHE, AUDIT_LOGS, PHEROMONES)
in foundational_agents.py with Couchbase-backed persistence, ensuring state
survives process restarts and container lifecycle events.

Key features:
- Registry: Agent registrations per pipeline run
- Plan cache: Plan hash to plan_id deduplication
- Audit logs: Append-only audit trail
- Pheromones: (pipeline_id, plan_id) -> intensity with time-based decay
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, ContextManager, Dict, List, Optional, Tuple, cast

from couchbase.exceptions import DocumentNotFoundException

from agentcy.orchestrator_core.couch.config import CNames
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.shared_lib.kv.backoff import with_backoff
from agentcy.shared_lib.kv.protocols import KVCollection

logger = logging.getLogger(__name__)

# Key format patterns
REGISTRY_KEY_FMT = "agent_reg::{run_id}::{agent_id}"
PLAN_CACHE_KEY_FMT = "plan_cache::{plan_hash_hex}"
AUDIT_LOG_KEY_FMT = "audit::{run_id}::{entry_id}"
PHEROMONE_KEY_FMT = "pheromone::{pipeline_id}::{plan_id}"

# Pheromone decay configuration
PHEROMONE_HALF_LIFE_SECONDS = float(__import__("os").getenv("PHEROMONE_HALF_LIFE_SECONDS", "900"))
PHEROMONE_MIN_VALUE = 0.01  # Below this, treat as zero


class FoundationalAgentStore:
    """
    Couchbase-backed store for foundational agent state.

    Provides persistent storage for:
    - Registry: Agent registrations per pipeline run
    - Plan cache: Hash-based deduplication
    - Audit logs: Append-only entries
    - Pheromones: Time-decayed intensity values
    """

    def __init__(self, pool: Optional[DynamicCouchbaseConnectionPool]) -> None:
        self._pool = pool
        if pool is None:
            logger.warning(
                "FoundationalAgentStore initialized without pool; "
                "falling back to in-memory mode"
            )

    @property
    def is_persistent(self) -> bool:
        """Returns True if backed by Couchbase, False if in-memory fallback."""
        return self._pool is not None

    # ─────────────────────────────────────────────────────────────────────────
    # Registry operations
    # ─────────────────────────────────────────────────────────────────────────

    @with_backoff(msg="foundational_store.registry_upsert")
    def registry_upsert(
        self,
        *,
        run_id: str,
        agent_id: str,
        capabilities: List[str],
        username: str,
        pipeline_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Register or update an agent in the registry for a specific run.

        Returns the document key.
        """
        if self._pool is None:
            raise RuntimeError("Store not configured with Couchbase pool")

        doc_key = REGISTRY_KEY_FMT.format(run_id=run_id, agent_id=agent_id)
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "run_id": run_id,
            "agent_id": agent_id,
            "capabilities": capabilities,
            "username": username,
            "pipeline_id": pipeline_id,
            "registered_at": now,
            "updated_at": now,
            "metadata": metadata or {},
        }

        with cast(
            ContextManager[KVCollection],
            self._pool.collections(CNames.AGENT_STATE_REGISTRY),
        ) as coll:
            coll.upsert(doc_key, doc)

        logger.debug("Registered agent %s for run %s", agent_id, run_id)
        return doc_key

    @with_backoff(msg="foundational_store.registry_get")
    def registry_get(self, *, run_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific agent registration."""
        if self._pool is None:
            return None

        doc_key = REGISTRY_KEY_FMT.format(run_id=run_id, agent_id=agent_id)
        try:
            with cast(
                ContextManager[KVCollection],
                self._pool.collections(CNames.AGENT_STATE_REGISTRY),
            ) as coll:
                res = coll.get(doc_key)
                return res.content_as[dict] if res else None
        except DocumentNotFoundException:
            return None

    @with_backoff(msg="foundational_store.registry_list")
    def registry_list(self, *, run_id: str) -> List[Dict[str, Any]]:
        """List all agents registered for a specific run."""
        if self._pool is None:
            return []

        prefix = f"agent_reg::{run_id}::"
        with self._pool.cluster() as cluster:
            from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_SCOPE
            coll_name = CNames.AGENT_STATE_REGISTRY
            q = (
                f"SELECT META(r).id AS id, r.* "
                f"FROM `{CB_BUCKET}`.`{CB_SCOPE}`.`{coll_name}` r "
                f"WHERE META(r).id LIKE '{prefix}%'"
            )
            rows = cluster.query(q)
            return [dict(row) for row in rows]

    def registry_count(self, *, run_id: str) -> int:
        """Count agents registered for a run."""
        return len(self.registry_list(run_id=run_id))

    # ─────────────────────────────────────────────────────────────────────────
    # Plan cache operations
    # ─────────────────────────────────────────────────────────────────────────

    @with_backoff(msg="foundational_store.plan_cache_get")
    def plan_cache_get(self, *, plan_hash: str) -> Optional[str]:
        """
        Look up a cached plan_id by plan hash.

        Returns the plan_id if found, None otherwise.
        """
        if self._pool is None:
            return None

        # Use hex representation of hash for key safety
        plan_hash_hex = plan_hash[:64] if len(plan_hash) > 64 else plan_hash
        doc_key = PLAN_CACHE_KEY_FMT.format(plan_hash_hex=self._hash_to_hex(plan_hash))

        try:
            with cast(
                ContextManager[KVCollection],
                self._pool.collections(CNames.AGENT_STATE_PLAN_CACHE),
            ) as coll:
                res = coll.get(doc_key)
                doc = res.content_as[dict] if res else None
                return doc.get("plan_id") if doc else None
        except DocumentNotFoundException:
            return None

    @with_backoff(msg="foundational_store.plan_cache_put")
    def plan_cache_put(self, *, plan_hash: str, plan_id: str) -> bool:
        """
        Cache a plan_id by hash.

        Returns True if this was a new entry, False if it already existed.
        """
        if self._pool is None:
            return True  # Pretend success for in-memory fallback

        doc_key = PLAN_CACHE_KEY_FMT.format(plan_hash_hex=self._hash_to_hex(plan_hash))
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "plan_hash": plan_hash,
            "plan_id": plan_id,
            "cached_at": now,
        }

        # Check if already exists
        existing = self.plan_cache_get(plan_hash=plan_hash)
        if existing is not None:
            return False  # Already cached

        with cast(
            ContextManager[KVCollection],
            self._pool.collections(CNames.AGENT_STATE_PLAN_CACHE),
        ) as coll:
            coll.upsert(doc_key, doc)

        logger.debug("Cached plan %s with hash %s...", plan_id, plan_hash[:16])
        return True

    @staticmethod
    def _hash_to_hex(plan_hash: str) -> str:
        """Convert plan hash to safe hex key (max 64 chars)."""
        import hashlib
        return hashlib.sha256(plan_hash.encode()).hexdigest()[:48]

    # ─────────────────────────────────────────────────────────────────────────
    # Audit log operations
    # ─────────────────────────────────────────────────────────────────────────

    @with_backoff(msg="foundational_store.audit_append")
    def audit_append(
        self,
        *,
        run_id: str,
        pipeline_id: str,
        username: str,
        stage: str,
        plan_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Append an entry to the audit log.

        Returns the entry ID.
        """
        if self._pool is None:
            raise RuntimeError("Store not configured with Couchbase pool")

        entry_id = str(uuid.uuid4())
        doc_key = AUDIT_LOG_KEY_FMT.format(run_id=run_id, entry_id=entry_id)
        now = datetime.now(timezone.utc).isoformat()

        doc = {
            "entry_id": entry_id,
            "pipeline_run_id": run_id,
            "pipeline_id": pipeline_id,
            "username": username,
            "stage": stage,
            "plan_id": plan_id,
            "payload": payload or {},
            "logged_at": now,
        }

        with cast(
            ContextManager[KVCollection],
            self._pool.collections(CNames.AGENT_STATE_AUDIT_LOGS),
        ) as coll:
            coll.upsert(doc_key, doc)

        logger.debug("Appended audit entry %s for run %s", entry_id, run_id)
        return entry_id

    @with_backoff(msg="foundational_store.audit_list")
    def audit_list(self, *, run_id: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """List audit log entries for a run, ordered by logged_at."""
        if self._pool is None:
            return []

        prefix = f"audit::{run_id}::"
        with self._pool.cluster() as cluster:
            from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_SCOPE
            coll_name = CNames.AGENT_STATE_AUDIT_LOGS
            q = (
                f"SELECT a.* "
                f"FROM `{CB_BUCKET}`.`{CB_SCOPE}`.`{coll_name}` a "
                f"WHERE META(a).id LIKE '{prefix}%' "
                f"ORDER BY a.logged_at ASC "
                f"LIMIT {limit}"
            )
            rows = cluster.query(q)
            return [dict(row) for row in rows]

    def audit_count(self, *, run_id: Optional[str] = None) -> int:
        """Count audit entries, optionally for a specific run."""
        if run_id:
            return len(self.audit_list(run_id=run_id))
        # Global count (expensive, use sparingly)
        if self._pool is None:
            return 0
        with self._pool.cluster() as cluster:
            from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_SCOPE
            coll_name = CNames.AGENT_STATE_AUDIT_LOGS
            q = f"SELECT COUNT(*) AS cnt FROM `{CB_BUCKET}`.`{CB_SCOPE}`.`{coll_name}`"
            for row in cluster.query(q):
                return int(row.get("cnt", 0))
        return 0

    # ─────────────────────────────────────────────────────────────────────────
    # Pheromone operations (with time-based decay)
    # ─────────────────────────────────────────────────────────────────────────

    @with_backoff(msg="foundational_store.pheromone_get")
    def pheromone_get(self, *, pipeline_id: str, plan_id: str) -> float:
        """
        Get the current pheromone intensity, applying time-based decay.

        Returns a value between 0.0 and 1.0.
        """
        if self._pool is None:
            return 0.0

        doc_key = PHEROMONE_KEY_FMT.format(pipeline_id=pipeline_id, plan_id=plan_id)

        try:
            with cast(
                ContextManager[KVCollection],
                self._pool.collections(CNames.AGENT_STATE_PHEROMONES),
            ) as coll:
                res = coll.get(doc_key)
                doc = res.content_as[dict] if res else None
                if not doc:
                    return 0.0

                # Apply time-based decay
                stored_value = float(doc.get("intensity", 0.0))
                last_update = doc.get("updated_at")
                if last_update:
                    try:
                        last_dt = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
                        age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
                        decay_factor = math.pow(0.5, age_seconds / PHEROMONE_HALF_LIFE_SECONDS)
                        decayed = stored_value * decay_factor
                        return decayed if decayed >= PHEROMONE_MIN_VALUE else 0.0
                    except (ValueError, TypeError):
                        pass
                return stored_value

        except DocumentNotFoundException:
            return 0.0

    @with_backoff(msg="foundational_store.pheromone_update")
    def pheromone_update(
        self,
        *,
        pipeline_id: str,
        plan_id: str,
        delta: float,
        max_value: float = 1.0,
        min_value: float = 0.0,
    ) -> float:
        """
        Update pheromone intensity by delta, applying decay first.

        Returns the new intensity value.
        """
        if self._pool is None:
            return 0.0

        doc_key = PHEROMONE_KEY_FMT.format(pipeline_id=pipeline_id, plan_id=plan_id)
        now = datetime.now(timezone.utc).isoformat()

        # Get current decayed value
        current = self.pheromone_get(pipeline_id=pipeline_id, plan_id=plan_id)

        # Apply delta and clamp
        new_value = max(min_value, min(max_value, current + delta))

        doc = {
            "pipeline_id": pipeline_id,
            "plan_id": plan_id,
            "intensity": new_value,
            "updated_at": now,
        }

        with cast(
            ContextManager[KVCollection],
            self._pool.collections(CNames.AGENT_STATE_PHEROMONES),
        ) as coll:
            coll.upsert(doc_key, doc)

        logger.debug(
            "Pheromone %s/%s: %.3f → %.3f (delta=%.3f)",
            pipeline_id, plan_id, current, new_value, delta
        )
        return new_value

    def pheromone_set(
        self,
        *,
        pipeline_id: str,
        plan_id: str,
        intensity: float,
    ) -> None:
        """Set pheromone intensity to an absolute value."""
        if self._pool is None:
            return

        doc_key = PHEROMONE_KEY_FMT.format(pipeline_id=pipeline_id, plan_id=plan_id)
        now = datetime.now(timezone.utc).isoformat()

        doc = {
            "pipeline_id": pipeline_id,
            "plan_id": plan_id,
            "intensity": max(0.0, min(1.0, intensity)),
            "updated_at": now,
        }

        with cast(
            ContextManager[KVCollection],
            self._pool.collections(CNames.AGENT_STATE_PHEROMONES),
        ) as coll:
            coll.upsert(doc_key, doc)
