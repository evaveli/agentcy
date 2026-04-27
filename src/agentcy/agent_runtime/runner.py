#src/agentcy/agent_runtime/runner.py

import asyncio
import json
import logging
import os
import uuid
from aio_pika import ExchangeType, Message, DeliveryMode
from datetime import datetime, timezone
from fastapi import HTTPException

from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import EntryMessage, TaskState, PipelineStatus, TaskStatus
from agentcy.orchestrator_core.utils import seed_initial_run
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager

logger = logging.getLogger(__name__)

class Runner:
    """
    1) launch(): handles StartPipelineCommand → EntryMessage → ephemeral doc
    2) run_task(): business-logic hook for incoming TaskState events
    """

    def __init__(self, rm: ResourceManager):
        self.rm = rm

    def _seed_initial_run(
        self,
        username: str,
        pipeline_id: str,
        run_id: str,
        pipeline_config_id: str = "",
        final_cfg: dict | None = None,
    ):
        # ① fetch final pipeline-config (contains task_dict)
        logger.info("→ _seed_initial_run(username=%s, pipeline_id=%s, run_id=%s)", username, pipeline_id, run_id)

        store = self.rm.pipeline_store
        if store is None:
            logger.error("ResourceManager.pipeline_store is not configured; cannot seed run %s", run_id)
            raise RuntimeError("pipeline_store missing on ResourceManager")
        if final_cfg is None:
            final_cfg = store.get_final_config(username, pipeline_id)

        logger.info("Fetched final_cfg for %s – tasks=%d", pipeline_id, len(final_cfg['task_dict']))
 

        # ② build a fully-formed PipelineRun with all tasks == PENDING
        pr = seed_initial_run(
            username=username,
            pipeline_id=pipeline_id,
            run_id=run_id,
            cfg=final_cfg,
            pipeline_config_id=pipeline_config_id
        )

        run_doc = pr.model_dump()
        run_doc["pipeline_config_id"] = pipeline_config_id

        try:
            hot = self.rm.ephemeral_store
            if hot is None:
                logger.error("ResourceManager.ephemeral_store is not configured; cannot create run %s", run_id)
                raise RuntimeError("ephemeral_store missing on ResourceManager")
            hot.create_run(username, pipeline_id, run_id, run_doc)
        except Exception:
            logger.exception("❌ Failed to create run doc %s in the hot bucket", run_id)
            raise
        else:
            logger.info("Hot-bucket run doc written (%s)", run_id)

    async def _wait_for_final_config(self, username: str, pipeline_id: str) -> dict:
        store = self.rm.pipeline_store
        if store is None:
            logger.error("ResourceManager.pipeline_store is not configured; cannot fetch final config")
            raise RuntimeError("pipeline_store missing on ResourceManager")

        attempts = int(os.getenv("PIPELINE_CONFIG_WAIT_ATTEMPTS", "20"))
        delay = float(os.getenv("PIPELINE_CONFIG_WAIT_SECONDS", "0.5"))
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return store.get_final_config(username, pipeline_id)
            except HTTPException as exc:
                last_exc = exc
                if exc.status_code == 404:
                    logger.info(
                        "Pipeline config not ready for %s/%s (attempt %d/%d)",
                        username,
                        pipeline_id,
                        attempt + 1,
                        attempts,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Pipeline config unavailable for {username}/{pipeline_id}")

    async def launch(self, *, username, pipeline_id, pipeline_run_config_id) -> str:
        logger.info("→ launch() username=%s pipeline_id=%s cfg_id=%s", username, pipeline_id, pipeline_run_config_id)
        run_id = str(uuid.uuid4())
        final_cfg = await self._wait_for_final_config(username, pipeline_id)
        self._seed_initial_run(username, pipeline_id, run_id, pipeline_run_config_id, final_cfg=final_cfg)
        entry = EntryMessage(
            pipeline_id=pipeline_id,
            username=username,
            pipeline_run_id=run_id,
            pipeline_config_id=pipeline_run_config_id,
        )

        logger.info("Ephemeral run doc %s created", run_id)
        logger.info("🟢 seeded run %s (user=%s, pipeline=%s)", run_id, username, pipeline_id)
        run_q  = f"pipeline_entry_queue_{run_id}"
        run_ex = f"pipeline_entry_{run_id}"

        rabbit_mgr = self.rm.rabbit_mgr
        if rabbit_mgr is None:
            logger.warning("StartPipelineCommand consumer skipped: no RabbitMQ manager.")
            raise RuntimeError("RabbitMQ manager missing; cannot launch pipeline run")
        
        async with rabbit_mgr.get_channel() as channel:
            exchange = await channel.declare_exchange(
                run_ex, type="direct", durable=True, auto_delete=True
            )

            queue  = await channel.declare_queue(run_q, durable=True, arguments={"x-expires": 900_000})
            await queue.bind(exchange, routing_key=run_q)
            logger.info(
                "📤 PUBLISH   exchange=%s rk=%s  payload=%s",
                run_ex, run_q, entry.model_dump_json(),
            )

            await exchange.publish(
                Message(
                    body=entry.model_dump_json().encode(),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key=run_q,
            )

            # declare the pipeline-events exchange
            events_xch = await channel.declare_exchange(
                "pipeline_events_exchange",
                ExchangeType.TOPIC,
                durable=True,
            )

            # emit the kickoff event (include pipeline_config_id so all
            # agents can construct EntryMessages for entry-task forwarding)
            await events_xch.publish(
                Message(
                    body=json.dumps({
                        "event":              "pipeline_started",
                        "username":           username,
                        "pipeline_id":        pipeline_id,
                        "pipeline_run_id":    run_id,
                        "pipeline_config_id": pipeline_run_config_id,
                    }).encode("utf-8"),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key="pipeline.events",
            )

        logger.info("pipeline_started event published for run %s", run_id)

        # ── Optional: auto-trigger CNP cycle ───────────────────────────────
        if (
            os.getenv("CNP_MANAGER_ENABLE", "0") == "1"
            and os.getenv("CNP_AUTO_ON_LAUNCH", "0") == "1"
        ):
            try:
                from agentcy.api_service.dependecies import CommandPublisher
                from agentcy.pydantic_models.commands import RunCNPCycleCommand

                pub = CommandPublisher(rabbit_mgr)
                await pub.publish(
                    "commands.run_cnp_cycle",
                    RunCNPCycleCommand(
                        username=username,
                        pipeline_id=pipeline_id,
                        pipeline_run_id=run_id,
                    ),
                )
                logger.info("CNP cycle auto-triggered for run %s", run_id)
            except Exception:
                logger.debug("CNP auto-trigger failed (non-fatal)", exc_info=True)

        logger.info("EntryMessage published to %s (run=%s)", run_q, run_id)
        return run_id

    @staticmethod
    async def run_task(message: TaskState, qinfo: dict, rm: ResourceManager) -> bool:
        """
        Internal handler for pipeline run state updates.

        Called to update the pipeline-run document when a task completes.
        This is NOT a microservice - it's an internal orchestration handler.

        Args:
            message: The TaskState containing task completion info
            qinfo: Queue metadata (currently unused, reserved for future use)
            rm: ResourceManager instance for store access

        Returns:
            bool: True if the run doc was successfully updated, False if the
                  run doc was not found (which may indicate a race condition
                  or stale message).

        Raises:
            RuntimeError: If the ephemeral store is not configured
        """
        logger.info("🔧 run_task(): %s status=%s  (%s/%s/%s)",
                     message.task_id, message.status,
                     message.username, message.pipeline_id, message.pipeline_run_id)

        store = rm.ephemeral_store
        if store is None:
            logger.error("Could not fetch the ephemeral store")
            raise RuntimeError("ephemeral_store is not configured on ResourceManager")

        pr_doc = store.read_run(
            message.username,
            message.pipeline_id,
            message.pipeline_run_id,
        )

        if not pr_doc:
            logger.warning("No ephemeral run doc found for %s/%s/%s - may be stale message",
                         message.username, message.pipeline_id, message.pipeline_run_id)
            return False

        logger.info("Loaded run doc – %d tasks tracked", len(pr_doc["tasks"]))

        # ---- 2) update the task entry --------------------------------------
        task_entry = pr_doc["tasks"].get(message.task_id)
        if task_entry is None:
            logger.warning("Task %s not found in run doc for %s - skipping update",
                          message.task_id, message.pipeline_run_id)
            return False

        task_entry["status"] = message.status
        task_entry["completed_at"] = datetime.now(timezone.utc).isoformat()

        # ---- 3) if this task failed → fail the whole pipeline --------------
        if message.status == TaskStatus.FAILED:
            pr_doc["status"] = PipelineStatus.FAILED

        # ---- 4) if it's the final task and succeeded → complete pipeline ---
        if (task_entry.get("is_final_task")
                and message.status == TaskStatus.COMPLETED):
            pr_doc["status"] = PipelineStatus.COMPLETED
        logger.info("🏷️  pipeline %s status→%s", message.pipeline_run_id, pr_doc["status"])

        # ---- 5) persist the changes ----------------------------------------
        store.update_run(
            message.username,
            message.pipeline_id,
            message.pipeline_run_id,
            pr_doc,
        )
        logger.info("Run doc updated for %s → status=%s", message.pipeline_run_id, pr_doc["status"])
        return True
