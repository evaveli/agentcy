from __future__ import annotations

import math
import os
from typing import Any, Dict, Tuple

from agentcy.agent_runtime.services.agent_utils import score_agent_for_task
from agentcy.pydantic_models.multi_agent_pipeline import RiskLevel, TaskSpec


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


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _priority_from_risk(risk: RiskLevel | str | None) -> int:
    if isinstance(risk, RiskLevel):
        risk = risk.value
    risk = (risk or "").lower()
    if risk == RiskLevel.HIGH.value:
        return 5
    if risk == RiskLevel.MEDIUM.value:
        return 3
    if risk == RiskLevel.LOW.value:
        return 1
    return 3


def task_params(spec: TaskSpec) -> Dict[str, Any]:
    metadata = spec.metadata if isinstance(spec.metadata, dict) else {}
    priority = _coerce_int(
        metadata.get("priority") or metadata.get("pri"), _priority_from_risk(spec.risk_level)
    )
    priority = _clamp(priority, 1, 5)
    stimulus = _coerce_float(
        metadata.get("stimulus") or metadata.get("sti"),
        0.2 + ((priority - 1) / 4.0) * 0.6,
    )
    stimulus = _clamp(stimulus, 0.0, 1.0)
    reward = _coerce_float(
        metadata.get("reward") or metadata.get("rew"),
        0.1 + float(priority),
    )
    reward = _clamp(reward, 0.1, 6.0)
    min_score_raw = metadata.get("min_score") or metadata.get("min_bid_score")
    min_score = None
    if min_score_raw is not None:
        min_score = _clamp(_coerce_float(min_score_raw, 0.0), 0.0, 1.0)
    task_type = metadata.get("task_type") or (
        spec.required_capabilities[0] if spec.required_capabilities else "general"
    )
    return {
        "priority": priority,
        "stimulus": stimulus,
        "reward": reward,
        "min_score": min_score,
        "task_type": str(task_type),
    }


def agent_cnp_state(agent: Dict[str, Any]) -> Dict[str, Any]:
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    cnp = metadata.get("cnp") if isinstance(metadata.get("cnp"), dict) else {}
    thresholds = dict(cnp.get("thresholds") or {})
    return {
        "tasks_received": _coerce_int(cnp.get("tasks_received"), 0),
        "tasks_acquired": _coerce_int(cnp.get("tasks_acquired"), 0),
        "tasks_completed": _coerce_int(cnp.get("tasks_completed"), 0),
        "load": _coerce_int(cnp.get("load"), 0),
        "max_load": _coerce_int(
            cnp.get("max_load"),
            _get_env_int("CNP_LMAX_DEFAULT", 3),
        ),
        "thresholds": thresholds,
        "metadata": metadata,
    }


def trust_score(state: Dict[str, Any]) -> float:
    c = state.get("tasks_received", 0)
    cacq = state.get("tasks_acquired", 0)
    ctrue = state.get("tasks_completed", 0)
    if c <= 0:
        return 0.0
    lambda1 = _get_env_float("CNP_LAMBDA1", 0.5)
    lambda2 = _get_env_float("CNP_LAMBDA2", 0.5)
    total = max(lambda1 + lambda2, 1e-6)
    lambda1 /= total
    lambda2 /= total
    y1 = cacq / c if c else 0.0
    y2 = ctrue / cacq if cacq else 0.0
    return _clamp((lambda1 * y1) + (lambda2 * y2), 0.0, 1.0)


def response_threshold(
    *,
    task_type: str,
    state: Dict[str, Any],
    default_threshold: float | None = None,
) -> float:
    thresholds = state.get("thresholds") or {}
    if task_type in thresholds:
        return _clamp(_coerce_float(thresholds.get(task_type), 0.0), 0.0, 1.0)
    if default_threshold is None:
        default_threshold = _get_env_float("CNP_RESPONSE_THRESHOLD_DEFAULT", 0.5)
    return _clamp(default_threshold, 0.0, 1.0)


def alpha_beta(priority: int, reward: float) -> Tuple[float, float]:
    alpha = (priority / 100.0) + (reward / 10.0)
    beta = (math.sin(2 * math.pi * priority * reward) / 100.0) + (
        math.exp(-((3 * reward) ** 2)) / 10.0
    )
    return _clamp(alpha, 0.001, 0.1), _clamp(beta, 0.001, 0.1)


def update_threshold(
    *,
    stimulus: float,
    trust: float,
    alpha: float,
    beta: float,
    success: bool,
) -> float:
    if success:
        value = stimulus + trust + alpha
    else:
        value = stimulus - beta
    return _clamp(value, 0.0, 1.0)


def estimate_cost(
    *,
    reward: float,
    capability_value: float,
    load: int,
    max_load: int,
) -> float:
    capability_value = max(capability_value, 0.1)
    load_factor = 1.0 + (load / max(max_load, 1))
    return (reward / capability_value) * load_factor


def capability_value(agent: Dict[str, Any], spec: TaskSpec) -> float:
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    raw = metadata.get("capability_value")
    if raw is not None:
        return _clamp(_coerce_float(raw, 1.0), 0.1, 6.0)
    score = score_agent_for_task(agent, spec)
    return _clamp(score * 6.0, 0.1, 6.0)


def failure_surface_penalty(
    failure_markers: list[dict],
    *,
    max_penalty: float | None = None,
    decay_per_count: float | None = None,
) -> float:
    """Compute a penalty score (0.0–1.0) from recent failure markers.

    The penalty grows with the *number* of consecutive failures and the
    *number* of distinct failure contexts.  A single failure is a mild
    signal; repeated failures in the same (task_type, error_category)
    context produce a strong penalty.

    Returns 0.0 when there are no failure markers (no penalty).
    """
    if not failure_markers:
        return 0.0
    if max_penalty is None:
        max_penalty = _get_env_float("CNP_FAILURE_MAX_PENALTY", 0.8)
    if decay_per_count is None:
        decay_per_count = _get_env_float("CNP_FAILURE_DECAY_PER_COUNT", 0.35)

    max_count = 0
    total_contexts = 0
    for marker in failure_markers:
        fc = marker.get("failure_context")
        if not fc or not isinstance(fc, dict):
            continue
        total_contexts += 1
        count = int(fc.get("count", 1))
        if count > max_count:
            max_count = count

    if total_contexts == 0:
        return 0.0

    # Exponential growth that saturates at max_penalty:
    #   penalty = max_penalty * (1 - e^(-decay * max_count))
    # count=1 → ~0.24*max_penalty, count=3 → ~0.65*max_penalty, count=5 → ~0.83*max_penalty
    import math
    raw_penalty = max_penalty * (1.0 - math.exp(-decay_per_count * max_count))

    # Slight boost for breadth of failure contexts (multiple distinct failure types)
    if total_contexts > 1:
        breadth_boost = min(0.1 * (total_contexts - 1), 0.2)
        raw_penalty = min(raw_penalty + breadth_boost, max_penalty)

    return _clamp(raw_penalty, 0.0, 1.0)


def score_bid(
    *,
    trust: float,
    cost: float,
    load: int,
    tmin: float,
    tmax: float,
    lmin: int,
    lmax: int,
    historical_success_rate: float | None = None,
    historical_avg_duration: float | None = None,
    duration_baseline: float | None = None,
    failure_penalty_score: float | None = None,
    learned_context_bias: float | None = None,
) -> float:
    lambda3 = _get_env_float("CNP_LAMBDA3", 0.6)
    lambda4 = _get_env_float("CNP_LAMBDA4", 0.6)
    lambda5 = _get_env_float("CNP_LAMBDA5", 0.15)
    lambda6 = _get_env_float("CNP_LAMBDA6", 0.10)
    lambda7 = _get_env_float("CNP_LAMBDA7", 0.25)
    time_norm = 0.0
    load_norm = 0.0
    if tmax > tmin:
        time_norm = (cost - tmin) / (tmax - tmin)
    if lmax > lmin:
        load_norm = (load - lmin) / (lmax - lmin)
    raw = (lambda3 * trust) - (lambda4 * time_norm) - ((1 - lambda4) * load_norm)

    # Historical boost: reward agents with proven track records
    if historical_success_rate is not None:
        raw += lambda5 * historical_success_rate
    if historical_avg_duration is not None and duration_baseline and duration_baseline > 0:
        speed_ratio = 1.0 - min(historical_avg_duration / duration_baseline, 1.0)
        raw += lambda6 * max(speed_ratio, 0.0)

    # Failure surface penalty: penalise agents with recent failures on similar work
    if failure_penalty_score is not None and failure_penalty_score > 0:
        raw -= lambda7 * failure_penalty_score

    # Bandit-learned bias: augments heuristic scoring with learned preferences
    if learned_context_bias is not None:
        lambda_bandit = _get_env_float("CNP_LAMBDA_BANDIT", 0.20)
        raw += lambda_bandit * learned_context_bias

    return _clamp(0.5 + (raw / 2.0), 0.0, 1.0)


def update_cnp_metadata(
    *,
    agent_doc: Dict[str, Any],
    task_type: str,
    stimulus: float,
    priority: int,
    reward: float,
    success: bool,
) -> Dict[str, Any]:
    state = agent_cnp_state(agent_doc)
    trust = trust_score(state)
    alpha, beta = alpha_beta(priority, reward)
    new_threshold = update_threshold(
        stimulus=stimulus,
        trust=trust,
        alpha=alpha,
        beta=beta,
        success=success,
    )

    thresholds = dict(state.get("thresholds") or {})
    thresholds[task_type] = new_threshold

    tasks_received = state.get("tasks_received", 0)
    tasks_acquired = state.get("tasks_acquired", 0) + 1
    tasks_completed = state.get("tasks_completed", 0) + (1 if success else 0)
    load = max(state.get("load", 0) - 1, 0)
    max_load = state.get("max_load", _get_env_int("CNP_LMAX_DEFAULT", 3))

    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    metadata["cnp"] = {
        "tasks_received": tasks_received,
        "tasks_acquired": tasks_acquired,
        "tasks_completed": tasks_completed,
        "load": load,
        "max_load": max_load,
        "thresholds": thresholds,
        "last_update": {
            "task_type": task_type,
            "success": success,
            "stimulus": stimulus,
            "priority": priority,
            "reward": reward,
        },
    }
    return metadata
