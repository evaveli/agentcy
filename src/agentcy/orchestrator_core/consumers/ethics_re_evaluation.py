# src/agentcy/orchestrator_core/consumers/ethics_re_evaluation.py
import logging
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import uuid4

from aio_pika import ExchangeType, Message, DeliveryMode

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import (
    EthicsReEvaluatedEvent,
    ReEvaluatePlanCommand,
    SchemaVersion,
)
from agentcy.agent_runtime.services.ethics_checker import run as ethics_run
from agentcy.api_service.dependecies import COMMAND_EXCHANGE_NAME

logger = logging.getLogger(__name__)


async def handle_re_evaluate(
    cmd: ReEvaluatePlanCommand,
    rm: ResourceManager,
    publish_event: Callable,
) -> Optional[EthicsReEvaluatedEvent]:
    """
    Core handler for a single ``ReEvaluatePlanCommand``.

    Re-runs the ethics checker with the re_evaluation_count from the command,
    which prevents infinite loops (bounded by policy/config max).
    """
    message = {
        "username": cmd.username,
        "pipeline_id": cmd.pipeline_id,
        "plan_id": cmd.plan_id,
        "pipeline_run_id": cmd.pipeline_run_id,
        "re_evaluation_count": cmd.re_evaluation_count,
        "original_issues": cmd.original_issues,
    }
    try:
        result = await ethics_run(rm, "re-eval", "ethics_checker", None, message)
    except Exception:
        logger.exception(
            "Re-evaluation ethics check failed for plan_id=%s (count=%d)",
            cmd.plan_id,
            cmd.re_evaluation_count,
        )
        return None

    evt = EthicsReEvaluatedEvent(
        schema_version=SchemaVersion.V1,
        username=cmd.username,
        pipeline_id=cmd.pipeline_id,
        plan_id=cmd.plan_id,
        pipeline_run_id=cmd.pipeline_run_id,
        approved=result.get("approved", False),
        re_evaluation_count=cmd.re_evaluation_count,
        timestamp=datetime.now(timezone.utc),
    )
    await publish_event(evt)
    logger.info(
        "EthicsReEvaluatedEvent published: plan_id=%s approved=%s count=%d",
        cmd.plan_id,
        evt.approved,
        cmd.re_evaluation_count,
    )
    return evt


async def ethics_re_evaluation_consumer(rm: ResourceManager):
    """
    Listens for ``ReEvaluatePlanCommand`` on ``commands.ethics_re_evaluate``.

    Flow
    ----
    1. Deserialise command
    2. Delegate to ``handle_re_evaluate()``
    3. Publish resulting event to RabbitMQ
    """
    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        logger.warning("ethics_re_evaluation_consumer: no RabbitMQ manager; skipping.")
        return

    store = rm.graph_marker_store
    if store is None:
        logger.error("ethics_re_evaluation_consumer: graph_marker_store not configured.")
        return

    async with rabbit_mgr.get_channel() as channel:
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(
            COMMAND_EXCHANGE_NAME,
            ExchangeType.TOPIC,
            durable=True,
        )

        queue = await channel.declare_queue("commands.ethics_re_evaluate", durable=True)
        await queue.bind(exchange, routing_key="commands.ethics_re_evaluate")
        logger.info("Listening for ReEvaluatePlanCommand on 'commands.ethics_re_evaluate'")

        async def _publish_event(evt: EthicsReEvaluatedEvent):
            await channel.default_exchange.publish(
                Message(
                    body=evt.model_dump_json().encode("utf-8"),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                    message_id=str(uuid4()),
                    timestamp=int(datetime.now().timestamp()),
                ),
                routing_key="events.ethics_re_evaluated",
            )

        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    try:
                        cmd = ReEvaluatePlanCommand.model_validate_json(msg.body)
                    except Exception:
                        logger.exception("Failed to parse ReEvaluatePlanCommand")
                        continue

                    logger.info(
                        "Received ReEvaluatePlanCommand(username=%s, plan_id=%s, count=%d)",
                        cmd.username,
                        cmd.plan_id,
                        cmd.re_evaluation_count,
                    )
                    await handle_re_evaluate(cmd, rm, _publish_event)
