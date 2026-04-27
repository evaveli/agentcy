import json

import pytest

from src.agentcy.agent_runtime.services import ethics_checker
from src.agentcy.agent_runtime.services.ethics_checker import (
    _build_prompt,
    _determine_action,
    _stub_ethics_check,
    run,
)
from src.agentcy.agent_runtime.services.ethics_policy_config import EthicsPolicyConfig
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    EthicsPolicy,
    EthicsPolicySeverity,
    EthicsRule,
    PlanDraft,
    PolicyViolation,
    TaskSpec,
)


class _FakeStore:
    def __init__(self):
        self.specs = []
        self.drafts = {}
        self.approvals = []
        self.ethics = []
        self.active_policy = None

    def list_task_specs(self, *, username):
        items = list(self.specs)
        return items, len(items)

    def list_plan_drafts(self, *, username, pipeline_id=None):
        drafts = list(self.drafts.values())
        if pipeline_id:
            drafts = [d for d in drafts if d.get("pipeline_id") == pipeline_id]
        return drafts, len(drafts)

    def get_plan_draft(self, *, username, plan_id):
        return self.drafts.get(plan_id)

    def save_plan_draft(self, *, username, draft):
        self.drafts[draft.plan_id] = draft.model_dump(mode="json")

    def list_human_approvals(self, *, username, plan_id=None):
        items = list(self.approvals)
        return items, len(items)

    def save_ethics_check(self, *, username, check):
        self.ethics.append((username, check))
        return "ethics-1"

    def get_active_ethics_policy(self, *, username):
        return self.active_policy


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store
        self.rabbit_mgr = None


def _fake_llm(response_text: str):
    class _FakeLLM:
        def __init__(self, provider):
            self.provider = provider

        async def start(self):
            return None

        async def stop(self):
            return None

        async def handle_incoming_requests(self, requests):
            return {request_id: response_text for request_id, _ in requests}

    return _FakeLLM


def _setup_store(username="alice", pipeline_id="pipe-1", plan_id="plan-1", task_desc="plan"):
    store = _FakeStore()
    spec = TaskSpec(
        task_id="task-1",
        username=username,
        description=task_desc,
        required_capabilities=["plan"],
        requires_human_approval=True,
        metadata={"pipeline_id": pipeline_id},
    )
    store.specs.append(spec.model_dump(mode="json"))
    draft = PlanDraft(
        plan_id=plan_id,
        username=username,
        pipeline_id=pipeline_id,
        graph_spec={
            "tasks": [{"task_id": "task-1", "description": task_desc, "required_capabilities": ["plan"]}],
            "edges": [],
        },
    )
    store.save_plan_draft(username=username, draft=draft)
    return store


# ──────────────────────────────────────────────────────────────────────
# Original test (backward compatibility)
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ethics_checker_requires_human_approval(monkeypatch):
    store = _setup_store()
    rm = _FakeRM(store)
    message = {"username": "alice", "pipeline_id": "pipe-1", "plan_id": "plan-1", "data": {}}
    response = json.dumps(
        {"approved": False, "issues": ["policy_violation"], "notes": "blocked by policy"}
    )
    monkeypatch.setenv("LLM_ETHICS_PROVIDER", "openai")
    monkeypatch.setattr(ethics_checker, "LLM_Connector", _fake_llm(response))
    result = await run(rm, "run-1", "ethics_checker", None, message)

    assert result["approved"] is False
    assert "policy_violation" in result["issues"]
    assert store.ethics


# ──────────────────────────────────────────────────────────────────────
# Backward compat: no policy, ETHICS_ENABLE_COMPANY_RULES=0
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_backward_compat_no_policy(monkeypatch):
    store = _setup_store()
    rm = _FakeRM(store)
    monkeypatch.setenv("LLM_STUB_MODE", "1")
    monkeypatch.setenv("ETHICS_ENABLE_COMPANY_RULES", "0")
    monkeypatch.delenv("LLM_ETHICS_PROVIDER", raising=False)

    message = {"username": "alice", "pipeline_id": "pipe-1", "plan_id": "plan-1"}
    result = await run(rm, "run-1", "ethics_checker", None, message)

    assert result["approved"] is True
    assert result["policy_id"] is None
    assert result["action"] == "pass"
    assert result["violations"] == []


# ──────────────────────────────────────────────────────────────────────
# Stub with default keywords detects destructive ops
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stub_with_default_keywords(monkeypatch):
    store = _setup_store(task_desc="delete all user data")
    rm = _FakeRM(store)
    monkeypatch.setenv("LLM_STUB_MODE", "1")
    monkeypatch.setenv("ETHICS_ENABLE_COMPANY_RULES", "0")
    monkeypatch.delenv("LLM_ETHICS_PROVIDER", raising=False)

    message = {"username": "alice", "pipeline_id": "pipe-1", "plan_id": "plan-1"}
    result = await run(rm, "run-1", "ethics_checker", None, message)

    assert result["approved"] is False
    assert any("delete" in issue.lower() for issue in result["issues"])
    assert len(result["violations"]) > 0


# ──────────────────────────────────────────────────────────────────────
# Stub with company policy applies custom rules
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stub_with_company_policy(monkeypatch):
    store = _setup_store(task_desc="send marketing email to all contacts")
    rm = _FakeRM(store)
    monkeypatch.setenv("LLM_STUB_MODE", "1")
    monkeypatch.setenv("ETHICS_ENABLE_COMPANY_RULES", "1")
    monkeypatch.delenv("LLM_ETHICS_PROVIDER", raising=False)

    policy = EthicsPolicy(
        policy_id="pol-1",
        username="alice",
        name="ACME Corp Policy",
        description="Internal company rules",
        rules=[
            EthicsRule(
                rule_id="no_mass_email",
                name="No Mass Marketing",
                category="compliance",
                severity=EthicsPolicySeverity.BLOCK,
                keywords=["marketing email", "mass email"],
                llm_instruction="Do not allow mass marketing emails without consent.",
            ),
        ],
    )
    store.active_policy = policy.model_dump(mode="json")

    message = {"username": "alice", "pipeline_id": "pipe-1", "plan_id": "plan-1"}
    result = await run(rm, "run-1", "ethics_checker", None, message)

    assert result["approved"] is False
    assert result["policy_id"] == "pol-1"
    assert any(v["rule_id"] == "no_mass_email" for v in result["violations"])


# ──────────────────────────────────────────────────────────────────────
# LLM prompt includes company rules
# ──────────────────────────────────────────────────────────────────────
def test_llm_prompt_includes_company_rules():
    policy = EthicsPolicy(
        policy_id="pol-1",
        username="alice",
        name="ACME Corp Policy",
        description="Safety first",
        rules=[
            EthicsRule(
                rule_id="no_pii",
                name="No PII Leakage",
                category="pii",
                severity=EthicsPolicySeverity.BLOCK,
                keywords=["ssn"],
                llm_instruction="Never expose personally identifiable information.",
            ),
            EthicsRule(
                rule_id="no_bias",
                name="No Unfair Bias",
                category="bias",
                severity=EthicsPolicySeverity.WARN,
                keywords=[],
                llm_instruction="Ensure no discriminatory outcomes.",
            ),
        ],
    )
    prompt = _build_prompt(
        plan_id="plan-1",
        pipeline_id="pipe-1",
        tasks=[{"task_id": "t1", "description": "test"}],
        edges=[],
        specs=[],
        approvals=[],
        policy=policy,
    )
    system_msg = prompt[0]["content"]
    assert "ACME Corp Policy" in system_msg
    assert "No PII Leakage" in system_msg
    assert "Never expose personally identifiable information" in system_msg
    assert "[BLOCK]" in system_msg
    assert "[WARN]" in system_msg
    assert "violations" in prompt[1]["content"]


# ──────────────────────────────────────────────────────────────────────
# LLM prompt uses default instructions when no policy
# ──────────────────────────────────────────────────────────────────────
def test_llm_prompt_default_instructions():
    prompt = _build_prompt(
        plan_id="plan-1",
        pipeline_id="pipe-1",
        tasks=[],
        edges=[],
        specs=[],
        approvals=[],
        policy=None,
    )
    system_msg = prompt[0]["content"]
    assert "no hallucinated data" in system_msg
    assert "no unfair bias" in system_msg
    assert "no sensitive data leakage" in system_msg


# ──────────────────────────────────────────────────────────────────────
# Enhanced result has violations
# ──────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_enhanced_result_has_violations(monkeypatch):
    store = _setup_store()
    rm = _FakeRM(store)
    response = json.dumps({
        "approved": False,
        "issues": ["data_leak"],
        "notes": "PII found",
        "violations": [
            {
                "rule_id": "pii_check",
                "rule_name": "PII Exposure",
                "severity": "block",
                "category": "pii",
                "task_id": "task-1",
                "detail": "SSN detected in output",
            }
        ],
    })
    monkeypatch.setenv("LLM_ETHICS_PROVIDER", "openai")
    monkeypatch.setattr(ethics_checker, "LLM_Connector", _fake_llm(response))
    result = await run(rm, "run-1", "ethics_checker", None, {
        "username": "alice",
        "pipeline_id": "pipe-1",
        "plan_id": "plan-1",
    })

    assert result["approved"] is False
    assert len(result["violations"]) == 1
    assert result["violations"][0]["rule_id"] == "pii_check"
    assert result["violations"][0]["severity"] == "block"


# ──────────────────────────────────────────────────────────────────────
# _determine_action tests
# ──────────────────────────────────────────────────────────────────────
def _make_config(**kwargs):
    defaults = dict(
        enable_company_rules=False,
        default_destructive_keywords=("delete",),
        default_pii_keywords=("ssn",),
        default_bias_keywords=("discriminate",),
        default_hallucination_keywords=("fabricate",),
        require_human_on_high_risk_block=False,
        max_re_evaluations=2,
        re_evaluation_queue="commands.ethics_re_evaluate",
        block_on_no_policy=False,
    )
    defaults.update(kwargs)
    return EthicsPolicyConfig(**defaults)


def test_determine_action_pass():
    action = _determine_action(
        True, [], policy=None, config=_make_config(), current_re_eval_count=0,
    )
    assert action == "pass"


def test_determine_action_re_evaluate():
    violations = [
        PolicyViolation(
            rule_id="r1", rule_name="Test", severity=EthicsPolicySeverity.BLOCK,
            category="destructive", detail="bad",
        )
    ]
    action = _determine_action(
        False, violations, policy=None, config=_make_config(), current_re_eval_count=0,
    )
    assert action == "re_evaluate"


def test_determine_action_veto_on_safety_block():
    violations = [
        PolicyViolation(
            rule_id="r1", rule_name="Safety", severity=EthicsPolicySeverity.BLOCK,
            category="safety", detail="critical",
        )
    ]
    action = _determine_action(
        False, violations, policy=None, config=_make_config(), current_re_eval_count=0,
    )
    assert action == "veto"


def test_determine_action_veto_on_max_reached():
    violations = [
        PolicyViolation(
            rule_id="r1", rule_name="Test", severity=EthicsPolicySeverity.BLOCK,
            category="pii", detail="bad",
        )
    ]
    action = _determine_action(
        False, violations, policy=None, config=_make_config(max_re_evaluations=2),
        current_re_eval_count=2,
    )
    assert action == "veto"


# ──────────────────────────────────────────────────────────────────────
# _stub_ethics_check unit tests
# ──────────────────────────────────────────────────────────────────────
def test_stub_detects_high_risk_without_approval():
    specs = [
        TaskSpec(
            task_id="t1", username="alice", description="important task",
            risk_level="high", requires_human_approval=False,
        ),
    ]
    approved, issues, notes, violations = _stub_ethics_check(
        [{"task_id": "t1", "description": "safe task"}], specs, config=_make_config(),
    )
    assert approved is False
    assert any(v.rule_id == "builtin_high_risk" for v in violations)
    assert any(v.category == "safety" for v in violations)


def test_stub_warn_does_not_block():
    config = _make_config(
        default_bias_keywords=("keyword_match",),
        default_destructive_keywords=(),
        default_pii_keywords=(),
    )
    tasks = [{"task_id": "t1", "description": "has keyword_match in it"}]
    approved, issues, notes, violations = _stub_ethics_check(
        tasks, [], config=config,
    )
    # bias keywords produce WARN severity, which should not block
    assert approved is True
    assert len(violations) > 0
    assert all(v.severity == EthicsPolicySeverity.WARN for v in violations)
