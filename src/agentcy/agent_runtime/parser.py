# src/agentcy/agent_runtime/parser.py

import logging
from typing import Any, Union
try:
    from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import EntryMessage, TaskState
except ImportError:
    from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import EntryMessage, TaskState

logger = logging.getLogger(__name__)

class AbstractMessageParser:
    @staticmethod
    def parse(message_data: Any) -> Union[EntryMessage, TaskState]:
        """
        Normalize incoming message_data into one of our supported models.

        - EntryMessage: kickoff payload
        - TaskState   : normal task event

        Aggregated dicts are handled upstream (fan-in aggregator) and should not
        be passed through this parser.
        """
        if isinstance(message_data, EntryMessage):
            logger.info("↪︎ Detected EntryMessage: pipeline_id=%s username=%s",
                        message_data.pipeline_id, message_data.username)
            logger.debug("EntryMessage payload: %s", message_data)
            return message_data
        if getattr(message_data, "__class__", None) and message_data.__class__.__name__ == "EntryMessage":
            try:
                data = message_data.model_dump() if hasattr(message_data, "model_dump") else message_data.dict()
                return EntryMessage.model_validate(data)
            except Exception:
                return message_data
        # Accept duck-typed BaseModel with matching fields too
        if isinstance(message_data, TaskState):
            logger.info("↪︎ Detected TaskState: run=%s task_id=%s",
                        message_data.pipeline_run_id, message_data.task_id)
            logger.debug("TaskState payload: %s", message_data)
            return message_data
        if hasattr(message_data, "__class__") and message_data.__class__.__name__ == "TaskState":
            try:
                data = message_data.model_dump() if hasattr(message_data, "model_dump") else message_data.dict()
                return TaskState.model_validate(data)
            except Exception:
                return message_data

        raise ValueError(
            f"message_data must be EntryMessage or TaskState, got {type(message_data).__name__}"
        )
