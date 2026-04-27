"""Integration tests: bandit ↔ score_bid, decision records, forwarder loop."""
from __future__ import annotations

import pytest

from src.agentcy.agent_runtime.services.cnp_utils import score_bid
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    BidFeatures,
    CandidateSnapshot,
    DecisionRecord,
    ExecutionOutcomeBandit,
    LinUCBModelState,
)
from src.agentcy.agent_runtime.services.bandit_learner import compute_reward


# ── score_bid with learned_context_bias ──────────────────────────────────


class TestScoreBidBanditBias:
    BID_KWARGS = dict(trust=0.8, cost=1.0, load=0, tmin=1.0, tmax=3.0, lmin=0, lmax=3)

    def test_backward_compatible_none(self):
        base = score_bid(**self.BID_KWARGS)
        with_none = score_bid(**self.BID_KWARGS, learned_context_bias=None)
        assert base == with_none

    def test_positive_bias_boosts(self):
        base = score_bid(**self.BID_KWARGS)
        boosted = score_bid(**self.BID_KWARGS, learned_context_bias=1.0)
        assert boosted > base

    def test_negative_bias_reduces(self):
        base = score_bid(**self.BID_KWARGS)
        reduced = score_bid(**self.BID_KWARGS, learned_context_bias=-1.0)
        assert reduced < base

    def test_zero_bias_no_change(self):
        base = score_bid(**self.BID_KWARGS)
        same = score_bid(**self.BID_KWARGS, learned_context_bias=0.0)
        assert base == same

    def test_still_clamped(self):
        result = score_bid(
            trust=0.0, cost=3.0, load=3,
            tmin=1.0, tmax=3.0, lmin=0, lmax=3,
            learned_context_bias=-10.0,
        )
        assert result >= 0.0
        assert result <= 1.0


# ── DecisionRecord model ────────────────────────────────────────────────


class TestDecisionRecordModel:
    def test_construction(self):
        features = BidFeatures(trust=0.8, cost_norm=0.2)
        snap = CandidateSnapshot(bidder_id="a1", bid_score=0.75, features=features)
        dr = DecisionRecord(
            task_id="t1",
            task_type="data_read",
            candidate_bidders=[snap],
            chosen_bidder_id="a1",
            chosen_features=features,
        )
        assert dr.task_type == "data_read"
        assert len(dr.candidate_bidders) == 1
        assert dr.reward is None
        assert dr.outcome is None

    def test_json_roundtrip(self):
        dr = DecisionRecord(
            task_id="t1",
            task_type="plan",
            chosen_bidder_id="agent-1",
            chosen_features=BidFeatures(trust=0.9),
        )
        d = dr.model_dump(mode="json")
        restored = DecisionRecord.model_validate(d)
        assert restored.task_type == "plan"
        assert restored.chosen_features.trust == 0.9

    def test_with_outcome(self):
        dr = DecisionRecord(
            task_id="t1",
            task_type="plan",
            chosen_bidder_id="a1",
            outcome=ExecutionOutcomeBandit(success=True, latency_seconds=5.2),
            reward=0.91,
        )
        assert dr.outcome.success is True
        assert dr.reward == 0.91


# ── LinUCBModelState model ──────────────────────────────────────────────


class TestLinUCBModelState:
    def test_defaults(self):
        s = LinUCBModelState(task_type="general")
        assert s.d == 6
        assert s.A_flat == []
        assert s.b_flat == []
        assert s.n_updates == 0

    def test_json_roundtrip(self):
        s = LinUCBModelState(
            task_type="data_read",
            A_flat=[float(i) for i in range(36)],
            b_flat=[float(i) for i in range(6)],
            n_updates=42,
        )
        d = s.model_dump(mode="json")
        restored = LinUCBModelState.model_validate(d)
        assert restored.task_type == "data_read"
        assert len(restored.A_flat) == 36
        assert restored.n_updates == 42


# ── Full reward computation scenarios ────────────────────────────────────


class TestRewardScenarios:
    def test_clean_success(self):
        """Task succeeds on first try, fast, cheap."""
        r = compute_reward(ExecutionOutcomeBandit(
            success=True, latency_seconds=2.0, retries=0, cost_actual=0.1,
        ))
        assert r > 0.9

    def test_messy_success(self):
        """Task succeeds but after 2 retries, slow, expensive."""
        r = compute_reward(ExecutionOutcomeBandit(
            success=True, latency_seconds=45.0, retries=3, cost_actual=5.0,
        ))
        # Still positive (success) but significantly reduced
        assert 0.0 > r or r < 0.5

    def test_clean_failure(self):
        """Task fails on first try."""
        r = compute_reward(ExecutionOutcomeBandit(
            success=False, retries=0,
        ))
        assert r == -1.0

    def test_catastrophic_failure(self):
        """Fails after many retries, slow, policy blocks."""
        r = compute_reward(ExecutionOutcomeBandit(
            success=False, retries=5, latency_seconds=120.0,
            cost_actual=8.0, policy_blocks=3,
        ))
        assert r == -2.0  # Clamped to floor


# ── Bandit-disabled backward compatibility ───────────────────────────────


class TestBanditDisabledCompat:
    """When CNP_BANDIT_ENABLE=0, all scoring must be identical to pre-bandit behavior."""

    def test_score_bid_identical(self, monkeypatch):
        monkeypatch.setenv("CNP_BANDIT_ENABLE", "0")
        kwargs = dict(trust=0.5, cost=2.0, load=1, tmin=1.0, tmax=3.0, lmin=0, lmax=3,
                      historical_success_rate=0.8, failure_penalty_score=0.3)
        base = score_bid(**kwargs)
        with_bandit = score_bid(**kwargs, learned_context_bias=None)
        assert base == with_bandit
