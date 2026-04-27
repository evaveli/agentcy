# tests/integration_tests/microservice_library_integration_tests/test_consume_message.py
import asyncio
import json
from collections import defaultdict
from unittest.mock import MagicMock, patch

import aio_pika
import pytest
from aio_pika import ExchangeType

from src.agentcy.pipeline_orchestrator.pub_sub.consumer_wrapper import (
    AsyncPipelineConsumerManager,
    get_tasks_for_service_name,
    get_subscribe_queues_for_task,
    generate_final_config_for,
    ConsumerManager,
)

# --------------------------------------------------------------------------- #
#  Tiny stand-in ResourceManager                                              #
# --------------------------------------------------------------------------- #
AMQP_URI = "amqp://guest:guest@localhost:5672/"


class _ChannelContext:
    """async-context-manager that returns an aio-pika channel and closes it."""

    def __init__(self, connection: aio_pika.RobustConnection):
        self._connection = connection
        self._channel: aio_pika.Channel | None = None

    async def __aenter__(self):
        self._channel = await self._connection.channel()
        return self._channel

    async def __aexit__(self, exc_type, exc, tb):
        if self._channel and not self._channel.is_closed:
            await self._channel.close()


class DummyRabbitConn:
    """Mimics the real RabbitMQConnectionManager interface used in prod."""

    def __init__(self, connection: aio_pika.RobustConnection):
        self._connection = connection

    # the production code does: `async with rm.rabbit_conn.get_channel() as ch:`
    def get_channel(self):
        return _ChannelContext(self._connection)


class DummyResourceManager:
    """
    This is **all** the consumer code needs: a .rabbit_conn with get_channel().
    Other attributes (cb_pool, message_bus, …) are mocked out for completeness.
    """

    def __init__(self, connection: aio_pika.RobustConnection):
        self.rabbit_conn = DummyRabbitConn(connection)
        self.cb_pool = MagicMock()
        self.message_bus = MagicMock()
        self.doc_store = MagicMock()


# --------------------------------------------------------------------------- #
#  small helper utilities                                                     #
# --------------------------------------------------------------------------- #
async def _short_delay(sec: float = 2.0):
    """give consumers a moment to spin up in CI."""
    await asyncio.sleep(sec)


async def _publish(
    connection: aio_pika.RobustConnection,
    exchange_name: str,
    exchange_type: str,
    routing_key: str,
    body: dict,
):
    channel = await connection.channel()
    exchange = await channel.declare_exchange(
        exchange_name, type=exchange_type, durable=True
    )
    await exchange.publish(
        aio_pika.Message(body=json.dumps(body).encode()), routing_key=routing_key
    )
    await channel.close()


class FakeIncomingMessage:
    """Minimal stub so we can call default_handler() directly."""

    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    async def process(self, requeue: bool = False):
        class _Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False  # propagate exceptions

        return _Ctx()


# --------------------------------------------------------------------------- #
#  a tiny “final_config” slice used in the tests                              #
# --------------------------------------------------------------------------- #
MINIMAL_FINAL_CONFIG = {
    "queues": {
        "test_queue": {"queue_name": "test_queue", "to_task": "task_11"}
    },
    "rabbitmq_configs": [
        {
            "task_id": "task_11",
            "rabbitmq": {
                "queue": "test_queue",
                "exchange": "test_exchange",
                "exchange_type": "direct",
                "routing_key": "test.key",
            },
        }
    ],
    "fan_in_metadata": {"task_11": {"required_steps": ["task_10", "task_5"]}},
    "task_dict": {
        "task_11": {"service_name": "test_service"},
        "task_10": {"service_name": "test_service"},
        "task_5": {"service_name": "test_service"},
    },
}

FULL_CONFIG = MINIMAL_FINAL_CONFIG  # re-used by the 3rd test


# --------------------------------------------------------------------------- #
#  TESTS                                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_default_handler_aggregates_message():
    connection = await aio_pika.connect_robust(AMQP_URI)
    rm = DummyResourceManager(connection)
    manager = AsyncPipelineConsumerManager(MINIMAL_FINAL_CONFIG, rm)

    qinfo = MINIMAL_FINAL_CONFIG["queues"]["test_queue"]
    msg = FakeIncomingMessage(
        {"pipeline_run_id": "run123", "from_task": "upstream_task", "payload": {}}
    )
    await manager.default_handler(msg, qinfo)

    assert "run123_task_11" in manager.aggregator_store
    await connection.close()


@pytest.mark.asyncio
async def test_custom_handler_invoked():
    connection = await aio_pika.connect_robust(AMQP_URI)
    rm = DummyResourceManager(connection)
    manager = AsyncPipelineConsumerManager(MINIMAL_FINAL_CONFIG, rm)
    event = asyncio.Event()

    async def _custom(message, qinfo):
        if json.loads(message.body.decode()).get("pipeline_run_id") == "run_custom":
            event.set()

    manager.register_queue_handler("test_queue", _custom)

    # monkey-patch consume logic to run without ACK hassle
    async def _patched_consume(queue_name, qinfo):
        async with rm.rabbit_conn.get_channel() as ch:
            q = await ch.declare_queue(queue_name, durable=True)
            await manager._setup_exchange_binding(ch, q, qinfo)

            async def _cb(message: aio_pika.IncomingMessage):
                async with message.process():
                    await manager.queue_handlers[queue_name](message, qinfo)

            await q.consume(_cb, no_ack=True)
            await asyncio.Future()  # keep task alive

    manager._consume_queue = _patched_consume  # type: ignore

    asyncio.create_task(manager.start_consumers())
    await _short_delay()

    await _publish(
        connection,
        "test_exchange",
        "direct",
        "test.key",
        {"pipeline_run_id": "run_custom", "from_task": "sender", "payload": {}},
    )

    await asyncio.wait_for(event.wait(), timeout=10)
    for t in manager.consumer_tasks:
        t.cancel()
    await asyncio.gather(*manager.consumer_tasks, return_exceptions=True)
    await connection.close()


@pytest.mark.asyncio
async def test_consumer_manager_integration_alternative():
    connection = await aio_pika.connect_robust(AMQP_URI)
    rm = DummyResourceManager(connection)

    # NOTE: correct import path for consumer_wrapper
    with patch(
        "src.agentcy.pipeline_orchestrator.pub_sub.consumer_wrapper.get_final_config",
        return_value=FULL_CONFIG,
    ):
        cm = ConsumerManager(
            "test_service",
            rm,
            username="dummy",
            pipeline_id="pipeline123",
            pipeline_run_id="run_cm",
        )
        q = asyncio.Queue()

        async def _custom(message, qinfo):
            body = json.loads(message.body.decode())
            if body.get("pipeline_run_id") == "cm_run":
                await q.put(body)

        cm._manager.register_queue_handler("test_queue", _custom)
        asyncio.create_task(cm._manager.start_consumers())
        await _short_delay(5)

        await _publish(
            connection,
            "test_exchange",
            "direct",
            "test.key",
            {"pipeline_run_id": "cm_run", "from_task": "sender", "payload": {}},
        )

        try:
            got = await asyncio.wait_for(q.get(), timeout=15)
            assert got["pipeline_run_id"] == "cm_run"
        finally:
            for t in cm._manager.consumer_tasks:
                t.cancel()
            await asyncio.gather(*cm._manager.consumer_tasks, return_exceptions=True)
            await connection.close()
            await asyncio.sleep(0.5)
