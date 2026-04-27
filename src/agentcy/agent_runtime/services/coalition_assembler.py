"""Orchestrator-mediated coalition assembly.

Given a task spec and solo candidates, attempts to assemble a 2-agent
coalition bid by matching a primary bidder with a complementary partner
from the agent registry.

This is NOT agent-to-agent free negotiation.  The orchestrator controls
the matching, which keeps it auditable, bounded, and observable.

Feature-gated behind ``CNP_COALITION_ENABLE=1``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agentcy.agent_runtime.services.coalition_scorer import coalition_enabled
from agentcy.pydantic_models.multi_agent_pipeline import (
    CoalitionBid,
    CoalitionMember,
    CoalitionRole,
    CoordinationMode,
    TaskSpec,
)

logger = logging.getLogger(__name__)


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ── Capability complementarity ───────────────────────────────────────────


_COMPLEMENTARY_ROLES: Dict[str, str] = {
    "planner": "verifier",
    "executor": "verifier",
    "classify": "verifier",
    "decide": "verifier",
}

_VERIFIER_CAPABILITIES = {"validate", "verification", "policy.check", "verify.semantic"}


def _is_verifier_candidate(agent: Dict[str, Any]) -> bool:
    """Check if agent has verification-related capabilities."""
    caps = set()
    for c in agent.get("capabilities", []):
        caps.add(c.lower().strip())
    tags = set()
    for t in agent.get("tags", []):
        tags.add(t.lower().strip())
    return bool(caps & _VERIFIER_CAPABILITIES) or "verifier" in tags or "verification" in tags


def _coalition_compatibility_score(
    primary: Dict[str, Any],
    partner: Dict[str, Any],
    signals: Optional[Dict[str, Any]] = None,
) -> float:
    """Score how well two agents complement each other for a coalition.

    Returns 0.0 – 1.0.
    """
    signals = signals or {}

    # Capability complementarity: partner has capabilities primary lacks
    primary_caps = set(c.lower() for c in primary.get("capabilities", []))
    partner_caps = set(c.lower() for c in partner.get("capabilities", []))
    new_caps = partner_caps - primary_caps
    complementarity = min(len(new_caps) / max(len(partner_caps), 1), 1.0) * 0.4

    # Joint success prior (from coalition signals if available)
    joint_success = float(signals.get("joint_trust", 0.5)) * 0.25

    # Trust compatibility
    primary_trust = float(primary.get("metadata", {}).get("cnp", {}).get("trust", 0.5))
    partner_trust = float(partner.get("metadata", {}).get("cnp", {}).get("trust", 0.5))
    trust_compat = min(primary_trust, partner_trust) * 0.15

    # Penalties
    handoff_friction = float(signals.get("handoff_friction", 0.0)) * 0.10
    load_penalty = 0.0
    partner_load = int(partner.get("metadata", {}).get("cnp", {}).get("load", 0))
    partner_max = int(partner.get("metadata", {}).get("cnp", {}).get("max_load", 3))
    if partner_max > 0:
        load_penalty = (partner_load / partner_max) * 0.10

    score = complementarity + joint_success + trust_compat - handoff_friction - load_penalty
    return max(0.0, min(1.0, score))


# ── Main assembly function ───────────────────────────────────────────────


def assemble_coalition(
    *,
    task_spec: TaskSpec,
    primary_agent: Dict[str, Any],
    primary_bid_score: float,
    all_agents: List[Dict[str, Any]],
    store: Optional[Any] = None,
    username: str = "",
    coalition_policy_mode: Optional[str] = None,
) -> Optional[CoalitionBid]:
    """Attempt to assemble a 2-agent coalition bid.

    Returns ``None`` if coalition is not allowed, no suitable partner exists,
    or the feature is disabled.

    Parameters
    ----------
    task_spec:
        The task specification (must have coordination_mode allowing coalition).
    primary_agent:
        The top-scoring solo bidder.
    primary_bid_score:
        The solo bid score of the primary agent.
    all_agents:
        Full agent registry list to search for partners.
    store:
        Optional GraphMarkerStore for loading coalition signals.
    coalition_policy_mode:
        Override from RuntimePolicyEngine: "enabled", "discouraged", or "disabled".
    username:
        Tenant identifier.
    """
    if not coalition_enabled():
        return None

    # Runtime policy override: disabled → block, discouraged → only if REQUIRED
    if coalition_policy_mode == "disabled":
        if task_spec.coordination_mode != CoordinationMode.COALITION_REQUIRED:
            return None
    elif coalition_policy_mode == "discouraged":
        if task_spec.coordination_mode not in (
            CoordinationMode.COALITION_REQUIRED,
        ):
            return None

    if task_spec.coordination_mode not in (
        CoordinationMode.COALITION_ALLOWED,
        CoordinationMode.COALITION_REQUIRED,
    ):
        return None

    primary_id = str(primary_agent.get("agent_id", ""))
    if not primary_id:
        return None

    max_team = _get_env_int("CNP_COALITION_MAX_TEAM_SIZE", 2)
    if max_team < 2:
        return None

    # Find best complementary partner
    best_partner: Optional[Dict[str, Any]] = None
    best_compat: float = 0.0

    # Load signals if store available
    signals: Dict[str, Any] = {}

    for agent in all_agents:
        partner_id = str(agent.get("agent_id", ""))
        if partner_id == primary_id or not partner_id:
            continue
        if not _is_verifier_candidate(agent):
            continue

        # Load coalition-specific signals
        if store and hasattr(store, "get_coalition_signal"):
            sig = f"{primary_id[:8]}+{partner_id[:8]}"
            for sig_type in ("joint_trust", "handoff_friction"):
                try:
                    doc = store.get_coalition_signal(
                        username=username, signal_type=sig_type,
                        coalition_signature=sig,
                        task_signature=task_spec.required_capabilities[0] if task_spec.required_capabilities else "",
                    )
                    if doc:
                        signals[sig_type] = doc.get("score", 0.0)
                except Exception:
                    pass

        compat = _coalition_compatibility_score(primary_agent, agent, signals)
        if compat > best_compat:
            best_compat = compat
            best_partner = agent

    if best_partner is None:
        return None

    min_compat = _get_env_float("CNP_COALITION_MIN_COMPAT", 0.2)
    if best_compat < min_compat:
        return None

    partner_id = str(best_partner.get("agent_id", ""))

    # Estimate coalition metrics
    partner_trust = float(best_partner.get("metadata", {}).get("cnp", {}).get("trust", 0.5))
    primary_trust = float(primary_agent.get("metadata", {}).get("cnp", {}).get("trust", 0.5))
    joint_trust = (primary_trust + partner_trust) / 2.0

    # Coalition confidence is boosted by complementarity
    joint_confidence = min(1.0, primary_bid_score + best_compat * 0.15)

    # Estimate latency/cost overhead
    overhead_factor = _get_env_float("CNP_COALITION_OVERHEAD_FACTOR", 1.4)

    return CoalitionBid(
        task_id=task_spec.task_id,
        members=[
            CoalitionMember(agent_id=primary_id, role=CoalitionRole.PLANNER),
            CoalitionMember(agent_id=partner_id, role=CoalitionRole.VERIFIER),
        ],
        handoff_plan=[
            f"{primary_id}_generate",
            f"{partner_id}_verify",
            f"{primary_id}_finalize",
        ],
        joint_confidence=round(joint_confidence, 4),
        expected_latency_ms=int(7000 * overhead_factor),
        expected_cost=round(0.2 * overhead_factor, 4),
        joint_trust_score=round(joint_trust, 4),
        fallback_mode="degrade_to_solo",
        fallback_agent_id=primary_id,
    )
