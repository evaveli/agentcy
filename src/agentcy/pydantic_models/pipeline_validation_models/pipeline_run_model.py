#src/agentcy/pydantic_models/pipeline_validation_models/pipeline_run_model.py
from enum import Enum
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Set
from pydantic import BaseModel, Field, field_validator, model_validator


def now_utc() -> datetime:
    return datetime.now(timezone.utc)

class PipelineStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    IDLE = "IDLE"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    IDLE = "IDLE"

class EntryMessage(BaseModel):
    """
    Represents the kickoff (entry) message payload:
      {
        "pipeline_id": <str>,
        "username": <str>,
        "pipeline_run_id": <str>
      }
    """
    pipeline_id: str
    username: str
    pipeline_run_id: str
    pipeline_config_id: Optional[str] = Field("default_pipeline_config", description="Optional pipeline configuration ID")

class Metadata(BaseModel):
    region: str = Field(..., description="AWS region where the pipeline is running.")
    environment: str = Field(..., description="Deployment environment (e.g., production, staging).")
    extra_info: str = Field(..., description="Additional arbitrary information.")


class TaskState(BaseModel):
    """
    Represents the state of a single task within a pipeline run.
    """
    status: TaskStatus = Field(..., description="Current status of the task.")
    attempts: int = Field(0, description="Number of times this task has been retried.")
    error: Optional[str] = Field(None, description="Error message if the task failed.")
    result: Optional[str] = Field(None, description="Result of the task if completed.")
    output_ref: Optional[str] = None
    is_final_task: Optional[bool] = Field(False, description="Indicates if this is the final task in the pipeline.")
    started_at: datetime = Field(default_factory=now_utc, description="When this task began.")
    started: datetime = Field(default_factory=now_utc, description="Back-compat alias for started_at")
    last_updated: datetime = Field(default_factory=now_utc, description="Timestamp of the last update to the task.")
    pipeline_run_id: str = Field(..., description="Unique ID for the pipeline run.")
    task_id: str = Field(..., description="Unique ID for the task.")
    username: str = Field(..., description="The user to which the pipeline belongs to.")
    pipeline_config_id: Optional[str] = None    
    pipeline_id: str = Field("", description="ID of the pipeline definition being executed.")
    service_name: str = Field("", description="Name of the service that will execute the task")
    data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("started", "started_at", "last_updated", mode="before")
    @classmethod
    def _coerce_dt(cls, v):
        # Treat empty/placeholder inputs as "now"
        if v in (None, "", 0, {}, []):
            return now_utc()
        if isinstance(v, str):
            try:
                # tolerate ISO strings
                return datetime.fromisoformat(v)
            except Exception:
                return now_utc()
        return v

    @model_validator(mode="after")
    def _sync_times(self):
        # keep started/started_at consistent, and last_updated at least set
        if not self.started_at:
            self.started_at = self.started
        if not self.started:
            self.started = self.started_at
        if not self.last_updated:
            self.last_updated = self.started
        # normalize tz → UTC
        for f in ("started", "started_at", "last_updated"):
            dt = getattr(self, f)
            if dt and dt.tzinfo is None:
                setattr(self, f, dt.replace(tzinfo=timezone.utc))
        return self

class PipelineRun(BaseModel):
    """
    Represents a pipeline run, including overall status, timestamps, and individual task states.
    """
    pipeline_run_id: str = Field(..., description="Unique ID for the pipeline run.")
    pipeline_id: str = Field(..., description="ID of the pipeline definition being executed.")
    status: PipelineStatus = Field(..., description="Overall status of the pipeline run.")
    tasks: Dict[str, TaskState] = Field(..., description="Mapping of task_id to its TaskState.")
    started_at: Optional[datetime]= Field (None, description="Timestamp when the pipeline run started.")
    finished_at: Optional[datetime] = Field(None, description="Timestamp when the pipeline run finished, if any.")
    triggered_by: str = Field(..., description="Name of the user that triggered the pipeline.")
    metadata: Optional[Metadata] = Field(None, description="Arbitrary metadata for the pipeline run.")
    pipeline_config_id: Optional[str] = Field(None, description="ID of the pipeline config used for this run.")
    final_task_ids: Set[str] = Field(default_factory=set,
                                     description="Tasks that must complete for the run to be marked COMPLETED.")
    paused: bool = Field(False, description="Soft pause flag for human-in-the-loop review.")
    pause_reason: Optional[str] = Field(None, description="Reason the run is paused.")
    pause_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    paused_at: Optional[datetime] = Field(None, description="When the run was paused.")
    resumed_at: Optional[datetime] = Field(None, description="When the run was resumed.")
    # Removed 'priority' as it's not present in the desired JSON

    def to_jsonld(self) -> dict:
        """
        Convert the PipelineRun instance into a JSON‑LD document.
        This method produces an envelope that maps core fields to semantic URIs.
        """
        jsonld_context = {
            "dcterms": "http://purl.org/dc/terms/",
            "pipeline_run_id": "dcterms:identifier",
            "pipeline_id": "http://example.org/ontology/pipelineDefinitionID",
            "status": "http://example.org/ontology/pipelineRunStatus",
            "tasks": "http://example.org/ontology/pipelineTasks",
            "started_at": "dcterms:created",
            "finished_at": "http://example.org/ontology/pipelineFinishedAt",
            "triggered_by": "dcterms:creator",
            "metadata": "http://example.org/ontology/pipelineMetadata",
            "region": "http://example.org/ontology/region",
            "environment": "http://example.org/ontology/environment",
            "extra_info": "http://example.org/ontology/extra_info"
        }

        # Create the basic JSON‑LD envelope.
        envelope = {
            "@context": jsonld_context,
            "@type": "PipelineRunStatus"
        }

        core_data = self.model_dump()
        envelope.update(core_data)
        return envelope
