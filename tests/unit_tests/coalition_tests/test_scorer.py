"""Tests for coalition vs solo utility scoring and margin threshold."""
import pytest
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    CoalitionBid,
    CoalitionMember,
    CoalitionRole,
)
from src.agentcy.agent_runtime.services.coalition_scorer import (
    coalition_utility,
    compare_solo_vs_coalition,
)


def _make_bid(**overrides) -> CoalitionBid:
    defaults = dict(
        task_id="t1",
        members=[
            CoalitionMember(agent_id="a1", role=CoalitionRole.PLANNER),
            CoalitionMember(agent_id="a2", role=CoalitionRole.VERIFIER),
        ],
        joint_confidence=0.85,
        expected_latency_ms=8000,
        expected_cost=0.30,
        joint_trust_score=0.80,
    )
    defaults.update(overrides)
    return CoalitionBid(**defaults)


class TestCoalitionUtility:
    def test_returns_in_range(self):
        util = coalition_utility(_make_bid())
        assert 0.0 <= util <= 1.0

    def test_higher_confidence_higher_utility(self):
        low = coalition_utility(_make_bid(joint_confidence=0.5))
        high = coalition_utility(_make_bid(joint_confidence=0.95))
        assert high > low

    def test_higher_trust_higher_utility(self):
        low = coalition_utility(_make_bid(joint_trust_score=0.3))
        high = coalition_utility(_make_bid(joint_trust_score=0.95))
        assert high > low

    def test_higher_cost_lower_utility(self):
        cheap = coalition_utility(_make_bid(expected_cost=0.05))
        expensive = coalition_utility(_make_bid(expected_cost=5.0))
        assert cheap > expensive

    def test_higher_latency_lower_utility(self):
        fast = coalition_utility(_make_bid(expected_latency_ms=1000))
        slow = coalition_utility(_make_bid(expected_latency_ms=25000))
        assert fast > slow

    def test_handoff_friction_penalty(self):
        no_friction = coalition_utility(_make_bid(), signals={})
        high_friction = coalition_utility(_make_bid(), signals={"handoff_friction": 0.8})
        assert no_friction > high_friction

    def test_complementarity_bonus(self):
        no_bonus = coalition_utility(_make_bid(), signals={})
        with_bonus = coalition_utility(_make_bid(), signals={"complementarity_bonus": 0.15})
        assert with_bonus > no_bonus

    def test_verification_bonus(self):
        no_bonus = coalition_utility(_make_bid(), signals={})
        with_bonus = coalition_utility(_make_bid(), signals={"verification_bonus": 0.10})
        assert with_bonus > no_bonus

    def test_zero_values(self):
        bid = CoalitionBid(
            task_id="t1",
            members=[CoalitionMember(agent_id="a1", role=CoalitionRole.PLANNER)],
            joint_confidence=0.0,
            expected_latency_ms=0,
            expected_cost=0.0,
            joint_trust_score=0.0,
        )
        util = coalition_utility(bid)
        assert 0.0 <= util <= 1.0


class TestCompareSoloVsCoalition:
    def test_coalition_wins_above_margin(self):
        assert compare_solo_vs_coalition(0.74, 0.81, margin=0.06) == "coalition"

    def test_solo_wins_below_margin(self):
        assert compare_solo_vs_coalition(0.74, 0.79, margin=0.06) == "solo"

    def test_coalition_wins_at_exact_margin(self):
        assert compare_solo_vs_coalition(0.74, 0.80, margin=0.06) == "coalition"

    def test_solo_wins_equal_scores(self):
        assert compare_solo_vs_coalition(0.80, 0.80, margin=0.06) == "solo"

    def test_coalition_much_better(self):
        assert compare_solo_vs_coalition(0.50, 0.90, margin=0.06) == "coalition"

    def test_zero_margin(self):
        # With zero margin, any higher coalition score wins
        assert compare_solo_vs_coalition(0.80, 0.81, margin=0.0) == "coalition"
        assert compare_solo_vs_coalition(0.80, 0.80, margin=0.0) == "coalition"
        assert compare_solo_vs_coalition(0.80, 0.79, margin=0.0) == "solo"

    def test_large_margin(self):
        # Very strict: coalition must be massively better
        assert compare_solo_vs_coalition(0.70, 0.85, margin=0.20) == "solo"
        assert compare_solo_vs_coalition(0.70, 0.91, margin=0.20) == "coalition"

    def test_default_margin(self, monkeypatch):
        monkeypatch.setenv("CNP_COALITION_MARGIN", "0.10")
        assert compare_solo_vs_coalition(0.74, 0.83) == "solo"
        assert compare_solo_vs_coalition(0.74, 0.85) == "coalition"
