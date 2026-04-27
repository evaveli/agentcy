from __future__ import annotations

import logging
from typing import Any, Dict

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager

logger = logging.getLogger(__name__)


def _extract_payload(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message.get("data") or message)
    return dict(getattr(message, "data", {}) or {})


async def run(
    _rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    """
    Lightweight input validation (content filter gate only).

    This stage does not create TaskSpecs. The Supervisor Agent handles
    translation of natural language into TaskSpecs downstream.
    """
    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    pipeline_id = getattr(message, "pipeline_id", None) or (message.get("pipeline_id") if isinstance(message, dict) else None)
    run_id = getattr(message, "pipeline_run_id", None) or (message.get("pipeline_run_id") if isinstance(message, dict) else None)
    if not username or not pipeline_id or not run_id:
        raise ValueError("input_validator requires username, pipeline_id, pipeline_run_id")

    payload = _extract_payload(message)
    if payload.get("content_filter_passed") is False or payload.get("blocked") is True:
        logger.warning("Input validator blocked content for %s/%s", username, pipeline_id)
        return {"validated": False, "blocked": True, "task_ids": []}

    return {"validated": True, "blocked": False}
