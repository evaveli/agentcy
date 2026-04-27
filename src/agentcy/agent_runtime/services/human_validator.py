from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.agent_runtime.services.plan_revision_utils import apply_delta
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import HumanApproval, RiskLevel, TaskSpec

logger = logging.getLogger(__name__)


def _payload_from_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message.get("data") or message)
    return dict(getattr(message, "data", {}) or {})


def _task_specs_for_pipeline(
    store,
    *,
    username: str,
    pipeline_id: Optional[str],
) -> List[TaskSpec]:
    raw, _ = store.list_task_specs(username=username)
    if pipeline_id:
        filtered = []
        for item in raw:
            meta = item.get("metadata") if isinstance(item, dict) else {}
            if isinstance(meta, dict) and meta.get("pipeline_id") == pipeline_id:
                filtered.append(item)
        raw = filtered or raw
    specs: List[TaskSpec] = []
    for item in raw:
        try:
            specs.append(TaskSpec.model_validate(item))
        except Exception:
            continue
    return specs


def _max_risk(specs: List[TaskSpec]) -> RiskLevel:
    order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
    current = RiskLevel.LOW
    for spec in specs:
        if order.get(spec.risk_level, 0) > order.get(current, 0):
            current = spec.risk_level
    return current if specs else RiskLevel.MEDIUM


def _requires_human(specs: List[TaskSpec]) -> bool:
    for spec in specs:
        if spec.requires_human_approval or spec.risk_level == RiskLevel.HIGH:
            return True
    return False


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    pipeline_id = getattr(message, "pipeline_id", None) or (message.get("pipeline_id") if isinstance(message, dict) else None)
    plan_id = getattr(message, "plan_id", None)
    if isinstance(message, dict):
        plan_id = message.get("plan_id", plan_id)

    if not username:
        raise ValueError("human_validator requires username")

    draft = load_plan_draft(store, username=username, pipeline_id=pipeline_id, plan_id=plan_id)

    payload = _payload_from_message(message)
    specs = _task_specs_for_pipeline(store, username=username, pipeline_id=pipeline_id)
    risk_level = _max_risk(specs)
    requires_human = _requires_human(specs)

    approved = payload.get("human_approved")
    if approved is None:
        approved = payload.get("approved")
    if approved is None:
        approved = not requires_human
    approved = bool(approved)

    approver = payload.get("approver") or os.getenv("HUMAN_APPROVER", "human")
    rationale = payload.get("rationale") or payload.get("notes")
    modifications = payload.get("modifications") if isinstance(payload.get("modifications"), dict) else None

    modifications_applied = 0
    if approved and modifications:
        updated_graph, modifications_applied = apply_delta(draft.graph_spec, modifications)
        updated_graph["human_approval"] = {
            "approved": approved,
            "approver": approver,
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
        draft = draft.model_copy(update={"graph_spec": updated_graph})
        store.save_plan_draft(username=username, draft=draft)

    approval = HumanApproval(
        plan_id=draft.plan_id,
        username=username,
        approver=str(approver),
        approved=approved,
        rationale=rationale,
        modifications=modifications,
        risk_level=risk_level,
    )
    store.save_human_approval(username=username, approval=approval)

    logger.info("Human validator stored approval for %s (approved=%s)", username, approved)
    return {
        "plan_id": draft.plan_id,
        "requires_human_approval": requires_human,
        "approved": approved,
        "risk_level": risk_level.value,
        "modifications_applied": modifications_applied,
    }
