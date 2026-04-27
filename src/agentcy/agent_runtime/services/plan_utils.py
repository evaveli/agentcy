from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from agentcy.pydantic_models.multi_agent_pipeline import PlanDraft


def _parse_timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _draft_sort_key(draft: Dict[str, Any]) -> float:
    meta = draft.get("_meta") or {}
    updated_at = draft.get("updated_at") or meta.get("updated_at")
    if updated_at:
        return _parse_timestamp(updated_at)
    return _parse_timestamp(draft.get("created_at"))


def _latest_draft(drafts: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not drafts:
        return None
    return max(drafts, key=_draft_sort_key)


def load_plan_draft(
    store: Any,
    *,
    username: str,
    pipeline_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    plan_id: Optional[str] = None,
) -> PlanDraft:
    if plan_id:
        doc = store.get_plan_draft(username=username, plan_id=plan_id)
    else:
        drafts, _ = store.list_plan_drafts(username=username, pipeline_id=pipeline_id)
        if pipeline_run_id:
            drafts = [
                draft
                for draft in drafts
                if draft.get("pipeline_run_id") == pipeline_run_id
            ] or drafts
        doc = _latest_draft(drafts)
    if doc is None:
        raise ValueError("Plan draft not found")
    return PlanDraft.model_validate(doc)
