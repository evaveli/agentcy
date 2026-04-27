# src/agentcy/pydantic_models/deployment_config_model.py

from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator, constr
from typing import Optional, List
from typing import Dict
import re

class RequestedResources(BaseModel):
    cpu: str = Field(..., description="CPU allocation (e.g., '500m' for 0.5 CPU).")
    memory: str = Field(..., description="Memory allocation (e.g., '256Mi').")
    @field_validator("cpu")
    def validate_cpu(cls, value):
        if not re.match(r'^\d+m?$', value):
            raise ValueError("CPU must match the format (e.g., '500m' for 0.5 CPU).")
        return value

    @field_validator("memory")
    def validate_memory(cls, value):
        if not re.match(r'^\d+(Ei|Pi|Ti|Gi|Mi|Ki|E|P|T|G|M|K)?$', value):
            raise ValueError("Memory must match the format (e.g., '256Mi').")
        return value
class ResourceLimits(BaseModel):
    cpu: str = Field(..., description="CPU allocation (e.g., '500m' for 0.5 CPU).")
    memory: str = Field(..., description="Memory allocation (e.g., '256Mi').")
    @field_validator("cpu")
    def validate_cpu(cls, value):
        if not re.match(r'^\d+m?$', value):
            raise ValueError("CPU must match the format (e.g., '500m' for 0.5 CPU).")
        return value

    @field_validator("memory")
    def validate_memory(cls, value):
        if not re.match(r'^\d+(Ei|Pi|Ti|Gi|Mi|Ki|E|P|T|G|M|K)?$', value):
            raise ValueError("Memory must match the format (e.g., '256Mi').")
        return value
class ResourceAllocation(BaseModel): 
    requested_resources: RequestedResources = Field(..., description="The requested resources per container")
    max_resources: ResourceLimits = Field(..., description="The max amount of resources you want to allocate per container")

    class Config:
        json_schema_extra = {
            "example": {
                "requested_resources": {
                    "cpu": "500m",
                    "memory": "256Mi"
                },
                "max_resources": {
                    "cpu": "1000m",
                    "memory": "512Mi"
                }
            }
        }


class EnvironmentVariables(BaseModel):
    variables: Dict[str, str] = Field(..., description="Environment variables as key-value pairs.")

    @model_validator(mode='before')
    def check_keys(cls, values):
        variables = values.get('variables', {})
        pattern = re.compile(r'^[A-Z_][A-Z0-9_]*$')
        for key in variables.keys():
            if not pattern.match(key):
                raise ValueError(f"Invalid environment variable name: {key}")
        return values

    class Config:
        json_schema_extra = {
            "example": {
                "variables": {
                    "DATABASE_URL": "postgres://user:password@localhost:5432/dbname",
                    "API_KEY": "12345-ABCDE"
                }
            }
        }


class HealthCheck(BaseModel):
    endpoint: str = Field(..., description="Health check endpoint path (e.g., '/health').")
    port: int = Field(...,gt=0, lt=65536, description="The port where to reach the healthcheck endpoint")
    interval: int = Field(..., description="Interval between health checks (e.g., '10s').")
    initial_delay: int = Field(..., description="Timeout for each health check (e.g., '1s').")
    deregister_after: Optional[int] = Field(None, description="Time after which to deregister the service if health checks fail (e.g., '1m').")

    class Config:
        json_schema_extra = {
            "example": {
                "endpoint": "/health",
                "interval": "10s",
                "timeout": "1s",
                "deregister_after": "1m"
            }
        }




#DEPLOYMENNTSTRATEGYENUM = enums['deploymentStrategyEnum']

class DeploymentStrategyEnum1(str, Enum):
    ROLLING = "Rolling"
    REPLACEMENT = "Replacement"
    BLUE_GREEN = "Blue-Green"

class  DeploymentConfig(BaseModel):
    deployment_strategy: DeploymentStrategyEnum1 = Field(..., description="Strategy to deploy the service.")
    replicas: int = Field(..., ge=1, description="Number of service instances to run.")
    resource_allocation: ResourceAllocation = Field(..., description="CPU and memory resource allocations for the service.")
    environment_variables: Optional[EnvironmentVariables] = Field(None, description="Environment variables to set in the service instances.")
    auto_scale: Optional[bool] = Field(False, description="Indicates if auto-scaling is enabled.")
    min_replicas: Optional[int] = Field(None, ge=1, description="Minimum number of service instances when auto-scaling is enabled.")
    max_replicas: Optional[int] = Field(None, ge=1, description="Maximum number of service instances when auto-scaling is enabled.")
    scaling_metrics: Optional[List[str]] = Field(None, description="List of metrics to monitor for scaling decisions.")
    health_check: Optional[HealthCheck] = Field(None, description="Health check configuration for the service.")

    class Config:
        use_enum_values = True
        json_schema_extra = {
            "example": {
                "deployment_strategy": "rolling",
                "replicas": 3,
                "resource_allocation": {
                    "cpu": "500m",
                    "memory": "256Mi"
                },
                "environment_variables": {
                    "variables": {
                        "DATABASE_URL": "postgres://user:password@localhost:5432/dbname",
                        "API_KEY": "12345-ABCDE"
                    }
                },
                "auto_scale": True,
                "min_replicas": 2,
                "max_replicas": 5,
                "scaling_metrics": ["CPUUsage", "MemoryUsage"],
                "health_check": {
                    "endpoint": "/health",
                    "interval": "10s",
                    "timeout": "1s",
                    "deregister_after": "1m"
                }
            }
        }

    @model_validator(mode="before")
    def check_auto_scale_fields(cls, values):
        auto_scale = values.get('auto_scale', False)
        min_replicas = values.get('min_replicas')
        max_replicas = values.get('max_replicas')

        if auto_scale:
            if min_replicas is None or max_replicas is None:
                raise ValueError("min_replicas and max_replicas must be set when auto_scale is enabled.")
            if min_replicas > max_replicas:
                raise ValueError("min_replicas cannot be greater than max_replicas.")
        return values

