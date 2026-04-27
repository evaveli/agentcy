#src/agentcy/pipeline_orchestrator/pub_sub/publisher_utilities/publishing_utilites.py
import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Dict, Any
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState

logger = logging.getLogger(__name__)

async def persist_output_and_update_task(
    rm,
    base_state: TaskState,
    raw_output: Dict[str, Any],
) -> TaskState:
    """
    1. shove the bulky blob into Couchbase
    2. patch the TaskState with a reference
    """
    output_ref = await rm.doc_store.save(
        base_state.username,
        base_state.pipeline_run_id,
        base_state.task_id,
        raw_output,
    )

    return base_state.model_copy(
        update={
            "output_ref":   output_ref,
            "last_updated": datetime.now(timezone.utc),
            "result":       raw_output.get("result"),
        }
    )


