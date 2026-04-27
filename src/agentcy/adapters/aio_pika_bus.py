# src/agentcy/adapters/aio_pika_bus.py
from datetime import date, datetime
from enum import Enum
import json, logging, aio_pika, aiormq
from typing import Dict, Any
from agentcy.pipeline_orchestrator.ports import MessageBus
from agentcy.pipeline_orchestrator.pub_sub.connection_manager import (
    RabbitMQConnectionManager,
)

log = logging.getLogger(__name__)

_EX_TYPES = {
    "direct": aio_pika.ExchangeType.DIRECT,
    "fanout": aio_pika.ExchangeType.FANOUT,
    "topic": aio_pika.ExchangeType.TOPIC,
    "headers": aio_pika.ExchangeType.HEADERS,
}

def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, set):
        return list(o)
    return str(o)


class AioPikaBus(MessageBus):
    """
    Concrete MessageBus that re-uses your *existing* RabbitMQConnectionManager.
    """
    def __init__(self, rmq_manager: RabbitMQConnectionManager):
        self._rmq = rmq_manager          # ← exactly the class you already have

    async def publish(
        self,
        exchange: str,
        routing_key: str,
        body: Dict[str, Any],
        *,
        exchange_type: str = "direct",
    ) -> None:
        # Connection / channel life-cycle is delegated to the manager
        async with self._rmq.get_channel() as ch:
            ex_type = _EX_TYPES.get(exchange_type, aio_pika.ExchangeType.DIRECT)
            current = ex_type
            try:
                ex = await ch.declare_exchange(exchange,
                                               type=ex_type,
                                               durable=True)
            except aiormq.exceptions.ChannelPreconditionFailed as exc:
                msg = str(exc).lower()
                if "current is 'topic'" in msg:
                    current = aio_pika.ExchangeType.TOPIC
                elif "current is 'fanout'" in msg:
                    current = aio_pika.ExchangeType.FANOUT
                elif "current is 'direct'" in msg:
                    current = aio_pika.ExchangeType.DIRECT

                try:
                    await ch.exchange_delete(exchange, if_unused=False, if_empty=False)
                except Exception:
                    pass

                if getattr(ch, "is_closed", False):
                    async with self._rmq.get_channel() as recovery_ch:
                        ex = await recovery_ch.declare_exchange(exchange, type=current, durable=True)
                        ch = recovery_ch
                else:
                    ex = await ch.declare_exchange(exchange, type=current, durable=True)
            raw = json.dumps(body, default=_json_default).encode()
            try:
                await ex.publish(
                    aio_pika.Message(
                        body=raw,
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key=routing_key,
                )
            except aiormq.exceptions.ChannelInvalidStateError:
                # Channel closed mid-flight; reopen and retry once.
                async with self._rmq.get_channel() as retry_ch:
                    retry_ex = await retry_ch.declare_exchange(exchange, type=current, durable=True)
                    await retry_ex.publish(
                        aio_pika.Message(
                            body=raw,
                            content_type="application/json",
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        ),
                        routing_key=routing_key,
                    )
            log.debug("[Bus] → %s (%s)  %s", exchange, routing_key, body)
