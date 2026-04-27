"""End-to-end pipeline generation service.

Wires all layers: health signals → policy engine → topology retrieval
(with performance weighting) → mutation → compilation → pipeline creation.

This is the core product entry point: customer provides a BusinessTemplate,
the platform returns a ready-to-run PipelineCreate.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from agentcy.cognitive.topology.orchestrator import (
    generate_pipeline_from_template,
    topology_prior_enabled,
)
from agentcy.cognitive.topology.outcome_recorder import (
    aggregate_topology_performance,
    build_topology_signature,
)
from agentcy.cognitive.topology.seeds import get_logistics_seeds
from agentcy.agent_runtime.services.runtime_policy import (
    RuntimePolicyEngine,
    runtime_policy_enabled,
)
from agentcy.pydantic_models.runtime_policy_models import HealthSignals, PolicyState
from agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    TopologyPerformance,
    TopologySkeleton,
)

logger = logging.getLogger(__name__)


class GenerationResult:
    """Result of pipeline generation with full traceability."""

    __slots__ = (
        "pipeline_create", "policy_state", "topology_metadata",
        "performance_used", "error",
    )

    def __init__(
        self,
        pipeline_create: Optional[Dict[str, Any]] = None,
        policy_state: Optional[PolicyState] = None,
        topology_metadata: Optional[Dict[str, Any]] = None,
        performance_used: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ):
        self.pipeline_create = pipeline_create
        self.policy_state = policy_state
        self.topology_metadata = topology_metadata
        self.performance_used = performance_used
        self.error = error

    @property
    def success(self) -> bool:
        return self.pipeline_create is not None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"success": self.success}
        if self.pipeline_create:
            result["pipeline_create"] = {
                k: v for k, v in self.pipeline_create.items()
                if k != "_topology_metadata"
            }
        if self.topology_metadata:
            result["topology_metadata"] = self.topology_metadata
        if self.policy_state:
            result["policy"] = {
                "coalition_mode": self.policy_state.coalition_mode.value,
                "verification_mode": self.policy_state.verification_mode.value,
                "topology_variant_bias": self.policy_state.topology_variant_bias.value,
                "triggered_rules": self.policy_state.triggered_rules,
            }
        if self.performance_used:
            result["performance"] = self.performance_used
        if self.error:
            result["error"] = self.error
        return result


def generate_system(
    *,
    business_template: BusinessTemplate,
    username: str,
    graph_marker_store: Optional[Any] = None,
    template_store: Optional[Any] = None,
    health_signals: Optional[HealthSignals] = None,
    vhost: str = "/",
    include_seeds: bool = True,
) -> GenerationResult:
    """Generate a pipeline from a business template, wiring all intelligence layers.

    This is the main product entry point. It:

    1. Evaluates runtime policy from current health signals
    2. Loads topology performance data for outcome-weighted retrieval
    3. Loads available skeletons (seeds + stored)
    4. Loads agent templates for service matching
    5. Calls the topology orchestrator with all context
    6. Returns a fully compiled PipelineCreate with traceability metadata

    Parameters
    ----------
    business_template:
        Structured intake form from the customer.
    username:
        Tenant identifier.
    graph_marker_store:
        Optional Couchbase store for topology outcomes + policy logs.
    template_store:
        Optional template store for agent templates.
    health_signals:
        Optional current system health. If None, uses healthy defaults.
    vhost:
        RabbitMQ vhost for the generated pipeline.
    include_seeds:
        If True, includes built-in logistics seed skeletons.
    """
    # Step 1: Evaluate runtime policy
    policy = PolicyState()
    if runtime_policy_enabled() and health_signals is not None:
        try:
            engine = RuntimePolicyEngine(store=graph_marker_store)
            policy = engine.evaluate(health_signals, username=username)
        except Exception:
            logger.debug("Policy engine evaluation failed, using defaults", exc_info=True)

    # Step 2: Load topology performance data
    performance_lookup: Dict[str, TopologyPerformance] = {}
    if graph_marker_store is not None and hasattr(graph_marker_store, "list_topology_outcomes"):
        try:
            perfs = aggregate_topology_performance(
                store=graph_marker_store,
                username=username,
            )
            for p in perfs:
                performance_lookup[p.skeleton_id] = p
        except Exception:
            logger.debug("Failed to load topology performance data", exc_info=True)

    # Step 3: Load available skeletons
    skeletons: List[TopologySkeleton] = []
    if include_seeds:
        skeletons.extend(get_logistics_seeds())

    # Load user-stored skeletons if store available
    if graph_marker_store is not None and hasattr(graph_marker_store, "list_topology_outcomes"):
        # Future: load custom skeletons from a skeleton store
        pass

    if not skeletons:
        return GenerationResult(error="No topology skeletons available.")

    # Step 4: Load agent templates
    agent_templates: List[Dict[str, Any]] = []
    if template_store is not None and hasattr(template_store, "list"):
        try:
            agent_templates = template_store.list(username=username, enabled=True)
        except Exception:
            logger.debug("Failed to load agent templates", exc_info=True)

    # Step 5: Generate pipeline via orchestrator
    pipeline_create = generate_pipeline_from_template(
        business_template=business_template,
        skeletons=skeletons,
        agent_templates=agent_templates,
        vhost=vhost,
        performance_lookup=performance_lookup or None,
        policy=policy,
    )

    if pipeline_create is None:
        return GenerationResult(
            policy_state=policy,
            error=f"No suitable topology found for workflow_class='{business_template.workflow_class}'.",
        )

    # Extract metadata
    topology_metadata = pipeline_create.pop("_topology_metadata", {})

    # Build performance summary for response
    perf_summary = None
    if topology_metadata.get("skeleton_id") in performance_lookup:
        p = performance_lookup[topology_metadata["skeleton_id"]]
        perf_summary = {
            "sample_count": p.sample_count,
            "success_rate": p.success_rate,
            "mean_latency_seconds": p.mean_latency_seconds,
            "mean_cost": p.mean_cost,
        }

    return GenerationResult(
        pipeline_create=pipeline_create,
        policy_state=policy,
        topology_metadata=topology_metadata,
        performance_used=perf_summary,
    )
