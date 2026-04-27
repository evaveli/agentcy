# src/agentcy/pydantic_models/scaling_config_model.py

from pydantic import BaseModel, Field, conint
from typing import List, Optional

class ScalingConfig(BaseModel):
    auto_scale: bool = Field(..., description="Indicates if auto-scaling is enabled.")
    min_replicas: conint(ge=1) = Field(..., description="Minimum number of service instances.") # type: ignore
    max_replicas: conint(ge=1) = Field(..., description="Maximum number of service instances.") # type: ignore
    metrics: List[str] = Field(..., description="List of metrics to monitor for scaling decisions.")

    class Config:
        json_schema_extra = {
            "example": {
                "auto_scale": True,
                "min_replicas": 2,
                "max_replicas": 10,
                "metrics": ["CPUUsage", "MemoryUsage"]
            }
        }
