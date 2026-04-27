# src/agentcy/pipeline_orchestrator/resource_manager.py
import contextlib
import os
import logging
import asyncio
from agentcy.pipeline_orchestrator.pub_sub.connection_manager import RabbitMQConnectionManager
from typing import Any, AsyncGenerator, Optional
from contextlib import asynccontextmanager
from agentcy.pipeline_orchestrator.couchbase_configs.couchbase_indexes import IndexManager
from agentcy.orchestrator_core.stores.ephemeral_pipeline_store import EphemeralPipelineStore
from agentcy.orchestrator_core.stores.pipeline_store import PipelineStore
from agentcy.orchestrator_core.stores.service_store import ServiceStore
from agentcy.orchestrator_core.stores.agent_registry_store import AgentRegistryStore
from agentcy.orchestrator_core.stores.graph_marker_store import GraphMarkerStore
from agentcy.adapters.aio_pika_bus import AioPikaBus
from agentcy.adapters.couchbase_doc_store import CouchbaseDocStore
from agentcy.orchestrator_core.couch.config import CB_BUCKET, CB_BUCKET_EPHEMERAL, CB_COLLECTIONS, EPHEMERAL_COLLECTIONS
from agentcy.orchestrator_core.couch.pool import DynamicCouchbaseConnectionPool
from agentcy.orchestrator_core.stores.catalog_store import UserCatalogStore
from agentcy.orchestrator_core.stores.foundational_agent_store import FoundationalAgentStore
from agentcy.orchestrator_core.stores.template_store import TemplateStore
from agentcy.semantic.ontology_manager import OntologyManager


logger = logging.getLogger(__name__)

class ResourceManager:
    def __init__(
        self,
        cb_pool:           Optional[DynamicCouchbaseConnectionPool],
        ephemeral_pool:    Optional[DynamicCouchbaseConnectionPool],
        rabbit_mgr:       Optional[RabbitMQConnectionManager]
    ):
        self.rabbit_mgr = rabbit_mgr
        self.cb_pool     = cb_pool
        self.ephemeral   = ephemeral_pool

        self.message_bus = (
            AioPikaBus(rabbit_mgr) if rabbit_mgr else None)
        
        self.ready = asyncio.Event()
        self.service_store  = ServiceStore(cb_pool)           if cb_pool     else None
        self.agent_registry_store = AgentRegistryStore(cb_pool) if cb_pool else None
        self.graph_marker_store = GraphMarkerStore(cb_pool) if cb_pool else None
        self.pipeline_store = PipelineStore(cb_pool)          if cb_pool     else None
        self.ephemeral_store= EphemeralPipelineStore(ephemeral_pool) if ephemeral_pool else None
        self.doc_store = (
            CouchbaseDocStore(self.ephemeral_store) if self.ephemeral_store else None
        )
        self.catalog_user_store = UserCatalogStore(cb_pool) if cb_pool else None
        self.template_store = TemplateStore(cb_pool) if cb_pool else None
        self.foundational_agent_store = FoundationalAgentStore(cb_pool)
        self.ontology_manager = OntologyManager(cb_pool)

        logger.info("ResourceManager ready: rmq=%s cb=%s eph=%s",
                    bool(rabbit_mgr), bool(cb_pool), bool(ephemeral_pool))
        


    @property
    def ready_event(self) -> asyncio.Event:
        return self.ready
    
    async def ping_rabbit(self, timeout: float = 0.6) -> None:
        """Opens a connection (and transient channel if available) within timeout or raises."""
        if not self.rabbit_mgr:
            raise RuntimeError("RabbitMQ not configured")
        conn = await asyncio.wait_for(self.rabbit_mgr.get_connection(), timeout=timeout)  # type: ignore[attr-defined]
        if hasattr(conn, "channel"):
            ch = await asyncio.wait_for(conn.channel(), timeout=max(0.2, timeout - 0.2))
            with contextlib.suppress(Exception):
                await ch.close()
    
    async def ping_cb(self, timeout: float = 0.6) -> None:
        """Borrow a bundle from the persistent pool and attempt a no-op ping."""
        if not self.cb_pool:
            raise RuntimeError("Couchbase pool not configured")
        bundle = self.cb_pool.acquire(timeout=timeout)
        try:
            # _ConnBundle.cluster is a TracedCluster wrapping a real Cluster
            raw = getattr(bundle.cluster, '_cluster', bundle.cluster)
            if hasattr(raw, "ping"):
                raw.ping()
        finally:
            self.cb_pool.release(bundle)
    
    async def ping_cb_ephemeral(self, timeout: float = 0.6) -> None:
        """Same as ping_cb but for the ephemeral pool if configured."""
        if not self.ephemeral:
            return
        bundle = self.ephemeral.acquire(timeout=timeout)
        try:
            raw = getattr(bundle.cluster, '_cluster', bundle.cluster)
            if hasattr(raw, "ping"):
                raw.ping()
        finally:
            self.ephemeral.release(bundle)

@asynccontextmanager
async def resource_manager_context(
    rmq:       bool = True,
    cb:        bool = True,
    ephemeral: bool = True
) -> AsyncGenerator["ResourceManager", None]:

    rabbit_mgr       = None
    cb_pool           = None
    ephemeral_cb_pool = None

    async def _init_cb_pool(
        *,
        bucket_name: str,
        collections_map: dict[str, str],
        min_size: int,
        max_size: int,
        idle_timeout: float,
    ) -> DynamicCouchbaseConnectionPool:
        retries = int(os.getenv("CB_POOL_INIT_RETRIES", "3"))
        delay = float(os.getenv("CB_POOL_INIT_DELAY_SECONDS", "1"))
        for attempt in range(1, retries + 1):
            try:
                return DynamicCouchbaseConnectionPool(
                    bucket_name=bucket_name,
                    collections_map=collections_map,
                    min_size=min_size,
                    max_size=max_size,
                    idle_timeout=idle_timeout,
                )
            except Exception as exc:
                if attempt >= retries:
                    raise
                logger.warning(
                    "Couchbase pool init failed (attempt %d/%d): %s",
                    attempt,
                    retries,
                    exc,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)

        raise RuntimeError("Couchbase pool init retries exhausted")

    try:
        if rmq:
            await RabbitMQConnectionManager.get_connection()
            rabbit_mgr = RabbitMQConnectionManager
            logger.info("RabbitMQ connection initialized.")

        if cb:
            cb_pool = await _init_cb_pool(
                bucket_name=CB_BUCKET,
                collections_map=CB_COLLECTIONS,
                min_size=int(os.getenv("CB_POOL_MIN_SIZE", "2")),
                max_size=int(os.getenv("CB_POOL_MAX_SIZE", "10")),
                idle_timeout=float(os.getenv("CB_POOL_IDLE_TIMEOUT", "60")),
            )
            logger.info("Persistent Couchbase pool initialized.")

        if ephemeral:
            ephemeral_cb_pool = await _init_cb_pool(
                bucket_name=CB_BUCKET_EPHEMERAL,
                collections_map=EPHEMERAL_COLLECTIONS,
                min_size=int(os.getenv("CB_EPHEMERAL_POOL_MIN_SIZE", "1")),
                max_size=int(os.getenv("CB_EPHEMERAL_POOL_MAX_SIZE", "5")),
                idle_timeout=float(os.getenv("CB_EPHEMERAL_POOL_IDLE_TIMEOUT", "60")),
            )
            logger.info("Ephemeral Couchbase pool initialized.")

        rm = ResourceManager(cb_pool, ephemeral_cb_pool, rabbit_mgr) # type: ignore
        rm.ready.set()
        yield rm

    finally:
        if rmq and rabbit_mgr:
            # ─── don’t await a sync close() ───
            if asyncio.iscoroutinefunction(rabbit_mgr.close):
                await rabbit_mgr.close()

        if cb and cb_pool:
            cb_pool.close_all()            # close_all() is sync
            logger.info("Persistent Couchbase pool closed.")

        if ephemeral and ephemeral_cb_pool:
            ephemeral_cb_pool.close_all()
            logger.info("Ephemeral Couchbase pool closed.")
