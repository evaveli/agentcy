# src/agentcy/orchestrator_core/consumers/cnp_lifecycle.py
"""Consumer for CNP lifecycle commands (task reassignment on failure)."""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from aio_pika import ExchangeType, Message, DeliveryMode

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import (
    ReassignTaskCommand,
    SchemaVersion,
    TaskReassignedEvent,
)
from agentcy.api_service.dependecies import COMMAND_EXCHANGE_NAME

logger = logging.getLogger(__name__)


async def handle_reassign_task(
    cmd: ReassignTaskCommand,
    rm: ResourceManager,
    publish_event: Callable,
) -> Optional[TaskReassignedEvent]:
    """Process a task reassignment: update agent load, emit event.

    Extracted from the consumer loop for testability (same pattern as
    ``handle_revise_plan``).
    """
    store = rm.graph_marker_store
    if store is None:
        logger.error("handle_reassign_task: graph_marker_store not configured")
        return None

    # Read the evaluation sequence to get new agent details
    seq_doc = store.get_evaluation_sequence(
        username=cmd.username, task_id=cmd.task_id, plan_id=cmd.plan_id,
    )
    if seq_doc is None:
        logger.warning(
            "handle_reassign_task: no evaluation sequence for task=%s plan=%s",
            cmd.task_id, cmd.plan_id,
        )
        return None

    current_index = int(seq_doc.get("current_index", 0))
    candidates = seq_doc.get("candidates") or []
    if current_index >= len(candidates):
        logger.warning("handle_reassign_task: sequence exhausted for task=%s", cmd.task_id)
        return None

    new_candidate = candidates[current_index]
    new_agent = new_candidate.get("bidder_id", "unknown")
    new_score = float(new_candidate.get("bid_score", 0))

    # Update agent load via registry (decrement failed, increment new)
    registry = getattr(rm, "agent_registry_store", None)
    if registry is not None:
        try:
            from agentcy.agent_runtime.services.graph_builder import _increment_agent_load
            _increment_agent_load(
                registry,
                username=cmd.username,
                agent_id=new_agent,
                task_id=cmd.task_id,
            )
        except Exception:
            logger.debug("handle_reassign_task: failed updating agent load", exc_info=True)

    # Emit event
    evt = TaskReassignedEvent(
        schema_version=SchemaVersion.V1,
        username=cmd.username,
        pipeline_id=cmd.pipeline_id,
        pipeline_run_id=cmd.pipeline_run_id,
        plan_id=cmd.plan_id,
        task_id=cmd.task_id,
        failed_agent_id=cmd.failed_agent_id,
        new_agent_id=new_agent,
        new_bid_score=new_score,
        sequence_index=current_index,
        timestamp=datetime.now(timezone.utc),
    )
    await publish_event(evt)
    logger.info(
        "TaskReassignedEvent published: task=%s %s→%s (seq_idx=%d)",
        cmd.task_id, cmd.failed_agent_id, new_agent, current_index,
    )
    return evt


async def cnp_lifecycle_consumer(rm: ResourceManager):
    """Listens for ``ReassignTaskCommand`` on ``commands.reassign_task``."""

    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        logger.warning("cnp_lifecycle_consumer: no RabbitMQ manager; skipping.")
        return

    store = rm.graph_marker_store
    if store is None:
        logger.error("cnp_lifecycle_consumer: graph_marker_store not configured.")
        return

    async with rabbit_mgr.get_channel() as channel:
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(
            COMMAND_EXCHANGE_NAME,
            ExchangeType.TOPIC,
            durable=True,
        )

        queue = await channel.declare_queue("commands.reassign_task", durable=True)
        await queue.bind(exchange, routing_key="commands.reassign_task")
        logger.info("Listening for ReassignTaskCommand on 'commands.reassign_task'")

        async def _publish_event(evt: TaskReassignedEvent):
            await channel.default_exchange.publish(
                Message(
                    body=evt.model_dump_json().encode("utf-8"),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                    message_id=str(uuid4()),
                    timestamp=int(datetime.now().timestamp()),
                ),
                routing_key="events.task_reassigned",
            )

        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    try:
                        cmd = ReassignTaskCommand.model_validate_json(msg.body)
                    except Exception:
                        logger.exception("Failed to parse ReassignTaskCommand")
                        continue

                    logger.info(
                        "Received ReassignTaskCommand(task=%s, failed=%s)",
                        cmd.task_id, cmd.failed_agent_id,
                    )
                    await handle_reassign_task(cmd, rm, _publish_event)
