"""Tests for topology outcome recording, performance aggregation, and outcome-weighted retrieval."""
import pytest
from typing import Any, Dict, List, Optional

from src.agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    TopologyOutcome,
    TopologyPerformance,
)
from src.agentcy.cognitive.topology.outcome_recorder import (
    aggregate_topology_performance,
    build_topology_signature,
    record_topology_outcome,
)
from src.agentcy.cognitive.topology.retrieval import (
    score_skeleton,
    retrieve_skeletons,
    _performance_score,
)
from src.agentcy.cognitive.topology.orchestrator import generate_pipeline_from_template
from src.agentcy.cognitive.topology.seeds import get_logistics_seeds


# ── Fake store ───────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self):
        self.outcomes: List[Dict[str, Any]] = []

    def save_topology_outcome(self, *, username, outcome):
        self.outcomes.append(outcome.model_dump(mode="json"))

    def list_topology_outcomes(self, *, username, topology_signature=None,
                               skeleton_id=None, limit=None, offset=0):
        items = list(self.outcomes)
        if topology_signature:
            items = [i for i in items if i.get("topology_signature") == topology_signature]
        if skeleton_id:
            items = [i for i in items if i.get("skeleton_id") == skeleton_id]
        if limit:
            items = items[:limit]
        return items, len(items)


# ── build_topology_signature ─────────────────────────────────────────────


class TestBuildTopologySignature:
    def test_no_mutations(self):
        assert build_topology_signature("skel1", []) == "skel1"

    def test_with_mutations(self):
        sig = build_topology_signature("skel1", ["rule_b", "rule_a"])
        assert sig == "skel1::rule_a+rule_b"  # sorted

    def test_single_mutation(self):
        sig = build_topology_signature("skel1", ["rule_x"])
        assert sig == "skel1::rule_x"


# ── record_topology_outcome ─────────────────────────────────────────────


class TestRecordTopologyOutcome:
    def test_records_outcome(self):
        store = _FakeStore()
        metadata = {
            "skeleton_id": "skel1",
            "mutations_applied": ["rule_a"],
            "workflow_class": "shipment_exception",
            "business_template": {"workflow_class": "shipment_exception"},
        }
        oid = record_topology_outcome(
            store=store, username="alice",
            pipeline_id="p1", pipeline_run_id="r1",
            topology_metadata=metadata,
            success=True, execution_time_seconds=12.5,
            task_count=6, retry_count=1,
        )
        assert oid is not None
        assert len(store.outcomes) == 1
        assert store.outcomes[0]["success"] is True
        assert store.outcomes[0]["topology_signature"] == "skel1::rule_a"

    def test_no_metadata_returns_none(self):
        store = _FakeStore()
        assert record_topology_outcome(
            store=store, username="alice",
            pipeline_id="p1", pipeline_run_id="r1",
            topology_metadata={}, success=True,
        ) is None

    def test_no_skeleton_id_returns_none(self):
        store = _FakeStore()
        assert record_topology_outcome(
            store=store, username="alice",
            pipeline_id="p1", pipeline_run_id="r1",
            topology_metadata={"mutations_applied": []},
            success=True,
        ) is None

    def test_variant_id_preserved(self):
        store = _FakeStore()
        metadata = {"skeleton_id": "s1", "mutations_applied": [], "workflow_class": "x"}
        record_topology_outcome(
            store=store, username="alice",
            pipeline_id="p1", pipeline_run_id="r1",
            topology_metadata=metadata, success=True,
            variant_id="baseline",
        )
        assert store.outcomes[0]["variant_id"] == "baseline"


# ── aggregate_topology_performance ───────────────────────────────────────


class TestAggregatePerformance:
    def _seed_outcomes(self, store, sig, n_success, n_fail, latencies=None):
        for i in range(n_success):
            o = TopologyOutcome(
                skeleton_id="skel1", pipeline_id=f"p{i}", workflow_class="test",
                topology_signature=sig, success=True,
                execution_time_seconds=(latencies[i] if latencies else 5.0),
                task_count=6, retry_count=0, cost_total=0.3,
            )
            store.outcomes.append(o.model_dump(mode="json"))
        for i in range(n_fail):
            o = TopologyOutcome(
                skeleton_id="skel1", pipeline_id=f"pf{i}", workflow_class="test",
                topology_signature=sig, success=False,
                execution_time_seconds=10.0,
                task_count=6, retry_count=2, cost_total=0.5,
                policy_violations=1,
            )
            store.outcomes.append(o.model_dump(mode="json"))

    def test_basic_aggregation(self):
        store = _FakeStore()
        self._seed_outcomes(store, "sig_a", n_success=8, n_fail=2)
        perfs = aggregate_topology_performance(store=store, username="alice")
        assert len(perfs) == 1
        p = perfs[0]
        assert p.topology_signature == "sig_a"
        assert p.sample_count == 10
        assert p.success_rate == pytest.approx(0.8)

    def test_multiple_signatures(self):
        store = _FakeStore()
        self._seed_outcomes(store, "sig_a", 9, 1)
        self._seed_outcomes(store, "sig_b", 5, 5)
        perfs = aggregate_topology_performance(store=store, username="alice")
        assert len(perfs) == 2
        # Sorted by success_rate descending
        assert perfs[0].topology_signature == "sig_a"
        assert perfs[1].topology_signature == "sig_b"

    def test_empty_outcomes(self):
        store = _FakeStore()
        perfs = aggregate_topology_performance(store=store, username="alice")
        assert perfs == []

    def test_latency_calculation(self):
        store = _FakeStore()
        self._seed_outcomes(store, "sig_a", 5, 0, latencies=[1.0, 2.0, 3.0, 4.0, 5.0])
        perfs = aggregate_topology_performance(store=store, username="alice")
        assert perfs[0].mean_latency_seconds == pytest.approx(3.0)
        assert perfs[0].latency_p95_seconds >= 4.0

    def test_retry_rate(self):
        store = _FakeStore()
        self._seed_outcomes(store, "sig_a", 5, 5)
        perfs = aggregate_topology_performance(store=store, username="alice")
        # 5 failures have 2 retries each = 10 retries / (10 * 6 tasks) = 0.1667
        assert perfs[0].retry_rate > 0


# ── _performance_score ───────────────────────────────────────────────────


class TestPerformanceScore:
    def test_none_returns_neutral(self):
        assert _performance_score(None) == 0.5

    def test_low_samples_returns_neutral(self):
        p = TopologyPerformance(
            topology_signature="x", skeleton_id="s", workflow_class="w",
            sample_count=2, success_rate=1.0,
        )
        assert _performance_score(p) == 0.5

    def test_high_success_rate(self):
        p = TopologyPerformance(
            topology_signature="x", skeleton_id="s", workflow_class="w",
            sample_count=50, success_rate=0.95,
        )
        score = _performance_score(p)
        assert score > 0.8

    def test_low_success_rate(self):
        p = TopologyPerformance(
            topology_signature="x", skeleton_id="s", workflow_class="w",
            sample_count=50, success_rate=0.4,
        )
        score = _performance_score(p)
        assert score < 0.5

    def test_penalties_reduce_score(self):
        p_clean = TopologyPerformance(
            topology_signature="x", skeleton_id="s", workflow_class="w",
            sample_count=50, success_rate=0.9,
        )
        p_messy = TopologyPerformance(
            topology_signature="x", skeleton_id="s", workflow_class="w",
            sample_count=50, success_rate=0.9,
            retry_rate=0.3, policy_incident_rate=0.2, human_escalation_rate=0.1,
        )
        assert _performance_score(p_clean) > _performance_score(p_messy)

    def test_confidence_discount_low_samples(self):
        p_low = TopologyPerformance(
            topology_signature="x", skeleton_id="s", workflow_class="w",
            sample_count=10, success_rate=1.0,  # Perfect but low confidence
        )
        p_high = TopologyPerformance(
            topology_signature="x", skeleton_id="s", workflow_class="w",
            sample_count=100, success_rate=1.0,
        )
        # Low sample count should pull score toward 0.5
        assert _performance_score(p_low) < _performance_score(p_high)


# ── Outcome-weighted retrieval ───────────────────────────────────────────


class TestOutcomeWeightedRetrieval:
    def test_performance_boosts_ranking(self):
        seeds = get_logistics_seeds()
        bt = BusinessTemplate(workflow_class="shipment_exception")

        # Without performance, shipment_exception skeleton wins
        cands_no_perf = retrieve_skeletons(bt, seeds)
        assert cands_no_perf[0].skeleton.workflow_class == "shipment_exception"

        # With high performance for a different skeleton, it should still lose
        # because workflow_class mismatch dominates (0.35 weight vs 0.20 performance)
        perf_lookup = {
            seeds[1].skeleton_id: TopologyPerformance(
                topology_signature="x", skeleton_id=seeds[1].skeleton_id,
                workflow_class=seeds[1].workflow_class,
                sample_count=100, success_rate=1.0,
            ),
        }
        cands_with_perf = retrieve_skeletons(bt, seeds, performance_lookup=perf_lookup)
        # Shipment exception should still be first (workflow match >> performance)
        assert cands_with_perf[0].skeleton.workflow_class == "shipment_exception"

    def test_performance_breaks_ties(self):
        """Two skeletons with same workflow_class — performance breaks the tie."""
        from src.agentcy.pydantic_models.topology_models import SkeletonStep, TopologySkeleton
        sk_a = TopologySkeleton(
            skeleton_id="sk_a", name="A", workflow_class="test",
            steps=[SkeletonStep(step_id="s1", role="intake", name="In", is_entry=True, is_final=True)],
        )
        sk_b = TopologySkeleton(
            skeleton_id="sk_b", name="B", workflow_class="test",
            steps=[SkeletonStep(step_id="s1", role="intake", name="In", is_entry=True, is_final=True)],
        )
        bt = BusinessTemplate(workflow_class="test")

        # Without perf, they tie
        cands = retrieve_skeletons(bt, [sk_a, sk_b])
        scores = {c.skeleton.skeleton_id: c.score for c in cands}
        assert scores["sk_a"] == scores["sk_b"]

        # With perf favoring B
        perf_lookup = {
            "sk_b": TopologyPerformance(
                topology_signature="sk_b", skeleton_id="sk_b",
                workflow_class="test", sample_count=50, success_rate=0.95,
            ),
        }
        cands_perf = retrieve_skeletons(bt, [sk_a, sk_b], performance_lookup=perf_lookup)
        b_score = next(c.score for c in cands_perf if c.skeleton.skeleton_id == "sk_b")
        a_score = next(c.score for c in cands_perf if c.skeleton.skeleton_id == "sk_a")
        assert b_score > a_score


# ── Experiment mode ──────────────────────────────────────────────────────


class TestExperimentMode:
    def test_experiment_produces_variant_id(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        seeds = get_logistics_seeds()
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="strict",
            experiment_mode=True,
        )
        result = generate_pipeline_from_template(bt, seeds, agent_templates=[])
        assert result is not None
        meta = result["_topology_metadata"]
        assert meta["variant_id"] is not None
        assert meta["variant_id"] in ("baseline",) or meta["variant_id"].startswith("skip_")

    def test_non_experiment_has_no_variant(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        seeds = get_logistics_seeds()
        bt = BusinessTemplate(workflow_class="shipment_exception", experiment_mode=False)
        result = generate_pipeline_from_template(bt, seeds, agent_templates=[])
        assert result is not None
        assert result["_topology_metadata"]["variant_id"] is None

    def test_experiment_variants_differ(self, monkeypatch):
        """Run experiment mode many times — should produce at least 2 distinct variants."""
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        seeds = get_logistics_seeds()
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="strict",
            human_approval_required=True,
            experiment_mode=True,
        )
        variants = set()
        for _ in range(20):
            result = generate_pipeline_from_template(bt, seeds, agent_templates=[])
            if result:
                variants.add(result["_topology_metadata"]["variant_id"])
        assert len(variants) >= 2, f"Expected at least 2 variants, got: {variants}"

    def test_metadata_includes_business_template(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        seeds = get_logistics_seeds()
        bt = BusinessTemplate(workflow_class="shipment_exception")
        result = generate_pipeline_from_template(bt, seeds, agent_templates=[])
        meta = result["_topology_metadata"]
        assert "business_template" in meta
        assert meta["business_template"]["workflow_class"] == "shipment_exception"

    def test_metadata_includes_topology_signature(self, monkeypatch):
        monkeypatch.setenv("TOPOLOGY_PRIOR_ENABLE", "1")
        seeds = get_logistics_seeds()
        bt = BusinessTemplate(
            workflow_class="shipment_exception",
            compliance_strictness="strict",
        )
        result = generate_pipeline_from_template(bt, seeds, agent_templates=[])
        meta = result["_topology_metadata"]
        assert "topology_signature" in meta
        assert meta["skeleton_id"] in meta["topology_signature"]
