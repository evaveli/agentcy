"""Tests for the LinUCB contextual bandit learner."""
from __future__ import annotations

import math
import os
from typing import Any, Dict, Optional

import numpy as np
import pytest

from src.agentcy.agent_runtime.services.bandit_learner import (
    BanditLearner,
    LinUCBContext,
    _bandit_enabled,
    compute_reward,
    features_to_array,
)
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    BidFeatures,
    ExecutionOutcomeBandit,
    LinUCBModelState,
)


# ── LinUCBContext ────────────────────────────────────────────────────────


class TestLinUCBContext:
    def test_init_identity_and_zeros(self):
        ctx = LinUCBContext(d=6)
        assert ctx.A.shape == (6, 6)
        assert ctx.b.shape == (6,)
        np.testing.assert_array_equal(ctx.A, np.eye(6))
        np.testing.assert_array_equal(ctx.b, np.zeros(6))
        assert ctx.n_updates == 0

    def test_predict_untrained_returns_exploration(self):
        ctx = LinUCBContext(d=6)
        x = np.ones(6, dtype=np.float64)
        # With A=I, b=0: theta=0, so mean=0, exploration = alpha * sqrt(x.T @ I @ x) = alpha * sqrt(6)
        alpha = 1.0
        p = ctx.predict(x, alpha)
        expected_exploration = alpha * math.sqrt(6.0)
        assert abs(p - expected_exploration) < 0.01

    def test_update_changes_state(self):
        ctx = LinUCBContext(d=6)
        x = np.array([1, 0, 0, 0, 0, 0], dtype=np.float64)
        ctx.update(x, reward=1.0)
        assert ctx.n_updates == 1
        assert ctx.A[0, 0] == 2.0  # I + outer(x,x)
        assert ctx.b[0] == 1.0

    def test_convergence_direction(self):
        """After many updates where feature[0]=1 correlates with reward, theta[0] should be positive."""
        ctx = LinUCBContext(d=6)
        x_good = np.array([1, 0, 0, 0, 0, 0], dtype=np.float64)
        x_bad = np.array([0, 1, 0, 0, 0, 0], dtype=np.float64)
        for _ in range(50):
            ctx.update(x_good, reward=1.0)
            ctx.update(x_bad, reward=-1.0)

        # Agent with high feature[0] should score higher
        p_good = ctx.predict(x_good, alpha=0.0)
        p_bad = ctx.predict(x_bad, alpha=0.0)
        assert p_good > p_bad

    def test_serialization_roundtrip(self):
        ctx = LinUCBContext(d=6)
        x = np.array([0.5, 0.3, 0.1, 0.2, 0.8, 0.4], dtype=np.float64)
        ctx.update(x, reward=0.7)
        ctx.update(x * 2, reward=-0.3)

        state = ctx.to_state("data_read")
        assert state.task_type == "data_read"
        assert state.d == 6
        assert len(state.A_flat) == 36
        assert len(state.b_flat) == 6
        assert state.n_updates == 2

        restored = LinUCBContext.from_state(state)
        np.testing.assert_array_almost_equal(restored.A, ctx.A)
        np.testing.assert_array_almost_equal(restored.b, ctx.b)
        assert restored.n_updates == 2

    def test_serialization_json_roundtrip(self):
        """Verify state survives JSON serialization (as Couchbase would do)."""
        ctx = LinUCBContext(d=6)
        ctx.update(np.ones(6), reward=1.0)

        state = ctx.to_state("plan")
        json_dict = state.model_dump(mode="json")
        restored_state = LinUCBModelState.model_validate(json_dict)
        restored_ctx = LinUCBContext.from_state(restored_state)

        np.testing.assert_array_almost_equal(restored_ctx.A, ctx.A)
        np.testing.assert_array_almost_equal(restored_ctx.b, ctx.b)


# ── features_to_array ────────────────────────────────────────────────────


class TestFeaturesToArray:
    def test_order_and_dtype(self):
        f = BidFeatures(trust=0.8, cost_norm=0.3, load_norm=0.1,
                        failure_penalty=0.2, hist_success=0.9, speed=0.5)
        arr = features_to_array(f)
        assert arr.dtype == np.float64
        assert arr.shape == (6,)
        np.testing.assert_array_equal(arr, [0.8, 0.3, 0.1, 0.2, 0.9, 0.5])

    def test_defaults_are_zeros(self):
        arr = features_to_array(BidFeatures())
        np.testing.assert_array_equal(arr, np.zeros(6))


# ── compute_reward ───────────────────────────────────────────────────────


class TestComputeReward:
    def test_success_base(self):
        r = compute_reward(ExecutionOutcomeBandit(success=True))
        assert r == 1.0

    def test_failure_base(self):
        r = compute_reward(ExecutionOutcomeBandit(success=False))
        assert r == -1.0

    def test_retry_penalty(self):
        r0 = compute_reward(ExecutionOutcomeBandit(success=True, retries=0))
        r1 = compute_reward(ExecutionOutcomeBandit(success=True, retries=1))
        r3 = compute_reward(ExecutionOutcomeBandit(success=True, retries=3))
        # First attempt (retries=0 or 1) has no penalty
        assert r0 == r1
        # retries=3 → penalty = 0.5 * max(0, 3-1) = 1.0
        assert r3 < r0

    def test_latency_penalty(self):
        r_fast = compute_reward(ExecutionOutcomeBandit(success=True, latency_seconds=1.0))
        r_slow = compute_reward(ExecutionOutcomeBandit(success=True, latency_seconds=30.0))
        assert r_fast > r_slow

    def test_latency_penalty_capped(self):
        r1 = compute_reward(ExecutionOutcomeBandit(success=True, latency_seconds=60.0))
        r2 = compute_reward(ExecutionOutcomeBandit(success=True, latency_seconds=600.0))
        # Both should hit the 0.3 cap
        assert abs(r1 - r2) < 0.01

    def test_cost_penalty(self):
        r_cheap = compute_reward(ExecutionOutcomeBandit(success=True, cost_actual=0.5))
        r_expensive = compute_reward(ExecutionOutcomeBandit(success=True, cost_actual=8.0))
        assert r_cheap > r_expensive

    def test_clamping(self):
        # Extreme failure: many retries + slow + expensive + policy blocks
        r = compute_reward(ExecutionOutcomeBandit(
            success=False, retries=10, latency_seconds=600,
            cost_actual=100, policy_blocks=5,
        ))
        assert r >= -2.0

        # Best case
        r_best = compute_reward(ExecutionOutcomeBandit(success=True))
        assert r_best <= 1.5


# ── BanditLearner ────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self):
        self.models: Dict[str, Dict[str, Any]] = {}
        self.decisions: list = []

    def get_bandit_model(self, *, username: str, task_type: str) -> Optional[Dict[str, Any]]:
        return self.models.get(f"{username}::{task_type}")

    def save_bandit_model(self, *, username: str, model_state: LinUCBModelState) -> str:
        key = f"{username}::{model_state.task_type}"
        self.models[key] = model_state.model_dump(mode="json")
        return key

    def save_decision_record(self, *, username, record):
        self.decisions.append(record.model_dump(mode="json"))


class TestBanditLearner:
    def test_disabled_returns_zero(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "0")
        learner = BanditLearner(_FakeStore(), "alice")
        bias = learner.get_bias("plan", BidFeatures(trust=0.8))
        assert bias == 0.0

    def test_disabled_no_explore(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "0")
        learner = BanditLearner(_FakeStore(), "alice")
        assert learner.should_explore() is False

    def test_disabled_no_reward(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "0")
        store = _FakeStore()
        learner = BanditLearner(store, "alice")
        learner.record_reward("plan", BidFeatures(), 1.0)
        assert len(store.models) == 0  # No persistence

    def test_enabled_returns_nonzero_bias(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "1")
        monkeypatch.setenv("CNP_BANDIT_ALPHA", "1.0")
        learner = BanditLearner(_FakeStore(), "alice")
        bias = learner.get_bias("plan", BidFeatures(trust=0.8))
        assert bias != 0.0  # Exploration term should be nonzero

    def test_epsilon_always_explores(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "1")
        monkeypatch.setenv("CNP_BANDIT_EPSILON", "1.0")
        learner = BanditLearner(_FakeStore(), "alice")
        assert all(learner.should_explore() for _ in range(10))

    def test_epsilon_never_explores(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "1")
        monkeypatch.setenv("CNP_BANDIT_EPSILON", "0.0")
        learner = BanditLearner(_FakeStore(), "alice")
        assert not any(learner.should_explore() for _ in range(10))

    def test_record_reward_persists(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "1")
        store = _FakeStore()
        learner = BanditLearner(store, "alice")
        learner.record_reward("data_read", BidFeatures(trust=0.9), 1.0)
        assert "alice::data_read" in store.models
        state = LinUCBModelState.model_validate(store.models["alice::data_read"])
        assert state.n_updates == 1

    def test_loads_persisted_model(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "1")
        monkeypatch.setenv("CNP_BANDIT_ALPHA", "0.0")  # Disable exploration for determinism

        store = _FakeStore()
        # Train a model
        learner1 = BanditLearner(store, "alice")
        for _ in range(20):
            learner1.record_reward("plan", BidFeatures(trust=0.9, cost_norm=0.1), 1.0)

        # Load from store in a new learner
        learner2 = BanditLearner(store, "alice")
        bias = learner2.get_bias("plan", BidFeatures(trust=0.9, cost_norm=0.1))
        # Should be positive because trust correlates with reward
        assert bias > 0

    def test_convergence_across_agents(self, monkeypatch):
        """Agent A always succeeds, Agent B always fails → A should get higher bias."""
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "1")
        monkeypatch.setenv("CNP_BANDIT_ALPHA", "0.0")

        store = _FakeStore()
        learner = BanditLearner(store, "alice")

        feat_a = BidFeatures(trust=0.9, hist_success=0.8)
        feat_b = BidFeatures(trust=0.3, hist_success=0.2)

        for _ in range(100):
            learner.record_reward("data", feat_a, 1.0)
            learner.record_reward("data", feat_b, -1.0)

        bias_a = learner.get_bias("data", feat_a)
        bias_b = learner.get_bias("data", feat_b)
        assert bias_a > bias_b


# ── _bandit_enabled ──────────────────────────────────────────────────────


class TestBanditEnabled:
    def test_default_disabled(self, monkeypatch):
        monkeypatch.delenv("CNP_BANDIT_ENABLE", raising=False)
        assert _bandit_enabled() is False

    def test_enabled(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "1")
        assert _bandit_enabled() is True

    def test_disabled_explicit(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "0")
        assert _bandit_enabled() is False
