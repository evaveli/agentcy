# tests/integration_tests/microservice_library_integration_tests/test_publish_message.py
import asyncio
import json
import os
from unittest.mock import patch

import aio_pika
import pytest

from src.agentcy.pipeline_orchestrator.pub_sub.pub_wrapper import publish_message

# --------------------------------------------------------------------------- #
#  Mini stub ResourceManager (matches consumer tests)                         #
# --------------------------------------------------------------------------- #
AMQP_URI = os.getenv("AMQP_URI", "amqp://guest:guest@localhost:5672/")


class _ChannelContext:
    def __init__(self, connection: aio_pika.RobustConnection):
        self._conn = connection
        self._ch: aio_pika.Channel | None = None

    async def __aenter__(self):
        self._ch = await self._conn.channel()
        return self._ch

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._ch and not self._ch.is_closed:
            await self._ch.close()


class DummyRabbitConn:
    def __init__(self, connection: aio_pika.RobustConnection):
        self._conn = connection

    def get_channel(self):
        return _ChannelContext(self._conn)


class DummyResourceManager:
    """Just enough interface for pub_wrapper.publish_message()."""

    def __init__(self, connection: aio_pika.RobustConnection):
        self.rabbit_conn = DummyRabbitConn(connection)
        # other attributes never accessed by pub_wrapper but included for clarity
        self.message_bus = None
        self.cb_pool = None
        self.doc_store = None


# --------------------------------------------------------------------------- #
#  helper utilities                                                           #
# --------------------------------------------------------------------------- #
async def _delay(sec: float = 0.5):
    await asyncio.sleep(sec)


async def _setup_consumer(connection, exchange, ex_type, rk):
    ch = await connection.channel()
    ex = await ch.declare_exchange(exchange, type=ex_type, durable=True)
    q = await ch.declare_queue("", exclusive=True)
    await q.bind(ex, routing_key=rk)
    return q, ch


# --------------------------------------------------------------------------- #
#  TESTS                                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_publish_message_success_integration():
    svc = "integration_service"
    run_id = "integration_run"
    cfg_id = "integration_config"
    payload = {"test": "full integration"}

    final_cfg = {
        "rabbitmq_configs": [
            {
                "task_id": svc,
                "rabbitmq": {
                    "exchange": "integration_exchange",
                    "exchange_type": "direct",
                    "routing_key": "integration.key",
                },
            }
        ]
    }

    conn = await aio_pika.connect_robust(AMQP_URI)
    queue, ch = await _setup_consumer(conn, "integration_exchange", "direct", "integration.key")
    rm = DummyResourceManager(conn)

    with patch(
        "src.agentcy.pipeline_orchestrator.pub_sub.pub_wrapper.get_final_config",
        new=lambda *_, **__: final_cfg,
    ):
        await publish_message(rm, svc, run_id, cfg_id, payload)

    await _delay()
    msg = await queue.get(timeout=5)
    body = json.loads(msg.body.decode())
    assert body == {"pipeline_run_id": run_id, "from_task": svc, "payload": payload}
    await msg.ack()

    await ch.close()
    await conn.close()


@pytest.mark.asyncio
async def test_publish_message_missing_config_integration():
    svc, run_id, cfg_id = "missing_service", "run_123", "config_abc"
    conn = await aio_pika.connect_robust(AMQP_URI)
    rm = DummyResourceManager(conn)

    with patch(
        "src.agentcy.pipeline_orchestrator.pub_sub.pub_wrapper.get_final_config",
        new=lambda *_, **__: {"rabbitmq_configs": []},
    ):
        with pytest.raises(KeyError, match="RabbitMQ configuration not found for service"):
            await publish_message(rm, svc, run_id, cfg_id, {"x": 1})

    await conn.close()


@pytest.mark.asyncio
async def test_publish_message_no_connection_integration():
    svc, run_id, cfg_id = "svc", "run", "cfg"
    final_cfg = {
        "rabbitmq_configs": [
            {
                "task_id": svc,
                "rabbitmq": {
                    "exchange": "ex",
                    "exchange_type": "direct",
                    "routing_key": "rk",
                },
            }
        ]
    }

    class RMNoConn:
        rabbit_conn = None

    with patch(
        "src.agentcy.pipeline_orchestrator.pub_sub.pub_wrapper.get_final_config",
        new=lambda *_, **__: final_cfg,
    ):
        with pytest.raises(Exception, match="RabbitMQ connection not initialized in ResourceManager"):
            await publish_message(RMNoConn(), svc, run_id, cfg_id, {})


@pytest.mark.asyncio
async def test_alternate_exchange_type_fanout():
    svc, run_id, cfg_id = "fanout_service", "fanout_run", "fanout_cfg"
    payload = {"msg": "fanout"}
    final_cfg = {
        "rabbitmq_configs": [
            {
                "task_id": svc,
                "rabbitmq": {
                    "exchange": "fanout_exchange",
                    "exchange_type": "fanout",
                    "routing_key": "",
                },
            }
        ]
    }

    conn = await aio_pika.connect_robust(AMQP_URI)
    queue, ch = await _setup_consumer(conn, "fanout_exchange", "fanout", "")
    rm = DummyResourceManager(conn)

    with patch(
        "src.agentcy.pipeline_orchestrator.pub_sub.pub_wrapper.get_final_config",
        new=lambda *_, **__: final_cfg,
    ):
        await publish_message(rm, svc, run_id, cfg_id, payload)

    await _delay()
    body = json.loads((await queue.get(timeout=5)).body.decode())
    assert body["payload"] == payload
    await ch.close()
    await conn.close()


# --------------------------------------------------------------------------- #
#  The remaining tests (concurrent, failure scenarios, etc.) only               #
#  need the new DummyResourceManager.  Their logic is unchanged – we            #
#  simply replace the old stub with the new one above.                          #
# --------------------------------------------------------------------------- #
# (The rest of the file is identical except for using DummyResourceManager.)
