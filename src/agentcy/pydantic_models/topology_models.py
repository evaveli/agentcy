"""
Pydantic models for the Topology Prior Library.

Defines the structured business intake, DAG skeletons with parameterised
steps, conditional mutation rules, and outcome tracking for topology
performance memory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Business Template (structured intake) ────────────────────────────────


class BusinessTemplate(BaseModel):
    """Structured intake form capturing a customer's workflow requirements."""

    template_id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_class: str = Field(
        ...,
        description="Workflow archetype: shipment_exception, order_fulfillment, "
        "carrier_selection, customs_compliance, generic, etc.",
    )
    decision_criticality: Literal["low", "medium", "high"] = "medium"
    compliance_strictness: Literal["none", "moderate", "strict"] = "none"
    human_approval_required: bool = False
    throughput_priority: Literal["latency_optimized", "cost_optimized", "balanced"] = "balanced"
    integration_types: List[str] = Field(
        default_factory=list,
        description="External systems: tms, wms, carrier_api, email, erp, customs_db, etc.",
    )
    volume_per_day: int = Field(0, ge=0)
    industry: Optional[str] = None
    description: Optional[str] = None
    experiment_mode: bool = Field(
        False,
        description="When True, the orchestrator generates 2 variants (baseline + mutation flip) "
        "and assigns them randomly for A/B comparison.",
    )
    created_at: datetime = Field(default_factory=_utcnow)


# ── Skeleton steps ───────────────────────────────────────────────────────


class SkeletonStep(BaseModel):
    """An abstract step within a topology skeleton."""

    step_id: str
    role: str = Field(
        ...,
        description="Abstract role: intake, classify, decide, execute, verify, "
        "notify, approve, integrate, aggregate.",
    )
    name: str
    description: str = ""
    required_capabilities: List[str] = Field(default_factory=list)
    required_tags: List[str] = Field(default_factory=list)
    is_entry: bool = False
    is_final: bool = False
    dependencies: List[str] = Field(
        default_factory=list,
        description="step_ids this step depends on.",
    )
    coordination_mode: Optional[str] = Field(
        None,
        description="If set, overrides TaskSpec coordination_mode for this step. "
        "Values: solo_only, solo_preferred, coalition_allowed, coalition_required.",
    )


# ── Mutation rules ───────────────────────────────────────────────────────


class MutationCondition(BaseModel):
    """A single condition evaluated against a BusinessTemplate."""

    field: str = Field(..., description="Dot-path into BusinessTemplate, e.g. 'compliance_strictness'.")
    operator: Literal["eq", "neq", "in", "gte", "lte"] = "eq"
    value: Any = None


class MutationAction(BaseModel):
    """An action to perform when mutation conditions are met."""

    action_type: Literal["insert_after", "insert_before", "remove", "modify_field"]
    target_step_id: str = Field(..., description="The step to act relative to.")
    step: Optional[SkeletonStep] = Field(
        None, description="The step to insert (for insert_after / insert_before)."
    )
    field_path: Optional[str] = Field(None, description="For modify_field: dotted field path on target step.")
    field_value: Optional[Any] = Field(None, description="For modify_field: new value.")


class MutationRule(BaseModel):
    """A conditional mutation applied to a skeleton based on business template constraints."""

    rule_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    conditions: List[MutationCondition] = Field(default_factory=list)
    actions: List[MutationAction] = Field(default_factory=list)
    priority: int = Field(0, description="Higher priority rules are applied first.")


# ── Topology Skeleton ────────────────────────────────────────────────────


class TopologySkeleton(BaseModel):
    """A canonical DAG pattern that can be parameterised and mutated."""

    skeleton_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    workflow_class: str
    description: str = ""
    steps: List[SkeletonStep] = Field(default_factory=list)
    mutation_rules: List[MutationRule] = Field(default_factory=list)
    control_patterns: List[str] = Field(
        default_factory=list,
        description="Embedded patterns: verification_gate, human_approval, "
        "retry_wrapper, fan_out_fan_in, coalition_eligible, etc.",
    )
    default_error_handling: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    version: str = "1.0.0"
    enabled: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


# ── Topology Outcome (performance memory) ────────────────────────────────


class TopologyOutcome(BaseModel):
    """Links a skeleton to execution results for learning."""

    outcome_id: str = Field(default_factory=lambda: str(uuid4()))
    skeleton_id: str
    pipeline_id: str
    pipeline_run_id: str = ""
    workflow_class: str
    topology_signature: str = Field(
        "", description="Canonical ID: skeleton_id + sorted mutations, e.g. 'skel_v1::rule_compliance+rule_human'.",
    )
    variant_id: Optional[str] = Field(
        None, description="Experiment variant tag for A/B testing.",
    )
    business_template: Dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of the BusinessTemplate used at generation time.",
    )
    mutations_applied: List[str] = Field(
        default_factory=list,
        description="rule_ids that were applied during mutation.",
    )
    success: bool = False
    execution_time_seconds: Optional[float] = None
    task_count: int = 0
    retry_count: int = 0
    coalition_usage_count: int = 0
    policy_violations: int = 0
    human_escalations: int = 0
    cost_total: float = 0.0
    error_summary: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class TopologyPerformance(BaseModel):
    """Aggregated performance summary for a topology variant over a rolling window."""

    topology_signature: str
    skeleton_id: str
    workflow_class: str
    sample_count: int = 0
    success_rate: float = 0.0
    mean_latency_seconds: float = 0.0
    latency_p95_seconds: float = 0.0
    mean_cost: float = 0.0
    retry_rate: float = 0.0
    coalition_usage_rate: float = 0.0
    policy_incident_rate: float = 0.0
    human_escalation_rate: float = 0.0
    fallback_rate: float = 0.0
    last_updated: datetime = Field(default_factory=_utcnow)


# ── Retrieval result ─────────────────────────────────────────────────────


class SkeletonCandidate:
    """Return type from skeleton retrieval: a scored match."""

    __slots__ = ("skeleton", "score", "match_details")

    def __init__(
        self,
        skeleton: TopologySkeleton,
        score: float = 0.0,
        match_details: Optional[Dict[str, float]] = None,
    ) -> None:
        self.skeleton = skeleton
        self.score = max(0.0, min(1.0, score))
        self.match_details = match_details or {}
