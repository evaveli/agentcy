"""Tests for the failure surface signal: pheromone → score_bid integration."""
from __future__ import annotations

import math

import pytest

from src.agentcy.agent_runtime.services.cnp_utils import failure_surface_penalty, score_bid
from src.agentcy.agent_runtime.services.pheromone_engine import (
    _build_failure_context,
    _classify_error,
    run,
)
from src.agentcy.pydantic_models.multi_agent_pipeline import (
    AffordanceMarker,
    FailureContext,
    TaskSpec,
)


# ── FailureContext model ─────────────────────────────────────────────────


class TestFailureContextModel:
    def test_basic_construction(self):
        fc = FailureContext(task_type="data_read", error_category="timeout", count=2)
        assert fc.task_type == "data_read"
        assert fc.error_category == "timeout"
        assert fc.count == 2

    def test_defaults(self):
        fc = FailureContext(task_type="general")
        assert fc.error_category is None
        assert fc.count == 1
        assert fc.last_error is None

    def test_embedded_in_marker(self):
        fc = FailureContext(task_type="plan", error_category="runtime", count=1)
        marker = AffordanceMarker(
            task_id="t1", agent_id="a1", failure_context=fc
        )
        assert marker.failure_context is not None
        assert marker.failure_context.task_type == "plan"

    def test_marker_without_failure_context(self):
        marker = AffordanceMarker(task_id="t1", agent_id="a1")
        assert marker.failure_context is None


# ── Error classification ─────────────────────────────────────────────────


class TestClassifyError:
    def test_timeout(self):
        assert _classify_error("Connection timed out after 30s") == "timeout"

    def test_validation(self):
        assert _classify_error("Pydantic validation error for field X") == "validation"

    def test_runtime(self):
        assert _classify_error("RuntimeError: unexpected state") == "runtime"

    def test_connection(self):
        assert _classify_error("Connection refused on port 5672") == "connection"

    def test_resource(self):
        assert _classify_error("OOM killed by kernel") == "resource"

    def test_permission(self):
        assert _classify_error("403 Forbidden") == "permission"

    def test_unknown(self):
        assert _classify_error("something weird happened") == "unknown"

    def test_none(self):
        assert _classify_error(None) == "unknown"

    def test_empty(self):
        assert _classify_error("") == "unknown"


# ── _build_failure_context ───────────────────────────────────────────────


class TestBuildFailureContext:
    def test_returns_none_on_success(self):
        entry = {"task_id": "t1", "agent_id": "a1", "success": True}
        fc = _build_failure_context(entry, task_type="plan", existing_marker=None)
        assert fc is None

    def test_builds_on_failure(self):
        entry = {
            "task_id": "t1",
            "agent_id": "a1",
            "success": False,
            "error": "Connection timed out",
        }
        fc = _build_failure_context(entry, task_type="data_read", existing_marker=None)
        assert fc is not None
        assert fc.task_type == "data_read"
        assert fc.error_category == "timeout"
        assert fc.count == 1
        assert "timed out" in fc.last_error

    def test_increments_count_on_repeated_failure(self):
        existing = AffordanceMarker(
            task_id="t1",
            agent_id="a1",
            failure_context=FailureContext(
                task_type="data_read", error_category="timeout", count=2
            ),
        )
        entry = {
            "task_id": "t1",
            "agent_id": "a1",
            "success": False,
            "error": "timed out again",
        }
        fc = _build_failure_context(entry, task_type="data_read", existing_marker=existing)
        assert fc.count == 3

    def test_resets_count_on_different_error(self):
        existing = AffordanceMarker(
            task_id="t1",
            agent_id="a1",
            failure_context=FailureContext(
                task_type="data_read", error_category="timeout", count=5
            ),
        )
        entry = {
            "task_id": "t1",
            "agent_id": "a1",
            "success": False,
            "error": "permission denied",
        }
        fc = _build_failure_context(entry, task_type="data_read", existing_marker=existing)
        assert fc.error_category == "permission"
        assert fc.count == 1

    def test_truncates_long_error(self):
        entry = {
            "task_id": "t1",
            "agent_id": "a1",
            "success": False,
            "error": "x" * 500,
        }
        fc = _build_failure_context(entry, task_type="general", existing_marker=None)
        assert len(fc.last_error) == 200


# ── failure_surface_penalty ──────────────────────────────────────────────


class TestFailureSurfacePenalty:
    def test_no_markers_returns_zero(self):
        assert failure_surface_penalty([]) == 0.0

    def test_single_failure_mild_penalty(self):
        markers = [
            {"failure_context": {"task_type": "plan", "error_category": "timeout", "count": 1}}
        ]
        penalty = failure_surface_penalty(markers, max_penalty=0.8, decay_per_count=0.35)
        # 0.8 * (1 - e^(-0.35)) ≈ 0.224
        expected = 0.8 * (1.0 - math.exp(-0.35))
        assert penalty == pytest.approx(expected, abs=0.01)

    def test_repeated_failures_strong_penalty(self):
        markers = [
            {"failure_context": {"task_type": "plan", "error_category": "timeout", "count": 5}}
        ]
        penalty = failure_surface_penalty(markers, max_penalty=0.8, decay_per_count=0.35)
        # 0.8 * (1 - e^(-1.75)) ≈ 0.66
        expected = 0.8 * (1.0 - math.exp(-0.35 * 5))
        assert penalty == pytest.approx(expected, abs=0.01)

    def test_multiple_contexts_breadth_boost(self):
        markers = [
            {"failure_context": {"task_type": "plan", "error_category": "timeout", "count": 1}},
            {"failure_context": {"task_type": "plan", "error_category": "runtime", "count": 1}},
        ]
        single = failure_surface_penalty(
            [markers[0]], max_penalty=0.8, decay_per_count=0.35
        )
        both = failure_surface_penalty(
            markers, max_penalty=0.8, decay_per_count=0.35
        )
        assert both > single

    def test_penalty_capped_at_max(self):
        markers = [
            {"failure_context": {"task_type": "plan", "error_category": "timeout", "count": 100}}
        ]
        penalty = failure_surface_penalty(markers, max_penalty=0.8, decay_per_count=0.35)
        assert penalty <= 0.8

    def test_markers_without_failure_context_ignored(self):
        markers = [
            {"intensity": 0.5},
            {"failure_context": None},
        ]
        assert failure_surface_penalty(markers) == 0.0


# ── score_bid with failure_penalty_score ─────────────────────────────────


class TestScoreBidFailurePenalty:
    BID_KWARGS = dict(trust=0.8, cost=1.0, load=0, tmin=1.0, tmax=3.0, lmin=0, lmax=3)

    def test_backward_compatible_no_penalty(self):
        """score_bid without failure_penalty_score behaves identically."""
        base = score_bid(**self.BID_KWARGS)
        with_none = score_bid(**self.BID_KWARGS, failure_penalty_score=None)
        with_zero = score_bid(**self.BID_KWARGS, failure_penalty_score=0.0)
        assert base == with_none
        assert base == with_zero

    def test_penalty_reduces_score(self):
        base = score_bid(**self.BID_KWARGS)
        penalised = score_bid(**self.BID_KWARGS, failure_penalty_score=0.5)
        assert penalised < base

    def test_high_penalty_significantly_reduces_score(self):
        base = score_bid(**self.BID_KWARGS)
        heavy = score_bid(**self.BID_KWARGS, failure_penalty_score=0.8)
        assert (base - heavy) > 0.05

    def test_score_still_clamped(self):
        result = score_bid(
            trust=0.0, cost=3.0, load=3,
            tmin=1.0, tmax=3.0, lmin=0, lmax=3,
            failure_penalty_score=1.0,
        )
        assert result >= 0.0
        assert result <= 1.0


# ── Pheromone engine integration ─────────────────────────────────────────


class _FakeStore:
    def __init__(self):
        self.markers = []
        self.specs = []

    def list_task_specs(self, *, username):
        return list(self.specs), len(self.specs)

    def list_affordance_markers(self, *, username, task_id=None, agent_id=None):
        items = list(self.markers)
        if task_id:
            items = [i for i in items if i.get("task_id") == task_id]
        if agent_id:
            items = [i for i in items if i.get("agent_id") == agent_id]
        return items, len(items)

    def add_affordance_marker(self, *, username, marker, ttl_seconds=None):
        doc = marker.model_dump(mode="json")
        for idx, existing in enumerate(self.markers):
            if existing.get("marker_id") == marker.marker_id:
                self.markers[idx] = doc
                return marker.marker_id
        self.markers.append(doc)
        return marker.marker_id


class _FakeRM:
    def __init__(self, store):
        self.graph_marker_store = store


class TestPheromoneEngineFailureContext:
    @pytest.mark.asyncio
    async def test_failure_writes_failure_context(self, monkeypatch):
        store = _FakeStore()
        store.specs.append(
            TaskSpec(
                task_id="task-1",
                username="alice",
                description="do stuff",
                required_capabilities=["data_read"],
            ).model_dump(mode="json")
        )
        rm = _FakeRM(store)
        monkeypatch.setenv("PHEROMONE_FAILURE_PENALTY", "0.3")

        payload = {
            "feedback": [
                {
                    "task_id": "task-1",
                    "agent_id": "agent-1",
                    "success": False,
                    "error": "Connection timed out",
                }
            ]
        }
        result = await run(
            rm, "run-1", "pheromone_engine", None,
            {"username": "alice", "data": payload},
        )

        assert result["mode"] == "feedback"
        assert len(store.markers) == 1
        marker = store.markers[0]
        fc = marker.get("failure_context")
        assert fc is not None
        assert fc["task_type"] == "data_read"
        assert fc["error_category"] == "timeout"
        assert fc["count"] == 1

    @pytest.mark.asyncio
    async def test_success_clears_failure_context(self, monkeypatch):
        store = _FakeStore()
        store.specs.append(
            TaskSpec(
                task_id="task-2",
                username="alice",
                description="retry",
                required_capabilities=["plan"],
            ).model_dump(mode="json")
        )
        # Pre-existing marker with failure_context
        existing = AffordanceMarker(
            task_id="task-2",
            agent_id="agent-2",
            intensity=0.3,
            failure_context=FailureContext(
                task_type="plan", error_category="timeout", count=2
            ),
        )
        store.markers.append(existing.model_dump(mode="json"))
        rm = _FakeRM(store)
        monkeypatch.setenv("PHEROMONE_SUCCESS_BONUS", "0.2")

        payload = {
            "feedback": [
                {"task_id": "task-2", "agent_id": "agent-2", "success": True}
            ]
        }
        await run(
            rm, "run-2", "pheromone_engine", None,
            {"username": "alice", "data": payload},
        )

        marker = store.markers[0]
        assert marker.get("failure_context") is None
        assert marker["intensity"] > 0.3
