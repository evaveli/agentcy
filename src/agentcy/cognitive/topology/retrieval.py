"""Score and rank topology skeletons against a structured business template.

Pure rule-based scoring — no LLM dependency.  Uses the same capability taxonomy
as the template matcher for semantic matching of integration requirements.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Set

from agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    SkeletonCandidate,
    TopologyPerformance,
    TopologySkeleton,
)
from agentcy.semantic.capability_taxonomy import expand_capabilities


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


_W_WORKFLOW = _env_float("TOPOLOGY_W_WORKFLOW", 0.35)
_W_INTEGRATION = _env_float("TOPOLOGY_W_INTEGRATION", 0.20)
_W_CONSTRAINT = _env_float("TOPOLOGY_W_CONSTRAINT", 0.15)
_W_CONTROL = _env_float("TOPOLOGY_W_CONTROL", 0.10)
_W_PERFORMANCE = _env_float("TOPOLOGY_W_PERFORMANCE", 0.20)
_MIN_PERF_SAMPLES = int(_env_float("TOPOLOGY_MIN_PERF_SAMPLES", 5))

# Maps business-level integration type names to capability requirements.
_INTEGRATION_CAPABILITY_MAP: Dict[str, str] = {
    "tms": "api_call",
    "wms": "api_call",
    "carrier_api": "http_request",
    "email": "integration",
    "erp": "db_read",
    "customs_db": "db_read",
    "webhook": "webhook",
    "queue": "queue_publish",
}


def _workflow_class_score(template: BusinessTemplate, skeleton: TopologySkeleton) -> float:
    """1.0 for exact match, 0.3 for generic skeleton, 0.0 otherwise."""
    if skeleton.workflow_class == template.workflow_class:
        return 1.0
    if skeleton.workflow_class == "generic":
        return 0.3
    return 0.0


def _integration_score(template: BusinessTemplate, skeleton: TopologySkeleton) -> float:
    """Fraction of business integration needs that the skeleton can accommodate."""
    if not template.integration_types:
        return 1.0  # No requirements → full score

    needed_caps: Set[str] = set()
    for itype in template.integration_types:
        cap = _INTEGRATION_CAPABILITY_MAP.get(itype.lower().strip())
        if cap:
            needed_caps.add(cap)
    if not needed_caps:
        return 1.0

    # Collect all capabilities across skeleton steps and expand them
    skeleton_caps: Set[str] = set()
    for step in skeleton.steps:
        skeleton_caps.update(c.lower().strip() for c in step.required_capabilities)
    skeleton_caps = expand_capabilities(skeleton_caps)

    matched = len(needed_caps & skeleton_caps)
    return matched / len(needed_caps)


def _constraint_alignment_score(template: BusinessTemplate, skeleton: TopologySkeleton) -> float:
    """How well skeleton control patterns align with business constraints."""
    patterns = {p.lower() for p in skeleton.control_patterns}
    sub_scores: List[float] = []

    # Compliance ↔ verification gate
    if template.compliance_strictness in ("strict", "moderate"):
        sub_scores.append(1.0 if "verification_gate" in patterns else 0.0)
    else:
        sub_scores.append(1.0)  # No requirement → neutral

    # Human approval
    if template.human_approval_required:
        sub_scores.append(1.0 if "human_approval" in patterns else 0.0)
    else:
        sub_scores.append(1.0)

    # High criticality ↔ retry/verification patterns
    if template.decision_criticality == "high":
        has_resilience = "retry_wrapper" in patterns or "verification_gate" in patterns
        sub_scores.append(1.0 if has_resilience else 0.3)
    else:
        sub_scores.append(1.0)

    return sum(sub_scores) / len(sub_scores) if sub_scores else 1.0


def _control_pattern_coverage(template: BusinessTemplate, skeleton: TopologySkeleton) -> float:
    """Fraction of inferred-needed control patterns present in skeleton."""
    needed: List[str] = []
    if template.compliance_strictness != "none":
        needed.append("verification_gate")
    if template.human_approval_required:
        needed.append("human_approval")
    if template.decision_criticality == "high":
        needed.append("retry_wrapper")

    if not needed:
        return 1.0

    patterns = {p.lower() for p in skeleton.control_patterns}
    matched = sum(1 for p in needed if p in patterns)
    return matched / len(needed)


def _performance_score(perf: Optional[TopologyPerformance]) -> float:
    """Score based on historical execution performance.

    Returns 0.5 (neutral) if no performance data or insufficient samples.
    Returns 0.0–1.0 based on success rate, weighted by sample confidence.
    """
    if perf is None or perf.sample_count < _MIN_PERF_SAMPLES:
        return 0.5  # Neutral — don't penalise new skeletons

    # Primary signal: success rate (0–1)
    base = perf.success_rate

    # Mild penalties for high retry/escalation rates
    base -= min(perf.retry_rate * 0.1, 0.1)
    base -= min(perf.policy_incident_rate * 0.2, 0.1)
    base -= min(perf.human_escalation_rate * 0.15, 0.1)

    # Confidence discount: below 20 samples, pull toward 0.5
    if perf.sample_count < 20:
        confidence = perf.sample_count / 20.0
        base = 0.5 + (base - 0.5) * confidence

    return max(0.0, min(1.0, base))


def score_skeleton(
    business_template: BusinessTemplate,
    skeleton: TopologySkeleton,
    performance: Optional[TopologyPerformance] = None,
) -> SkeletonCandidate:
    """Score a single skeleton against a business template.  Returns a SkeletonCandidate.

    When *performance* data is available, it is weighted into the score.
    Without performance data, the performance factor is neutral (0.5).
    """
    wf = _workflow_class_score(business_template, skeleton)
    integ = _integration_score(business_template, skeleton)
    constraint = _constraint_alignment_score(business_template, skeleton)
    control = _control_pattern_coverage(business_template, skeleton)
    perf = _performance_score(performance)

    total = (
        (_W_WORKFLOW * wf)
        + (_W_INTEGRATION * integ)
        + (_W_CONSTRAINT * constraint)
        + (_W_CONTROL * control)
        + (_W_PERFORMANCE * perf)
    )
    total = min(1.0, max(0.0, total))

    return SkeletonCandidate(
        skeleton=skeleton,
        score=round(total, 4),
        match_details={
            "workflow_class": round(wf, 4),
            "integration": round(integ, 4),
            "constraint_alignment": round(constraint, 4),
            "control_pattern_coverage": round(control, 4),
            "performance": round(perf, 4),
        },
    )


def retrieve_skeletons(
    business_template: BusinessTemplate,
    skeletons: List[TopologySkeleton],
    min_score: float = 0.0,
    performance_lookup: Optional[Dict[str, TopologyPerformance]] = None,
) -> List[SkeletonCandidate]:
    """Score all skeletons and return candidates sorted descending, filtered by *min_score*.

    *performance_lookup* maps skeleton_id → TopologyPerformance for outcome-weighted scoring.
    """
    candidates = []
    for sk in skeletons:
        if not sk.enabled:
            continue
        perf = performance_lookup.get(sk.skeleton_id) if performance_lookup else None
        candidate = score_skeleton(business_template, sk, performance=perf)
        if candidate.score >= min_score:
            candidates.append(candidate)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates
