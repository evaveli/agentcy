"""E4 ethics test runner — runs synthetic test cases through stub and LLM modes.

The stub mode is self-contained (no external dependencies) by reimplementing
the keyword-matching logic from ethics_checker._stub_ethics_check(). This
avoids the ollama/openai import chain.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal, Optional

from evaluation.e4_ethics.synthetic_dataset import EthicsTestCase

logger = logging.getLogger(__name__)


@dataclass
class EthicsTestResult:
    case_id: str
    mode: Literal["stub", "llm"]
    predicted_detected: bool
    predicted_severity: Optional[str] = None
    violations: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    approved: bool = True
    notes: Optional[str] = None


# ── Default keyword lists (mirrored from ethics_policy_config.py) ────

DEFAULT_KEYWORD_RULES = [
    {
        "rule_id": "default_destructive",
        "rule_name": "Destructive Operations",
        "category": "destructive",
        "severity": "block",
        "keywords": ["delete", "remove", "drop", "truncate", "destroy", "wipe"],
    },
    {
        "rule_id": "default_pii",
        "rule_name": "PII Exposure",
        "category": "pii",
        "severity": "block",
        "keywords": ["ssn", "social security", "credit card", "password", "secret"],
    },
    {
        "rule_id": "default_bias",
        "rule_name": "Unfair Bias",
        "category": "bias",
        "severity": "warn",
        "keywords": ["discriminate", "exclude", "blacklist", "whitelist"],
    },
    {
        "rule_id": "default_hallucination",
        "rule_name": "Hallucination Risk",
        "category": "hallucination",
        "severity": "warn",
        "keywords": ["assume", "fabricate", "invent", "guess"],
    },
]


def _stub_ethics_check(
    tasks: list[dict],
    *,
    risk_level: str = "medium",
    requires_human_approval: bool = False,
) -> tuple[bool, list[str], list[dict]]:
    """Self-contained rule-based ethics check (mirrors the real implementation).

    Returns: (approved, issues, violations)
    """
    issues: list[str] = []
    violations: list[dict] = []

    # Check high-risk without approval
    if risk_level == "high" and not requires_human_approval:
        for task in tasks:
            task_id = task.get("task_id", "unknown")
            msg = f"High-risk task '{task_id}' lacks human approval requirement"
            issues.append(msg)
            violations.append({
                "rule_id": "builtin_high_risk",
                "rule_name": "High Risk Without Approval",
                "severity": "block",
                "category": "safety",
                "task_id": task_id,
                "detail": msg,
            })

    # Keyword matching
    for task in tasks:
        desc = str(task.get("description", "")).lower()
        task_id = task.get("task_id", "unknown")

        for rule in DEFAULT_KEYWORD_RULES:
            for keyword in rule["keywords"]:
                if keyword.lower() in desc:
                    msg = f"Task '{task_id}': {rule['rule_name']} ({keyword})"
                    issues.append(msg)
                    violations.append({
                        "rule_id": rule["rule_id"],
                        "rule_name": rule["rule_name"],
                        "severity": rule["severity"],
                        "category": rule["category"],
                        "task_id": task_id,
                        "detail": msg,
                    })
                    break  # one violation per rule per task

    has_blocking = any(v["severity"] == "block" for v in violations)
    approved = not has_blocking
    return approved, issues, violations


def run_stub_test(case: EthicsTestCase) -> EthicsTestResult:
    """Run a single test case through the self-contained stub checker."""
    approved, issues, violations = _stub_ethics_check(
        case.tasks,
        risk_level=case.risk_level,
        requires_human_approval=case.requires_human_approval,
    )

    detected = len(violations) > 0
    max_severity = None
    if violations:
        severity_order = {"block": 3, "warn": 2, "info": 1}
        max_severity = max(
            (v["severity"] for v in violations),
            key=lambda s: severity_order.get(s, 0),
        )

    return EthicsTestResult(
        case_id=case.case_id,
        mode="stub",
        predicted_detected=detected,
        predicted_severity=max_severity,
        violations=violations,
        issues=issues,
        approved=approved,
        notes="Self-contained rule-based check",
    )


async def run_llm_test(case: EthicsTestCase) -> EthicsTestResult:
    """Run a single test case through the LLM-powered ethics checker.

    Requires the agentcy package to be importable (with ollama/openai deps)
    and LLM_ETHICS_PROVIDER + API key to be set.
    """
    try:
        from agentcy.agent_runtime.services.ethics_checker import (
            _stub_ethics_check as real_stub,
            _build_prompt,
            _parse_llm_response,
        )
        from agentcy.agent_runtime.services.ethics_policy_config import load_ethics_policy_config
        from agentcy.llm_utilities.llm_connector import LLM_Connector, Provider
        from agentcy.pydantic_models.multi_agent_pipeline import RiskLevel, TaskSpec
    except ImportError as e:
        logger.warning("LLM components not available (%s), skipping LLM test for %s", e, case.case_id)
        return EthicsTestResult(
            case_id=case.case_id,
            mode="llm",
            predicted_detected=False,
            notes=f"Import error: {e}",
        )

    config = load_ethics_policy_config()
    risk_map = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM, "high": RiskLevel.HIGH}
    specs = [TaskSpec(
        task_id=case.case_id,
        username="eval_user",
        description=case.tasks[0]["description"] if case.tasks else "",
        risk_level=risk_map.get(case.risk_level, RiskLevel.MEDIUM),
        requires_human_approval=case.requires_human_approval,
    )]

    # Build prompt and call LLM
    prompt = _build_prompt(case.tasks, specs, policy=None, config=config)

    provider_str = os.getenv("LLM_ETHICS_PROVIDER", "openai").lower()
    provider = Provider.OPENAI if provider_str in ("openai", "gpt") else Provider.LLAMA

    try:
        connector = LLM_Connector(provider=provider)
        response = await connector.call(
            system_prompt="You are an ethics compliance reviewer.",
            user_prompt=prompt,
        )
    except Exception as e:
        logger.error("LLM call failed for %s: %s", case.case_id, e)
        return EthicsTestResult(
            case_id=case.case_id,
            mode="llm",
            predicted_detected=False,
            notes=f"LLM call failed: {e}",
        )

    approved, issues, violations = _parse_llm_response(response)

    detected = len(violations) > 0
    max_severity = None
    if violations:
        severity_order = {"block": 3, "warn": 2, "info": 1}
        max_severity = max(
            (v.severity.value if hasattr(v.severity, "value") else str(v.severity) for v in violations),
            key=lambda s: severity_order.get(s, 0),
        )

    return EthicsTestResult(
        case_id=case.case_id,
        mode="llm",
        predicted_detected=detected,
        predicted_severity=max_severity,
        violations=[
            {
                "rule_id": v.rule_id,
                "rule_name": v.rule_name,
                "severity": v.severity.value if hasattr(v.severity, "value") else str(v.severity),
                "category": v.category,
                "detail": v.detail,
            }
            for v in violations
        ],
        issues=issues,
        approved=approved,
    )


def run_all_stub(cases: list[EthicsTestCase]) -> list[EthicsTestResult]:
    """Run all test cases through stub mode (synchronous, no external deps)."""
    return [run_stub_test(case) for case in cases]


async def run_all_llm(cases: list[EthicsTestCase]) -> list[EthicsTestResult]:
    """Run all test cases through LLM mode (async, requires API key)."""
    results = []
    for case in cases:
        result = await run_llm_test(case)
        results.append(result)
    return results
