from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from agentcy.pydantic_models.agent_registry_model import AgentStatus


@dataclass(frozen=True)
class RegistryPolicyConfig:
    enable: bool
    filter_stale: bool
    include_coverage: bool
    freshness_warn_seconds: int
    freshness_stale_seconds: int
    freshness_offline_seconds: int
    decay_half_life_seconds: int
    decay_min_factor: float
    coverage_min_per_cap: int
    coverage_target_per_cap: int
    coverage_min_per_tag: int
    coverage_target_per_tag: int
    coverage_status_allow: tuple[str, ...]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    if not raw:
        return tuple()
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def load_registry_policy_config() -> RegistryPolicyConfig:
    warn = max(0, _env_int("AGENT_REGISTRY_FRESHNESS_WARN_SECONDS", 60))
    stale = max(warn, _env_int("AGENT_REGISTRY_FRESHNESS_STALE_SECONDS", 300))
    offline = max(stale, _env_int("AGENT_REGISTRY_FRESHNESS_OFFLINE_SECONDS", 900))
    return RegistryPolicyConfig(
        enable=_env_bool("AGENT_REGISTRY_POLICY_ENABLE", True),
        filter_stale=_env_bool("AGENT_REGISTRY_POLICY_FILTER_STALE", True),
        include_coverage=_env_bool("AGENT_REGISTRY_POLICY_INCLUDE_COVERAGE", True),
        freshness_warn_seconds=warn,
        freshness_stale_seconds=stale,
        freshness_offline_seconds=offline,
        decay_half_life_seconds=max(0, _env_int("AGENT_REGISTRY_DECAY_HALF_LIFE_SECONDS", 900)),
        decay_min_factor=max(0.0, min(1.0, _env_float("AGENT_REGISTRY_DECAY_MIN_FACTOR", 0.2))),
        coverage_min_per_cap=max(0, _env_int("AGENT_REGISTRY_COVERAGE_MIN_PER_CAP", 1)),
        coverage_target_per_cap=max(1, _env_int("AGENT_REGISTRY_COVERAGE_TARGET_PER_CAP", 3)),
        coverage_min_per_tag=max(0, _env_int("AGENT_REGISTRY_COVERAGE_MIN_PER_TAG", 1)),
        coverage_target_per_tag=max(1, _env_int("AGENT_REGISTRY_COVERAGE_TARGET_PER_TAG", 3)),
        coverage_status_allow=_env_csv(
            "AGENT_REGISTRY_COVERAGE_STATUS_ALLOW", "online,idle,busy"
        ),
    )


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _coerce_status(value: Any) -> str:
    if isinstance(value, AgentStatus):
        return value.value
    if value is None:
        return ""
    return str(value).strip().lower()


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _freshness_state(age_seconds: float, config: RegistryPolicyConfig) -> tuple[str, bool, bool]:
    if config.freshness_offline_seconds and age_seconds >= config.freshness_offline_seconds:
        return "offline", True, True
    if config.freshness_stale_seconds and age_seconds >= config.freshness_stale_seconds:
        return "stale", True, False
    if config.freshness_warn_seconds and age_seconds >= config.freshness_warn_seconds:
        return "warn", False, False
    return "fresh", False, False


def _freshness_score(age_seconds: float, config: RegistryPolicyConfig) -> float:
    if config.freshness_stale_seconds <= 0:
        return 1.0
    score = 1.0 - (age_seconds / float(config.freshness_stale_seconds))
    return max(0.0, min(1.0, score))


def _decay_factor(age_seconds: float, config: RegistryPolicyConfig) -> float:
    if config.decay_half_life_seconds <= 0:
        return 1.0
    # Exponential decay: factor halves every half-life period.
    factor = math.pow(0.5, age_seconds / float(config.decay_half_life_seconds))
    return max(config.decay_min_factor, min(1.0, factor))


def _coverage_score(counts: Iterable[int], target: int) -> float:
    values = list(counts)
    if not values:
        return 0.0
    if target <= 0:
        return 1.0
    score = min(value / float(target) for value in values)
    return max(0.0, min(1.0, score))


def _evaluate_entries(
    entries: Iterable[Dict[str, Any]],
    *,
    config: RegistryPolicyConfig,
    now: datetime,
) -> List[Dict[str, Any]]:
    evaluated: List[Dict[str, Any]] = []
    for entry in entries:
        doc = dict(entry)
        last_heartbeat = _parse_dt(doc.get("last_heartbeat")) or _parse_dt(doc.get("registered_at"))
        expires_at = _parse_dt(doc.get("expires_at"))

        age_seconds = 0.0
        if last_heartbeat is not None:
            age_seconds = max(0.0, (now - last_heartbeat).total_seconds())

        state, stale, offline = _freshness_state(age_seconds, config)
        if expires_at is not None and expires_at <= now:
            state = "expired"
            stale = True
            offline = True

        freshness_score = _freshness_score(age_seconds, config)
        decay_factor = _decay_factor(age_seconds, config)

        status_raw = _coerce_status(doc.get("status"))
        effective_status = status_raw
        if offline:
            effective_status = AgentStatus.OFFLINE.value

        policy = dict(doc.get("policy") or {})
        policy.update(
            {
                "evaluated_at": now.isoformat(),
                "effective_status": effective_status,
                "stale": stale,
                "freshness": {
                    "age_seconds": age_seconds,
                    "score": freshness_score,
                    "state": state,
                },
                "decay": {
                    "age_seconds": age_seconds,
                    "factor": decay_factor,
                },
            }
        )
        doc["policy"] = policy
        evaluated.append(doc)
    return evaluated


def _coverage_counts(
    entries: Iterable[Dict[str, Any]],
    *,
    config: RegistryPolicyConfig,
) -> tuple[Dict[str, int], Dict[str, int]]:
    cap_counts: Dict[str, int] = {}
    tag_counts: Dict[str, int] = {}
    allow = set(config.coverage_status_allow)

    for entry in entries:
        policy = entry.get("policy") or {}
        status = _coerce_status(policy.get("effective_status") or entry.get("status"))
        if allow and status not in allow:
            continue
        if policy.get("stale"):
            continue

        for cap in set(_as_list(entry.get("capabilities"))):
            cap_counts[cap] = cap_counts.get(cap, 0) + 1
        for tag in set(_as_list(entry.get("tags"))):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return cap_counts, tag_counts


def apply_registry_policies(
    entries: Iterable[Dict[str, Any]],
    *,
    config: Optional[RegistryPolicyConfig] = None,
    now: Optional[datetime] = None,
    include_coverage: Optional[bool] = None,
    coverage_context: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    cfg = config or load_registry_policy_config()
    if not cfg.enable:
        return [dict(entry) for entry in entries]

    now = now or datetime.now(timezone.utc)
    evaluated = _evaluate_entries(entries, config=cfg, now=now)

    want_coverage = cfg.include_coverage if include_coverage is None else include_coverage
    if want_coverage:
        context_entries = coverage_context if coverage_context is not None else evaluated
        # Ensure the coverage context has policy snapshots too.
        if coverage_context is not None:
            context_entries = _evaluate_entries(context_entries, config=cfg, now=now)
        cap_counts, tag_counts = _coverage_counts(context_entries, config=cfg)

        for doc in evaluated:
            caps = _as_list(doc.get("capabilities"))
            tags = _as_list(doc.get("tags"))
            cap_map = {cap: cap_counts.get(cap, 0) for cap in caps}
            tag_map = {tag: tag_counts.get(tag, 0) for tag in tags}
            cap_under = [cap for cap, count in cap_map.items() if count < cfg.coverage_min_per_cap]
            tag_under = [tag for tag, count in tag_map.items() if count < cfg.coverage_min_per_tag]
            cap_score = _coverage_score(cap_map.values(), cfg.coverage_target_per_cap)
            tag_score = _coverage_score(tag_map.values(), cfg.coverage_target_per_tag)
            if caps and tags:
                score = min(cap_score, tag_score)
            elif caps:
                score = cap_score
            elif tags:
                score = tag_score
            else:
                score = 0.0

            policy = dict(doc.get("policy") or {})
            policy["coverage"] = {
                "capability_counts": cap_map,
                "tag_counts": tag_map,
                "under_covered_capabilities": cap_under,
                "under_covered_tags": tag_under,
                "capability_score": cap_score,
                "tag_score": tag_score,
                "score": score,
                "min_per_capability": cfg.coverage_min_per_cap,
                "target_per_capability": cfg.coverage_target_per_cap,
                "min_per_tag": cfg.coverage_min_per_tag,
                "target_per_tag": cfg.coverage_target_per_tag,
            }
            doc["policy"] = policy

    return evaluated
