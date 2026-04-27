# src/agentcy/pipeline_orchestrator/pub_sub/connection_manager.py

import asyncio
import random
import aio_pika
import aiormq
from aio_pika.abc import AbstractRobustConnection, AbstractChannel  # ← add
from contextlib import asynccontextmanager
import contextlib
import os
import logging
from aio_pika.exceptions import AMQPConnectionError
from typing import AsyncGenerator, Optional

import urllib.parse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _resolve_amqp_uri() -> str:
    user  = os.getenv("RABBITMQ_DEFAULT_USER", "guest")
    pwd   = os.getenv("RABBITMQ_DEFAULT_PASS", "guest")
    host  = os.getenv("RABBITMQ_HOST", "rabbitmq")     # <— service DNS name
    port  = os.getenv("RABBITMQ_PORT", "5672")
    vhost = urllib.parse.quote(os.getenv("RABBITMQ_DEFAULT_VHOST", "/"), safe="")  # %2F
    return f"amqp://{user}:{pwd}@{host}:{port}/{vhost}"

def _redact(uri: str) -> str:
    try:
        if "://" in uri and "@" in uri:
            scheme, rest = uri.split("://", 1)
            creds, tail = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:******@{tail}"
    except Exception:
        pass
    return uri

class RabbitMQConnectionManager:
    """
    Robust connection manager with:
      - Persistent connection reuse (singleton-ish)
      - Automatic reconnection with exponential backoff + jitter
      - Channel helper (async context manager)
      - Clean shutdown handling
    """

    _connection: Optional[AbstractRobustConnection] = None  # ← robust type
    _closing: bool = False
    _max_retries = 3
    _base_retry_delay = 1.0
    _max_retry_delay = 10.0

    @classmethod
    async def get_connection(cls) -> AbstractRobustConnection:  # ← robust type
        amqp_uri = _resolve_amqp_uri()
        if not amqp_uri:
            raise EnvironmentError("AMQP_URI environment variable is not set.")

        if cls._connection and not cls._connection.is_closed:
            return cls._connection

        for attempt in range(cls._max_retries):
            try:
                logger.info("Attempting to connect to RabbitMQ (attempt %d).", attempt + 1)
                if cls._connection is None or cls._connection.is_closed:
                    cls._connection = await aio_pika.connect_robust(
                        amqp_uri,
                        timeout=10.0,  # float is fine for TimeoutType
                    )
                logger.info("Established new RabbitMQ connection")
                return cls._connection
            except AMQPConnectionError as conn_err:
                logger.warning(
                    "AMQP connection failed (attempt %d/%d): %s",
                    attempt + 1, cls._max_retries, conn_err
                )
                if attempt == cls._max_retries - 1:
                    raise
                delay = cls._exponential_backoff_delay(attempt)
                logger.info("Retrying in %.2f seconds...", delay)
                await asyncio.sleep(delay)
            except Exception as e:
                logger.exception(
                    "Unexpected error while connecting to RabbitMQ (attempt %d/%d): %s",
                    attempt + 1, cls._max_retries, e
                )
                if attempt == cls._max_retries - 1:
                    raise
                delay = cls._exponential_backoff_delay(attempt)
                logger.info("Retrying in %.2f seconds...", delay)
                await asyncio.sleep(delay)

        raise AMQPConnectionError("Exceeded maximum retries to connect to RabbitMQ.")

    @classmethod
    @asynccontextmanager
    async def get_channel(cls) -> AsyncGenerator[AbstractChannel, None]:  # ← typed
        """Yield a channel and close it on exit."""
        channel: AbstractChannel | None = None
        last_exc: Exception | None = None
        for attempt in range(2):
            connection = await cls.get_connection()
            try:
                channel = await connection.channel()
                break
            except (RuntimeError, AMQPConnectionError) as exc:
                last_exc = exc
                logger.warning("Channel open failed (%s). Reconnecting...", exc)
                cls._connection = None
                if attempt == 1:
                    raise
                await asyncio.sleep(0.5)
        if channel is None:
            raise last_exc or RuntimeError("Failed to open RabbitMQ channel")
        try:
            yield channel
        finally:
            if not channel.is_closed:
                try:
                    await channel.close()
                    logger.info("Channel closed cleanly.")
                except (asyncio.CancelledError, aiormq.exceptions.ChannelInvalidStateError):
                    logger.info("Channel close skipped during shutdown.")

    @classmethod
    async def close(cls) -> None:
        if not cls._connection or cls._connection.is_closed:
            cls._connection = None
            return
        cls._closing = True
        connection = cls._connection
        try:
            channels = getattr(connection, "_RobustConnection__channels", None)
            if channels:
                await asyncio.gather(
                    *(
                        ch.close()
                        for ch in tuple(channels)
                        if getattr(ch, "is_closed", False) is False
                    ),
                    return_exceptions=True,
                )
        finally:
            with contextlib.suppress(Exception):
                await connection.close(exc=asyncio.CancelledError)
                if hasattr(connection, "closed"):
                    await connection.closed()
            logger.info("Closed RabbitMQ connection.")
            cls._connection = None
            cls._closing = False

    @classmethod
    def _exponential_backoff_delay(cls, attempt: int) -> float:
        base = cls._base_retry_delay * (2 ** attempt)
        jitter = random.uniform(0, 0.3 * base)
        return min(base + jitter, cls._max_retry_delay)
