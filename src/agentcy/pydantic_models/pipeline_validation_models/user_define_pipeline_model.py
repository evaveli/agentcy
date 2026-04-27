#src/agentcy/pydantic_models/pipeline_validation_models/user_define_pipeline_model.py
from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Any
from datetime import datetime

# Assuming these are defined as in your original model:
class Task(BaseModel):
    id: str
    name: str
    available_services: str
    action: str
    is_entry: bool = False
    description: Optional[str]
    inputs: Optional[Dict[str, Any]]
    is_final_task: bool = False

class DAGConfig(BaseModel):
    tasks: List[Task]
    @model_validator(mode="after")
    def _must_have_at_least_one_final(self):
        if not any(t.is_final_task for t in self.tasks):
            raise ValueError("At least one task must have is_final_task=True.")
        return self

class RetryPolicy(BaseModel):
    max_retries: int
    backoff_strategy: str

class ErrorHandling(BaseModel):
    retry_policy: RetryPolicy
    on_failure: str

# Create a PipelineCreate model for user input (note: no pipeline_id or timestamps)
class PipelineCreate(BaseModel):
    authors: Optional[List[str]] = []
    vhost: str
    name: str
    description: Optional[str]
    pipeline_name: str
    dag: DAGConfig
    error_handling: ErrorHandling

    class Config:
        # Forbid extra fields so the user can’t sneak in system-controlled values.
        extra = "forbid"
