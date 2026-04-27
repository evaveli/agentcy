from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from agentcy.pydantic_models.pipeline_validation_models.user_define_pipeline_model import (
    PipelineCreate,
)
from agentcy.pydantic_models.service_registration_model import ServiceRegistration


# --------------------------------------------------------------------------- #
#  helpers                                                                     #
# --------------------------------------------------------------------------- #
class SchemaVersion(str, Enum):
    V1 = "1.0"


class Command(BaseModel):
    schema_version: Literal[SchemaVersion.V1] = Field(
        default=SchemaVersion.V1,
        description="Schema version (must always be 1.0)",
    )


# --------------------------------------------------------------------------- #
#  service commands                                                            #
# --------------------------------------------------------------------------- #
class RegisterServiceCommand(Command):
    username: str
    service: ServiceRegistration


class UpdateServiceCommand(Command):
    username: str
    service_id: UUID
    new_service: ServiceRegistration


class DeleteServiceCommand(Command):
    username: str
    service_id: UUID


# --------------------------------------------------------------------------- #
#  pipeline commands                                                           #
# --------------------------------------------------------------------------- #
class RegisterPipelineCommand(Command):
    """
    Command to register a new pipeline.

    Supports two modes:
    1. Direct payload: `pipeline` contains the full PipelineCreate object
    2. Reference mode: `payload_ref` points to stored config, `pipeline` is None

    Reference mode is preferred for large payloads to avoid RabbitMQ message size limits.
    The consumer will fetch the full config from Couchbase using the payload_ref.
    """
    username: str
    pipeline_id: str
    pipeline: Optional[PipelineCreate] = Field(
        default=None,
        description="Full pipeline config (deprecated for large payloads)"
    )
    payload_ref: Optional[str] = Field(
        default=None,
        description="Reference to stored pipeline config (e.g., 'pipeline::{username}::{pipeline_id}')"
    )

    @model_validator(mode="after")
    def check_payload_or_ref(self) -> "RegisterPipelineCommand":
        """Ensure either pipeline or payload_ref is provided."""
        if self.pipeline is None and self.payload_ref is None:
            raise ValueError("Either 'pipeline' or 'payload_ref' must be provided")
        return self


class UpdatePipelineCommand(Command):
    """
    Command to update an existing pipeline.

    Supports same modes as RegisterPipelineCommand.
    """
    username: str
    pipeline_id: str
    pipeline: Optional[PipelineCreate] = Field(
        default=None,
        description="Full pipeline config (deprecated for large payloads)"
    )
    payload_ref: Optional[str] = Field(
        default=None,
        description="Reference to stored pipeline config"
    )

    @model_validator(mode="after")
    def check_payload_or_ref(self) -> "UpdatePipelineCommand":
        """Ensure either pipeline or payload_ref is provided."""
        if self.pipeline is None and self.payload_ref is None:
            raise ValueError("Either 'pipeline' or 'payload_ref' must be provided")
        return self


class DeletePipelineCommand(Command):
    username: str
    pipeline_id: str


class StartPipelineCommand(Command):
    username: str
    pipeline_id: str
    pipeline_run_config_id: str


# --------------------------------------------------------------------------- #
#  plan revision commands                                                       #
# --------------------------------------------------------------------------- #
class RevisePlanCommand(Command):
    """
    Command to revise an existing plan draft.

    Uses payload_ref pattern: the validated candidate revision is stored in
    Couchbase before publishing.  The consumer fetches it by key.
    """
    username: str
    pipeline_id: str
    plan_id: str
    pipeline_run_id: Optional[str] = Field(
        default=None,
        description="Run ID if revision applies to a running pipeline",
    )
    payload_ref: str = Field(
        ...,
        description="Key to stored revision candidate in graph_markers "
                    "(e.g. 'revision_candidate::{username}::{plan_id}::{rev}')",
    )
    suggestion_id: Optional[str] = Field(
        default=None,
        description="Suggestion that originated this revision (if any)",
    )
    created_by: str = Field(default="system")
    reason: str = Field(default="revision")


# --------------------------------------------------------------------------- #
#  CNP lifecycle commands                                                       #
# --------------------------------------------------------------------------- #
class ReassignTaskCommand(Command):
    """Re-assign a failed task to the next candidate in the evaluation sequence.

    Published by the tracker when a contractor fails and a fallback candidate
    is available (paper §3.5 failure re-forwarding).
    """
    username: str
    pipeline_id: str
    pipeline_run_id: str
    plan_id: str
    task_id: str
    failed_agent_id: str
    reason: str = Field(default="task_failure")


class DispatchTaskCommand(Command):
    """Dispatch a task to a different service (cross-service CNP re-forward).

    Used when the forwarder detects that the new agent's service differs from
    the current process and cannot retry inline.
    """
    username: str
    pipeline_id: str
    pipeline_run_id: str
    plan_id: str
    task_id: str
    new_agent_id: str
    new_service: str
    task_input_ref: str = Field(
        description="Ephemeral store key for persisted task input"
    )
    reforward_count: int = Field(default=1)
    reason: str = Field(default="cnp_cross_service_dispatch")


# --------------------------------------------------------------------------- #
#  ethics re-evaluation commands                                                #
# --------------------------------------------------------------------------- #
class ReEvaluatePlanCommand(Command):
    """Request the ethics checker to re-evaluate a plan after agent rework.

    Published when an ethics check fails with action='re_evaluate' and
    the re-evaluation count is below the policy maximum.
    """
    username: str
    pipeline_id: str
    plan_id: str
    pipeline_run_id: Optional[str] = None
    re_evaluation_count: int = Field(1, ge=1)
    reason: str = Field(default="ethics_re_evaluation")
    original_issues: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  events                                                                       #
# --------------------------------------------------------------------------- #
class PipelineRegisteredEvent(BaseModel):
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    config_key: str
    timestamp: datetime


class ServiceRegisteredEvent(BaseModel):
    schema_version: SchemaVersion = Field(SchemaVersion.V1)
    username: str
    service_id: str
    config_key: str
    timestamp: datetime


class PlanRevisedEvent(BaseModel):
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    plan_id: str
    revision: int
    pipeline_run_id: Optional[str] = None
    created_by: str
    reason: str
    timestamp: datetime


class TaskReassignedEvent(BaseModel):
    """Emitted when a failed task is re-assigned to the next candidate
    in the evaluation sequence (paper §3.5 failure re-forwarding)."""
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    pipeline_run_id: str
    plan_id: str
    task_id: str
    failed_agent_id: str
    new_agent_id: str
    new_bid_score: float
    sequence_index: int = Field(description="Position in evaluation sequence")
    timestamp: datetime


class TaskDispatchedEvent(BaseModel):
    """Emitted after a task is successfully (re-)dispatched to a new agent."""
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    pipeline_run_id: str
    task_id: str
    agent_id: str
    service_name: str
    dispatch_type: str = Field(description="'inline' or 'cross_service'")
    reforward_count: int
    timestamp: datetime


# --------------------------------------------------------------------------- #
#  CNP manager commands                                                        #
# --------------------------------------------------------------------------- #
class RunCNPCycleCommand(Command):
    """Trigger a full CNP Announce->Bid->Award cycle for a pipeline.

    Published by the API (or pipeline kickoff logic) when a plan needs
    agent allocation via the improved Contract Net Protocol.
    """
    username: str
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    task_ids: List[str] = Field(
        default_factory=list,
        description="Specific task IDs to run CNP for. Empty = all TaskSpecs.",
    )
    max_rounds: Optional[int] = Field(
        default=None,
        description="Override CNP_MAX_ROUNDS env var for this cycle.",
    )
    bid_timeout_seconds: Optional[int] = Field(
        default=None,
        description="Override CNP_CFP_TTL_SECONDS env var for this cycle.",
    )
    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Idempotency key for this cycle request.",
    )


# --------------------------------------------------------------------------- #
#  CNP manager events                                                          #
# --------------------------------------------------------------------------- #
class CNPCycleStartedEvent(BaseModel):
    """Emitted when a CNP cycle begins."""
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    cycle_id: str
    task_count: int
    max_rounds: int
    timestamp: datetime


class CNPRoundCompletedEvent(BaseModel):
    """Emitted after each bid collection round completes."""
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    cycle_id: str
    round_number: int
    bids_collected: int
    tasks_with_bids: int
    tasks_without_bids: int
    stimulus_level: float
    timestamp: datetime


class CFPBroadcastEvent(BaseModel):
    """Emitted when CFPs are broadcast (hook for future distributed mode)."""
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    cycle_id: str
    round_number: int
    cfp_ids: List[str] = Field(default_factory=list)
    required_capabilities: List[str] = Field(default_factory=list)
    stimulus: float
    closes_at: datetime
    timestamp: datetime


class CNPCycleCompletedEvent(BaseModel):
    """Emitted when a full CNP cycle finishes (all rounds done, awards made)."""
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    pipeline_run_id: Optional[str] = None
    cycle_id: str
    plan_id: str
    total_rounds: int
    total_bids: int
    tasks_awarded: int
    tasks_unawarded: int
    timestamp: datetime


class EthicsReEvaluatedEvent(BaseModel):
    schema_version: SchemaVersion = SchemaVersion.V1
    username: str
    pipeline_id: str
    plan_id: str
    pipeline_run_id: Optional[str] = None
    approved: bool
    re_evaluation_count: int
    timestamp: datetime


# --------------------------------------------------------------------------- #
#  resolve forward refs                                                         #
# --------------------------------------------------------------------------- #
RegisterServiceCommand.model_rebuild()
UpdateServiceCommand.model_rebuild()
DeleteServiceCommand.model_rebuild()
RegisterPipelineCommand.model_rebuild()
UpdatePipelineCommand.model_rebuild()
DeletePipelineCommand.model_rebuild()
StartPipelineCommand.model_rebuild()
RevisePlanCommand.model_rebuild()
ReassignTaskCommand.model_rebuild()
DispatchTaskCommand.model_rebuild()
ReEvaluatePlanCommand.model_rebuild()
RunCNPCycleCommand.model_rebuild()
