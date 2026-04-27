# src/agentcy/orchestrator_core/consumers/task_dispatch.py
"""Consumer for cross-service CNP task dispatch.

Handles the uncommon case where a re-forwarded task needs to be dispatched
to a different service process than the one that detected the failure.
For same-service re-dispatch (the common CNP case), the forwarder handles
it inline via its retry loop.
"""
import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import uuid4

from aio_pika import ExchangeType, Message, DeliveryMode

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import (
    DispatchTaskCommand,
    SchemaVersion,
    TaskDispatchedEvent,
)
from agentcy.api_service.dependecies import COMMAND_EXCHANGE_NAME

logger = logging.getLogger(__name__)


async def handle_dispatch_task(
    cmd: DispatchTaskCommand,
    rm: ResourceManager,
    publish_event: Callable,
) -> Optional[TaskDispatchedEvent]:
    """Process a cross-service task dispatch.

    Reads the persisted task input from the ephemeral store, publishes it
    to the target service's input queue so that the appropriate
    ConsumerManager picks it up naturally.
    """
    # 1. Read persisted task input
    ephemeral = getattr(rm, "ephemeral_store", None)
    if ephemeral is None:
        logger.error("handle_dispatch_task: ephemeral_store not configured")
        return None

    input_doc = None
    try:
        input_doc = ephemeral.read_task_output(
            username=cmd.username,
            task_id=f"input_{cmd.task_id}",
            run_id=cmd.pipeline_run_id,
        )
    except Exception:
        logger.debug("handle_dispatch_task: read_task_output failed", exc_info=True)

    if not input_doc:
        logger.error(
            "handle_dispatch_task: task input not found for task=%s run=%s",
            cmd.task_id, cmd.pipeline_run_id,
        )
        return None

    # 2. Look up target queue from pipeline config
    pipeline_store = getattr(rm, "pipeline_store", None)
    if pipeline_store is None:
        logger.error("handle_dispatch_task: pipeline_store not configured")
        return None

    try:
        final_cfg = pipeline_store.get_final_config(cmd.username, cmd.pipeline_id)
    except Exception:
        logger.exception("handle_dispatch_task: failed fetching pipeline config")
        return None

    # Find rabbitmq configs whose destination includes the target task
    from agentcy.pipeline_orchestrator.pub_sub.pub_wrapper import get_dynamic_names_from_config

    target_rbs = []
    for c in final_cfg.get("rabbitmq_configs", []):
        rb = c.get("rabbitmq", {})
        # Edges where the queue feeds into our task
        to_tasks = c.get("to_tasks") or []
        if cmd.task_id in to_tasks or rb.get("queue", "").endswith(cmd.task_id):
            target_rbs.append(rb)

    bus = getattr(rm, "message_bus", None)
    if bus is None:
        logger.error("handle_dispatch_task: message_bus not configured")
        return None

    envelope = {
        "pipeline_run_id": cmd.pipeline_run_id,
        "username": cmd.username,
        "pipeline_id": cmd.pipeline_id,
        "from_task": "__cnp_dispatch__",
        "payload": input_doc,
    }

    published = False
    for rb in target_rbs:
        try:
            run_queue, routing_key, mapped_type = get_dynamic_names_from_config(
                rb, cmd.pipeline_run_id,
            )
            exchange_name = rb.get("exchange", "")
            await bus.publish(exchange_name, routing_key, envelope, exchange_type=mapped_type)
            published = True
            logger.info(
                "handle_dispatch_task: published to exch=%s rk=%s for task=%s",
                exchange_name, routing_key, cmd.task_id,
            )
        except Exception:
            logger.exception("handle_dispatch_task: failed publishing to queue")

    if not published:
        logger.warning(
            "handle_dispatch_task: no target queues found for task=%s", cmd.task_id,
        )
        return None

    # 3. Emit TaskDispatchedEvent
    evt = TaskDispatchedEvent(
        schema_version=SchemaVersion.V1,
        username=cmd.username,
        pipeline_id=cmd.pipeline_id,
        pipeline_run_id=cmd.pipeline_run_id,
        task_id=cmd.task_id,
        agent_id=cmd.new_agent_id,
        service_name=cmd.new_service,
        dispatch_type="cross_service",
        reforward_count=cmd.reforward_count,
        timestamp=datetime.now(timezone.utc),
    )
    await publish_event(evt)
    logger.info(
        "TaskDispatchedEvent published: task=%s agent=%s service=%s",
        cmd.task_id, cmd.new_agent_id, cmd.new_service,
    )
    return evt


async def task_dispatch_consumer(rm: ResourceManager):
    """Listens for ``DispatchTaskCommand`` on ``commands.dispatch_task``."""

    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        logger.warning("task_dispatch_consumer: no RabbitMQ manager; skipping.")
        return

    async with rabbit_mgr.get_channel() as channel:
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(
            COMMAND_EXCHANGE_NAME,
            ExchangeType.TOPIC,
            durable=True,
        )

        queue = await channel.declare_queue("commands.dispatch_task", durable=True)
        await queue.bind(exchange, routing_key="commands.dispatch_task")
        logger.info("Listening for DispatchTaskCommand on 'commands.dispatch_task'")

        async def _publish_event(evt: TaskDispatchedEvent):
            await channel.default_exchange.publish(
                Message(
                    body=evt.model_dump_json().encode("utf-8"),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                    message_id=str(uuid4()),
                    timestamp=int(datetime.now().timestamp()),
                ),
                routing_key="events.task_dispatched",
            )

        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    try:
                        cmd = DispatchTaskCommand.model_validate_json(msg.body)
                    except Exception:
                        logger.exception("Failed to parse DispatchTaskCommand")
                        continue

                    logger.info(
                        "Received DispatchTaskCommand(task=%s, agent=%s, svc=%s)",
                        cmd.task_id, cmd.new_agent_id, cmd.new_service,
                    )
                    await handle_dispatch_task(cmd, rm, _publish_event)
