"""Runtime policy engine: telemetry-shaped adaptation.

Reads current health signals and produces a PolicyState that influences
coalition mode, verification strictness, fallback aggressiveness, topology
variant bias, and human gate placement.

Feature-gated behind ``RUNTIME_POLICY_ENABLE=1``.

Design principles:
- Deterministic rules first (no ML) — fully inspectable and overrideable.
- Every decision is logged for auditability.
- Defaults are neutral — system behaves normally when the engine is off.
- Rules are evaluated in priority order; multiple rules can fire.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from agentcy.pydantic_models.runtime_policy_models import (
    CoalitionPolicyMode,
    FallbackPolicyMode,
    HealthSignals,
    HumanGateBias,
    PolicyDecisionLog,
    PolicyState,
    TopologyVariantBias,
    VerificationPolicyMode,
)

logger = logging.getLogger(__name__)


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def runtime_policy_enabled() -> bool:
    return os.getenv("RUNTIME_POLICY_ENABLE", "0") == "1"


# ── Threshold configuration ─────────────────────────────────────────────

# All thresholds are env-configurable for per-deployment tuning.

_T_QUEUE_LAG_HIGH = _get_env_float("POLICY_QUEUE_LAG_HIGH_MS", 500.0)
_T_QUEUE_LAG_CRITICAL = _get_env_float("POLICY_QUEUE_LAG_CRITICAL_MS", 2000.0)
_T_VERIFIER_SAT_HIGH = _get_env_float("POLICY_VERIFIER_SAT_HIGH", 0.7)
_T_TIMEOUT_RATE_HIGH = _get_env_float("POLICY_TIMEOUT_RATE_HIGH", 0.15)
_T_POLICY_INCIDENT_HIGH = _get_env_float("POLICY_INCIDENT_RATE_HIGH", 0.05)
_T_HANDOFF_FAIL_HIGH = _get_env_float("POLICY_HANDOFF_FAIL_HIGH", 0.20)
_T_COST_BURN_HIGH = _get_env_float("POLICY_COST_BURN_HIGH_PER_MIN", 5.0)
_T_HUMAN_BACKLOG_HIGH = _get_env_float("POLICY_HUMAN_BACKLOG_HIGH", 10)
_T_AGENT_SAT_HIGH = _get_env_float("POLICY_AGENT_SAT_HIGH", 0.8)
_T_RETRY_RATE_HIGH = _get_env_float("POLICY_RETRY_RATE_HIGH", 0.25)


# ── Policy rules ─────────────────────────────────────────────────────────
# Each rule is a function: (signals, state) → None.
# Rules mutate state in-place and append their name to triggered_rules.


def _rule_queue_lag_discourages_coalitions(signals: HealthSignals, state: PolicyState) -> None:
    """High queue lag → discourage coalitions (they add messages)."""
    if signals.queue_lag_ms >= _T_QUEUE_LAG_CRITICAL:
        state.coalition_mode = CoalitionPolicyMode.DISABLED
        state.triggered_rules.append("queue_lag_critical_disables_coalitions")
    elif signals.queue_lag_ms >= _T_QUEUE_LAG_HIGH:
        state.coalition_mode = CoalitionPolicyMode.DISCOURAGED
        state.coalition_margin_override = 0.15  # Harder for coalitions to win
        state.triggered_rules.append("queue_lag_high_discourages_coalitions")


def _rule_verifier_saturation(signals: HealthSignals, state: PolicyState) -> None:
    """Verifier pool saturated → prefer solo + aggressive fallback."""
    if signals.verifier_pool_saturation >= _T_VERIFIER_SAT_HIGH:
        if state.coalition_mode == CoalitionPolicyMode.ENABLED:
            state.coalition_mode = CoalitionPolicyMode.DISCOURAGED
        state.fallback_policy = FallbackPolicyMode.AGGRESSIVE
        state.triggered_rules.append("verifier_saturated_prefer_solo")


def _rule_timeout_spike(signals: HealthSignals, state: PolicyState) -> None:
    """Elevated timeout rate → tighten retry budgets, prefer solo."""
    if signals.recent_timeout_rate >= _T_TIMEOUT_RATE_HIGH:
        state.retry_budget_multiplier = max(0.5, state.retry_budget_multiplier * 0.7)
        state.fallback_policy = FallbackPolicyMode.AGGRESSIVE
        state.triggered_rules.append("timeout_spike_tighten_retries")


def _rule_policy_incident_spike(signals: HealthSignals, state: PolicyState) -> None:
    """Policy incidents spiking → force stricter verification."""
    if signals.recent_policy_incident_rate >= _T_POLICY_INCIDENT_HIGH:
        state.verification_mode = VerificationPolicyMode.STRICTER
        state.triggered_rules.append("policy_incidents_force_stricter_verification")


def _rule_handoff_friction_spike(signals: HealthSignals, state: PolicyState) -> None:
    """High coalition handoff failures → discourage coalitions."""
    if signals.recent_coalition_handoff_failure_rate >= _T_HANDOFF_FAIL_HIGH:
        state.coalition_mode = CoalitionPolicyMode.DISCOURAGED
        state.coalition_margin_override = 0.12
        state.triggered_rules.append("handoff_friction_discourages_coalitions")


def _rule_cost_burn_high(signals: HealthSignals, state: PolicyState) -> None:
    """Cost burn too high → bias toward cheaper topology variants."""
    if signals.cost_burn_rate_per_min >= _T_COST_BURN_HIGH:
        state.topology_variant_bias = TopologyVariantBias.LOW_COST
        state.verification_mode = VerificationPolicyMode.MINIMAL
        state.triggered_rules.append("cost_burn_high_prefer_cheap")


def _rule_human_backlog(signals: HealthSignals, state: PolicyState) -> None:
    """Human approval backlog high → push human gates later (unless critical)."""
    if signals.human_approval_backlog >= _T_HUMAN_BACKLOG_HIGH:
        state.human_gate_bias = HumanGateBias.LATER
        state.triggered_rules.append("human_backlog_defer_gates")


def _rule_agent_pool_pressure(signals: HealthSignals, state: PolicyState) -> None:
    """Agent pool near saturation → disable coalitions, tighten retries."""
    if signals.agent_pool_saturation >= _T_AGENT_SAT_HIGH:
        state.coalition_mode = CoalitionPolicyMode.DISABLED
        state.retry_budget_multiplier = min(state.retry_budget_multiplier, 0.5)
        state.triggered_rules.append("agent_pool_pressure_disable_coalitions")


def _rule_retry_rate_high(signals: HealthSignals, state: PolicyState) -> None:
    """High retry rate → bias toward high-safety topology variant."""
    if signals.recent_retry_rate >= _T_RETRY_RATE_HIGH:
        state.topology_variant_bias = TopologyVariantBias.HIGH_SAFETY
        state.triggered_rules.append("retry_rate_high_prefer_safety")


# Ordered list of all rules (evaluated in this order)
_RULES: List[Callable[[HealthSignals, PolicyState], None]] = [
    _rule_queue_lag_discourages_coalitions,
    _rule_verifier_saturation,
    _rule_timeout_spike,
    _rule_policy_incident_spike,
    _rule_handoff_friction_spike,
    _rule_cost_burn_high,
    _rule_human_backlog,
    _rule_agent_pool_pressure,
    _rule_retry_rate_high,
]


# ── Engine ───────────────────────────────────────────────────────────────


class RuntimePolicyEngine:
    """Evaluates health signals against deterministic rules to produce a PolicyState.

    Usage::

        engine = RuntimePolicyEngine()
        signals = HealthSignals(queue_lag_ms=800, verifier_pool_saturation=0.9)
        policy = engine.evaluate(signals, username="tenant-1")

        if policy.coalition_mode == CoalitionPolicyMode.DISABLED:
            # skip coalition assembly
            ...
    """

    def __init__(self, store: Any = None) -> None:
        """*store* is an optional GraphMarkerStore for persisting decision logs."""
        self._store = store

    def evaluate(
        self,
        signals: HealthSignals,
        username: str = "",
    ) -> PolicyState:
        """Evaluate all rules against current signals.

        Returns a neutral ``PolicyState`` if the engine is disabled.
        """
        if not runtime_policy_enabled():
            return PolicyState()

        state = PolicyState()

        for rule in _RULES:
            try:
                rule(signals, state)
            except Exception:
                logger.debug("Policy rule %s failed", rule.__name__, exc_info=True)

        # Log the decision
        log = PolicyDecisionLog(
            username=username,
            signals_snapshot=signals.model_dump(mode="json"),
            policy_state=state.model_dump(mode="json"),
            triggered_rules=list(state.triggered_rules),
        )

        if state.triggered_rules:
            logger.info(
                "Policy engine fired %d rules for %s: %s",
                len(state.triggered_rules), username, state.triggered_rules,
            )

        # Persist if store available
        if self._store and hasattr(self._store, "upsert_raw"):
            try:
                key = f"policy_log::{username}::{log.log_id}"
                self._store.upsert_raw(key, log.model_dump(mode="json"))
            except Exception:
                logger.debug("Failed to persist policy decision log", exc_info=True)

        return state

    @staticmethod
    def default_policy() -> PolicyState:
        """Return a neutral policy state (all defaults, no rules triggered)."""
        return PolicyState()
