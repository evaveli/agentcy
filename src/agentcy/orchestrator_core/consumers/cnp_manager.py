# src/agentcy/orchestrator_core/consumers/cnp_manager.py
"""CNP Manager consumer — orchestrates the full Announce → Bid → Award cycle.

Implements the paper's improved Contract Net Protocol with ant-colony-optimised
bidding (S > τ threshold filtering, dynamic stimulus escalation, evaluation
sequences for failure re-forwarding).

Feature-flagged behind ``CNP_MANAGER_ENABLE=1`` (default OFF).
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from aio_pika import ExchangeType, Message, DeliveryMode

from agentcy.api_service.dependecies import COMMAND_EXCHANGE_NAME
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import (
    CFPBroadcastEvent,
    CNPCycleCompletedEvent,
    CNPCycleStartedEvent,
    CNPRoundCompletedEvent,
    RunCNPCycleCommand,
    SchemaVersion,
)
from agentcy.pydantic_models.multi_agent_pipeline import (
    CNPCycleState,
    CNPCycleStatus,
)
from agentcy.agent_runtime.services import blueprint_bidder, path_seeder
from agentcy.agent_runtime.services.graph_builder import build_plan_draft

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
#  Handler (extracted for testability)
# ---------------------------------------------------------------------------
async def handle_run_cnp_cycle(
    cmd: RunCNPCycleCommand,
    rm: ResourceManager,
    publish_event: Callable,
) -> Optional[CNPCycleState]:
    """Orchestrate a full CNP Announce → Bid → Award cycle.

    Parameters
    ----------
    cmd : RunCNPCycleCommand
        The command with cycle parameters.
    rm : ResourceManager
        Shared resource manager (stores, RabbitMQ, etc.).
    publish_event : Callable
        ``async def publish_event(evt, routing_key)`` closure from the consumer.

    Returns
    -------
    CNPCycleState | None
        The final cycle state, or None if the store is unavailable.
    """
    store = rm.graph_marker_store
    if store is None:
        logger.error("handle_run_cnp_cycle: graph_marker_store not configured")
        return None

    max_rounds = cmd.max_rounds or _env_int("CNP_MAX_ROUNDS", 3)
    bid_timeout = cmd.bid_timeout_seconds or _env_int("CNP_CFP_TTL_SECONDS", 300)
    round_delay = _env_float("CNP_CENTRALIZED_ROUND_DELAY", 0.5)

    # ── 1. Initialise cycle state ──────────────────────────────────────────
    cycle = CNPCycleState(
        username=cmd.username,
        pipeline_id=cmd.pipeline_id,
        pipeline_run_id=cmd.pipeline_run_id,
        status=CNPCycleStatus.BIDDING,
        task_ids=list(cmd.task_ids),
        max_rounds=max_rounds,
        bid_timeout_seconds=bid_timeout,
    )
    store.save_cnp_cycle(username=cmd.username, cycle=cycle)

    # ── 2. Emit CNPCycleStartedEvent ───────────────────────────────────────
    task_count = len(cmd.task_ids) if cmd.task_ids else 0
    if task_count == 0:
        # Count all task specs for this user when no specific tasks given
        try:
            specs_raw, _ = store.list_task_specs(username=cmd.username)
            task_count = len(specs_raw)
        except Exception:
            task_count = 0

    await publish_event(
        CNPCycleStartedEvent(
            username=cmd.username,
            pipeline_id=cmd.pipeline_id,
            pipeline_run_id=cmd.pipeline_run_id,
            cycle_id=cycle.cycle_id,
            task_count=task_count,
            max_rounds=max_rounds,
            timestamp=_now_utc(),
        ),
        "events.cnp_cycle_started",
    )

    # ── 3. Multi-round bidding loop ────────────────────────────────────────
    total_bids = 0
    for round_num in range(1, max_rounds + 1):
        cycle.current_round = round_num

        # Call centralized bidder
        bid_result: Dict[str, Any] = {}
        try:
            bid_msg: Dict[str, Any] = {
                "username": cmd.username,
                "data": {
                    "cnp_force_local": True,
                    "pipeline_id": cmd.pipeline_id,
                },
            }
            if cmd.task_ids:
                bid_msg["data"]["task_ids"] = list(cmd.task_ids)
            bid_result = await blueprint_bidder.run(
                rm,
                f"cnp-cycle-{cycle.cycle_id}",
                "blueprint_bidder",
                None,
                bid_msg,
            )
        except Exception:
            logger.exception(
                "CNP cycle %s: bidder failed on round %d", cycle.cycle_id, round_num,
            )

        bids_created = bid_result.get("bids_created", 0)
        cfps_created = bid_result.get("cfps_created", 0)
        total_bids += bids_created

        # Emit CFPBroadcastEvent (hook for future distributed mode)
        cfp_ids = bid_result.get("cfp_ids", [])
        required_caps = bid_result.get("required_capabilities", [])
        stimulus = bid_result.get("stimulus", 0.0)
        closes_at = _now_utc() + timedelta(seconds=bid_timeout)

        await publish_event(
            CFPBroadcastEvent(
                username=cmd.username,
                pipeline_id=cmd.pipeline_id,
                cycle_id=cycle.cycle_id,
                round_number=round_num,
                cfp_ids=cfp_ids if isinstance(cfp_ids, list) else [],
                required_capabilities=required_caps if isinstance(required_caps, list) else [],
                stimulus=float(stimulus) if stimulus else 0.0,
                closes_at=closes_at,
                timestamp=_now_utc(),
            ),
            "events.cfp_broadcast",
        )

        # Check bid coverage
        tasks_with_bids = 0
        tasks_without_bids = 0
        target_task_ids = set(cmd.task_ids) if cmd.task_ids else set()

        try:
            all_bids, _ = store.list_bids(username=cmd.username)
            if target_task_ids:
                covered = {b.get("task_id") for b in all_bids if b.get("task_id") in target_task_ids}
                tasks_with_bids = len(covered)
                tasks_without_bids = len(target_task_ids) - tasks_with_bids
            else:
                covered = {b.get("task_id") for b in all_bids}
                tasks_with_bids = len(covered)
                tasks_without_bids = max(0, task_count - tasks_with_bids)
        except Exception:
            logger.debug("CNP cycle %s: failed to check bid coverage", cycle.cycle_id)

        # Record round summary
        round_summary = {
            "round": round_num,
            "bids_collected": bids_created,
            "cfps_created": cfps_created,
            "tasks_with_bids": tasks_with_bids,
            "tasks_without_bids": tasks_without_bids,
            "stimulus": float(stimulus) if stimulus else 0.0,
        }
        cycle.round_history.append(round_summary)

        await publish_event(
            CNPRoundCompletedEvent(
                username=cmd.username,
                pipeline_id=cmd.pipeline_id,
                cycle_id=cycle.cycle_id,
                round_number=round_num,
                bids_collected=bids_created,
                tasks_with_bids=tasks_with_bids,
                tasks_without_bids=tasks_without_bids,
                stimulus_level=float(stimulus) if stimulus else 0.0,
                timestamp=_now_utc(),
            ),
            "events.cnp_round_completed",
        )

        # Persist intermediate state
        cycle.total_bids = total_bids
        store.save_cnp_cycle(username=cmd.username, cycle=cycle)

        # Break early if all tasks covered
        if tasks_without_bids == 0 and tasks_with_bids > 0:
            logger.info(
                "CNP cycle %s: all tasks covered after round %d",
                cycle.cycle_id, round_num,
            )
            break

        # Delay between rounds (centralized mode)
        if round_num < max_rounds:
            await asyncio.sleep(round_delay)

    # ── 4. Award phase ─────────────────────────────────────────────────────
    cycle.status = CNPCycleStatus.AWARDING
    store.save_cnp_cycle(username=cmd.username, cycle=cycle)

    plan_draft = None
    try:
        plan_draft = await build_plan_draft(
            rm,
            username=cmd.username,
            pipeline_id=cmd.pipeline_id,
            pipeline_run_id=cmd.pipeline_run_id,
            task_ids=list(cmd.task_ids) if cmd.task_ids else None,
        )
    except Exception:
        logger.exception("CNP cycle %s: build_plan_draft failed", cycle.cycle_id)
        cycle.status = CNPCycleStatus.FAILED
        cycle.error = "graph_builder_failed"
        cycle.completed_at = _now_utc()
        store.save_cnp_cycle(username=cmd.username, cycle=cycle)
        return cycle

    cycle.plan_id = plan_draft.plan_id if plan_draft else None

    # ── 5. Seed pheromone markers (non-fatal) ──────────────────────────────
    try:
        await path_seeder.run(
            rm,
            f"cnp-seed-{cycle.cycle_id}",
            "path_seeder",
            None,
            {"username": cmd.username, "data": {"pipeline_id": cmd.pipeline_id}},
        )
    except Exception:
        logger.debug("CNP cycle %s: path_seeder failed (non-fatal)", cycle.cycle_id, exc_info=True)

    # ── 6. Finalise ────────────────────────────────────────────────────────
    cycle.status = CNPCycleStatus.COMPLETED
    cycle.total_bids = total_bids
    cycle.completed_at = _now_utc()
    store.save_cnp_cycle(username=cmd.username, cycle=cycle)

    # Count awarded / unawarded tasks
    tasks_awarded = 0
    tasks_unawarded = 0
    if plan_draft and plan_draft.graph_spec:
        assignments = plan_draft.graph_spec.get("assignments", {})
        tasks_awarded = len(assignments)
        if target_task_ids:
            tasks_unawarded = len(target_task_ids) - tasks_awarded
        else:
            tasks_unawarded = max(0, task_count - tasks_awarded)

    await publish_event(
        CNPCycleCompletedEvent(
            username=cmd.username,
            pipeline_id=cmd.pipeline_id,
            pipeline_run_id=cmd.pipeline_run_id,
            cycle_id=cycle.cycle_id,
            plan_id=cycle.plan_id or "",
            total_rounds=cycle.current_round,
            total_bids=total_bids,
            tasks_awarded=tasks_awarded,
            tasks_unawarded=tasks_unawarded,
            timestamp=_now_utc(),
        ),
        "events.cnp_cycle_completed",
    )

    logger.info(
        "CNP cycle %s completed: rounds=%d bids=%d awarded=%d plan=%s",
        cycle.cycle_id, cycle.current_round, total_bids,
        tasks_awarded, cycle.plan_id,
    )
    return cycle


# ---------------------------------------------------------------------------
#  Consumer
# ---------------------------------------------------------------------------
async def cnp_manager_consumer(rm: ResourceManager):
    """Listens for ``RunCNPCycleCommand`` on ``commands.run_cnp_cycle``.

    Feature-gated: exits immediately when ``CNP_MANAGER_ENABLE != "1"``.
    """
    if os.getenv("CNP_MANAGER_ENABLE", "0") != "1":
        logger.info("cnp_manager_consumer: CNP_MANAGER_ENABLE != 1; idling.")
        while True:
            await asyncio.sleep(3600)

    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        logger.warning("cnp_manager_consumer: no RabbitMQ manager; skipping.")
        return

    prefetch = _env_int("CNP_MANAGER_PREFETCH", 5)

    async with rabbit_mgr.get_channel() as channel:
        await channel.set_qos(prefetch_count=prefetch)

        exchange = await channel.declare_exchange(
            COMMAND_EXCHANGE_NAME,
            ExchangeType.TOPIC,
            durable=True,
        )

        queue = await channel.declare_queue("commands.run_cnp_cycle", durable=True)
        await queue.bind(exchange, routing_key="commands.run_cnp_cycle")
        logger.info("Listening for RunCNPCycleCommand on 'commands.run_cnp_cycle'")

        async def _publish_event(evt, routing_key: str = "events.cnp"):
            await channel.default_exchange.publish(
                Message(
                    body=evt.model_dump_json().encode("utf-8"),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                    message_id=str(uuid4()),
                    timestamp=int(datetime.now().timestamp()),
                ),
                routing_key=routing_key,
            )

        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    try:
                        cmd = RunCNPCycleCommand.model_validate_json(msg.body)
                    except Exception:
                        logger.exception("Failed to parse RunCNPCycleCommand")
                        continue

                    logger.info(
                        "Received RunCNPCycleCommand(pipeline=%s, user=%s, tasks=%d)",
                        cmd.pipeline_id, cmd.username, len(cmd.task_ids),
                    )
                    await handle_run_cnp_cycle(cmd, rm, _publish_event)
