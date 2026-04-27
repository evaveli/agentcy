from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EthicsPolicyConfig:
    enable_company_rules: bool
    default_destructive_keywords: tuple[str, ...]
    default_pii_keywords: tuple[str, ...]
    default_bias_keywords: tuple[str, ...]
    default_hallucination_keywords: tuple[str, ...]
    require_human_on_high_risk_block: bool
    max_re_evaluations: int
    re_evaluation_queue: str
    block_on_no_policy: bool


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


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    if not raw:
        return tuple()
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def load_ethics_policy_config() -> EthicsPolicyConfig:
    return EthicsPolicyConfig(
        enable_company_rules=_env_bool("ETHICS_ENABLE_COMPANY_RULES", False),
        default_destructive_keywords=_env_csv(
            "ETHICS_DESTRUCTIVE_KEYWORDS",
            "delete,remove,drop,truncate,destroy,wipe",
        ),
        default_pii_keywords=_env_csv(
            "ETHICS_PII_KEYWORDS",
            "ssn,social security,credit card,password,secret",
        ),
        default_bias_keywords=_env_csv(
            "ETHICS_BIAS_KEYWORDS",
            "discriminate,exclude,blacklist,whitelist",
        ),
        default_hallucination_keywords=_env_csv(
            "ETHICS_HALLUCINATION_KEYWORDS",
            "assume,fabricate,invent,guess",
        ),
        require_human_on_high_risk_block=_env_bool("ETHICS_REQUIRE_HUMAN_ON_BLOCK", False),
        max_re_evaluations=max(0, min(10, _env_int("ETHICS_MAX_RE_EVALUATIONS", 2))),
        re_evaluation_queue=os.getenv("ETHICS_RE_EVALUATION_QUEUE", "commands.ethics_re_evaluate"),
        block_on_no_policy=_env_bool("ETHICS_BLOCK_ON_NO_POLICY", False),
    )
