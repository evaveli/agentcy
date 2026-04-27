#src/agentcy/orchestrator_core/consumers/service.py

import aio_pika, logging, uuid
from datetime import datetime, timezone
from agentcy.pydantic_models.commands  import RegisterServiceCommand, SchemaVersion, ServiceRegisteredEvent
from agentcy.orchestrator_core.stores.service_store import ServiceStore
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.parsing_layer.parse_and_distribute import InputToInfrastructure
from agentcy.pipeline_orchestrator.pub_sub.connection_manager import RabbitMQConnectionManager
from agentcy.orchestrator_core.stores.service_store import SERVICE_KEY_FMT

logger = logging.getLogger(__name__)




async def register_service_consumer(rm: ResourceManager):
    """
    1) read RegisterServiceCommand
    2) persist via service_store
    3) generate manifests
    4) emit ServiceRegisteredEvent with the document key only
    """

    # Guard: RabbitMQ manager must exist
    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        logger.warning("register_pipeline_consumer: no RabbitMQ manager; skipping consumer.")
        return
    # Guard: pipeline_store must exist
    store = rm.service_store
    if store is None:
        logger.error("register_pipeline_consumer: pipeline_store not configured; cannot persist pipelines.")
        return

    async with rabbit_mgr.get_channel() as channel:
        await channel.set_qos(prefetch_count=10)

        queue = await channel.declare_queue(
            "commands.register_service", durable=True
        )

        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    # — 1) Deserialize incoming command
                    cmd: RegisterServiceCommand = (
                        RegisterServiceCommand
                        .model_validate_json(msg.body)
                    )
                    username = cmd.username
                    svc      = cmd.service

                    # — 2) Persist the service registration
                    service_id = store.upsert(username, svc)

                    # — 3) Generate any YAML / manifests
                    InputToInfrastructure().render_service_yaml(service=svc)

                    # — 4) Compute the Couchbase document key
                    config_key = SERVICE_KEY_FMT.format(
                        username=username,
                        service_id=service_id
                    )

                    # — 5) Publish the “service registered” event
                    evt = ServiceRegisteredEvent(
                        username   = username,
                        service_id = service_id,
                        config_key = config_key,
                        schema_version=SchemaVersion.V1,
                        timestamp  = datetime.now(),
                    )

                    await channel.default_exchange.publish(
                        aio_pika.Message(
                            body         = evt.model_dump_json().encode("utf-8"),
                            delivery_mode= aio_pika.DeliveryMode.PERSISTENT,
                            content_type = "application/json",
                            message_id   = str(uuid.uuid4()),
                            timestamp    = int(datetime.now().timestamp()),
                        ),
                        routing_key = "events.service_registered",
                    )

                    logger.info(
                        "ServiceRegisteredEvent published for %s", service_id
                    )
