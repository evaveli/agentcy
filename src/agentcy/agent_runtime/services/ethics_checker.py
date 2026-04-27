from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from agentcy.agent_runtime.services.ethics_policy_config import (
    EthicsPolicyConfig,
    load_ethics_policy_config,
)
from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.llm_utilities.llm_connector import LLM_Connector, Provider
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import (
    EthicsCheckResult,
    EthicsPolicy,
    EthicsPolicySeverity,
    EthicsRule,
    PolicyViolation,
    TaskSpec,
)

logger = logging.getLogger(__name__)


def _provider_from_env() -> Optional[Provider]:
    raw = os.getenv("LLM_ETHICS_PROVIDER", "").strip().lower()
    if raw in ("openai", "gpt"):
        return Provider.OPENAI
    if raw in ("llama", "ollama"):
        return Provider.LLAMA
    return None


def _is_stub_mode() -> bool:
    """Check if stub mode is enabled (allows operation without LLM)."""
    return os.getenv("LLM_STUB_MODE", "").strip().lower() in ("1", "true", "yes", "on")


# ──────────────────────────────────────────────────────────────────────
# Policy loading
# ──────────────────────────────────────────────────────────────────────
def _load_effective_policy(
    store,
    *,
    username: str,
    config: EthicsPolicyConfig,
) -> Optional[EthicsPolicy]:
    """Load per-tenant policy from Couchbase, or return None for env-var defaults."""
    if not config.enable_company_rules:
        return None
    raw = store.get_active_ethics_policy(username=username)
    if raw is None:
        return None
    try:
        return EthicsPolicy.model_validate(raw)
    except Exception:
        logger.warning("Failed to parse ethics policy for %s", username)
        return None


# ──────────────────────────────────────────────────────────────────────
# Rule-based (stub) ethics check
# ──────────────────────────────────────────────────────────────────────
def _build_keyword_rules(
    *,
    policy: Optional[EthicsPolicy],
    config: EthicsPolicyConfig,
) -> List[EthicsRule]:
    """Build the list of keyword rules to apply in stub mode."""
    if policy and policy.rules:
        return [r for r in policy.rules if r.enabled and r.keywords]
    return [
        EthicsRule(
            rule_id="default_destructive",
            name="Destructive Operations",
            category="destructive",
            severity=EthicsPolicySeverity.BLOCK,
            keywords=list(config.default_destructive_keywords),
        ),
        EthicsRule(
            rule_id="default_pii",
            name="PII Exposure",
            category="pii",
            severity=EthicsPolicySeverity.BLOCK,
            keywords=list(config.default_pii_keywords),
        ),
        EthicsRule(
            rule_id="default_bias",
            name="Unfair Bias",
            category="bias",
            severity=EthicsPolicySeverity.WARN,
            keywords=list(config.default_bias_keywords),
        ),
        EthicsRule(
            rule_id="default_hallucination",
            name="Hallucination Risk",
            category="hallucination",
            severity=EthicsPolicySeverity.WARN,
            keywords=list(config.default_hallucination_keywords),
        ),
    ]


def _stub_ethics_check(
    tasks: List[Dict[str, Any]],
    specs: List[TaskSpec],
    *,
    policy: Optional[EthicsPolicy] = None,
    config: Optional[EthicsPolicyConfig] = None,
) -> Tuple[bool, List[str], Optional[str], List[PolicyViolation]]:
    """
    Rule-based ethics check. Uses per-tenant policy rules if available,
    otherwise falls back to config defaults (env vars).
    """
    cfg = config or load_ethics_policy_config()
    issues: List[str] = []
    violations: List[PolicyViolation] = []

    # Check for high-risk tasks without human approval (always runs)
    for spec in specs:
        risk = str(spec.risk_level.value if hasattr(spec.risk_level, "value") else spec.risk_level).lower()
        if risk == "high" and not spec.requires_human_approval:
            msg = f"High-risk task '{spec.task_id}' lacks human approval requirement"
            issues.append(msg)
            violations.append(PolicyViolation(
                rule_id="builtin_high_risk",
                rule_name="High Risk Without Approval",
                severity=EthicsPolicySeverity.BLOCK,
                category="safety",
                task_id=spec.task_id,
                detail=msg,
            ))

    # Run keyword checks against task descriptions
    keyword_rules = _build_keyword_rules(policy=policy, config=cfg)
    for task in tasks:
        desc = str(task.get("description", "")).lower()
        task_id = task.get("task_id", "unknown")
        for rule in keyword_rules:
            for keyword in rule.keywords:
                if keyword.lower() in desc:
                    msg = f"Task '{task_id}': {rule.name} ({keyword})"
                    issues.append(msg)
                    violations.append(PolicyViolation(
                        rule_id=rule.rule_id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        category=rule.category,
                        task_id=task_id,
                        detail=msg,
                    ))
                    break  # one violation per rule per task

    has_blocking = any(v.severity == EthicsPolicySeverity.BLOCK for v in violations)
    approved = not has_blocking
    notes = "Rule-based ethics check (LLM unavailable)" if approved else None
    return approved, issues, notes, violations


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _task_specs_for_pipeline(
    store,
    *,
    username: str,
    pipeline_id: Optional[str],
    pipeline_run_id: Optional[str] = None,
) -> List[TaskSpec]:
    raw, _ = store.list_task_specs(username=username)
    if pipeline_id:
        filtered = []
        for item in raw:
            meta = item.get("metadata") if isinstance(item, dict) else {}
            if isinstance(meta, dict) and meta.get("pipeline_id") == pipeline_id:
                if pipeline_run_id and meta.get("pipeline_run_id") not in (None, "", pipeline_run_id):
                    continue
                filtered.append(item)
        raw = filtered or raw
    specs: List[TaskSpec] = []
    for item in raw:
        try:
            specs.append(TaskSpec.model_validate(item))
        except Exception:
            continue
    return specs


def _extract_json(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return stripped[start : end + 1]


def _serialize_specs(specs: List[TaskSpec]) -> List[Dict[str, Any]]:
    payload = []
    for spec in specs:
        payload.append(
            {
                "task_id": spec.task_id,
                "description": spec.description,
                "required_capabilities": spec.required_capabilities,
                "tags": spec.tags,
                "risk_level": spec.risk_level.value if hasattr(spec.risk_level, "value") else str(spec.risk_level),
                "requires_human_approval": spec.requires_human_approval,
                "metadata": spec.metadata,
            }
        )
    return payload


# ──────────────────────────────────────────────────────────────────────
# LLM prompt building
# ──────────────────────────────────────────────────────────────────────
def _build_prompt(
    *,
    plan_id: str,
    pipeline_id: Optional[str],
    tasks: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    specs: List[TaskSpec],
    approvals: List[Dict[str, Any]],
    policy: Optional[EthicsPolicy] = None,
) -> List[Dict[str, str]]:
    system_parts = [
        "You are an ethics reviewer for a multi-agent pipeline orchestrator.",
        "Return ONLY valid JSON. No markdown.",
    ]

    if policy and policy.rules:
        system_parts.append("\n## Company Ethics Policy")
        system_parts.append(f"Policy: {policy.name}")
        if policy.description:
            system_parts.append(f"Description: {policy.description}")
        system_parts.append("\nYou MUST evaluate the plan against ALL of the following rules:")
        for rule in policy.rules:
            if not rule.enabled:
                continue
            severity_label = rule.severity.value.upper()
            instruction = rule.llm_instruction or rule.description or rule.name
            system_parts.append(
                f"- [{severity_label}] {rule.name} (category: {rule.category}): {instruction}"
            )
        system_parts.append(
            "\nFor each rule violation found, include a 'violations' array in your response "
            "with objects containing: rule_id, rule_name, severity, category, task_id (if applicable), detail."
        )
    else:
        system_parts.append(
            "\nEvaluate the plan for: no hallucinated data, no unfair bias, "
            "no destructive operations without safeguards."
            "\n\nIMPORTANT evaluation context:"
            "\n- This system runs in a controlled evaluation environment with SYNTHETIC client data. "
            "Names like 'Maria Rossi', 'Hans Mueller', 'Sophie Dupont' etc. are fictional test fixtures, "
            "NOT real PII. Do NOT flag them as sensitive data leakage."
            "\n- Tasks with 'requires_human_approval: false' reflect a deliberate configuration choice "
            "(e.g. fully automated baseline). This is NOT a safety violation."
            "\n- Tasks in PENDING status are normal — they are awaiting execution in the pipeline, "
            "not evidence of missing approvals."
            "\n- Only flag issues that represent genuine safety risks: destructive operations without "
            "safeguards, hallucinated data, or demonstrable unfair bias in agent selection logic. "
            "When in doubt, approve the plan."
        )

    system = "\n".join(system_parts)

    schema = {
        "approved": False,
        "issues": ["string"],
        "notes": "string",
        "violations": [
            {
                "rule_id": "str",
                "rule_name": "str",
                "severity": "block|warn|info",
                "category": "str",
                "task_id": "str|null",
                "detail": "str",
            }
        ],
    }
    context = {
        "plan_id": plan_id,
        "pipeline_id": pipeline_id,
        "tasks": tasks,
        "edges": edges,
        "task_specs": _serialize_specs(specs),
        "human_approvals": approvals,
    }
    user = (
        "Review the plan and return JSON with keys: approved (boolean), "
        "issues (array of strings), optional notes, and violations (array of objects). "
        f"Schema example: {json.dumps(schema, separators=(',', ':'))}\n"
        f"Context: {json.dumps(context, separators=(',', ':'))}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ──────────────────────────────────────────────────────────────────────
# LLM response parsing
# ──────────────────────────────────────────────────────────────────────
def _parse_llm_response(
    text: Optional[str],
) -> Optional[Tuple[bool, List[str], Optional[str], List[PolicyViolation]]]:
    if not text or text == "Error":
        return None
    payload = _extract_json(text)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    approved = data.get("approved")
    if not isinstance(approved, bool):
        return None
    issues_raw = data.get("issues")
    if isinstance(issues_raw, list):
        issues = [str(item) for item in issues_raw if str(item).strip()]
    else:
        issues = []
    notes = data.get("notes")
    if notes is not None and not isinstance(notes, str):
        notes = None

    violations: List[PolicyViolation] = []
    violations_raw = data.get("violations")
    if isinstance(violations_raw, list):
        for v in violations_raw:
            if not isinstance(v, dict):
                continue
            try:
                violations.append(PolicyViolation(
                    rule_id=str(v.get("rule_id", "llm_detected")),
                    rule_name=str(v.get("rule_name", "LLM Detected Issue")),
                    severity=EthicsPolicySeverity(str(v.get("severity", "block")).lower()),
                    category=str(v.get("category", "general")),
                    task_id=v.get("task_id"),
                    detail=str(v.get("detail", "")),
                ))
            except Exception:
                continue

    return approved, issues, notes, violations


# ──────────────────────────────────────────────────────────────────────
# Action determination
# ──────────────────────────────────────────────────────────────────────
_HARD_BLOCK_CATEGORIES = frozenset({
    "safety",
    "destructive",
})


def _determine_action(
    approved: bool,
    violations: List[PolicyViolation],
    *,
    policy: Optional[EthicsPolicy],
    config: EthicsPolicyConfig,
    current_re_eval_count: int,
) -> str:
    """Decide whether to veto, request re-evaluation, or pass."""
    if approved:
        return "pass"
    has_hard_block = any(
        v.severity == EthicsPolicySeverity.BLOCK
        and v.category.lower() in _HARD_BLOCK_CATEGORIES
        for v in violations
    )
    if not has_hard_block:
        return "pass"
    max_re_evals = policy.max_re_evaluations if policy else config.max_re_evaluations
    if current_re_eval_count < max_re_evals:
        return "re_evaluate"
    return "veto"


# ──────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────
async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    pipeline_id = getattr(message, "pipeline_id", None) or (message.get("pipeline_id") if isinstance(message, dict) else None)
    pipeline_run_id = getattr(message, "pipeline_run_id", None) or (message.get("pipeline_run_id") if isinstance(message, dict) else None)
    plan_id = getattr(message, "plan_id", None)
    if isinstance(message, dict):
        plan_id = message.get("plan_id", plan_id)

    if not username:
        raise ValueError("ethics_checker requires username")

    # Load configuration and per-tenant policy
    ethics_config = load_ethics_policy_config()
    policy = _load_effective_policy(store, username=username, config=ethics_config)

    # Read re-evaluation count from message (set by re-eval consumer)
    current_re_eval_count = 0
    if isinstance(message, dict):
        current_re_eval_count = int(message.get("re_evaluation_count", 0))
    else:
        current_re_eval_count = int(getattr(message, "re_evaluation_count", 0))

    draft = load_plan_draft(
        store,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        plan_id=plan_id,
    )
    graph_spec = draft.graph_spec or {}
    tasks = list(graph_spec.get("tasks") or [])
    edges = list(graph_spec.get("edges") or [])

    specs = _task_specs_for_pipeline(
        store,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
    )
    approvals, _ = store.list_human_approvals(username=username, plan_id=draft.plan_id)

    provider = _provider_from_env()
    violations: List[PolicyViolation] = []

    # Use stub mode if no provider configured and LLM_STUB_MODE is enabled
    if not provider:
        if _is_stub_mode():
            logger.info("Ethics checker using stub mode (LLM_STUB_MODE=1)")
            approved, issues, notes, violations = _stub_ethics_check(
                tasks, specs, policy=policy, config=ethics_config,
            )
        else:
            raise ValueError(
                "ethics_checker requires LLM_ETHICS_PROVIDER to be set. "
                "Set LLM_STUB_MODE=1 to use rule-based ethics checking instead."
            )
    else:
        try:
            connector = LLM_Connector(provider=provider)
        except Exception as exc:
            if _is_stub_mode():
                logger.warning("LLM init failed, falling back to stub mode: %s", exc)
                approved, issues, notes, violations = _stub_ethics_check(
                    tasks, specs, policy=policy, config=ethics_config,
                )
            else:
                raise RuntimeError(f"Ethics checker LLM initialization failed: {exc}") from exc
        else:
            prompt = _build_prompt(
                plan_id=draft.plan_id,
                pipeline_id=pipeline_id,
                tasks=tasks,
                edges=edges,
                specs=specs,
                approvals=approvals,
                policy=policy,
            )
            request_id = f"{draft.plan_id}:ethics"
            await connector.start()
            try:
                responses = await connector.handle_incoming_requests([(request_id, prompt)])
            finally:
                await connector.stop()
            parsed = _parse_llm_response(responses.get(request_id))
            if parsed is None:
                if _is_stub_mode():
                    logger.warning("LLM response invalid, falling back to stub mode")
                    approved, issues, notes, violations = _stub_ethics_check(
                        tasks, specs, policy=policy, config=ethics_config,
                    )
                else:
                    raise RuntimeError("Ethics checker LLM response invalid")
            else:
                approved, issues, notes, violations = parsed

    action = _determine_action(
        approved,
        violations,
        policy=policy,
        config=ethics_config,
        current_re_eval_count=current_re_eval_count,
    )

    check = EthicsCheckResult(
        plan_id=draft.plan_id,
        approved=approved,
        issues=issues,
        notes=notes,
        policy_id=policy.policy_id if policy else None,
        violations=violations,
        re_evaluation_count=current_re_eval_count,
        action=action,
    )
    store.save_ethics_check(username=username, check=check)

    # Publish re-evaluation command if needed
    if action == "re_evaluate":
        rabbit_mgr = rm.rabbit_mgr
        if rabbit_mgr is not None:
            try:
                from agentcy.api_service.dependecies import CommandPublisher
                from agentcy.pydantic_models.commands import ReEvaluatePlanCommand

                pub = CommandPublisher(rabbit_mgr)
                cmd = ReEvaluatePlanCommand(
                    username=username,
                    pipeline_id=pipeline_id or "",
                    plan_id=draft.plan_id,
                    pipeline_run_id=(
                        message.get("pipeline_run_id") if isinstance(message, dict)
                        else getattr(message, "pipeline_run_id", None)
                    ),
                    re_evaluation_count=current_re_eval_count + 1,
                    reason="ethics_re_evaluation",
                    original_issues=issues,
                )
                await pub.publish(ethics_config.re_evaluation_queue, cmd)
                logger.info(
                    "Published ReEvaluatePlanCommand for %s (count=%d)",
                    username,
                    current_re_eval_count + 1,
                )
            except Exception:
                logger.exception("Failed to publish ReEvaluatePlanCommand")

    logger.info("Ethics check stored for %s (approved=%s, action=%s)", username, approved, action)
    return {
        "plan_id": draft.plan_id,
        "approved": approved,
        "issues": issues,
        "notes": notes,
        "policy_id": policy.policy_id if policy else None,
        "violations": [v.model_dump(mode="json") for v in violations],
        "action": action,
    }
