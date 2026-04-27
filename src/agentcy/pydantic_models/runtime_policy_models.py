"""Models for the runtime policy engine.

HealthSignals captures current operating conditions.
PolicyState captures the policy decisions derived from those signals.
PolicyDecisionLog records every policy evaluation for auditability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Health signals (input) ───────────────────────────────────────────────


class HealthSignals(BaseModel):
    """Snapshot of current system operating conditions.

    Populated from Prometheus/OTel metrics, recent outcome aggregates,
    or a lightweight health endpoint.  Values default to healthy baselines
    so the policy engine degrades gracefully when metrics are unavailable.
    """

    # Messaging layer
    queue_lag_ms: float = Field(0.0, ge=0.0, description="Current RabbitMQ queue backlog in ms.")
    queue_consumer_count: int = Field(1, ge=0, description="Active consumers on main queues.")

    # Container / agent runtime
    container_startup_latency_ms: float = Field(0.0, ge=0.0)
    agent_pool_saturation: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Fraction of agent pool at max load (0=idle, 1=fully saturated).",
    )
    verifier_pool_saturation: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Fraction of verifier agents at max load.",
    )

    # Cost / token pressure
    token_burn_rate_per_min: float = Field(0.0, ge=0.0, description="Current LLM token consumption rate.")
    cost_burn_rate_per_min: float = Field(0.0, ge=0.0, description="Current dollar cost rate.")

    # Recent outcome signals (from rolling window)
    recent_timeout_rate: float = Field(0.0, ge=0.0, le=1.0)
    recent_policy_incident_rate: float = Field(0.0, ge=0.0, le=1.0)
    recent_coalition_handoff_failure_rate: float = Field(0.0, ge=0.0, le=1.0)
    recent_retry_rate: float = Field(0.0, ge=0.0, le=1.0)

    # Human-in-the-loop
    human_approval_backlog: int = Field(0, ge=0, description="Pending human approvals.")
    human_approval_avg_wait_ms: float = Field(0.0, ge=0.0)

    # Timestamp
    captured_at: datetime = Field(default_factory=_utcnow)


# ── Policy state (output) ────────────────────────────────────────────────


class CoalitionPolicyMode(str, Enum):
    ENABLED = "enabled"
    DISCOURAGED = "discouraged"
    DISABLED = "disabled"


class VerificationPolicyMode(str, Enum):
    NORMAL = "normal"
    STRICTER = "stricter"
    MINIMAL = "minimal"


class FallbackPolicyMode(str, Enum):
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"


class TopologyVariantBias(str, Enum):
    BASELINE = "baseline"
    LOW_LATENCY = "low_latency"
    HIGH_SAFETY = "high_safety"
    LOW_COST = "low_cost"


class HumanGateBias(str, Enum):
    NORMAL = "normal"
    EARLIER = "earlier"
    LATER = "later"


class PolicyState(BaseModel):
    """Runtime policy overrides derived from current health signals.

    Every component that makes allocation/structure decisions should
    consult this state.  All fields default to neutral values so the
    system behaves normally when no policy engine is active.
    """

    coalition_mode: CoalitionPolicyMode = CoalitionPolicyMode.ENABLED
    verification_mode: VerificationPolicyMode = VerificationPolicyMode.NORMAL
    fallback_policy: FallbackPolicyMode = FallbackPolicyMode.NORMAL
    topology_variant_bias: TopologyVariantBias = TopologyVariantBias.BASELINE
    human_gate_bias: HumanGateBias = HumanGateBias.NORMAL

    # Numeric adjustments
    coalition_margin_override: Optional[float] = Field(
        None, description="If set, overrides CNP_COALITION_MARGIN.",
    )
    retry_budget_multiplier: float = Field(
        1.0, ge=0.1, le=3.0,
        description="Multiply default retry counts by this factor.",
    )
    mutation_suppression: List[str] = Field(
        default_factory=list,
        description="rule_ids to suppress regardless of conditions.",
    )
    mutation_escalation: List[str] = Field(
        default_factory=list,
        description="rule_ids to force-apply regardless of conditions.",
    )

    # Traceability
    triggered_rules: List[str] = Field(
        default_factory=list,
        description="Names of policy rules that fired to produce this state.",
    )
    evaluated_at: datetime = Field(default_factory=_utcnow)


# ── Decision log ─────────────────────────────────────────────────────────


class PolicyDecisionLog(BaseModel):
    """Audit record for every policy evaluation."""

    log_id: str = Field(default_factory=lambda: str(uuid4()))
    username: str = ""
    signals_snapshot: Dict[str, Any] = Field(default_factory=dict)
    policy_state: Dict[str, Any] = Field(default_factory=dict)
    triggered_rules: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
