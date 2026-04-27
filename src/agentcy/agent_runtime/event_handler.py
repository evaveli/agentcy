#src/agentcy/agent_runtime/event_handler.py

import json
import os
from typing import Any, Awaitable, Callable, final
from aio_pika import ExchangeType
import logging
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pipeline_orchestrator.pub_sub.helpers import declare_event_resources
import asyncio
import aiormq
from agentcy.pipeline_orchestrator.pub_sub.consumer_wrapper import ConsumerManager
from agentcy.agent_runtime.forwarder import DefaultForwarder
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import EntryMessage
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)
# Guard to ensure the pipeline-event exchange/queue is declared only once
_pipeline_event_setup_lock = asyncio.Lock()
_pipeline_event_setup_evt: asyncio.Event = asyncio.Event()
_pipeline_event_binding: tuple[str, str, str] | None = None
_pipeline_listener_started = False

logger = logging.getLogger(__name__)

async def pipeline_event_listener(rm: ResourceManager, callback: Callable[[dict], Awaitable[None]]):
    """
    Listen for pipeline events by declaring (idempotently) the necessary exchange and queue.
    
    Parameters:
      rm: ResourceManager instance with an initialized RabbitMQ connection.
      callback: A callable to handle incoming event data.
    """
    
    # Ensure the event resources (exchange and queue) are declared only once
    global _pipeline_event_binding
    global _pipeline_listener_started
    async with _pipeline_event_setup_lock:
        if not _pipeline_event_setup_evt.is_set():
            logger.info("Setting up pipeline-event resources…")
            exchange_name, queue_name, routing_key = await declare_event_resources(rm)
            _pipeline_event_binding = (exchange_name, queue_name, routing_key)
            _pipeline_event_setup_evt.set()
            vhost = os.getenv("AMQP_VHOST", "/")
            logger.info(
                "Resources declared: exchange='%s', queue='%s', routing_key='%s', vhost='%s'",
                exchange_name, queue_name, routing_key, vhost
            )
        else:
            binding = _pipeline_event_binding
            assert binding is not None, "setup_evt set but binding is None – invariant broken"
            exchange_name, queue_name, routing_key = binding
            logger.info("Pipeline-event resources already set up; reusing binding.")
        if _pipeline_listener_started:
            logger.info("Pipeline event listener already running; skipping new consumer.")
            return
        _pipeline_listener_started = True

    try:
        rabbit_mgr = rm.rabbit_mgr
        if rabbit_mgr is None:
            logger.warning("StartPipelineCommand consumer skipped: no RabbitMQ manager.")
            return
        async with rabbit_mgr.get_channel() as channel:
            logger.info("Opened RabbitMQ channel for pipeline events")
            # Re-declare is safe/idempotent and ensures we have objects on this channel.
            exchange = await channel.declare_exchange(exchange_name, type=ExchangeType.TOPIC, durable=True)
            queue = await channel.declare_queue(queue_name, durable=True)
            # Bind the queue to the exchange (this is idempotent)
            await queue.bind(exchange, routing_key=routing_key)
            logger.info("Bound queue '%s' to exchange '%s' with routing_key '%s'",
                        queue.name, exchange_name, routing_key)
            logger.info("Pipeline event listener ready – consuming from %s", queue.name)
            try:
                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process(requeue=True):
                            logger.info("Raw message body: %r", message.body)
                            try:
                                event_data = json.loads(message.body.decode("utf-8"))
                                logger.info(f"Pipeline event received: {event_data}")
                                if callback:
                                    logger.info("Invoking pipeline-event callback…")
                                    await callback(event_data)
                                    logger.info("Callback completed successfully")
                            except Exception as exc:
                                logger.exception("Error processing pipeline event: %s", exc)
                                raise
            except asyncio.CancelledError:
                logger.info("Pipeline event listener cancelled.")
                raise
    finally:
        _pipeline_listener_started = False


def make_on_pipeline_event(
    rm,
    microservice_logic: Callable,
    service_name: str,
    *,
    shutdown_event: asyncio.Event | None = None,
):
    """
    Factory function that returns an async callback. The returned callback
    captures 'rm' in a closure, so you don't need to pass 'rm' repeatedly.
    """

    async def _ensure_run_topology(username: str, pipeline_id: str, run_id: str) -> None:
        """
        Declare/bind per-run, per-edge queues and bindings.

        Rules:
        - fanout -> topic (so we can use per-run RKs)
        - topic RK = <base_queue>.<run_id>
        - direct RK = (<routing_key or base_queue>).<run_id>
        """
        try:
            final_cfg = await asyncio.to_thread(
                rm.pipeline_store.get_final_config, username, pipeline_id
            )

            # index rabbitmq config by (from_task, base_queue) so we bind the *right* edge
            rb_index = {}
            for c in final_cfg.get("rabbitmq_configs", []):
                rb = c.get("rabbitmq") or {}
                task_id = c.get("task_id")
                q = rb.get("queue")
                if task_id and q:
                    rb_index[(task_id, q)] = rb

            async with rm.rabbit_mgr.get_channel() as ch:
                for base_queue, edge in (final_cfg.get("queues") or {}).items():
                    from_task = edge.get("from_task")
                    to_task   = edge.get("to_task")
                    if not from_task or not to_task:
                        continue

                    rb = rb_index.get((from_task, base_queue))
                    if not rb:
                        logger.warning("RabbitMQ config missing for edge %s→%s (base=%s)", from_task, to_task, base_queue)
                        continue

                    exch_name = rb["exchange"]
                    x_raw = (rb.get("exchange_type") or "direct").lower()
                    if x_raw not in ("direct", "fanout", "topic"):
                        x_raw = "direct"
                    x_mapped = x_raw
                    desired_type = getattr(ExchangeType, x_mapped.upper(), ExchangeType.DIRECT)
                    existing_type = None

                    cleanup_mgr = getattr(rm, "rabbit_mgr", None) or getattr(rm, "rabbit_conn", None)
                    if cleanup_mgr is not None:
                        try:
                            async with cleanup_mgr.get_channel() as cleanup_ch:
                                try:
                                    await cleanup_ch.declare_exchange(exch_name, type=desired_type, durable=True)
                                except aiormq.exceptions.ChannelPreconditionFailed as exc:
                                    msg = str(exc).lower()
                                    if "current is 'topic'" in msg:
                                        existing_type = ExchangeType.TOPIC
                                    elif "current is 'fanout'" in msg:
                                        existing_type = ExchangeType.FANOUT
                                    elif "current is 'direct'" in msg:
                                        existing_type = ExchangeType.DIRECT

                                    try:
                                        if getattr(cleanup_ch, "is_closed", False):
                                            async with cleanup_mgr.get_channel() as delete_ch:
                                                await delete_ch.exchange_delete(exch_name, if_unused=False, if_empty=False)
                                        else:
                                            await cleanup_ch.exchange_delete(exch_name, if_unused=False, if_empty=False)
                                    except Exception:
                                        pass

                                    try:
                                        async with cleanup_mgr.get_channel() as recreate_ch:
                                            await recreate_ch.declare_exchange(exch_name, type=desired_type, durable=True)
                                        existing_type = None
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    exch_type = desired_type
                    if existing_type and existing_type != desired_type:
                        exch_type = existing_type
                    exch_obj  = await ch.declare_exchange(exch_name, exch_type, durable=True)

                    run_queue = f"{base_queue}.{run_id}"
                    queue_obj = await ch.declare_queue(
                        run_queue,
                        durable=False,
                        auto_delete=True,
                        exclusive=False,
                        arguments={"x-expires": 900_000},
                    )

                    if x_mapped == "fanout":
                        binding_rk = rb.get("routing_key") or ""
                    elif x_mapped == "topic":
                        binding_rk = run_queue  # <base_queue>.<run_id>
                    else:
                        base_rk = rb.get("routing_key") or base_queue
                        binding_rk = f"{base_rk}.{run_id}"

                    await queue_obj.bind(exch_obj, routing_key=binding_rk)
                    logger.info(
                        "Bound %s → exch=%s type=%s rk=%s   (%s→%s)",
                        run_queue, exch_name, x_mapped, binding_rk, from_task, to_task
                    )

            logger.info("Run-topology ensured for %s", run_id)

        except Exception as e:
            logger.exception("Failed to ensure per-run topology for %s: %s", run_id, e)


    
    _bootstrapped_runs: set[str] = set()

    async def on_pipeline_event(event_data: dict[str, Any]):
        """
        Callback that handles pipeline events.

        Expects event_data containing:
          - 'username'
          - 'pipeline_id'

        Fetches
          - 'service_name'
        from the env vars

        Uses 'rm' from the outer function's scope (the closure).
        """
        try:
            logger.info("on_pipeline_event → data: %s", event_data)
            evt_type = event_data.get("event")
            # Skip non-bootstrap lifecycle events (pipeline_completed, run_status_changed, etc.)
            # but allow pipeline_started through so all agents set up their ConsumerManagers.
            if evt_type and evt_type != "pipeline_started":
                logger.info("Skipping non-bootstrap event %r", evt_type)
                return
            is_broadcast_start = (evt_type == "pipeline_started")
        except Exception as e:
            logger.exception("Error processing pipeline event: %s", e)
            return

        try:
            logger.info("on_pipeline_event → data: %s", event_data)

            username = event_data.get("username")
            pipeline_id = event_data.get("pipeline_id")
            run_id = event_data.get("pipeline_run_id")

            if not (isinstance(username, str) and isinstance(pipeline_id, str) and isinstance(run_id, str)):
                logger.info("Malformed event (username/pipeline_id/pipeline_run_id missing or invalid): %s", event_data)
                return

            # Dedup ConsumerManager creation only — the entry forwarding must
            # still happen even if we already bootstrapped from the broadcast.
            already_bootstrapped = run_id in _bootstrapped_runs
            resource_manager = rm
            forwarder = DefaultForwarder(rm=resource_manager, microservice_logic=microservice_logic)

            if not already_bootstrapped:
                _bootstrapped_runs.add(run_id)
                await _ensure_run_topology(username, pipeline_id, run_id)

                logger.info("Creating ConsumerManager(%s, %s, %s, %s)",
                             service_name, username, pipeline_id, run_id)
                consumer_manager = ConsumerManager(
                    service_name,
                    resource_manager,
                    username,
                    pipeline_id,
                    run_id,
                    shutdown_event=shutdown_event,
                )
                logger.info("Registering forwarder…")
                consumer_manager.register_forwarder(forwarder)
                logger.info("Launching background consumers for %s/%s (run=%s)",
                            username, pipeline_id, run_id)
                task = asyncio.create_task(consumer_manager.start_async(), name=f"cm-{service_name}-{run_id}")
                logger.info("ConsumerManager task created: %s", task.get_name())
                logger.info("Consumers started for service='%s' at task %s",
                            service_name, task.get_name())
            else:
                logger.info("ConsumerManager already exists for run %s / service '%s'; skipping CM creation.", run_id, service_name)
                consumer_manager = None

            active_managers = [consumer_manager] if consumer_manager else []
            
            # -------------------- TEST/DEV ONLY --------------------
            # Optionally spin up consumers for *all* services in this pipeline
            # so the DAG can progress in a single process.
            # Keep this OFF in prod to preserve strict microservice boundaries.
            if os.getenv("SINGLE_PROCESS_ALL_SERVICES") == "1":
                try:
                    final_cfg = await asyncio.to_thread(
                        resource_manager.pipeline_store.get_final_config,
                        username,
                        pipeline_id,
                    )
                    service_ids = {
                        t.get("available_services")
                        for t in final_cfg.get("task_dict", {}).values()
                        if t.get("available_services")
                    }
                    # start all other services except the one we already launched
                    for svc in sorted(service_ids - {service_name}):
                        cm = ConsumerManager(
                            svc,
                            resource_manager,
                            username,
                            pipeline_id,
                            run_id,
                            shutdown_event=shutdown_event,
                        )
                        cm.register_forwarder(forwarder)
                        t_all = asyncio.create_task(
                            cm.start_async(),
                            name=f"cm-{svc}-{run_id}"
                        )
                        logger.info("Consumers started for service='%s' at task %s",
                                    svc, t_all.get_name())
                        active_managers.append(cm)
                except Exception as e:
                    logger.exception("Failed starting SINGLE_PROCESS_ALL_SERVICES managers: %s", e)

                if active_managers and getattr(resource_manager, "ephemeral_store", None) is not None:
                    async def _stop_on_completion() -> None:
                        while True:
                            try:
                                run_doc = await asyncio.to_thread(
                                    resource_manager.ephemeral_store.read_run,
                                    username,
                                    pipeline_id,
                                    run_id,
                                )
                                status = (run_doc or {}).get("status")
                                if status in ("COMPLETED", "FAILED"):
                                    for cm in active_managers:
                                        try:
                                            await cm._manager.stop_consumers()
                                        except Exception:
                                            logger.exception("Failed stopping consumers for %s", cm.service_name)
                                    return
                            except Exception:
                                logger.exception("Run-completion watcher failed")
                                return
                            await asyncio.sleep(1.0)

                    asyncio.create_task(_stop_on_completion(), name=f"cm-stop-{run_id}")
            # ------------------ END TEST/DEV ONLY -------------------

            if os.getenv("SINGLE_PROCESS_ALL_SERVICES") == "1":
                try:
                    delay = float(os.getenv("SINGLE_PROCESS_STARTUP_DELAY_SECONDS", "0.5"))
                except ValueError:
                    delay = 0.5
                if delay > 0:
                    await asyncio.sleep(delay)
            
            # Entry forwarding: every agent checks if its service matches any
            # entry task.  Only the agent whose service_name matches the entry
            # task's available_services will actually execute and forward.
            if "task_id" not in event_data:
                # Build the EntryMessage from whatever path delivered the event
                pipeline_config_id = event_data.get("pipeline_config_id", pipeline_id)
                entry_msg = EntryMessage(
                    pipeline_id=pipeline_id,
                    username=username,
                    pipeline_run_id=run_id,
                    pipeline_config_id=pipeline_config_id,
                )

                final_cfg = await asyncio.to_thread(
                    resource_manager.pipeline_store.get_final_config,
                    username,
                    pipeline_id,
                )

                entry_tasks = [
                    tid for tid, meta in final_cfg["task_dict"].items()
                    if meta.get("is_entry")
                ]
                if not entry_tasks:
                    logger.error("No entry tasks found in pipeline config for %s/%s",
                                 username, pipeline_id)
                    return

                for to_task in entry_tasks:
                    # Only the agent whose service matches executes the entry task
                    tmeta = final_cfg["task_dict"].get(to_task, {})
                    task_service = tmeta.get("available_services", "")
                    if task_service != service_name:
                        logger.info("Entry task %s needs service '%s', not '%s'; skipping.",
                                    to_task, task_service, service_name)
                        continue
                    try:
                        await forwarder.forward(
                            message_data=entry_msg,
                            triggered_by="entry",
                            to_task=to_task,
                        )
                        logger.info("Forwarded entry message to task %s (service=%s)", to_task, service_name)
                    except Exception as e:
                        logger.exception("Failed to forward entry message to %s: %s", to_task, e)

            
        except Exception as e:
            logger.exception("Error processing pipeline event: %s", e)

    return on_pipeline_event



async def pipeline_entry_listener(
    rm: ResourceManager,
    on_event: Callable[[dict], Awaitable[None]],
    *,
    run_id: str,
) -> None:
    """
    Consume exactly one seed from pipeline_entry_queue_{run_id}, then start per-run consumers.
    """
    queue_name = f"pipeline_entry_queue_{run_id}"
    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        logger.warning("StartPipelineCommand consumer skipped: no RabbitMQ manager.")
        return
    async with rabbit_mgr.get_channel() as ch:
        # Must match Runner.launch(): durable + x-expires
        entry_q = await ch.declare_queue(
            queue_name, durable=True, arguments={"x-expires": 900_000}
        )
        logger.info("Listening for pipeline-entry on %s", queue_name)

        async with entry_q.iterator() as it:
            async for msg in it:
                try:
                    async with msg.process():  # ack on success, no requeue
                        try:
                            payload = json.loads(msg.body)
                        except Exception:
                            logger.exception("Invalid seed payload; dropping.")
                            break

                        logger.info("entry-message → %r", payload)

                        # start per-run consumers (no fan-out here)
                        await on_event(payload)
                        break
                except aiormq.exceptions.ChannelInvalidStateError:
                    logger.warning("Entry listener channel closed before ack; stopping.")
                    return
