"""Main entry point for the Topology Prior Library.

Retrieves the best skeleton, applies mutations, and compiles to PipelineCreate.
Supports performance-weighted retrieval and controlled A/B experiments.
Feature-gated behind ``TOPOLOGY_PRIOR_ENABLE=1``.
"""
from __future__ import annotations

import logging
import os
import random
from typing import Any, Dict, List, Optional

from agentcy.cognitive.topology.compiler import compile_skeleton_to_pipeline
from agentcy.cognitive.topology.mutation import apply_mutations
from agentcy.cognitive.topology.outcome_recorder import build_topology_signature
from agentcy.cognitive.topology.retrieval import retrieve_skeletons
from agentcy.pydantic_models.runtime_policy_models import (
    PolicyState,
    TopologyVariantBias,
    VerificationPolicyMode,
)
from agentcy.pydantic_models.topology_models import (
    BusinessTemplate,
    TopologyPerformance,
    TopologySkeleton,
)

logger = logging.getLogger(__name__)


def topology_prior_enabled() -> bool:
    return os.getenv("TOPOLOGY_PRIOR_ENABLE", "0") == "1"


def _build_pipeline(
    skeleton: TopologySkeleton,
    business_template: BusinessTemplate,
    agent_templates: List[Dict[str, Any]],
    vhost: str,
    retrieval_score: float,
    match_details: Dict[str, float],
    variant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply mutations, compile, and attach metadata."""
    mutated, applied_rules = apply_mutations(skeleton, business_template)
    if applied_rules:
        logger.info("Applied %d mutation rules: %s", len(applied_rules), applied_rules)

    pipeline_create = compile_skeleton_to_pipeline(
        mutated, business_template, agent_templates, vhost=vhost,
    )

    topology_sig = build_topology_signature(skeleton.skeleton_id, applied_rules)

    pipeline_create["_topology_metadata"] = {
        "skeleton_id": skeleton.skeleton_id,
        "skeleton_name": skeleton.name,
        "workflow_class": business_template.workflow_class,
        "retrieval_score": retrieval_score,
        "match_details": match_details,
        "mutations_applied": applied_rules,
        "topology_signature": topology_sig,
        "variant_id": variant_id,
        "business_template": business_template.model_dump(mode="json"),
    }

    return pipeline_create


def generate_pipeline_from_template(
    business_template: BusinessTemplate,
    skeletons: List[TopologySkeleton],
    agent_templates: List[Dict[str, Any]],
    vhost: str = "/",
    min_score: float = 0.3,
    performance_lookup: Optional[Dict[str, TopologyPerformance]] = None,
    policy: Optional[PolicyState] = None,
) -> Optional[Dict[str, Any]]:
    """Generate a PipelineCreate dict from a business template.

    Returns ``None`` if the feature is disabled or no suitable skeleton is found.

    When *performance_lookup* is provided, historical execution data is
    weighted into skeleton ranking.

    When *policy* is provided, the runtime policy state influences variant
    selection and mutation suppression/escalation.

    When ``business_template.experiment_mode`` is True, the orchestrator
    picks the top skeleton but randomly flips one mutation rule to create
    an A/B variant (tagged with ``variant_id`` in metadata).
    """
    if not topology_prior_enabled():
        return None

    policy = policy or PolicyState()

    # Step 1: Apply policy-driven template adjustments
    effective_template = _apply_policy_to_template(business_template, policy)

    # Step 2: Retrieve and rank skeletons (with optional performance weighting)
    candidates = retrieve_skeletons(
        effective_template, skeletons,
        min_score=min_score,
        performance_lookup=performance_lookup,
    )

    # Fallback: if no matches for specific workflow_class, try generic
    if not candidates:
        generic_template = effective_template.model_copy(update={"workflow_class": "generic"})
        candidates = retrieve_skeletons(
            generic_template, skeletons, min_score=0.0,
            performance_lookup=performance_lookup,
        )

    if not candidates:
        logger.warning("No topology skeleton found for workflow_class=%s", business_template.workflow_class)
        return None

    best = candidates[0]
    logger.info(
        "Selected skeleton '%s' (score=%.3f) for workflow_class=%s",
        best.skeleton.name,
        best.score,
        business_template.workflow_class,
    )

    # Step 3: Apply policy-driven mutation adjustments to skeleton
    effective_skeleton = _apply_policy_to_skeleton(best.skeleton, policy)

    # Step 4: Experiment mode — randomly flip one mutation to create a variant
    if effective_template.experiment_mode and effective_skeleton.mutation_rules:
        return _generate_experiment_variant(
            effective_skeleton, effective_template, agent_templates, vhost,
            best.score, best.match_details,
        )

    # Step 5: Standard path — apply all matching mutations
    return _build_pipeline(
        effective_skeleton, effective_template, agent_templates, vhost,
        best.score, best.match_details,
    )


def _apply_policy_to_template(
    template: BusinessTemplate,
    policy: PolicyState,
) -> BusinessTemplate:
    """Adjust business template based on runtime policy."""
    updates: Dict[str, Any] = {}

    # High-safety bias → escalate compliance
    if policy.topology_variant_bias == TopologyVariantBias.HIGH_SAFETY:
        if template.compliance_strictness == "none":
            updates["compliance_strictness"] = "moderate"
        if template.decision_criticality == "low":
            updates["decision_criticality"] = "medium"

    # Stricter verification → escalate compliance
    if policy.verification_mode == VerificationPolicyMode.STRICTER:
        if template.compliance_strictness != "strict":
            updates["compliance_strictness"] = "strict"

    # Minimal verification → relax compliance
    if policy.verification_mode == VerificationPolicyMode.MINIMAL:
        if template.compliance_strictness == "strict":
            updates["compliance_strictness"] = "moderate"

    # Human gate bias: later → suppress human approval requirement
    if policy.human_gate_bias.value == "later" and template.human_approval_required:
        updates["human_approval_required"] = False

    if not updates:
        return template
    return template.model_copy(update=updates)


def _apply_policy_to_skeleton(
    skeleton: TopologySkeleton,
    policy: PolicyState,
) -> TopologySkeleton:
    """Adjust skeleton mutation rules based on runtime policy."""
    if not policy.mutation_suppression and not policy.mutation_escalation:
        return skeleton

    adjusted = skeleton.model_copy(deep=True)

    # Suppress specified rules
    if policy.mutation_suppression:
        suppressed = set(policy.mutation_suppression)
        adjusted.mutation_rules = [
            r for r in adjusted.mutation_rules
            if r.rule_id not in suppressed
        ]

    # Force-apply escalation rules would need to be provided as full MutationRule
    # objects; for v1, escalation is handled by template adjustments instead.

    return adjusted


def _generate_experiment_variant(
    skeleton: TopologySkeleton,
    business_template: BusinessTemplate,
    agent_templates: List[Dict[str, Any]],
    vhost: str,
    retrieval_score: float,
    match_details: Dict[str, float],
) -> Dict[str, Any]:
    """Generate one of two variants randomly for A/B testing.

    Variant A (``baseline``): all matching mutations applied normally.
    Variant B (``experiment``): one randomly selected mutation rule is skipped.

    The chosen variant is tagged in ``_topology_metadata.variant_id``.
    """
    # Find which rules would fire
    from agentcy.cognitive.topology.mutation import evaluate_condition
    active_rules = [
        r for r in skeleton.mutation_rules
        if all(evaluate_condition(c, business_template) for c in r.conditions)
    ]

    if not active_rules or random.random() < 0.5:
        # Variant A: baseline (all mutations)
        logger.info("Experiment: selected baseline variant")
        return _build_pipeline(
            skeleton, business_template, agent_templates, vhost,
            retrieval_score, match_details, variant_id="baseline",
        )
    else:
        # Variant B: skip one random active rule
        skip_rule = random.choice(active_rules)
        logger.info("Experiment: skipping rule '%s' for variant", skip_rule.name)

        # Create a skeleton copy with the skipped rule removed
        variant_skeleton = skeleton.model_copy(deep=True)
        variant_skeleton.mutation_rules = [
            r for r in variant_skeleton.mutation_rules
            if r.rule_id != skip_rule.rule_id
        ]

        return _build_pipeline(
            variant_skeleton, business_template, agent_templates, vhost,
            retrieval_score, match_details,
            variant_id=f"skip_{skip_rule.rule_id}",
        )
