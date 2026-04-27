# src/agentcy/orchestrator_core/stores/service_store.py
import uuid, logging
from typing import Dict, List, Optional, cast, ContextManager
from agentcy.shared_lib.kv.backoff import with_backoff
from agentcy.shared_lib.kv.protocols import KVCollection
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.pydantic_models.service_registration_model import ServiceRegistration
from agentcy.orchestrator_core.couch.config import (
CB_BUCKET, 
CB_COLLECTIONS, 
CB_SCOPE,
CNames
)
logger = logging.getLogger(__name__)
SERVICE_KEY_FMT = "service::{username}::{service_id}"

class ServiceStore:
    """
    CRUD façade over Couchbase, using DynamicCouchbaseConnectionPool + KVCollection.
    """

    def __init__(self, pool: DynamicCouchbaseConnectionPool):
        self._pool = pool

    @staticmethod
    def _doc_key(username: str, service_id: str|uuid.UUID) -> str:
        return SERVICE_KEY_FMT.format(username=username, service_id=service_id)

    @with_backoff(msg="service_store.upsert")
    def upsert(self, username: str, svc: ServiceRegistration) -> str:
        key = self._doc_key(username, svc.service_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CB_COLLECTIONS[CNames.AGENTS])) as agents:    # agents: KVCollection
            agents.upsert(key, svc.model_dump(mode="json"))
        logger.info("Upserted service %s for user=%s", svc.service_id, username)
        return str(svc.service_id)

    @with_backoff(msg="service_store.get")
    def get(self, username: str, service_id: str) -> Optional[dict]:
        key = self._doc_key(username, service_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CB_COLLECTIONS[CNames.AGENTS])) as agents:
            res = agents.get(key)
        return res.content_as[dict] if res is not None else None

    @with_backoff(msg="service_store.delete")
    def delete(self, username: str, service_id: str) -> None:
        key = self._doc_key(username, service_id)
        with cast(ContextManager[KVCollection], self._pool.collections(CB_COLLECTIONS[CNames.AGENTS])) as agents:
            agents.remove(key)
        logger.info("Deleted service %s for user=%s", service_id, username)

    # ----------------------------------------------------------------------- #
    # private helper: actually runs the N1QL and returns raw rows
    # ----------------------------------------------------------------------- #
    @with_backoff(msg="service_store._list_ids")
    def _list_ids(self, username: str) -> List[Dict[str, str]]:
        """
        Internal: list all service IDs + names for the given user.
        """
        prefix = self._doc_key(username, "")
        # derive bucket, scope, collection name
        bucket_name     = CB_BUCKET
        scope_name      = CB_SCOPE
        agents_col_name = CB_COLLECTIONS[CNames.AGENTS]

        q = (
            "SELECT META(a).id AS id, a.service_name "
            f"FROM `{bucket_name}`.`{scope_name}`.`{agents_col_name}` a "
            f"WHERE META(a).id LIKE '{prefix}%'"
        )

        # we only need the cluster handle here to run the N1QL
        # so we borrow one bundle, run the query, then release it
        bundle = self._pool.acquire()
        try:
            rows = bundle.cluster.query(q)
        finally:
            self._pool.release(bundle)

        # strip off the prefix and return just id + name
        return [
            {
                "service_id": row["id"][len(prefix):],
                "service_name": row["service_name"],
            }
            for row in rows
        ]

    # ----------------------------------------------------------------------- #
    # public API: this is what your router calls
    # ----------------------------------------------------------------------- #
    def list_all(self, username: str) -> List[Dict[str, str]]:
        """
        Returns a list of all services for `username`, each as:
          { "service_id": "...", "service_name": "..." }
        """
        return self._list_ids(username)
