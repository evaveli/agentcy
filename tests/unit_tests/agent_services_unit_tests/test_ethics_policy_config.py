import pytest

from src.agentcy.agent_runtime.services.ethics_policy_config import (
    EthicsPolicyConfig,
    load_ethics_policy_config,
)


def test_defaults():
    config = load_ethics_policy_config()
    assert config.enable_company_rules is False
    assert "delete" in config.default_destructive_keywords
    assert "ssn" in config.default_pii_keywords
    assert "discriminate" in config.default_bias_keywords
    assert "fabricate" in config.default_hallucination_keywords
    assert config.require_human_on_high_risk_block is False
    assert config.max_re_evaluations == 2
    assert config.re_evaluation_queue == "commands.ethics_re_evaluate"
    assert config.block_on_no_policy is False


def test_env_override_enable(monkeypatch):
    monkeypatch.setenv("ETHICS_ENABLE_COMPANY_RULES", "1")
    config = load_ethics_policy_config()
    assert config.enable_company_rules is True


def test_env_override_keywords(monkeypatch):
    monkeypatch.setenv("ETHICS_DESTRUCTIVE_KEYWORDS", "nuke,obliterate")
    config = load_ethics_policy_config()
    assert config.default_destructive_keywords == ("nuke", "obliterate")
    assert "delete" not in config.default_destructive_keywords


def test_env_override_max_re_evaluations(monkeypatch):
    monkeypatch.setenv("ETHICS_MAX_RE_EVALUATIONS", "5")
    config = load_ethics_policy_config()
    assert config.max_re_evaluations == 5


def test_env_override_max_re_evaluations_clamped(monkeypatch):
    monkeypatch.setenv("ETHICS_MAX_RE_EVALUATIONS", "99")
    config = load_ethics_policy_config()
    assert config.max_re_evaluations == 10


def test_env_override_block_on_no_policy(monkeypatch):
    monkeypatch.setenv("ETHICS_BLOCK_ON_NO_POLICY", "true")
    config = load_ethics_policy_config()
    assert config.block_on_no_policy is True


def test_frozen_immutability():
    config = load_ethics_policy_config()
    with pytest.raises(AttributeError):
        config.enable_company_rules = True


def test_empty_keywords_env(monkeypatch):
    monkeypatch.setenv("ETHICS_DESTRUCTIVE_KEYWORDS", "")
    config = load_ethics_policy_config()
    assert config.default_destructive_keywords == ()


def test_invalid_int_falls_back(monkeypatch):
    monkeypatch.setenv("ETHICS_MAX_RE_EVALUATIONS", "not_a_number")
    config = load_ethics_policy_config()
    assert config.max_re_evaluations == 2  # default
