#src/agentcy/adaptars/couchbase_doc_store.py
import asyncio
from typing import Any, Dict
from agentcy.pipeline_orchestrator.ports import DocStore
from agentcy.orchestrator_core.stores.ephemeral_pipeline_store import EphemeralPipelineStore

class CouchbaseDocStore(DocStore):
    def __init__(self, backing: EphemeralPipelineStore):
        self._back = backing                       

    async def save(
        self,
        username: str,
        run_id:   str,
        task_id:  str,
        raw_output: Dict[str, Any],
    ) -> str:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._back.store_task_output,          # signature matches now
            username, task_id, run_id, raw_output
        )
        return f"task_output::{username}::{task_id}::{run_id}"

    async def load(self, ref: str) -> Dict[str, Any]:
        # ref format: task_output::<username>::<task_id>::<run_id>
        _, username, task_id, run_id = ref.split("::", 3)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._back.read_task_output,
            username, task_id, run_id
        )