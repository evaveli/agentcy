# src/agentcy/orchestrator_core/consumers/plan_revision.py
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from aio_pika import ExchangeType, Message, DeliveryMode

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import (
    PlanRevisedEvent,
    RevisePlanCommand,
    SchemaVersion,
)
from agentcy.pydantic_models.multi_agent_pipeline import PlanDraft, PlanRevision
from agentcy.api_service.dependecies import COMMAND_EXCHANGE_NAME

logger = logging.getLogger(__name__)


async def handle_revise_plan(
    cmd: RevisePlanCommand,
    rm: ResourceManager,
    publish_event: Callable,
) -> Optional[PlanRevisedEvent]:
    """
    Core handler for a single ``RevisePlanCommand``.

    Extracted from the consumer loop so it can be tested without RabbitMQ.

    Returns the ``PlanRevisedEvent`` on success, or ``None`` on skip/error.
    """
    store = rm.graph_marker_store
    username = cmd.username
    plan_id = cmd.plan_id

    # ── 1. Fetch candidate from Couchbase ──
    candidate_doc = store.get_raw(cmd.payload_ref)
    if not candidate_doc:
        logger.error(
            "Revision candidate not found for payload_ref=%s",
            cmd.payload_ref,
        )
        return None

    candidate_graph: Dict[str, Any] = candidate_doc.get("candidate_graph") or {}
    delta = candidate_doc.get("delta") or {}
    validation = candidate_doc.get("validation") or {}
    base_revision = int(candidate_doc.get("base_revision", 1))
    next_revision = int(candidate_doc.get("next_revision", base_revision + 1))

    # ── 2. Load current plan draft ──
    draft_doc = store.get_plan_draft(username=username, plan_id=plan_id)
    if draft_doc is None:
        logger.error("Plan draft not found for plan_id=%s", plan_id)
        return None
    draft = PlanDraft.model_validate(draft_doc)

    # ── 3. Optionally rebuild semantic RDF graph ──
    if os.getenv("SEMANTIC_RDF_EXPORT", "1") != "0":
        try:
            from agentcy.semantic.plan_graph import (
                build_plan_graph,
                serialize_graph,
            )
            from agentcy.semantic.fuseki_client import ingest_turtle
            from agentcy.semantic.namespaces import RESOURCE

            rdf_graph = build_plan_graph(
                candidate_graph,
                plan_id=draft.plan_id,
                pipeline_id=draft.pipeline_id,
                username=draft.username,
                include_prov=False,
            )
            turtle = serialize_graph(rdf_graph)
            candidate_graph["semantic_graph"] = {
                "format": "turtle",
                "data": turtle,
            }
            graph_uri = f"{RESOURCE}graph/plan/{draft.plan_id}"
            await ingest_turtle(turtle, graph_uri=graph_uri)
        except Exception:
            logger.exception(
                "Failed to rebuild semantic graph for %s", plan_id,
            )

    # ── 4. Update plan draft ──
    updated = draft.model_copy(
        update={
            "graph_spec": candidate_graph,
            "revision": next_revision,
            "is_valid": bool(validation.get("conforms")),
            "shacl_report": validation,
        }
    )
    store.save_plan_draft(username=username, draft=updated)

    # ── 5. Save plan revision record ──
    revision_doc = PlanRevision(
        plan_id=plan_id,
        username=username,
        pipeline_id=cmd.pipeline_id,
        pipeline_run_id=cmd.pipeline_run_id,
        revision=next_revision,
        parent_revision=base_revision,
        graph_spec=candidate_graph,
        delta=delta,
        status="APPLIED",
        created_by=cmd.created_by,
        reason=cmd.reason,
        validation=validation,
    )
    store.save_plan_revision(username=username, revision=revision_doc)

    # ── 6. Update run doc if applicable ──
    if cmd.pipeline_run_id and getattr(rm, "ephemeral_store", None):
        try:
            run_doc = rm.ephemeral_store.read_run(
                username, cmd.pipeline_id, cmd.pipeline_run_id,
            )
            if isinstance(run_doc, dict):
                run_doc["plan_id"] = plan_id
                run_doc["plan_revision"] = next_revision
                rm.ephemeral_store.update_run(
                    username,
                    cmd.pipeline_id,
                    cmd.pipeline_run_id,
                    run_doc,
                )
        except Exception:
            logger.debug(
                "Failed to update run doc revision", exc_info=True,
            )

    # ── 7. Build and publish PlanRevisedEvent ──
    evt = PlanRevisedEvent(
        schema_version=SchemaVersion.V1,
        username=username,
        pipeline_id=cmd.pipeline_id,
        plan_id=plan_id,
        revision=next_revision,
        pipeline_run_id=cmd.pipeline_run_id,
        created_by=cmd.created_by,
        reason=cmd.reason,
        timestamp=datetime.now(timezone.utc),
    )
    await publish_event(evt)
    logger.info(
        "PlanRevisedEvent published: plan_id=%s revision=%d",
        plan_id, next_revision,
    )
    return evt


async def revise_plan_consumer(rm: ResourceManager):
    """
    Listens for ``RevisePlanCommand`` on ``commands.revise_plan``.

    Flow
    ----
    1. Deserialise command
    2. Delegate to ``handle_revise_plan()``
    3. Publish resulting event to RabbitMQ
    """

    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        logger.warning("revise_plan_consumer: no RabbitMQ manager; skipping.")
        return

    store = rm.graph_marker_store
    if store is None:
        logger.error("revise_plan_consumer: graph_marker_store not configured.")
        return

    async with rabbit_mgr.get_channel() as channel:
        await channel.set_qos(prefetch_count=10)

        exchange = await channel.declare_exchange(
            COMMAND_EXCHANGE_NAME,
            ExchangeType.TOPIC,
            durable=True,
        )

        queue = await channel.declare_queue("commands.revise_plan", durable=True)
        await queue.bind(exchange, routing_key="commands.revise_plan")
        logger.info("Listening for RevisePlanCommand on 'commands.revise_plan'")

        async def _publish_event(evt: PlanRevisedEvent):
            await channel.default_exchange.publish(
                Message(
                    body=evt.model_dump_json().encode("utf-8"),
                    delivery_mode=DeliveryMode.PERSISTENT,
                    content_type="application/json",
                    message_id=str(uuid4()),
                    timestamp=int(datetime.now().timestamp()),
                ),
                routing_key="events.plan_revised",
            )

        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():
                    try:
                        cmd = RevisePlanCommand.model_validate_json(msg.body)
                    except Exception:
                        logger.exception("Failed to parse RevisePlanCommand")
                        continue

                    logger.info(
                        "Received RevisePlanCommand(username=%s, plan_id=%s, ref=%s)",
                        cmd.username, cmd.plan_id, cmd.payload_ref,
                    )
                    await handle_revise_plan(cmd, rm, _publish_event)
