#src/agentcy/pipeline_orchestrator/pub_sub/pub_wrapper.py

import asyncio
import json
import logging
import os
import aio_pika
import aiormq
from typing import Dict, Any



def get_dynamic_names_from_config(rb: dict, run_id: str) -> tuple[str, str, str]:
    """
    Returns: (exchange_name, routing_key, exchange_type)
    Append run_id when per_run flag is set in config OR when PIPELINE_RUN_SUFFIX
    env var is enabled globally. This ensures publishers and consumers agree on
    queue names for run-scoped isolation.
    """
    import os
    exchange_name = rb.get("exchange") or ""
    exchange_type = (rb.get("exchange_type") or "direct").lower()

    routing_key = (rb.get("routing_key") or rb.get("queue") or "").strip()
    should_suffix = rb.get("per_run") or os.getenv("PIPELINE_RUN_SUFFIX", "0") != "0"
    if should_suffix and run_id:
        if routing_key and not routing_key.endswith(f".{run_id}"):
            routing_key = f"{routing_key}.{run_id}"
    return exchange_name, routing_key, exchange_type


def get_final_config(rm, username: str, pipeline_id: str) -> dict:
    """
    Helper to fetch final pipeline config using the ResourceManager's pipeline_store.
    """
    if hasattr(rm, "pipeline_store") and rm.pipeline_store:
        return rm.pipeline_store.get_final_config(username, pipeline_id)
    raise RuntimeError("ResourceManager missing pipeline_store; cannot load final config")


#TODO: Delete
async def publish_message(
        rm, 
        service_name: str,
        pipeline_run_id: str,
        pipeline_config_id: str,
        payload: dict          
):
    # Ensure that the RabbitMQ connection is properly initialized.
    if not getattr(rm, "rabbit_conn", None):
        logging.error("RabbitMQ connection is not properly initialized.")
        raise Exception("RabbitMQ connection not initialized in ResourceManager.")

    try:
        # Parse the configuration for the given service/task.
        store = getattr(rm, "pipeline_store", None)
        if store:
            final_config = await asyncio.to_thread(store.get_final_config, pipeline_config_id)
        else:
            # fallback to helper (often patched in tests)
            final_config = await asyncio.to_thread(get_final_config, rm, None, pipeline_config_id)
    except Exception as e:
        logging.error("Failed to retrieve pipeline configuration.", exc_info=True)
        raise
    
    try:
        for config_object in final_config["rabbitmq_configs"]:
            if config_object["task_id"] == service_name:
                rabbitmq_config = config_object["rabbitmq"]
                break
        else:
            raise KeyError(f"RabbitMQ configuration not found for service '{service_name}'.")
    except KeyError as e:
        logging.error("Configuration error: %s", e, exc_info=True)
        raise

    try:
        exchange_name, routing_key, exchange_type = get_dynamic_names_from_config(rabbitmq_config, pipeline_run_id)
    except KeyError as e:
        logging.error("Missing required configuration field: %s", e, exc_info=True)
        raise

    logging.info(
        f"Preparing to publish message for service '{service_name}': "
        f"exchange='{exchange_name}', type='{exchange_type}', routing_key='{routing_key}'."
    )

    try:
        async with rm.rabbit_conn.get_channel() as channel:
            exchange_obj = None
            try:
                exchange_obj = await channel.declare_exchange(
                    exchange_name,
                    type=exchange_type,
                    durable=True
                )
            except aiormq.exceptions.ChannelPreconditionFailed as exc:
                msg = str(exc).lower()
                existing_type = exchange_type
                if "current is 'topic'" in msg:
                    existing_type = "topic"
                elif "current is 'fanout'" in msg:
                    existing_type = "fanout"
                elif "current is 'direct'" in msg:
                    existing_type = "direct"

                # Adjust routing key if we fall back to topic and none provided
                if existing_type == "topic" and not routing_key:
                    routing_key = rabbitmq_config.get("routing_key") or rabbitmq_config.get("queue") or ""

                try:
                    await channel.exchange_delete(exchange_name, if_unused=False, if_empty=False)
                except Exception:
                    pass

                # Use a fresh channel if the current one was closed
                if getattr(channel, "is_closed", False):
                    async with rm.rabbit_conn.get_channel() as recovery_ch:
                        exchange_obj = await recovery_ch.declare_exchange(exchange_name, type=existing_type, durable=True)
                        channel = recovery_ch
                else:
                    exchange_obj = await channel.declare_exchange(exchange_name, type=existing_type, durable=True)

            message_body = {
                "pipeline_run_id": pipeline_run_id,
                "from_task": service_name,
                "payload": payload,
            }
            try:
                message_json = json.dumps(message_body)
            except Exception as e:
                logging.error("Failed to serialize message body.", exc_info=True)
                raise

            message = aio_pika.Message(body=message_json.encode("utf-8"))

            await exchange_obj.publish(message, routing_key=routing_key)
            logging.info(
                f"[Publisher] Sent from '{service_name}' to '{exchange_name}' "
                f"(rk='{routing_key}'): {message_body}"
            )
    except AttributeError as e:
        logging.error("RabbitMQ connection is not properly initialized.", exc_info=True)
        raise
    except aio_pika.exceptions.AMQPError as e:
        logging.error("AMQP error occurred during publishing.", exc_info=True)
        raise
    except Exception as e:
        logging.error("Unexpected error occurred during message publishing.", exc_info=True)
        raise
