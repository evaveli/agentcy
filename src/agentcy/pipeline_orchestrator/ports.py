#src/agentcy/pipeline_orchestrator/ports.py
from typing import Protocol, Any, Dict

class MessageBus(Protocol):
    async def publish(
        self,
        exchange: str,
        routing_key: str,
        body: Dict[str, Any],
        *,
        exchange_type: str = "direct",     # ← default keeps signature short
    ) -> None: ...


class DocStore(Protocol):
    async def save(self,username: str, run_id: str, task_id: str, raw_output: Dict[str, Any]) -> str: ...
    async def load(self, ref: str) -> Dict[str, Any]: ...

