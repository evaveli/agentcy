#src/agentcy/endpoints/helpers.py

from datetime import datetime, timezone
import json
import os
import uuid
from aio_pika import connect_robust, Message, ExchangeType
import logging
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import PipelineRun, PipelineStatus, TaskState, TaskStatus
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager


logger = logging.getLogger(__name__)


# Helper function: Publish kickoff message to the entry exchange.
async def publish_kickoff_message(rm: ResourceManager, entry_exchange: str, payload: PipelineRun, routing_key: str, username: str):

    #Implement payload here
    pipeline_start_payload = {
        "pipeline_id" : payload.pipeline_id,
        "username" : username,
        "pipeline_run_id" : payload.pipeline_run_id
    }

    await publish_pipeline_started_event(rm=rm, payload=pipeline_start_payload)
    
    try:
        if rm.rabbit_mgr is None:
            raise ValueError("ResourceManager.rabbit_mgr is not initialized.")
        async with rm.rabbit_mgr.get_channel() as channel:
            # Declare the entry exchange as a fanout exchange (idempotently).
            exchange = await channel.declare_exchange(entry_exchange, type="direct", durable=True)
            message_body = json.dumps(payload).encode("utf-8")
            message = Message(body=message_body)
            # For fanout exchanges, the routing key is ignored.
            await exchange.publish(message, routing_key=routing_key)
            logger.info("Published kickoff message to exchange '%s'", entry_exchange)
    except Exception as exc:
        logger.exception("Error publishing kickoff message: %s", exc)
        raise


def get_dynamic_resource_names(pipeline_id: str, pipeline_run_id: str) -> tuple:
    """
    Generate dynamic names for exchange, queue, and routing key.
    """
    exchange_name = f"pipeline_entry_{pipeline_id}_{pipeline_run_id}"
    queue_name = f"pipeline_entry_queue_{pipeline_id}_{pipeline_run_id}"
    routing_key = queue_name  # or you could use f"pipeline.entry.{pipeline_run_id}" if preferred
    return exchange_name, queue_name, routing_key



def create_pipeline_run_from_existing_config(
        username: str,
        pipeline_run_config_id: str,
        pipeline_id: str,
        pipeline_doc_manager
):
    
    pipeline_definition = pipeline_doc_manager.read_pipeline_run(
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_config_id
        )
    
    orchestration = pipeline_definition.get("orchestration")
    if not orchestration:
        raise ValueError("Pipeline definition is missing the orchestration block.")

    tasks_list = orchestration.get("tasks")
    if not tasks_list:
        raise ValueError("Orchestration block is missing the tasks list.")
    

    tasks_map = {}
    pipeline_run_id = uuid.uuid4()

    for task in orchestration["tasks"]:
        task_id = task.get("task_id")
        tasks_map[task_id] = TaskState(
            status=TaskStatus.IDLE.value,
            attempts=0,
            error=None,
            is_final_task=task.get("is_final_task", False),
            last_updated=None,
            output_ref='',
            pipeline_run_id=pipeline_run_id,
            task_id=task_id,
            username=username,
            pipeline_config_id=pipeline_run_config_id,
            pipeline_id=pipeline_id,
            service_name=task.get("service", "")
        )  # type: ignore
    
    pipeline_run = PipelineRun(
        pipeline_run_id=pipeline_run_id,
        pipeline_id=pipeline_id,
        status=PipelineStatus.IDLE,
        tasks=tasks_map,
        started_at=datetime.now(timezone.utc),
        triggered_by=username
    ) #type: ignore
    return pipeline_run




async def publish_pipeline_started_event(rm: ResourceManager, payload: dict) -> None:
    """
    Publish a 'pipeline started' event to the dedicated pipeline events exchange.
    
    Parameters:
      rm: ResourceManager instance with an initialized RabbitMQ connection.
      payload: Dictionary containing the pipeline event details.
    """

    exchange_name, _, routing_key = await declare_event_resources(rm)

    try:
        if rm.rabbit_mgr is None:
            raise ValueError("ResourceManager.rabbit_mgr is not initialized.")
        async with rm.rabbit_mgr.get_channel() as channel:
            exchange = await channel.declare_exchange(exchange_name, type=ExchangeType.TOPIC, durable=True)
            message_body = json.dumps(payload).encode("utf-8")
            message = Message(body=message_body)

            #Publish message with routing key
            await exchange.publish(message, routing_key=routing_key)
            logger.info(f"Published pipeline started event to exchange '{exchange_name}' with routing key '{routing_key}'.")
    except Exception as exc:
        logger.exception("Error publishing pipeline started event: %s", exc)
        raise



async def declare_event_resources(rm: ResourceManager) -> tuple:
    """
    Declare the pipeline events exchange and queue, and bind the queue to the exchange.
    Uses environment variables for names and routing key. Because declarations are idempotent,
    this function can be safely called at runtime to ensure resources exist.

    Environment Variables:
      - PIPELINE_EVENTS_EXCHANGE: Name of the exchange (default: "pipeline_events_exchange")
      - PIPELINE_EVENTS_QUEUE: Name of the queue (default: "pipeline_events_queue")
      - PIPELINE_EVENTS_ROUTING_KEY: Routing key to bind the queue to the exchange (default: "pipeline.events")
      - AMQP_VHOST: Virtual host (default: "/")

    Parameters:
        rm (ResourceManager): A ResourceManager instance with an initialized RabbitMQ connection.

    Returns:
        tuple: A tuple containing (exchange_name, queue_name, routing_key) that were declared.
    """
    exchange_name = os.getenv("PIPELINE_EVENTS_EXCHANGE", "pipeline_events_exchange")
    queue_name = os.getenv("PIPELINE_EVENTS_QUEUE", "pipeline_events_queue")
    routing_key = os.getenv("PIPELINE_EVENTS_ROUTING_KEY", "pipeline.events")
    vhost = os.getenv("AMQP_VHOST", "/")

    # Acquire a channel from the ResourceManager's RabbitMQ connection
    if rm.rabbit_mgr is None:
        raise ValueError("ResourceManager.rabbit_mgr is not initialized.")
    async with rm.rabbit_mgr.get_channel() as channel:
        # Idempotently declare the exchange
        await channel.declare_exchange(exchange_name, type=ExchangeType.TOPIC, durable=True)
        # Idempotently declare the queue
        queue = await channel.declare_queue(queue_name, durable=True)
        # Bind the queue to the exchange using the routing key
        await queue.bind(exchange_name, routing_key=routing_key)
        logger.info(
            f"Declared exchange '{exchange_name}', queue '{queue_name}', bound with routing key '{routing_key}' on vhost '{vhost}'."
        )
    
    return exchange_name, queue_name, routing_key
