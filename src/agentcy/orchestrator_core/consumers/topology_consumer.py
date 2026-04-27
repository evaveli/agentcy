# src/agentcy/orchestrator_core/consumers/topology_consumer.py
import asyncio
import json
import logging
from typing import cast

from aio_pika import ExchangeType
import aio_pika
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.orchestrator_core.utils import ensure_topology
from agentcy.api_service.dependecies import COMMAND_EXCHANGE_NAME

log = logging.getLogger(__name__)

# Serialize ensure_topology per (username/pipeline_id)
_topology_locks: dict[str, asyncio.Lock] = {}


async def pipeline_registered_consumer(rm: ResourceManager):
    """
    Listens to events.pipeline_registered, loads the stored pipeline config,
    and calls `ensure_topology` exactly once per event.
    A fresh channel is used for declarations and the event is ACKed only after success.
    """

    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        log.warning("register_pipeline_consumer: no RabbitMQ manager; skipping consumer.")
        return
    async with rabbit_mgr.get_channel() as ch:
        await ch.set_qos(prefetch_count=10)

        exch = await ch.declare_exchange(
            COMMAND_EXCHANGE_NAME,
            ExchangeType.TOPIC,
            durable=True,
        )

        queue = await ch.declare_queue("events.pipeline_registered", durable=True)
        await queue.bind(exch, routing_key="events.pipeline_registered")

        log.info("Topology consumer ready – waiting for events…")

        async with queue.iterator() as it:
            async for msg in it:
                # Ack only after a successful topology ensure; requeue on failure
                async with msg.process(requeue=True):
                    evt = json.loads(msg.body)
                    user = evt["username"]
                    pipeline_id = evt["pipeline_id"]
                    config_key = evt.get("config_key", "<unknown>")

                    log.info("↪︎ pipeline_registered %s/%s/%s", user, pipeline_id, config_key)

                    store = rm.pipeline_store
                    if store is None:
                        log.error("register_pipeline_consumer: pipeline_store not configured; cannot persist pipelines.")
                        return

                    # Fetch the *final* config that register_pipeline_consumer wrote
                    cfg = store.read(user, pipeline_id)
                    if cfg is None:
                        log.error("Config not found for %s/%s", user, pipeline_id)
                        # Raising inside msg.process(requeue=True) will NACK & requeue
                        raise RuntimeError(f"missing config for {user}/{pipeline_id}")

                    log.info("Loaded pipeline config for %s/%s; ensuring topology…", user, pipeline_id)

                    # Serialize per pipeline; use a fresh channel for declarations to avoid channel reuse issues
                    lock_key = f"{user}/{pipeline_id}"
                    lock = _topology_locks.setdefault(lock_key, asyncio.Lock())
                    async with lock:
                        async with rabbit_mgr.get_channel() as topo_ch:
                            await ensure_topology(cfg, cast(aio_pika.Channel, topo_ch))

                    log.info("Topology successfully ensured for %s/%s", user, pipeline_id)
