# tests/unit_tests/microservice_library_unit_tests/test_rabbit_publisher.py
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aio_pika
import pytest

from src.agentcy.pipeline_orchestrator.pub_sub.pub_wrapper import publish_message


# --------------------------------------------------------------------------- #
#  ultra-small stub that satisfies publish_message()                          #
# --------------------------------------------------------------------------- #
class _DummyChannelCtx:
    """Async context manager that yields our mocked channel."""

    def __init__(self, mock_channel):
        self._ch = mock_channel

    async def __aenter__(self):
        return self._ch

    async def __aexit__(self, exc_type, exc, tb):  # pragma: no cover
        pass


def _make_resource_manager(mock_exchange: AsyncMock):
    """
    Fabricate a ResourceManager that supplies a channel whose
    declare_exchange() returns `mock_exchange`.
    """
    mock_channel = AsyncMock()
    mock_channel.declare_exchange.return_value = mock_exchange

    mock_rabbit_conn = MagicMock()
    mock_rabbit_conn.get_channel.return_value = _DummyChannelCtx(mock_channel)

    class DummyResourceManager:
        rabbit_conn = mock_rabbit_conn

    return DummyResourceManager()


# --------------------------------------------------------------------------- #
#  TESTS                                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_publish_message_success():
    svc, run_id, cfg_id = "test_service", "run_123", "cfg_abc"
    payload = {"key": "value"}

    final_cfg = {
        "rabbitmq_configs": [
            {
                "task_id": svc,
                "rabbitmq": {
                    "exchange": "test_exchange",
                    "exchange_type": "direct",
                    "routing_key": "test.key",
                },
            }
        ]
    }

    mock_exchange = AsyncMock()
    mock_exchange.publish = AsyncMock()

    rm = _make_resource_manager(mock_exchange)

    with patch(
        "src.agentcy.pipeline_orchestrator.pub_sub.pub_wrapper.get_final_config",
        new=lambda *_, **__: final_cfg,
    ):
        await publish_message(rm, svc, run_id, cfg_id, payload)

    # --- assertions --------------------------------------------------------
    mock_exchange.publish.assert_awaited_once()
    sent_msg = mock_exchange.publish.call_args[0][0]  # aio_pika.Message
    assert json.loads(sent_msg.body.decode()) == {
        "pipeline_run_id": run_id,
        "from_task": svc,
        "payload": payload,
    }


@pytest.mark.asyncio
async def test_publish_message_no_config():
    svc, run_id, cfg_id = "missing_service", "run", "cfg"
    no_cfg = {"rabbitmq_configs": []}

    rm = _make_resource_manager(AsyncMock())

    with patch(
        "src.agentcy.pipeline_orchestrator.pub_sub.pub_wrapper.get_final_config",
        new=lambda *_, **__: no_cfg,
    ):
        with pytest.raises(KeyError, match="RabbitMQ configuration not found for service"):
            await publish_message(rm, svc, run_id, cfg_id, {"x": 1})


@pytest.mark.asyncio
async def test_publish_message_no_connection():
    svc, run_id, cfg_id = "svc", "run", "cfg"
    cfg = {
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
        new=lambda *_, **__: cfg,
    ):
        with pytest.raises(Exception, match="RabbitMQ connection not initialized in ResourceManager"):
            await publish_message(RMNoConn(), svc, run_id, cfg_id, {})
