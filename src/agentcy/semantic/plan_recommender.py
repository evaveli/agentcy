"""
Cross-plan learning service.

Queries the knowledge graph for similar past plans and their execution
outcomes, returning structured context suitable for LLM prompt injection
so that the supervisor agent can learn from historical successes and
failures.

All public functions are async and feature-gated: they return ``None``
silently when Fuseki is disabled.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5


def _fuseki_enabled() -> bool:
    raw = os.getenv("FUSEKI_ENABLE", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(os.getenv("FUSEKI_URL"))


async def get_plan_context(
    *,
    capabilities: List[str],
    tags: Optional[List[str]] = None,
    plan_id: Optional[str] = None,
    limit: int = 3,
) -> Optional[Dict[str, Any]]:
    """Build LLM-ready context from similar past plans.

    Queries the KG for plans that share capabilities, retrieves their
    execution outcomes, and returns a structured dict that can be
    injected into the supervisor agent prompt.

    Feature-gated: returns ``None`` when Fuseki is not enabled.

    Returns:
        A dict with ``similar_plans``, ``capability_stats``, and
        ``recommended_templates``; or ``None`` if unavailable.
    """
    if not _fuseki_enabled():
        return None

    if not capabilities:
        return None

    try:
        from agentcy.semantic.queries import (
            find_similar_plans,
            find_plans_by_capabilities,
            get_plan_execution_outcomes,
            get_task_avg_duration,
            get_best_template_for_capability,
        )

        # 1. Find similar plans
        similar_raw: Optional[List[Dict]] = None
        if plan_id:
            similar_raw = await find_similar_plans(plan_id, limit=limit)
        if not similar_raw:
            similar_raw = await find_plans_by_capabilities(
                capabilities, limit=limit,
            )

        similar_plans: List[Dict[str, Any]] = []
        for row in similar_raw or []:
            sim_plan_id = row.get("otherPlanId") or row.get("planId") or ""
            shared = int(row.get("sharedCaps", 0))
            # Fetch execution outcomes for this similar plan
            outcomes = await get_plan_execution_outcomes(sim_plan_id)
            exec_summary: Dict[str, Any] = {}
            if outcomes and len(outcomes) > 0:
                o = outcomes[0]
                total = int(o.get("total", 0))
                successes = int(o.get("successes", 0))
                exec_summary = {
                    "total": total,
                    "successes": successes,
                    "avg_duration": (
                        float(o["avgDuration"]) if o.get("avgDuration") else None
                    ),
                }
            similar_plans.append({
                "plan_id": sim_plan_id,
                "shared_capabilities": shared,
                "execution_summary": exec_summary,
            })

        # 2. Capability stats (top 5)
        capability_stats: Dict[str, Any] = {}
        for cap in capabilities[:5]:
            dur_rows = await get_task_avg_duration(cap)
            if dur_rows and len(dur_rows) > 0:
                r = dur_rows[0]
                avg = float(r["avgDuration"]) if r.get("avgDuration") else None
                count = int(r.get("sampleCount", 0))
                if avg is not None and count > 0:
                    capability_stats[cap] = {
                        "avg_duration": avg,
                        "sample_count": count,
                    }

        # 3. Recommended templates (top caps)
        recommended_templates: List[Dict[str, Any]] = []
        seen_templates: set = set()
        for cap in capabilities[:5]:
            tmpl_rows = await get_best_template_for_capability(cap, limit=2)
            for tr in tmpl_rows or []:
                tid = tr.get("templateId")
                if tid and tid not in seen_templates:
                    seen_templates.add(tid)
                    total = int(tr.get("total", 0))
                    successes = int(tr.get("successes", 0))
                    recommended_templates.append({
                        "template_id": tid,
                        "template_name": tr.get("templateName"),
                        "success_rate": (
                            round(successes / total, 4) if total > 0 else 0.0
                        ),
                    })

        return {
            "similar_plans": similar_plans,
            "capability_stats": capability_stats,
            "recommended_templates": recommended_templates[:10],
        }
    except Exception:
        logger.debug("get_plan_context failed, returning None", exc_info=True)
        return None
