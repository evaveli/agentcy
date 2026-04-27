#src/agentcy/orchestrator_core/consumers/pipeline.py
import logging
from datetime import datetime, timezone
from uuid import uuid4
from aio_pika import ExchangeType, Message, DeliveryMode
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import PipelineRegisteredEvent, RegisterPipelineCommand, SchemaVersion
from agentcy.pydantic_models.pipeline_validation_models.pipeline_model import PipelineConfig
from agentcy.orchestrator_core.utils import build_pipeline_config
from agentcy.orchestrator_core.stores.pipeline_store import PIPELINE_KEY_FMT
from agentcy.api_service.dependecies import COMMAND_EXCHANGE_NAME

logger = logging.getLogger(__name__)

async def register_pipeline_consumer(rm: ResourceManager):

    # Guard: RabbitMQ manager must exist
    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        logger.warning("register_pipeline_consumer: no RabbitMQ manager; skipping consumer.")
        return
    # Guard: pipeline_store must exist
    store = rm.pipeline_store
    if store is None:
        logger.error("register_pipeline_consumer: pipeline_store not configured; cannot persist pipelines.")
        return

    async with rabbit_mgr.get_channel() as channel:
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(
            COMMAND_EXCHANGE_NAME,
            ExchangeType.TOPIC,
            durable=True,
        )


        queue = await channel.declare_queue("commands.register_pipeline", durable=True)

        await queue.bind(exchange, routing_key="commands.register_pipeline")
        logger.info("Listening for RegisterPipelineCommand on 'commands.register_pipeline'")


        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    cmd = RegisterPipelineCommand.model_validate_json(msg.body)
                    username    = cmd.username
                    pipeline_id = cmd.pipeline_id
                    logger.info("Received RegisterPipelineCommand(username=%s, pipeline_id=%s)", username, pipeline_id)

                    # Handle payload_ref pattern: fetch from store if payload_ref provided
                    if cmd.payload_ref is not None:
                        # Config already stored by API - just verify it exists
                        logger.info("Using payload_ref pattern, fetching from store: %s", cmd.payload_ref)
                        try:
                            existing_cfg = store.get_final_config(username, pipeline_id)
                            if existing_cfg:
                                logger.info("Pipeline config already persisted via payload_ref")
                                # Config is already persisted, just emit the event
                                config_key = PIPELINE_KEY_FMT.format(
                                    username=username,
                                    pipeline_id=pipeline_id
                                )
                            else:
                                logger.error("payload_ref provided but config not found in store")
                                continue
                        except Exception as e:
                            logger.error("Failed to fetch config via payload_ref: %s", e)
                            continue
                    elif cmd.pipeline is not None:
                        # Legacy mode: full payload in message (backwards compatible)
                        create_dto = cmd.pipeline
                        # Build full domain model
                        full_cfg = build_pipeline_config(dto=create_dto, pipeline_id=pipeline_id)
                        # Persist with versioning
                        store.update(username, full_cfg.pipeline_id, full_cfg)

                        config_key = PIPELINE_KEY_FMT.format(
                            username=username,
                            pipeline_id=pipeline_id
                        )
                        logger.info("Persisted pipeline config under key=%s (legacy mode)", config_key)
                    else:
                        logger.error("Neither pipeline nor payload_ref provided in command")
                        continue

                    # 4) emit PipelineRegisteredEvent
                    evt = PipelineRegisteredEvent(
                        schema_version = SchemaVersion.V1,
                        username       = username,
                        pipeline_id    = pipeline_id,
                        config_key     = config_key,
                        timestamp      = datetime.now(timezone.utc),
                    )
                    await channel.default_exchange.publish(
                        Message(
                            body=evt.model_dump_json().encode("utf-8"),
                            delivery_mode=DeliveryMode.PERSISTENT,
                            content_type="application/json",
                            message_id=str(uuid4()),
                            timestamp=int(datetime.now().timestamp()),
                        ),
                        routing_key="events.pipeline_registered",
                    )
                    logger.info("PipelineRegisteredEvent published for %s", pipeline_id)
