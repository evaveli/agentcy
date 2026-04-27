"""Coalition vs solo utility scoring with margin threshold.

Feature-gated behind ``CNP_COALITION_ENABLE=1``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Literal, Optional

from agentcy.pydantic_models.multi_agent_pipeline import CoalitionBid


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def coalition_enabled() -> bool:
    return os.getenv("CNP_COALITION_ENABLE", "0") == "1"


def coalition_utility(
    bid: CoalitionBid,
    signals: Optional[Dict[str, Any]] = None,
) -> float:
    """Compute utility score for a coalition bid.

    Uses the same clamped 0-1 range as solo ``score_bid``.
    """
    signals = signals or {}

    w_confidence = _get_env_float("CNP_COAL_W_CONFIDENCE", 0.30)
    w_trust = _get_env_float("CNP_COAL_W_TRUST", 0.20)
    w_cost = _get_env_float("CNP_COAL_W_COST", 0.15)
    w_latency = _get_env_float("CNP_COAL_W_LATENCY", 0.10)
    w_handoff = _get_env_float("CNP_COAL_W_HANDOFF", 0.10)
    w_overhead = _get_env_float("CNP_COAL_W_OVERHEAD", 0.05)

    raw = 0.0
    raw += w_confidence * bid.joint_confidence
    raw += w_trust * bid.joint_trust_score

    # Bonuses
    complementarity = float(signals.get("complementarity_bonus", 0.0))
    verification = float(signals.get("verification_bonus", 0.0))
    raw += complementarity + verification

    # Penalties (normalised to 0-1 range before weighting)
    if bid.expected_cost > 0:
        raw -= w_cost * min(bid.expected_cost / 10.0, 1.0)
    if bid.expected_latency_ms > 0:
        raw -= w_latency * min(bid.expected_latency_ms / 30000.0, 1.0)

    handoff_risk = float(signals.get("handoff_friction", 0.0))
    raw -= w_handoff * handoff_risk

    overhead = float(signals.get("coalition_overhead", 0.0))
    raw -= w_overhead * overhead

    return _clamp(0.5 + (raw / 2.0), 0.0, 1.0)


def compare_solo_vs_coalition(
    best_solo_score: float,
    coalition_score: float,
    margin: Optional[float] = None,
) -> Literal["solo", "coalition"]:
    """Coalition wins only if it beats the best solo bid by a configurable margin.

    This is the non-negotiable gate that prevents coalition spam.
    """
    if margin is None:
        margin = _get_env_float("CNP_COALITION_MARGIN", 0.06)
    if coalition_score >= best_solo_score + margin:
        return "coalition"
    return "solo"
