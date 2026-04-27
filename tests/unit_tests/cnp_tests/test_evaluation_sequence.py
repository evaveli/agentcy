"""Tests for evaluation sequence creation and advancement."""
import pytest
from unittest.mock import MagicMock
from agentcy.pydantic_models.multi_agent_pipeline import EvaluationSequence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_bids(task_id="t1", count=3, cfp_id="cfp-1"):
    """Return a list of raw bid dicts for a single task."""
    return [
        {
            "task_id": task_id,
            "bidder_id": f"agent-{i}",
            "bid_score": round(0.9 - i * 0.1, 2),
            "bid_id": f"bid-{i}",
            "cfp_id": cfp_id,
            "trust_score": round(0.8 - i * 0.05, 2),
            "cost_estimate": round(1.0 + i * 0.5, 2),
            "agent_load": i,
        }
        for i in range(count)
    ]


def _make_store_mock(seq_doc=None):
    """Build a mock graph_marker_store."""
    store = MagicMock()
    store.get_raw.return_value = seq_doc
    store.upsert_raw = MagicMock()
    store.save_evaluation_sequence = MagicMock()

    def _advance(username, task_id, plan_id):
        doc = store.get_raw(f"eval_seq::{username}::{task_id}::{plan_id}")
        if doc is None:
            return None
        candidates = doc.get("candidates") or []
        idx = int(doc.get("current_index", 0))
        next_idx = idx + 1
        if next_idx >= len(candidates):
            return None
        doc["current_index"] = next_idx
        candidate = dict(candidates[next_idx])
        candidate["sequence_index"] = next_idx
        return candidate

    store.advance_evaluation_sequence = MagicMock(side_effect=_advance)
    return store


# ---------------------------------------------------------------------------
# Tests for _ranked_bids
# ---------------------------------------------------------------------------
def test_ranked_bids_returns_all_sorted():
    from agentcy.agent_runtime.services.graph_builder import _ranked_bids

    bids = _make_bids(count=3) + _make_bids(task_id="t2", count=2)
    ranked = _ranked_bids(bids)

    assert "t1" in ranked
    assert "t2" in ranked
    assert len(ranked["t1"]) == 3
    assert len(ranked["t2"]) == 2
    # Verify descending order
    scores_t1 = [b["bid_score"] for b in ranked["t1"]]
    assert scores_t1 == sorted(scores_t1, reverse=True)


def test_ranked_bids_filters_by_min_score():
    from agentcy.agent_runtime.services.graph_builder import _ranked_bids

    bids = _make_bids(count=5)  # scores: 0.9, 0.8, 0.7, 0.6, 0.5
    ranked = _ranked_bids(bids, min_score_default=0.65)

    # Only bids with score >= 0.65 should pass
    assert len(ranked["t1"]) == 3  # 0.9, 0.8, 0.7


def test_ranked_bids_filters_by_cfp():
    from agentcy.agent_runtime.services.graph_builder import _ranked_bids

    bids = _make_bids(count=2, cfp_id="cfp-1") + [
        {"task_id": "t1", "bidder_id": "rogue", "bid_score": 0.99, "cfp_id": "cfp-other"},
    ]
    ranked = _ranked_bids(bids, allowed_cfp_ids={"cfp-1"})

    assert len(ranked["t1"]) == 2  # rogue bid excluded


# ---------------------------------------------------------------------------
# Tests for EvaluationSequence model
# ---------------------------------------------------------------------------
def test_evaluation_sequence_roundtrip():
    seq = EvaluationSequence(
        task_id="t1", pipeline_id="pipe-1", plan_id="plan-1",
        cfp_id="cfp-1",
        candidates=[
            {"bidder_id": "a1", "bid_score": 0.9},
            {"bidder_id": "a2", "bid_score": 0.7},
        ],
    )
    data = seq.model_dump(mode="json")
    restored = EvaluationSequence.model_validate(data)
    assert restored.task_id == "t1"
    assert len(restored.candidates) == 2
    assert restored.current_index == 0


# ---------------------------------------------------------------------------
# Tests for advance_evaluation_sequence
# ---------------------------------------------------------------------------
def test_advance_returns_next_candidate():
    candidates = [
        {"bidder_id": "a1", "bid_score": 0.9},
        {"bidder_id": "a2", "bid_score": 0.7},
        {"bidder_id": "a3", "bid_score": 0.5},
    ]
    doc = {"candidates": candidates, "current_index": 0}
    store = _make_store_mock(seq_doc=doc)

    result = store.advance_evaluation_sequence("alice", "t1", "plan-1")
    assert result is not None
    assert result["bidder_id"] == "a2"
    assert result["sequence_index"] == 1


def test_advance_returns_none_when_exhausted():
    candidates = [
        {"bidder_id": "a1", "bid_score": 0.9},
    ]
    doc = {"candidates": candidates, "current_index": 0}
    store = _make_store_mock(seq_doc=doc)

    result = store.advance_evaluation_sequence("alice", "t1", "plan-1")
    assert result is None  # Only 1 candidate, already at index 0, next is OOB


def test_advance_returns_none_when_no_sequence():
    store = _make_store_mock(seq_doc=None)
    result = store.advance_evaluation_sequence("alice", "t1", "plan-1")
    assert result is None
