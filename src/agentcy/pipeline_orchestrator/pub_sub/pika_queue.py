# src/agentcy/pipeline_orchestrator/pub_sub/pika_queue.py

import asyncio
import os
from importlib.metadata import entry_points
import json
from typing import Dict, Any, Callable, List, Union, Optional, DefaultDict, cast
from collections import defaultdict
import logging

import aio_pika
import aiormq
from aio_pika import ExchangeType
from aio_pika.abc import AbstractIncomingMessage, AbstractChannel
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.agent_runtime.forwarder import ForwarderInterface

from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState, TaskStatus

logger = logging.getLogger(__name__)

class AsyncPipelineConsumerManager:
    def __init__(self, final_config: Dict[str, Any], rm: ResourceManager):
        """
        :param final_config: The dictionary your pipeline code produced (with 'queues', etc.)
        """
        self.final_config = final_config
        self.rm = rm
        self.queue_handlers: Dict[str, Callable] = {}

        # upstream aggregation store: run+task → {from_task: envelope}
        self.aggregator_store: DefaultDict[str, DefaultDict[str, Dict[str, Any]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self.required_upstreams: Dict[str, List[str]] = {
            task: list(metadata.get("required_steps", []))
            for task, metadata in final_config.get("fan_in_metadata", {}).items()
        }

        self.consumer_tasks: List[asyncio.Task] = []
        self.shutdown_event = asyncio.Event()

        self.forwarder: Optional[ForwarderInterface] = None

    def register_queue_handler(self, queue_name: str, handler_func: Callable):
        """Register a custom async handler to process messages from 'queue_name'."""
        self.queue_handlers[queue_name] = handler_func

    def register_forwarder(self, forwarder: ForwarderInterface):
        self.forwarder = forwarder
        logger.info("Forwarder registered")

    async def _wait_if_paused(self, payload: Dict[str, Any]) -> None:
        run_id = payload.get("pipeline_run_id")
        username = payload.get("username")
        pipeline_id = payload.get("pipeline_id")
        store = getattr(self.rm, "ephemeral_store", None)
        if not (run_id and username and pipeline_id and store):
            return
        poll = float(os.getenv("PIPELINE_PAUSE_POLL_SECONDS", "0.5"))
        while True:
            run_doc = store.read_run(username, pipeline_id, run_id)
            if not isinstance(run_doc, dict):
                return
            if not run_doc.get("paused"):
                return
            status = str(run_doc.get("status") or "").upper()
            if status in ("COMPLETED", "FAILED"):
                return
            logger.info("Pipeline run %s paused; holding task delivery", run_id)
            await asyncio.sleep(poll)

    def _auto_detect_forwarder(self):
        """Try to find a forwarder via entry points (optional)."""
        if self.forwarder is not None:
            return
        try:
            eps = entry_points()
            # modern importlib.metadata uses .select(); .get() may not exist
            candidates = []
            try:
                candidates = list(eps.select(group="pipeline.forwarder"))  # py3.10+
            except Exception:
                candidates = list(eps.get("pipeline.forwarder", []))  # fallback

            if candidates:
                forwarder_cls = candidates[0].load()
                self.forwarder = forwarder_cls()
                logger.info("Auto-detected forwarder: %s", forwarder_cls)
            else:
                logger.warning("No forwarder plugin found via entry points.")
        except Exception as e:
            logger.error("Error auto-detecting forwarder: %s", e)

    def _make_next_task_state(
        self,
        to_task: str,
        base_msg: Dict[str, Any],
        aggregated: Optional[Dict[str, Any]] = None,
    ) -> TaskState:
        """
        Build a TaskState for the *next* task using the inbound message as the payload.
        Falls back to final_config for IDs if the inbound message doesn't carry them.
        """
        task_meta = self.final_config.get("task_dict", {}).get(to_task, {})
        service_name = task_meta.get("available_services", "")

        # input for the microservice
        data: Dict[str, Any]
        if aggregated is not None:
            data = {"upstreams": aggregated}
        else:
            data = {"from_task": base_msg.get("from_task"), "payload": base_msg}

        return TaskState(
            status=TaskStatus.PENDING,       # forwarder will transition to RUNNING
            attempts=0,
            error=None,
            result=None,
            output_ref="",                   # empty string is fine for “not yet persisted”
            is_final_task=False,             # forwarder can decide this later
            pipeline_run_id=base_msg["pipeline_run_id"],
            task_id=to_task,
            username=base_msg.get("username", ""),
            pipeline_config_id=(
                base_msg.get("pipeline_config_id")
                or self.final_config.get("pipeline_run_config_id", "")
                or ""
            ),
            pipeline_id=(
                base_msg.get("pipeline_id")
                or self.final_config.get("pipeline_id", "")
                or ""
            ),
            service_name=service_name,
            data=data,
        )

    async def _dispatch_to_handler(
        self,
        handler: Callable,
        message: AbstractIncomingMessage,
        qinfo: Dict[str, Any],
        payload: Dict[str, Any],
    ):
        """
        Back-compat dispatcher:
        1) (message, qinfo, payload)
        2) (message, qinfo)
        3) (payload, qinfo)
        """
        try:
            return await handler(message, qinfo, payload)   # new preferred signature
        except TypeError:
            try:
                return await handler(message, qinfo)        # legacy signature
            except TypeError:
                return await handler(payload, qinfo)        # very old/custom signature

    async def start_consumers(self):
        """
        Dynamically iterate over final_config["queues"]
        and create async consumers for each queue.
        """
        queues_config: Dict[str, Any] = self.final_config.get("queues", {})
        for queue_name, qinfo in queues_config.items():
            logger.debug("Creating consumer for queue='%s' qinfo=%s", queue_name, qinfo)
            task = asyncio.create_task(self._consume_queue(queue_name, qinfo))
            self.consumer_tasks.append(task)

    async def _setup_exchange_binding(
        self,
        channel: AbstractChannel,
        queue_obj: aio_pika.Queue,
        qinfo: Dict[str, Any],
    ):
        """
        Declare an exchange and bind the given queue to it based on rabbitmq_configs.
        Expects the mini-config to have per-run queue names and routing keys already.
        Falls back to building a per-run routing key if missing.
        """
        queue_name = qinfo["queue_name"]

        # Find the rabbit config entry for this (already-suffixed) queue
        rabbit_configs = self.final_config.get("rabbitmq_configs", [])
        cfg = next((c for c in rabbit_configs if c.get("rabbitmq", {}).get("queue") == queue_name), None)
        if not cfg:
            logger.warning("No exchange config found for queue '%s'. Skipping binding.", queue_name)
            return

        rb = cfg["rabbitmq"]
        exchange_name = rb.get("exchange")
        exchange_type_str = (rb.get("exchange_type") or "direct").lower()

        exchange_type = getattr(ExchangeType, exchange_type_str.upper(), ExchangeType.DIRECT)

        # Prefer the routing_key from the mini-config (already run-suffixed).
        routing_key = rb.get("routing_key")

        # Fallback: rebuild a sane key mirroring publisher logic
        if not routing_key:
            rk_base = rb.get("routing_key") or rb.get("queue") or queue_name
            run_id = self.final_config.get("run_id")
            if qinfo.get("per_run") and run_id:
                routing_key = f"{rk_base}.{run_id}"
            else:
                routing_key = rk_base

        # Track if we discover a pre-existing exchange type (e.g., topic) to avoid precondition errors
        existing_type = None

        # Try to tear down any stale exchange (type mismatches from prior runs) using a separate channel
        rabbit_mgr = getattr(self.rm, "rabbit_mgr", None) or getattr(self.rm, "rabbit_conn", None)
        if rabbit_mgr is not None:
            try:
                async with rabbit_mgr.get_channel() as cleanup_ch:
                    try:
                        await cleanup_ch.declare_exchange(exchange_name, type=exchange_type, durable=True)
                    except aiormq.exceptions.ChannelPreconditionFailed as exc:
                        msg = str(exc).lower()
                        if "current is 'topic'" in msg:
                            existing_type = ExchangeType.TOPIC
                        elif "current is 'fanout'" in msg:
                            existing_type = ExchangeType.FANOUT
                        elif "current is 'direct'" in msg:
                            existing_type = ExchangeType.DIRECT
                        try:
                            await cleanup_ch.exchange_delete(exchange_name, if_unused=False, if_empty=False)
                        except Exception:
                            pass
                        # Channel may be closed after a precondition failure; open a fresh one to recreate.
                        if getattr(cleanup_ch, "is_closed", False):
                            async with rabbit_mgr.get_channel() as recreate_ch:
                                await recreate_ch.declare_exchange(exchange_name, type=existing_type or exchange_type, durable=True)
                        else:
                            await cleanup_ch.declare_exchange(exchange_name, type=existing_type or exchange_type, durable=True)
                    except Exception:
                        pass
            except Exception:
                pass

        # If we detected an existing type, align to it to avoid PRECONDITION failures.
        if existing_type and existing_type != exchange_type:
            exchange_type = existing_type
            if exchange_type == ExchangeType.TOPIC and not routing_key:
                routing_key = queue_name

        # Declare & bind on the active channel
        exchange_obj = await channel.declare_exchange(exchange_name, type=exchange_type, durable=True)
        logger.info(
            "Binding queue '%s' → exchange '%s' (%s) rk='%s'",
            queue_name, exchange_name, exchange_type.name.lower(), routing_key,
        )
        await queue_obj.bind(exchange_obj, routing_key=routing_key)

    async def _consume_queue(self, queue_name: str, qinfo: Dict[str, Any]):
        """
        Connect to or reuse the existing connection, open a channel, declare the queue, and consume.
        """
        is_per_run = bool(qinfo.get("per_run", False))

        # Use the connection from rm (which should be pre-initialized)
        rabbit_mgr = getattr(self.rm, "rabbit_mgr", None) or getattr(self.rm, "rabbit_conn", None)
        if rabbit_mgr is None:
            logger.error("ResourceManager.rabbit_mgr is not initialized. Cannot consume queue '%s'.", queue_name)
            return

        async with rabbit_mgr.get_channel() as channel:
            await channel.set_qos(prefetch_count=1)

            # Explicit kwargs (avoid typed-dict issues)
            arguments = {"x-expires": 900_000} if is_per_run else None
            queue_obj = await channel.declare_queue(
                queue_name,
                durable=not is_per_run,
                exclusive=False,
                auto_delete=is_per_run,
                arguments=arguments,
            )

            await self._setup_exchange_binding(channel, queue_obj, qinfo)

            async def handle_incoming_message(message: AbstractIncomingMessage):
                try:
                    async with message.process(requeue=False):  # ack on success; don't requeue on handler error
                        body_txt = message.body.decode("utf-8", errors="replace")

                        # Parse JSON up-front; drop non-JSON payloads early
                        try:
                            parsed: Dict[str, Any] = json.loads(body_txt)
                        except json.JSONDecodeError:
                            logger.warning("Non-JSON payload on %s; dropping", qinfo.get("queue_name"))
                            return

                        await self._wait_if_paused(parsed)

                        logger.info(
                            "📥 CONSUME queue=%s rk=%s size=%dB",
                            queue_name,
                            getattr(message, "routing_key", "<none>"),
                            len(message.body) if message.body else 0,
                        )
                        handler = self.queue_handlers.get(queue_name, self.default_handler)
                        try:
                            await self._dispatch_to_handler(handler, message, qinfo, parsed)
                        except Exception:
                            logger.exception("Handler error on queue='%s'", queue_name)
                            return
                except (asyncio.CancelledError, aiormq.exceptions.ChannelInvalidStateError):
                    logger.warning("Message handler aborted for '%s' due to channel shutdown.", queue_name)
                    return

            await queue_obj.consume(handle_incoming_message, no_ack=False)
            logger.info("[AsyncPipelineConsumerManager] Now consuming on queue='%s'", queue_name)

            # wait until we’re told to stop
            await self.shutdown_event.wait()
            logger.info("[AsyncPipelineConsumerManager] Shutdown signal received for queue='%s'.", queue_name)

    async def forward_message(self, message_data: Any, triggered_by: Union[str, List[str]], to_task: str):
        """Delegate actual forwarding to the registered forwarder."""
        if not self.forwarder:
            logger.warning("No forwarder registered. Dropping message for to_task='%s'.", to_task)
            return
        await self.forwarder.forward(message_data, triggered_by=triggered_by, to_task=to_task)

    async def default_handler(
        self,
        message: AbstractIncomingMessage,
        qinfo: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
    ):
        """
        Default async handler if no custom handler is registered for this queue.
        Parses/uses message JSON (pipeline_run_id, from_task), then aggregates for fan-in.
        """
        if payload is None:
            try:
                payload = json.loads(message.body.decode("utf-8"))
            except json.JSONDecodeError:
                logger.warning("Default handler received non-JSON payload; dropping.")
                return

        # From here on, treat as non-optional
        msg_data: Dict[str, Any] = cast(Dict[str, Any], payload)

        to_task = qinfo.get("to_task")
        if not to_task:
            logger.warning("No 'to_task' in qinfo for queue='%s'. Skipping aggregator logic.", qinfo.get("queue_name"))
            return

        pipeline_run_id = msg_data.get("pipeline_run_id")
        from_task = msg_data.get("from_task", "<unknown>")
        if not pipeline_run_id:
            logger.warning("No pipeline_run_id in message; ignoring aggregator logic.")
            return

        aggregator_key = f"{pipeline_run_id}_{to_task}"
        # store the raw envelope under its originating task
        self.aggregator_store[aggregator_key][from_task] = msg_data

        needed = self.required_upstreams.get(to_task, [])
        arrived = self.aggregator_store[aggregator_key].keys()
        still_missing = set(needed) - set(arrived)

        logger.debug(
            "[Aggregator] Received from='%s' → to='%s' run='%s' | needed=%s arrived=%s missing=%s",
            from_task, to_task, pipeline_run_id, needed, list(arrived), list(still_missing)
        )

        if not needed:
            logger.info("[Aggregator] No aggregation needed for to_task='%s'. Forwarding triggered by '%s'.",
                        to_task, from_task)
            try:
                next_state = self._make_next_task_state(to_task, msg_data)
            except Exception as e:
                logger.error("Failed to validate TaskState from message: %s", e)
                return
            await self.forward_message(next_state, triggered_by=from_task, to_task=to_task)
            return

        if not still_missing:
            aggregated_parts = dict(self.aggregator_store.pop(aggregator_key, {}))
            if not aggregated_parts:
                logger.debug(
                    "[Aggregator] Fan-in already flushed for %s in run=%s.",
                    to_task,
                    pipeline_run_id,
                )
                return
            try:
                next_state = self._make_next_task_state(to_task, msg_data, aggregated=aggregated_parts)
            except Exception as e:
                logger.exception("Failed to coerce fan-in bundle to TaskState objects for '%s': %s", to_task, e)
                return

            logger.info(
                "[Aggregator] All upstream tasks arrived for %s in run=%s (%d total).",
                to_task, pipeline_run_id, len(needed)
            )
            all_triggering_tasks = list(aggregated_parts.keys())
            await self.forward_message(next_state, triggered_by=all_triggering_tasks, to_task=to_task)

    async def join(self):
        """Wait for all consumer tasks to complete."""
        await asyncio.gather(*self.consumer_tasks)

    async def stop_consumers(self):
        """Signal all consumers to shut down and wait for their tasks to complete."""
        self.shutdown_event.set()
        await self.join()
