from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import PlanDraft

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_key(draft: PlanDraft) -> str:
    payload = json.dumps(draft.graph_spec or {}, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"{draft.pipeline_id}:{draft.plan_id}:{digest}"


async def cache_plan_draft(
    rm: ResourceManager,
    *,
    username: str,
    pipeline_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    plan_id: Optional[str] = None,
) -> Dict[str, Any]:
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    draft = load_plan_draft(
        store,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        plan_id=plan_id,
    )
    if not draft.is_valid:
        logger.warning("Plan cache skipped invalid plan %s for %s", draft.plan_id, username)
        return {"plan_id": draft.plan_id, "pipeline_id": draft.pipeline_id, "cached": False, "reason": "plan_not_valid"}

    cache_key = _cache_key(draft)
    graph_spec = dict(draft.graph_spec or {})
    graph_spec["cache"] = {"cache_key": cache_key, "cached_at": _now_iso()}
    updated = draft.model_copy(update={"cached": True, "graph_spec": graph_spec})
    store.save_plan_draft(username=username, draft=updated)
    logger.info("Plan cache stored plan %s for %s", updated.plan_id, username)
    return {"plan_id": updated.plan_id, "pipeline_id": updated.pipeline_id, "cached": True, "cache_key": cache_key}


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    username = getattr(message, "username", None) or message.get("username")
    pipeline_id = getattr(message, "pipeline_id", None) or message.get("pipeline_id")
    pipeline_run_id = getattr(message, "pipeline_run_id", None) or message.get("pipeline_run_id")
    plan_id = getattr(message, "plan_id", None)
    if isinstance(message, dict):
        plan_id = message.get("plan_id", plan_id)

    if not username:
        raise ValueError("Plan cache requires username")

    return await cache_plan_draft(
        rm,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        plan_id=plan_id,
    )
