# tests/e2e_tests/helpers_e2e.py
import asyncio
import json
import logging
import os
import time
from typing import Dict, Any, Set, DefaultDict

import aio_pika
from httpx import AsyncClient

from src.agentcy.agent_runtime.runner import Runner
from src.agentcy.agent_runtime.forwarder import DefaultForwarder, enforce_raw_output_structure
from src.agentcy.pydantic_models.commands import StartPipelineCommand
from src.agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from src.agentcy.pipeline_orchestrator.pub_sub.helpers import declare_event_resources
from src.agentcy.pipeline_orchestrator.pub_sub.consumer_wrapper import ConsumerManager
from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState, EntryMessage


TEST_USER = "e2e_tester"
RUN_CONFIG = "e2e_run_cfg"

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 1
MAX_WAIT_SECONDS = 300

AMQP_URI = os.getenv("AMQP_URI", "amqp://guest:guest@localhost:5672/")

# ────────────────────────────────────────────────────────────────────────────────
# Dummy micro-service logic used by all agent consumers
# ────────────────────────────────────────────────────────────────────────────────
@enforce_raw_output_structure
async def dummy_logic(message: Any) -> Dict[str, Any]:
    task_id = "entry"
    run_id = "unknown"
    if isinstance(message, TaskState):
        task_id, run_id = message.task_id, message.pipeline_run_id
    elif isinstance(message, EntryMessage):
        run_id = message.pipeline_run_id

    await asyncio.sleep(0.05)
    return {"raw_output": json.dumps({"ok": True, "task": task_id, "run": run_id})}


# ────────────────────────────────────────────────────────────────────────────────
# Pipeline-run polling helper
# ────────────────────────────────────────────────────────────────────────────────
async def poll_run_until(
    client:     AsyncClient,
    username:   str,
    pipeline_id:str,
    run_id:     str,
    target_status: str = "COMPLETED",
    timeout:    int = MAX_WAIT_SECONDS,
    interval:   int = POLL_INTERVAL_SECONDS
) -> Dict[str, Any]:
    url = f"/pipelines/{username}/{pipeline_id}/{run_id}"
    start = time.time()
    while time.time() - start < timeout:
        resp = await client.get(url)
        if resp.status_code == 404:
            await asyncio.sleep(interval)
            continue

        resp.raise_for_status()
        doc = resp.json()
        status = doc.get("status")
        logger.info("Polling run %s – status=%s", run_id, status)
        if status in ("COMPLETED", "FAILED"):
            assert status == target_status, f"Run ended with {status}"
            return doc

        await asyncio.sleep(interval)

    raise AssertionError(f"Run {run_id} did not reach {target_status}")


# ────────────────────────────────────────────────────────────────────────────────
# Fire‐and‐forget start‐pipeline helper (unchanged)
# ────────────────────────────────────────────────────────────────────────────────
async def publish_start_pipeline(username: str, pipeline_id: str, run_cfg: str) -> None:
    cmd = StartPipelineCommand(
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_config_id=run_cfg,
    )
    connection = await aio_pika.connect_robust(AMQP_URI)
    async with connection.channel() as ch:
        await ch.default_exchange.publish(
            aio_pika.Message(body=cmd.model_dump_json().encode()),
            routing_key="commands.start_pipeline",
        )
    await connection.close()
    logger.debug("StartPipelineCommand published for %s / %s", username, pipeline_id)


async def wait_until_registered(
    client:      AsyncClient,
    username:    str,
    pipeline_id: str,
    timeout:     float = 10.0,
    interval:    float = 0.5,
) -> None:
    """
    Polls GET /pipelines/{username}/{pipeline_id} until the endpoint
    returns 200 *and* the response body contains a non-empty
    'dag' / 'rabbitmq_configs' (i.e. the consumer finished).

    Raises TimeoutError if it doesn’t materialise in <timeout> seconds.
    """
    url   = f"/pipelines/{username}/{pipeline_id}"
    start = time.time()
    while time.time() - start < timeout:
        resp = await client.get(url)
        if resp.status_code == 200:
            body = resp.json()
            if body.get("rabbitmq_configs"):      # whatever field proves it's ready
                return
        await asyncio.sleep(interval)

    raise TimeoutError(f"Pipeline {pipeline_id} not registered within {timeout}s")

async def wait_for_run(client, pipeline_id: str, run_id: str, timeout=60):
    return await poll_run_until(
        client,
        TEST_USER,
        pipeline_id,     # now use the real pipeline_id
        run_id,
        target_status="COMPLETED",
        timeout=timeout,
    )