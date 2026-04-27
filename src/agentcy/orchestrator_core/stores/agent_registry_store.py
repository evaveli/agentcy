from __future__ import annotations

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, Optional, ContextManager, cast

from couchbase.exceptions import DocumentNotFoundException

from agentcy.shared_lib.kv.backoff import with_backoff
from agentcy.shared_lib.kv.protocols import KVCollection
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_COLLECTIONS, CB_SCOPE, CNames
from agentcy.pydantic_models.agent_registry_model import AgentRegistryEntry, AgentStatus
from agentcy.orchestrator_core.stores.agent_registry_policy import (
    apply_registry_policies,
    load_registry_policy_config,
)

logger = logging.getLogger(__name__)

AGENT_REGISTRY_KEY_FMT = "agent_registry::{username}::{agent_id}"
DEFAULT_TTL_SECONDS = int(os.getenv("AGENT_REGISTRY_TTL_SECONDS", "300"))


class AgentNotFound(Exception):
    pass


class AgentRegistryStore:
    """
    Couchbase-backed registry for live agent instances (heartbeat, status, capabilities).
    Stored in the AGENTS collection with a dedicated key prefix.
    """

    def __init__(
        self,
        pool: DynamicCouchbaseConnectionPool,
        *,
        default_ttl_seconds: Optional[int] = None,
    ) -> None:
        self._pool = pool
        self._default_ttl = (
            DEFAULT_TTL_SECONDS if default_ttl_seconds is None else default_ttl_seconds
        )

    @staticmethod
    def _doc_key(username: str, agent_id: str) -> str:
        return AGENT_REGISTRY_KEY_FMT.format(username=username, agent_id=agent_id)

    def _expiry(self, ttl_seconds: Optional[int]) -> Optional[timedelta]:
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        if ttl and ttl > 0:
            return timedelta(seconds=ttl)
        return None

    def _fetch_all(self, *, username: str) -> list[Dict[str, Any]]:
        prefix = self._doc_key(username, "")
        bucket_name = CB_BUCKET
        scope_name = CB_SCOPE
        agents_col_name = CB_COLLECTIONS[CNames.AGENTS]
        q = (
            "SELECT META(a).id AS id, a.* "
            f"FROM `{bucket_name}`.`{scope_name}`.`{agents_col_name}` a "
            f"WHERE META(a).id LIKE '{prefix}%'"
        )

        bundle = self._pool.acquire()
        try:
            rows = bundle.cluster.query(q)
        finally:
            self._pool.release(bundle)

        docs: list[Dict[str, Any]] = []
        for row in rows:
            doc = dict(row)
            doc_id = doc.pop("id", None)
            if doc_id and "agent_id" not in doc:
                doc["agent_id"] = doc_id[len(prefix):]
            docs.append(doc)
        return docs

    def _get_raw(self, *, username: str, agent_id: str) -> Optional[Dict[str, Any]]:
        doc_key = self._doc_key(username, agent_id)
        try:
            with cast(
                ContextManager[KVCollection], self._pool.collections(CNames.AGENTS)
            ) as agents:
                res = agents.get(doc_key)
        except DocumentNotFoundException:
            return None
        return res.content_as[dict] if res is not None else None

    def _apply_policies(
        self,
        docs: list[Dict[str, Any]],
        *,
        include_coverage: bool,
        coverage_context: Optional[list[Dict[str, Any]]] = None,
    ) -> list[Dict[str, Any]]:
        config = load_registry_policy_config()
        if not config.enable:
            return docs
        return apply_registry_policies(
            docs,
            config=config,
            include_coverage=include_coverage,
            coverage_context=coverage_context,
        )

    @staticmethod
    def _doc_status(doc: Dict[str, Any]) -> str:
        policy = doc.get("policy") if isinstance(doc.get("policy"), dict) else {}
        status = policy.get("effective_status") or doc.get("status") or ""
        if isinstance(status, AgentStatus):
            return status.value
        return str(status).strip().lower()

    @staticmethod
    def _is_stale(doc: Dict[str, Any]) -> bool:
        policy = doc.get("policy") if isinstance(doc.get("policy"), dict) else {}
        if isinstance(policy.get("stale"), bool):
            return bool(policy.get("stale"))
        return False

    @with_backoff(msg="agent_registry.upsert")
    def upsert(
        self,
        *,
        username: str,
        entry: AgentRegistryEntry,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        expiry = self._expiry(ttl_seconds)
        effective_ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        payload = entry.model_copy(
            update={
                "owner": entry.owner or username,
                "last_heartbeat": now,
                "registered_at": entry.registered_at or now,
            }
        )
        if expiry and effective_ttl:
            payload = payload.model_copy(
                update={"expires_at": now + timedelta(seconds=effective_ttl)}
            )

        doc_key = self._doc_key(username, entry.agent_id)
        with cast(
            ContextManager[KVCollection], self._pool.collections(CNames.AGENTS)
        ) as agents:
            if expiry:
                agents.upsert(doc_key, payload.model_dump(mode="json"), expiry=expiry)
            else:
                agents.upsert(doc_key, payload.model_dump(mode="json"))
        logger.info("Upserted agent registry entry %s for user=%s", entry.agent_id, username)
        return doc_key

    @with_backoff(msg="agent_registry.get")
    def get(self, *, username: str, agent_id: str) -> Optional[Dict[str, Any]]:
        doc = self._get_raw(username=username, agent_id=agent_id)
        if not doc:
            return doc
        config = load_registry_policy_config()
        if not config.enable:
            return doc
        try:
            # Optionally enrich with coverage using full registry context.
            coverage_context = None
            if config.include_coverage:
                coverage_context = self._fetch_all(username=username)
            updated = self._apply_policies(
                [doc],
                include_coverage=config.include_coverage,
                coverage_context=coverage_context,
            )
            return updated[0] if updated else doc
        except Exception:
            logger.warning(
                "Agent registry policy enrichment failed for user=%s agent=%s; returning raw document",
                username,
                agent_id,
                exc_info=True,
            )
            return doc

    @with_backoff(msg="agent_registry.delete")
    def delete(self, *, username: str, agent_id: str) -> None:
        doc_key = self._doc_key(username, agent_id)
        with cast(
            ContextManager[KVCollection], self._pool.collections(CNames.AGENTS)
        ) as agents:
            agents.remove(doc_key)
        logger.info("Deleted agent registry entry %s for user=%s", agent_id, username)

    @with_backoff(msg="agent_registry.heartbeat")
    def heartbeat(
        self,
        *,
        username: str,
        agent_id: str,
        status: Optional[AgentStatus] = None,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Heartbeats are on the hot path; avoid policy enrichment that can trigger
        # expensive registry scans and is not needed to update liveness metadata.
        doc = self._get_raw(username=username, agent_id=agent_id)
        if not doc:
            raise AgentNotFound(f"Agent {agent_id} not registered for {username}")

        now = datetime.now(timezone.utc)
        doc["last_heartbeat"] = now.isoformat()
        if status:
            doc["status"] = status.value
        if metadata:
            current = doc.get("metadata") or {}
            current.update(metadata)
            doc["metadata"] = current

        expiry = self._expiry(ttl_seconds)
        effective_ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        if expiry and effective_ttl:
            doc["expires_at"] = (now + timedelta(seconds=effective_ttl)).isoformat()
        elif ttl_seconds is not None and ttl_seconds <= 0:
            doc.pop("expires_at", None)

        doc_key = self._doc_key(username, agent_id)
        with cast(
            ContextManager[KVCollection], self._pool.collections(CNames.AGENTS)
        ) as agents:
            if expiry:
                agents.upsert(doc_key, doc, expiry=expiry)
            else:
                agents.upsert(doc_key, doc)
        return doc

    @with_backoff(msg="agent_registry.list")
    def list(
        self,
        *,
        username: str,
        service_name: Optional[str] = None,
        capability: Optional[str] = None,
        status: Optional[AgentStatus | str] = None,
        tags: Optional[Iterable[str]] = None,
    ) -> list[Dict[str, Any]]:
        docs = self._fetch_all(username=username)
        config = load_registry_policy_config()
        if config.enable:
            docs = self._apply_policies(docs, include_coverage=True, coverage_context=docs)

        status_value = status.value if isinstance(status, AgentStatus) else status
        status_value = str(status_value).strip().lower() if status_value else None
        tag_set = set(tags) if tags else set()

        out: list[Dict[str, Any]] = []
        for doc in docs:
            if config.enable and config.filter_stale and self._is_stale(doc):
                continue
            if service_name and doc.get("service_name") != service_name:
                continue
            if capability and capability not in (doc.get("capabilities") or []):
                continue
            if status_value and self._doc_status(doc) != status_value:
                continue
            if tag_set and not tag_set.issubset(set(doc.get("tags") or [])):
                continue
            out.append(doc)

        return out
