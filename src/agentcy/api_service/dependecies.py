# src/agentcy/api_service/dependecies.py
import asyncio
import logging
import os
from datetime import datetime
from uuid import uuid4
from typing import Any, Optional

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel
from aio_pika import ExchangeType, Message, DeliveryMode
from aio_pika.exceptions import AMQPError

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pipeline_orchestrator.pub_sub.connection_manager import RabbitMQConnectionManager
from agentcy.pydantic_models.pagination import PaginationParams, SortParams, SortOrder

logger = logging.getLogger(__name__)

def get_rm(request: Request) -> ResourceManager:
    rm: ResourceManager | None = getattr(request.app.state, "rm", None)
    if rm is None:
        raise HTTPException(503, "resource-manager not initialised")
    return rm

RABBIT_URI             = os.getenv("AMQP_URI", "amqp://guest:guest@localhost:5672/")
COMMAND_EXCHANGE_NAME  = os.getenv("COMMAND_EXCHANGE", "commands")
COMMAND_EXCHANGE_TYPE  = ExchangeType.TOPIC
PUBLISH_RETRY_ATTEMPTS = int(os.getenv("PUBLISH_RETRY_ATTEMPTS", "5"))
PUBLISH_RETRY_BACKOFF  = float(os.getenv("PUBLISH_RETRY_BACKOFF_SEC", "0.5"))


class CommandPublisher:
    def __init__(self, mgr: RabbitMQConnectionManager):
        self._mgr = mgr
        self._lock = asyncio.Lock()

    async def publish(
        self,
        routing_key: str,
        payload: BaseModel,
    ) -> None:
        """
        Publish a Pydantic BaseModel as JSON to our named 'commands' topic exchange.

        • durable messages
        • publisher confirms (channel created with confirms enabled by the manager)
        • retries with backoff
        • rich metadata (message_id, timestamp, type, app_id)
        """
        body = payload.model_dump_json().encode("utf-8")

        # Optional header: include schema_version if model defines it
        _sv: Optional[Any] = getattr(payload, "schema_version", None)
        _headers: dict[str, Any] | None = {"schema_version": _sv} if isinstance(_sv, (str, int)) else None

        msg = Message(
            body=body,
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            message_id=str(uuid4()),
            timestamp=int(datetime.now().timestamp()),
            type=payload.__class__.__name__,
            app_id="api_service",
            headers=_headers,
        )

        last_exc: Exception | None = None
        for attempt in range(1, PUBLISH_RETRY_ATTEMPTS + 1):
            try:
                # Open a fresh channel, (re)declare exchange idempotently, publish, close.
                async with self._mgr.get_channel() as channel:
                    await channel.set_qos(prefetch_count=1)
                    exchange = await channel.declare_exchange(
                        COMMAND_EXCHANGE_NAME,
                        COMMAND_EXCHANGE_TYPE,
                        durable=True,
                    )
                    await exchange.publish(msg, routing_key=routing_key)

                logger.info(
                    "Published %s(%s) → exchange=%r routing_key=%r",
                    msg.type, msg.message_id, COMMAND_EXCHANGE_NAME, routing_key
                )
                return
            except AMQPError as e:
                last_exc = e
                backoff = PUBLISH_RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "Publish attempt %d/%d failed (%s). retrying in %.1fs…",
                    attempt, PUBLISH_RETRY_ATTEMPTS, e, backoff
                )
                await asyncio.sleep(backoff)

        # if we get here, all retries failed
        logger.error(
            "Failed to publish after %d attempts: exchange=%r rk=%r payload=%s",
            PUBLISH_RETRY_ATTEMPTS, COMMAND_EXCHANGE_NAME, routing_key, payload
        )
        raise last_exc or RuntimeError("Unknown publish error")


# ──────────────────────────────────────────────────────────────────────────────
# FastAPI dependency to inject your publisher into path operations
# ──────────────────────────────────────────────────────────────────────────────
async def get_publisher(
    rm: ResourceManager = Depends(get_rm),
) -> CommandPublisher:
    mgr = rm.rabbit_mgr
    if mgr is None:
        raise HTTPException(503, "RabbitMQ manager not initialised")
    return CommandPublisher(mgr)


# ──────────────────────────────────────────────────────────────────────────────
# Pagination and Sorting Dependencies
# ──────────────────────────────────────────────────────────────────────────────
def pagination_params(
    limit: Optional[int] = Query(
        default=None,
        ge=1,
        le=1000,
        description="Max items to return. Omit for all (backward compatible).",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Items to skip.",
    ),
) -> PaginationParams:
    """FastAPI dependency for pagination parameters."""
    return PaginationParams(limit=limit, offset=offset)


def sort_params(
    sort_by: Optional[str] = Query(
        default=None,
        description="Field to sort by.",
    ),
    sort_order: SortOrder = Query(
        default=SortOrder.DESC,
        description="Sort direction: 'asc' or 'desc'.",
    ),
) -> SortParams:
    """FastAPI dependency for sorting parameters."""
    return SortParams(sort_by=sort_by, sort_order=sort_order)
