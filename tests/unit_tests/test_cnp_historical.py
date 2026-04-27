"""Tests for historical stats integration in CNP bid scoring."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.agentcy.agent_runtime.services.cnp_utils import score_bid


# ── score_bid backward compatibility ──────────────────────────────────────


def test_score_bid_no_historical():
    """score_bid without historical params returns same result as before."""
    result = score_bid(
        trust=0.8,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
    )
    assert 0.0 <= result <= 1.0


def test_score_bid_historical_none():
    """Passing None for historical params is same as not passing them."""
    base = score_bid(
        trust=0.8,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
    )
    with_none = score_bid(
        trust=0.8,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
        historical_success_rate=None,
        historical_avg_duration=None,
        duration_baseline=None,
    )
    assert base == with_none


# ── Historical success rate boost ──────────────────────────────────────


def test_score_bid_with_success_rate():
    """Higher success rate produces a higher score."""
    base = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
    )
    with_high_sr = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
        historical_success_rate=1.0,
    )
    assert with_high_sr > base


def test_score_bid_zero_success_rate():
    """Zero success rate adds no boost (0 * lambda5 = 0)."""
    base = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
    )
    with_zero = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
        historical_success_rate=0.0,
    )
    assert with_zero == base


# ── Duration speed bonus ──────────────────────────────────────────────


def test_score_bid_with_fast_duration():
    """Agent faster than baseline gets a speed bonus."""
    base = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
    )
    fast_agent = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
        historical_avg_duration=2.0,
        duration_baseline=10.0,
    )
    assert fast_agent > base


def test_score_bid_slow_agent_no_bonus():
    """Agent at or above baseline gets zero speed bonus."""
    base = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
    )
    slow_agent = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
        historical_avg_duration=15.0,
        duration_baseline=10.0,
    )
    assert slow_agent == base


def test_score_bid_duration_no_baseline():
    """Duration without baseline has no effect."""
    base = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
    )
    no_baseline = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
        historical_avg_duration=2.0,
        duration_baseline=None,
    )
    assert no_baseline == base


# ── Combined boost ────────────────────────────────────────────────────


def test_score_bid_combined_boost():
    """Both historical params together produce a higher boost than either alone."""
    with_sr_only = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
        historical_success_rate=0.9,
    )
    with_both = score_bid(
        trust=0.5,
        cost=1.0,
        load=1,
        tmin=0.5,
        tmax=2.0,
        lmin=0,
        lmax=3,
        historical_success_rate=0.9,
        historical_avg_duration=2.0,
        duration_baseline=10.0,
    )
    assert with_both > with_sr_only


# ── _fetch_historical_stats ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_historical_stats_success():
    """Fetches success rate and duration from mocked queries."""
    mock_sr = AsyncMock(return_value=[{"total": "10", "successes": "8"}])
    mock_dur = AsyncMock(return_value=[{"avgDuration": "5.5"}])

    with patch("agentcy.semantic.queries.get_agent_success_rate", mock_sr):
        with patch("agentcy.semantic.queries.get_task_avg_duration", mock_dur):
            from src.agentcy.agent_runtime.services.blueprint_bidder import _fetch_historical_stats
            sr, avg_dur, baseline = await _fetch_historical_stats("agent-1", ["data_read"])

    assert sr == pytest.approx(0.8)
    assert avg_dur == pytest.approx(5.5)
    assert baseline == pytest.approx(5.5)


@pytest.mark.asyncio
async def test_fetch_historical_stats_no_data():
    """Returns None tuple when no execution data exists."""
    mock_sr = AsyncMock(return_value=[{"total": "0", "successes": "0"}])
    mock_dur = AsyncMock(return_value=[{}])

    with patch("agentcy.semantic.queries.get_agent_success_rate", mock_sr):
        with patch("agentcy.semantic.queries.get_task_avg_duration", mock_dur):
            from src.agentcy.agent_runtime.services.blueprint_bidder import _fetch_historical_stats
            sr, avg_dur, baseline = await _fetch_historical_stats("agent-2", ["data_read"])

    assert sr is None
    assert avg_dur is None
    assert baseline is None


@pytest.mark.asyncio
async def test_fetch_historical_stats_exception():
    """Returns None tuple when queries raise."""
    mock_sr = AsyncMock(side_effect=Exception("boom"))

    with patch("agentcy.semantic.queries.get_agent_success_rate", mock_sr):
        from src.agentcy.agent_runtime.services.blueprint_bidder import _fetch_historical_stats
        sr, avg_dur, baseline = await _fetch_historical_stats("agent-3", ["data_read"])

    assert sr is None
    assert avg_dur is None
    assert baseline is None


@pytest.mark.asyncio
async def test_fetch_historical_stats_no_capabilities():
    """Returns None duration when no capabilities provided."""
    mock_sr = AsyncMock(return_value=[{"total": "5", "successes": "4"}])

    with patch("agentcy.semantic.queries.get_agent_success_rate", mock_sr):
        from src.agentcy.agent_runtime.services.blueprint_bidder import _fetch_historical_stats
        sr, avg_dur, baseline = await _fetch_historical_stats("agent-4", [])

    assert sr == pytest.approx(0.8)
    assert avg_dur is None
    assert baseline is None
