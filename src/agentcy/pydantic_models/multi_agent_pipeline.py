"""
Pydantic schemas for the multi-agent planning/execution track described in the
architecture diagram (agent seeding → bidding → plan validation → human
approval → strategist/ethics/system execution → audit/escalation).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CoordinationMode(str, Enum):
    """Controls whether a task can be awarded to a coalition."""
    SOLO_ONLY = "solo_only"
    SOLO_PREFERRED = "solo_preferred"
    COALITION_ALLOWED = "coalition_allowed"
    COALITION_REQUIRED = "coalition_required"


class AgentRegistration(BaseModel):
    agent_id: str
    name: str
    description: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    registered_at: datetime = Field(default_factory=_utcnow)
    registered_by: str = Field(..., description="User or system that registered the agent.")


class TaskIntake(BaseModel):
    task_id: str
    username: str
    description: str
    content_hash: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_human_approval: bool = False
    content_filter_passed: bool = True
    submitted_at: datetime = Field(default_factory=_utcnow)


class TaskSpec(BaseModel):
    task_id: str
    username: str
    description: str
    required_capabilities: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_human_approval: bool = False
    coordination_mode: CoordinationMode = CoordinationMode.SOLO_PREFERRED
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class BlueprintBid(BaseModel):
    task_id: str
    bidder_id: str
    bid_score: float = Field(..., ge=0.0, le=1.0)
    bid_type: str = Field("solo", description="solo, coalition_intent, or coalition")
    rationale: Optional[str] = None
    cfp_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    ttl_seconds: int = Field(300, description="How long this bid remains valid.")
    task_priority: Optional[int] = None
    task_stimulus: Optional[float] = None
    task_reward: Optional[float] = None
    capability_score: Optional[float] = None
    cost_estimate: Optional[float] = None
    agent_load: Optional[int] = None
    response_threshold: Optional[float] = None
    trust_score: Optional[float] = None
    cnp_round: Optional[int] = None
    created_at: datetime = Field(default_factory=_utcnow)


class MarkerStatus(str, Enum):
    RESERVED = "reserved"
    RELEASED = "released"
    COMPLETED = "completed"


class FailureContext(BaseModel):
    """Encodes what went wrong so downstream consumers can penalise similar failures."""
    task_type: str = Field(..., description="Capability / task type that failed.")
    error_category: Optional[str] = Field(
        None, description="Normalised error class (e.g. 'timeout', 'validation', 'runtime')."
    )
    count: int = Field(1, ge=1, description="Consecutive failures in this context.")
    last_error: Optional[str] = Field(None, description="Truncated last error message.")


class AffordanceMarker(BaseModel):
    marker_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    agent_id: str
    capability: Optional[str] = None
    intensity: float = Field(1.0, ge=0.0)
    rationale: Optional[str] = None
    pipeline_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    failure_context: Optional[FailureContext] = Field(
        None, description="Present when this marker records a failure surface signal."
    )
    ttl_seconds: int = Field(300, ge=0)
    created_at: datetime = Field(default_factory=_utcnow)


class ReservationMarker(BaseModel):
    marker_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    agent_id: str
    status: MarkerStatus = MarkerStatus.RESERVED
    ttl_seconds: int = Field(300, ge=0)
    created_at: datetime = Field(default_factory=_utcnow)


class PlanDraft(BaseModel):
    plan_id: str
    username: str
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    revision: int = 1
    graph_spec: Dict[str, Any] = Field(default_factory=dict, description="DAG or graph JSON.")
    is_valid: bool = False
    shacl_report: Optional[Dict[str, Any]] = None
    cached: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class CallForProposal(BaseModel):
    cfp_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    pipeline_id: Optional[str] = None
    required_capabilities: List[str] = Field(default_factory=list)
    status: str = Field("open", description="open or closed")
    round: int = Field(1, ge=1)
    priority: Optional[int] = None
    stimulus: Optional[float] = None
    reward: Optional[float] = None
    min_score: Optional[float] = None
    max_bids: Optional[int] = None
    lmax: Optional[int] = None
    status_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    closes_at: Optional[datetime] = None


class ContractAward(BaseModel):
    award_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    bidder_id: str
    bid_id: Optional[str] = None
    cfp_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    status: str = Field("awarded", description="awarded or released")
    awarded_at: datetime = Field(default_factory=_utcnow)


class HumanApproval(BaseModel):
    plan_id: str
    username: str
    approver: str
    approved: bool
    rationale: Optional[str] = None
    modifications: Optional[Dict[str, Any]] = None
    suggestion_id: Optional[str] = None
    plan_revision: Optional[int] = None
    risk_level: RiskLevel = RiskLevel.MEDIUM
    decided_at: datetime = Field(default_factory=_utcnow)


class PlanRevision(BaseModel):
    plan_id: str
    username: str
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    revision: int = 1
    parent_revision: Optional[int] = None
    graph_spec: Dict[str, Any] = Field(default_factory=dict)
    delta: Optional[Dict[str, Any]] = None
    status: str = Field("APPLIED", description="APPLIED|PENDING_REVIEW|REJECTED")
    created_by: Optional[str] = None
    reason: Optional[str] = None
    validation: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=_utcnow)


class PlanSuggestion(BaseModel):
    suggestion_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    username: str
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    base_revision: int = 1
    candidate_revision: int = 1
    delta: Dict[str, Any] = Field(default_factory=dict)
    graph_spec: Dict[str, Any] = Field(default_factory=dict)
    status: str = Field("PENDING_REVIEW", description="PENDING_REVIEW|APPLIED|REJECTED")
    created_by: Optional[str] = None
    reason: Optional[str] = None
    validation: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=_utcnow)


class EthicsPolicySeverity(str, Enum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


class EthicsRule(BaseModel):
    rule_id: str
    name: str
    description: Optional[str] = None
    severity: EthicsPolicySeverity = EthicsPolicySeverity.BLOCK
    category: str = Field("general", description="pii|destructive|bias|hallucination|safety|sensitive_data|general")
    keywords: List[str] = Field(default_factory=list, description="Keyword triggers for stub mode")
    llm_instruction: Optional[str] = Field(None, description="Natural-language instruction injected into LLM prompt")
    enabled: bool = True
    applies_to_risk_levels: List[RiskLevel] = Field(
        default_factory=lambda: [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EthicsPolicy(BaseModel):
    policy_id: str
    username: str
    name: str = Field("default", description="Policy name")
    description: Optional[str] = None
    rules: List[EthicsRule] = Field(default_factory=list)
    require_human_approval_on_block: bool = False
    max_re_evaluations: int = Field(2, ge=0, le=10)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class PolicyViolation(BaseModel):
    rule_id: str
    rule_name: str
    severity: EthicsPolicySeverity
    category: str
    task_id: Optional[str] = None
    detail: str


class EthicsCheckResult(BaseModel):
    plan_id: str
    reviewer: str = "ethics_check_agent"
    approved: bool
    issues: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    checked_at: datetime = Field(default_factory=_utcnow)
    policy_id: Optional[str] = None
    violations: List[PolicyViolation] = Field(default_factory=list)
    re_evaluation_count: int = 0
    action: str = Field("veto", description="veto|re_evaluate|pass")


class StrategyPlan(BaseModel):
    strategy_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    pipeline_id: Optional[str] = None
    summary: str
    phases: List[Dict[str, Any]] = Field(default_factory=list)
    critical_path: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


class ExecutionOutcome(BaseModel):
    task_id: str
    agent_id: Optional[str] = None
    success: bool = True
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionReport(BaseModel):
    report_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_id: str
    pipeline_run_id: Optional[str] = None
    outcomes: List[ExecutionOutcome] = Field(default_factory=list)
    success_rate: float = 1.0
    created_at: datetime = Field(default_factory=_utcnow)


# ── Bandit learning models ──────────────────────────────────────────────


class BidFeatures(BaseModel):
    """6-dim feature vector snapshot captured at bid scoring time."""
    trust: float = 0.0
    cost_norm: float = 0.0
    load_norm: float = 0.0
    failure_penalty: float = 0.0
    hist_success: float = 0.0
    speed: float = 0.0


class CandidateSnapshot(BaseModel):
    """Snapshot of a candidate bidder at decision time."""
    bidder_id: str
    bid_score: float
    features: BidFeatures


class ExecutionOutcomeBandit(BaseModel):
    """Outcome filled asynchronously after task execution completes."""
    success: bool = False
    latency_seconds: Optional[float] = None
    retries: int = 0
    cost_actual: Optional[float] = None
    policy_blocks: int = 0


class DecisionRecord(BaseModel):
    """Persisted record of every CNP award decision for bandit learning."""
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    task_type: str
    required_capabilities: List[str] = Field(default_factory=list)
    pipeline_id: Optional[str] = None
    cnp_round: int = 1
    candidate_bidders: List[CandidateSnapshot] = Field(default_factory=list)
    chosen_bidder_id: str = ""
    chosen_features: Optional[BidFeatures] = None
    reward: Optional[float] = None
    outcome: Optional[ExecutionOutcomeBandit] = None
    created_at: datetime = Field(default_factory=_utcnow)
    outcome_recorded_at: Optional[datetime] = None


class LinUCBModelState(BaseModel):
    """Serialised state of one LinUCB context (one task_type)."""
    task_type: str
    d: int = Field(6, description="Feature dimension.")
    A_flat: List[float] = Field(default_factory=list, description="d*d flattened A matrix.")
    b_flat: List[float] = Field(default_factory=list, description="d-length b vector.")
    n_updates: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


# ── Coalition CNP models ────────────────────────────────────────────────


class CoalitionRole(str, Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    SAFETY = "safety"


class CoalitionMember(BaseModel):
    """A member of a coalition with an assigned role."""
    agent_id: str
    role: CoalitionRole


class CoalitionBid(BaseModel):
    """A fully formed coalition bid submitted by a bounded team."""
    coalition_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    members: List[CoalitionMember]
    handoff_plan: List[str] = Field(
        default_factory=list,
        description="Ordered execution steps, e.g. ['planner_generate', 'verifier_review'].",
    )
    joint_confidence: float = Field(0.0, ge=0.0, le=1.0)
    expected_latency_ms: int = Field(0, ge=0)
    expected_cost: float = Field(0.0, ge=0.0)
    joint_trust_score: float = Field(0.0, ge=0.0, le=1.0)
    fallback_mode: str = Field(
        "degrade_to_solo",
        description="What to do if coalition fails: degrade_to_solo or fail_fast.",
    )
    fallback_agent_id: Optional[str] = None
    cfp_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class CoalitionContract(BaseModel):
    """Materialised contract for an awarded coalition — source of truth during execution."""
    coalition_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    status: str = Field("awarded", description="awarded, executing, completed, failed, fallback")
    members: List[CoalitionMember] = Field(default_factory=list)
    execution_plan: Dict[str, Any] = Field(
        default_factory=dict,
        description="{'steps': [...], 'max_handoffs': 2}",
    )
    timeouts: Dict[str, Any] = Field(
        default_factory=lambda: {"overall_ms": 15000, "member_step_ms": 7000},
    )
    fallback: Dict[str, Any] = Field(
        default_factory=lambda: {"mode": "degrade_to_solo", "preferred_agent_id": None},
    )
    policy: Dict[str, Any] = Field(
        default_factory=lambda: {"verification_required": False, "human_approval_required": False},
    )
    created_at: datetime = Field(default_factory=_utcnow)


class CoalitionFailureState(str, Enum):
    PARTNER_TIMEOUT = "partner_timeout"
    HANDOFF_VALIDATION_FAILED = "handoff_validation_failed"
    JOINT_POLICY_BLOCKED = "joint_policy_blocked"
    COALITION_ABORTED = "coalition_aborted"
    FALLBACK_TO_SOLO = "fallback_to_solo"
    PARTIAL_COMPLETION_UNUSABLE = "partial_completion_unusable"


class CoalitionOutcome(BaseModel):
    """Records the result of a coalition execution for institutional memory."""
    outcome_id: str = Field(default_factory=lambda: str(uuid4()))
    coalition_id: str
    coalition_signature: str = Field(
        ..., description="Canonical pairing, e.g. 'planner+verifier'.",
    )
    members: List[str] = Field(default_factory=list, description="Agent IDs.")
    task_id: str = ""
    task_signature: str = Field("", description="Task type / capability signature.")
    success: bool = False
    retries: int = 0
    handoff_failures: int = 0
    latency_ms: int = 0
    cost_actual: float = 0.0
    policy_violations: int = 0
    quality_score: float = Field(0.0, ge=0.0, le=1.0)
    failure_state: Optional[CoalitionFailureState] = None
    created_at: datetime = Field(default_factory=_utcnow)


class CoalitionSignal(BaseModel):
    """Stigmergic signal for coalition coordination intelligence."""
    signal_type: str = Field(
        ...,
        description="joint_trust, handoff_friction, or coalition_overhead.",
    )
    coalition_signature: str
    task_signature: str = ""
    score: float = 0.0
    sample_size: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class EscalationNotice(BaseModel):
    pipeline_run_id: str
    reason: str
    severity: RiskLevel = RiskLevel.MEDIUM
    escalated_to: Optional[str] = None
    retries_exhausted: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class EvaluationSequence(BaseModel):
    """Ranked candidates for a task (paper §3.4 evaluation sequence table).

    Stores all scoring bids in descending order so the tracker can fall back
    to the next candidate when the current contractor fails.
    """
    task_id: str
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    plan_id: str
    cfp_id: Optional[str] = None
    candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Ordered by bid_score DESC.  Each entry: "
                    "{bidder_id, bid_score, bid_id, trust_score, cost_estimate, agent_load}",
    )
    current_index: int = Field(
        0, description="Index of the currently-awarded candidate in `candidates`."
    )
    created_at: datetime = Field(default_factory=_utcnow)


class CNPCycleStatus(str, Enum):
    STARTED = "started"
    BIDDING = "bidding"
    AWARDING = "awarding"
    COMPLETED = "completed"
    FAILED = "failed"


class CNPCycleState(BaseModel):
    """Tracks the state of a CNP cycle across rounds.

    Persisted in graph_marker_store for observability and crash recovery.
    """
    cycle_id: str = Field(default_factory=lambda: str(uuid4()))
    username: str
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    status: CNPCycleStatus = CNPCycleStatus.STARTED
    task_ids: List[str] = Field(default_factory=list)
    current_round: int = 0
    max_rounds: int = 3
    bid_timeout_seconds: int = 300
    total_bids: int = 0
    plan_id: Optional[str] = None
    round_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Summary per round: {round, bids_collected, tasks_covered, stimulus}",
    )
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None


class AuditLogEntry(BaseModel):
    event_type: str
    pipeline_run_id: str
    actor: str
    rationale: Optional[str] = None
    provenance: Optional[str] = Field(None, description="Identifier for wasGeneratedBy/who.")
    trace_id: Optional[str] = Field(None, description="OpenTelemetry trace id (hex).")
    span_id: Optional[str] = Field(None, description="OpenTelemetry span id (hex).")
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class ExecutionRecord(BaseModel):
    """A single task execution record for the knowledge graph."""

    execution_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    agent_id: str
    pipeline_run_id: str
    pipeline_id: str = ""
    username: str = ""
    plan_id: str = ""
    status: str = Field("completed", description="'completed' or 'failed'")
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    attempt_number: int = 1
    executed_at: datetime = Field(default_factory=_utcnow)


class DataFlowRecord(BaseModel):
    """A data flow edge between tasks for the knowledge graph."""

    flow_id: str = Field(default_factory=lambda: str(uuid4()))
    from_task: str
    to_task: str
    pipeline_run_id: str
    pipeline_id: str = ""
    username: str = ""
    plan_id: str = ""
    payload_size_bytes: Optional[int] = None
    payload_fields: List[str] = Field(default_factory=list)
    flow_timestamp: datetime = Field(default_factory=_utcnow)
