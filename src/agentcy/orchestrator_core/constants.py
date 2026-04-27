# src/agentcy/constants.py

from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import (
    PipelineStatus,
)

# TERMINAL states for a run (migrated hot → cold)
TERMINAL: set[PipelineStatus] = {
    PipelineStatus.COMPLETED,
    PipelineStatus.FAILED,
}
