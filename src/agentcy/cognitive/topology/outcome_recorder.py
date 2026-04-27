"""Record topology outcomes and aggregate performance summaries.

Closes the feedback loop: pipeline execution results flow back into
the topology prior library so that retrieval scoring becomes
outcome-weighted over time.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agentcy.pydantic_models.topology_models import (
    TopologyOutcome,
    TopologyPerformance,
)

logger = logging.getLogger(__name__)


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def build_topology_signature(skeleton_id: str, mutations_applied: List[str]) -> str:
    """Build a canonical topology signature from skeleton ID + sorted mutations."""
    if not mutations_applied:
        return skeleton_id
    sorted_mutations = "+".join(sorted(mutations_applied))
    return f"{skeleton_id}::{sorted_mutations}"


def record_topology_outcome(
    *,
    store: Any,
    username: str,
    pipeline_id: str,
    pipeline_run_id: str,
    topology_metadata: Dict[str, Any],
    success: bool,
    execution_time_seconds: Optional[float] = None,
    task_count: int = 0,
    retry_count: int = 0,
    coalition_usage_count: int = 0,
    policy_violations: int = 0,
    human_escalations: int = 0,
    cost_total: float = 0.0,
    error_summary: Optional[str] = None,
    variant_id: Optional[str] = None,
) -> Optional[str]:
    """Record a topology outcome after pipeline completion.

    *topology_metadata* is the ``_topology_metadata`` dict attached to the
    PipelineCreate by the topology orchestrator.

    Returns the outcome_id or None if recording fails.
    """
    if not topology_metadata:
        return None

    skeleton_id = topology_metadata.get("skeleton_id", "")
    mutations = topology_metadata.get("mutations_applied", [])
    workflow_class = topology_metadata.get("workflow_class", "")

    if not skeleton_id:
        return None

    signature = build_topology_signature(skeleton_id, mutations)

    outcome = TopologyOutcome(
        skeleton_id=skeleton_id,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        workflow_class=workflow_class,
        topology_signature=signature,
        variant_id=variant_id,
        business_template=topology_metadata.get("business_template", {}),
        mutations_applied=mutations,
        success=success,
        execution_time_seconds=execution_time_seconds,
        task_count=task_count,
        retry_count=retry_count,
        coalition_usage_count=coalition_usage_count,
        policy_violations=policy_violations,
        human_escalations=human_escalations,
        cost_total=cost_total,
        error_summary=error_summary,
    )

    try:
        if hasattr(store, "save_topology_outcome"):
            store.save_topology_outcome(username=username, outcome=outcome)
            logger.info(
                "Recorded topology outcome for %s (sig=%s, success=%s)",
                pipeline_run_id, signature, success,
            )
            return outcome.outcome_id
    except Exception:
        logger.debug("Failed to record topology outcome", exc_info=True)
    return None


def aggregate_topology_performance(
    *,
    store: Any,
    username: str,
    topology_signature: Optional[str] = None,
    skeleton_id: Optional[str] = None,
    max_samples: int = 0,
) -> List[TopologyPerformance]:
    """Aggregate outcomes into performance summaries per topology signature.

    If *topology_signature* is given, aggregates only that variant.
    Otherwise aggregates all available outcomes grouped by signature.

    *max_samples* limits how many recent outcomes to consider (0 = all).
    """
    if max_samples <= 0:
        max_samples = _get_env_int("TOPOLOGY_PERF_MAX_SAMPLES", 500)

    if not hasattr(store, "list_topology_outcomes"):
        return []

    try:
        outcomes, _ = store.list_topology_outcomes(
            username=username,
            topology_signature=topology_signature,
            limit=max_samples,
        )
    except Exception:
        logger.debug("Failed to list topology outcomes", exc_info=True)
        return []

    if not outcomes:
        return []

    # Group by topology_signature
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for o in outcomes:
        sig = o.get("topology_signature", "")
        if not sig:
            continue
        groups.setdefault(sig, []).append(o)

    summaries: List[TopologyPerformance] = []
    for sig, items in groups.items():
        n = len(items)
        successes = sum(1 for o in items if o.get("success"))
        latencies = [o.get("execution_time_seconds", 0) or 0 for o in items if o.get("execution_time_seconds")]
        costs = [o.get("cost_total", 0) or 0 for o in items]
        retries = sum(o.get("retry_count", 0) for o in items)
        coalitions = sum(o.get("coalition_usage_count", 0) for o in items)
        policy_incidents = sum(o.get("policy_violations", 0) for o in items)
        escalations = sum(o.get("human_escalations", 0) for o in items)
        total_tasks = sum(o.get("task_count", 0) for o in items)

        mean_lat = sum(latencies) / len(latencies) if latencies else 0.0
        sorted_lats = sorted(latencies)
        p95_lat = sorted_lats[int(len(sorted_lats) * 0.95)] if sorted_lats else 0.0

        perf = TopologyPerformance(
            topology_signature=sig,
            skeleton_id=items[0].get("skeleton_id", ""),
            workflow_class=items[0].get("workflow_class", ""),
            sample_count=n,
            success_rate=successes / n if n > 0 else 0.0,
            mean_latency_seconds=round(mean_lat, 3),
            latency_p95_seconds=round(p95_lat, 3),
            mean_cost=round(sum(costs) / n, 4) if n > 0 else 0.0,
            retry_rate=round(retries / total_tasks, 4) if total_tasks > 0 else 0.0,
            coalition_usage_rate=round(coalitions / total_tasks, 4) if total_tasks > 0 else 0.0,
            policy_incident_rate=round(policy_incidents / n, 4) if n > 0 else 0.0,
            human_escalation_rate=round(escalations / n, 4) if n > 0 else 0.0,
        )
        summaries.append(perf)

    # Sort by success_rate descending
    summaries.sort(key=lambda p: p.success_rate, reverse=True)
    return summaries


def get_performance_for_skeleton(
    *,
    store: Any,
    username: str,
    skeleton_id: str,
) -> Optional[TopologyPerformance]:
    """Get the best-performing variant for a given skeleton_id."""
    perfs = aggregate_topology_performance(store=store, username=username, skeleton_id=skeleton_id)
    matching = [p for p in perfs if p.skeleton_id == skeleton_id]
    return matching[0] if matching else None
