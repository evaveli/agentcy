# src/agentcy/pydantic_models/service_registration_model.py
from pydantic import BaseModel, Field, AnyHttpUrl, field_validator
from typing import List, Optional, Union, Literal
from agentcy.pydantic_models.endpoint_model import Endpoint
from uuid import UUID
from enum import Enum

# --- New: runtime + artifact refs ---
class RuntimeEnum(str, Enum):
    PYTHON_PLUGIN = "python_plugin"
    CONTAINER = "container"

class WheelArtifact(BaseModel):
    kind: Literal["wheel"] = "wheel"
    name: str                      # package name on private PyPI
    version: str                   # exact version
    sha256: str                    # wheel digest
    index_url: AnyHttpUrl          # Nexus PyPI base URL
    entry: str                     # entry under agentcy.logic
    requires_python: str = "~=3.10"
    agentcy_abi: str = "1"

class OciArtifact(BaseModel):
    kind: Literal["oci"] = "oci"
    repo: str                      # e.g., mytasks/formatter
    tag: str                       # e.g., 1.2.3
    digest: str                    # e.g., sha256:abcd...
    ref: str                       # registry/repo:tag@sha256:...
    registry_url: AnyHttpUrl       # https://nexus.example.com

class EntryArtifact(BaseModel):
    kind: Literal["entry"] = "entry"
    entry: str                    # python entrypoint, e.g. module:callable
    requires_python: str = "~=3.10"
    agentcy_abi: str = "1"

ArtifactRef = Union[WheelArtifact, OciArtifact, EntryArtifact]

# --- Existing model, adapted ---
class ServiceRegistration(BaseModel):
    service_id: UUID = Field(..., description="Global unique identifier (UUID).")
    service_name: str = Field(..., description="K8s-friendly name.")
    version: Optional[str] = Field(None, description="Service config/version (not the artifact).")

    # DEPRECATED: keep for backward compat with old callers
    image_tag: Optional[str] = Field(
        None, description="Deprecated. Use `artifact` with kind='oci'."
    )

    description: Optional[str] = None

    # Now optional; will be filled post-deploy/discovery
    base_url: Optional[AnyHttpUrl] = Field(
        None, description="Service URL if known; may be resolved after deployment."
    )

    healthcheck_endpoint: Endpoint = Field(..., description="Healthcheck endpoint definition.")

    # NEW: what we actually run
    runtime: RuntimeEnum = RuntimeEnum.PYTHON_PLUGIN
    artifact: Optional[ArtifactRef] | str = Field(
        None, description="Wheel or OCI reference used by the runtime."
    )

    @field_validator('service_id')
    def validate_service_id(cls, v):
        if v == UUID("00000000-0000-0000-0000-000000000000"):
            raise ValueError("Invalid service_id: cannot be nil UUID.")
        return v

    # allow DNS-1123-ish names plus underscores for agent service parity
    @field_validator('service_name')
    def validate_service_name(cls, v):
        import re
        if not re.fullmatch(r"[a-z0-9]([-_a-z0-9]{1,62})", v):
            raise ValueError("service_name must match: [a-z0-9]([-_a-z0-9]{1,62})")
        return v

    @field_validator('base_url')
    def validate_base_url(cls, v):
        # Optional now; only validate scheme if provided
        if v is not None:
            s = str(v)
            if not (s.startswith("http://") or s.startswith("https://")):
                raise ValueError("base_url must start with http:// or https://")
        return v

    @field_validator('healthcheck_endpoint')
    def validate_health_endpoint(cls, v):
        if not isinstance(v, Endpoint):
            raise ValueError("healthcheck_endpoint must be an Endpoint")
        return v

    class Config:
        json_encoders = {UUID: lambda v: str(v)}
        json_schema_extra = {
            "example": {
                "service_id": "123e4567-e89b-12d3-a456-426614174000",
                "service_name": "authservice",
                "version": "1.0.0",
                "runtime": "python_plugin",
                "artifact": {
                    "kind": "wheel",
                    "name": "warehouse_task_formatter",
                    "version": "1.2.3",
                    "sha256": "5ad5...c1f",
                    "index_url": "https://nexus.example.com/repository/pypi-internal/",
                    "entry": "task_9",
                    "requires_python": "~=3.11",
                    "agentcy_abi": "1"
                },
                "description": "Handles user authentication and authorization.",
                "base_url": None,
                "healthcheck_endpoint": {
                    "name": "health",
                    "path": "/health",
                    "methods": ["GET"],
                    "description": "Health check endpoint.",
                    "parameters": []
                }
            }
        }
