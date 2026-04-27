#src/agentcy/agent_runtime/consumers.py
import json
import logging
import asyncio
import os
from typing import Any, Awaitable, Callable

from aio_pika import ExchangeType
from agentcy.agent_runtime.runner import Runner
from agentcy.agent_runtime.event_handler import (
    pipeline_event_listener,
    make_on_pipeline_event,
    pipeline_entry_listener
)
from agentcy.pydantic_models.commands import StartPipelineCommand
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.agent_runtime.forwarder import MicroserviceLogicFunc
from agentcy.orchestrator_core.handlers.run_mover import finalize_run

log = logging.getLogger("agent_runtime.consumers")


def _start_pipeline_consumer_enabled(service_name: str) -> bool:
    """Only the designated launcher service should consume StartPipelineCommand."""
    explicit = os.getenv("START_PIPELINE_CONSUMER_ENABLE")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}
    launcher_service = os.getenv("START_PIPELINE_LAUNCHER_SERVICE", "call-transcription").strip()
    return service_name == launcher_service


async def _start_pipeline_consumer(
    rm: ResourceManager,
    on_event: Callable[[dict], Awaitable[None]],
) -> None:

    # Wait until ResourceManager finished initialising pools/stores.
    await rm.ready_event.wait()
    log.info("StartPipelineCommand consumer: ResourceManager ready; proceeding to bind queue.")

    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        log.warning("StartPipelineCommand consumer skipped: no RabbitMQ manager.")
        return

    async with rabbit_mgr.get_channel() as channel:
        log.info("Setting up StartPipelineCommand consumer…")
        exchange = await channel.declare_exchange("commands", ExchangeType.TOPIC, durable=True)
        queue = await channel.declare_queue("commands.start_pipeline", durable=True)
        await queue.bind(exchange, routing_key="commands.start_pipeline")
        log.info("Start-pipeline queue declared and bound to 'commands' exchange")
        log.info("Listening for StartPipelineCommand on 'commands.start_pipeline'")
        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    log.info("Received raw message body: %s", msg.body)
                    try:
                        if not rm.ready_event.is_set():
                            await rm.ready_event.wait()
                        cmd = StartPipelineCommand.model_validate_json(msg.body.decode())
                        log.info("→ StartPipelineCommand: %s", cmd)
                        log.info("Invoking Runner.launch for %s/%s", cmd.username, cmd.pipeline_id)
                        runner = Runner(rm)
                        run_id = await runner.launch(
                            username=cmd.username,
                            pipeline_id=cmd.pipeline_id,
                            pipeline_run_config_id=cmd.pipeline_run_config_id,
                        )
                        log.info("→ Launched pipeline run %s for %s/%s",
                                 run_id, cmd.username, cmd.pipeline_id)
                        
                        task = asyncio.create_task(
                            pipeline_entry_listener(
                                rm,
                                run_id=run_id,
                                on_event=on_event),
                            name=f"pipeline-entry-{run_id}",
                        )
                        log.info("Spawned pipeline_entry_listener task %s", task.get_name())

                    except Exception:
                        log.exception("Error in StartPipelineCommand handler", exc_info=True)

                    

async def _run_finalizer_consumer(rm: ResourceManager) -> None:
    """
    Fire `finalize_run()` when the pipeline broadcasts that it has finished.
    """
    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        log.warning("StartPipelineCommand consumer skipped: no RabbitMQ manager.")
        return
    
    async with rabbit_mgr.get_channel() as ch:
        exchange = await ch.declare_exchange(
            "pipeline_events_exchange", ExchangeType.TOPIC, durable=True
        )

        # isolate this consumer with its own fan-out queue
        queue = await ch.declare_queue(
            "", exclusive=True, durable=False, auto_delete=True
        )
        await queue.bind(exchange, routing_key="pipeline.events")
        log.info("Run-finalizer consumer ready – queue %s", queue.name)
        log.info("Configured run-finalizer on exchange '%s' → queue %s", exchange.name, queue.name)
        log.info("Run-finalizer consumer ready – listening for pipeline.events")

        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    log.info("Run-finalizer got raw msg: %s", msg.body)
                    evt = json.loads(msg.body)
                    log.info("Run-finalizer event: %s", evt.get("event"))

                    # The event the engine emits when everything is done
                    if evt.get("event") == "pipeline_completed":
                        status = evt.get("status", "COMPLETED")

                    # (legacy) some deployments emit run_status_changed
                    elif (
                        evt.get("event") == "run_status_changed"
                        and evt.get("status") in ("COMPLETED", "FAILED")
                    ):
                        status = evt["status"]
                    else:
                        continue  # not interesting for the finaliser
                    log.info("→ finalizing run %s (status=%s)", evt["pipeline_run_id"], status)

                    try:
                        hot = rm.ephemeral
                        if hot is None:
                            log.warning("Run-finalizer: skipping finalize_run – no ephemeral (hot) pool available.")
                            continue

                        finalize_run(
                            hot_pool=hot,
                            username=evt["username"],
                            pipeline_id=evt["pipeline_id"],
                            run_id=evt["pipeline_run_id"],
                    )
                    except Exception as e:
                        log.exception("Error finalizing run %s: %s", evt["pipeline_run_id"], e)
                    else:
                        log.info("Finalized run %s (%s)", evt["pipeline_run_id"], status)





async def run_consumers(
    rm: ResourceManager,
    service_name: str,
    microservice_logic: MicroserviceLogicFunc,
    *,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """
    Kick off both listeners:
     - commands.start_pipeline
     - pipeline registration / kickoff events
    """
    # 1) on-demand run kicker
    await rm.ready_event.wait()
    log.info("run_consumers: ResourceManager ready; starting consumers.")
    on_event = make_on_pipeline_event(rm, microservice_logic, service_name, shutdown_event=shutdown_event)
    tasks: list[asyncio.Task[None]] = []
    if rm.cb_pool and _start_pipeline_consumer_enabled(service_name):
        log.info("Spawning StartPipelineCommand consumer task…")
        task1 = asyncio.create_task(_start_pipeline_consumer(rm, on_event), name="start-pipeline")
        log.info("Task %s started", task1.get_name())
        tasks.append(task1)
    else:
        log.info(
            "Skipping StartPipeline consumer (cb_pool=%s launcher_enabled=%s service=%s)",
            bool(rm.cb_pool),
            _start_pipeline_consumer_enabled(service_name),
            service_name,
        )

    # 2) spin up consumers when a pipeline is registered/kicked off
    #    The microservice_logic callback handles task execution, while
    #    Runner.run_task is a separate internal handler for updating run state.
    log.info("Spawning PipelineRun event listener for service '%s'…", service_name)
    
    

    

    task2 = asyncio.create_task(pipeline_event_listener(rm, on_event), name="pipeline-event")
    tasks.append(task2)
    log.info("Task %s started", task2.get_name())

    log.info("Spawning run-finalizer consumer task…")
    task3 = asyncio.create_task(_run_finalizer_consumer(rm), name="run-finalizer")
    log.info("Task %s started", task3.get_name())
    tasks.append(task3)

    log.info("All consumer tasks up; entering gather()")
    await asyncio.gather(*tasks)
