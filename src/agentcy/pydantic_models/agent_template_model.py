"""
Pydantic models for the Agent Template catalog.

An AgentTemplate is a reusable blueprint defining an agent type that can be
instantiated to handle specific workflow steps.  The NL → Pipeline compiler
uses the catalog to match natural-language descriptions to concrete agents.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
from uuid import uuid4

from agentcy.pydantic_models.multi_agent_pipeline import RiskLevel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TemplateCategory(str, Enum):
    DATA_PROCESSING = "data_processing"
    VALIDATION = "validation"
    INTEGRATION = "integration"
    NOTIFICATION = "notification"
    PAYMENT = "payment"
    STORAGE = "storage"
    ANALYTICS = "analytics"
    ML_INFERENCE = "ml_inference"
    SECURITY = "security"
    CUSTOM = "custom"


class TemplateRuntime(str, Enum):
    PYTHON_PLUGIN = "python_plugin"
    CONTAINER = "container"


class ArtifactKind(str, Enum):
    WHEEL = "wheel"
    OCI = "oci"
    ENTRY = "entry"


class TemplateArtifact(BaseModel):
    """Reference to an agent implementation artifact.

    ``kind`` mirrors the discriminator used by the existing
    ``ArtifactRef`` union (``WheelArtifact | OciArtifact | EntryArtifact``).
    ``ref`` carries the full artifact payload matching those schemas.
    """
    kind: ArtifactKind
    ref: Dict[str, Any] = Field(
        default_factory=dict,
        description="Full artifact details matching the existing ArtifactRef schemas.",
    )


class TemplateInputSchema(BaseModel):
    """Declares what data an agent expects and produces."""
    required_fields: List[str] = Field(default_factory=list)
    optional_fields: List[str] = Field(default_factory=list)
    output_fields: List[str] = Field(default_factory=list)


class AgentTemplate(BaseModel):
    """A pre-defined agent building block.

    The NL compiler matches workflow steps to templates using:
    1. Capability overlap (primary signal)
    2. Tag intersection (secondary signal)
    3. Keyword fuzzy match (NL signal)
    4. Category filter (coarse grouping)
    """
    template_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(
        ..., min_length=1, max_length=128,
        description="Machine name, e.g. 'inventory_checker'.",
    )
    display_name: str = Field(
        ..., min_length=1, max_length=256,
        description="Human-readable name.",
    )
    description: str = Field(
        ..., min_length=1,
        description="NL description for LLM matching.",
    )
    category: TemplateCategory = TemplateCategory.CUSTOM

    # Matching dimensions
    capabilities: List[str] = Field(default_factory=list, max_length=50)
    tags: List[str] = Field(default_factory=list, max_length=50)
    keywords: List[str] = Field(
        default_factory=list, max_length=100,
        description="NL keywords for fuzzy matching (e.g. 'stock', 'warehouse').",
    )

    # Runtime specification
    runtime: TemplateRuntime = TemplateRuntime.PYTHON_PLUGIN
    artifact: Optional[TemplateArtifact] = None

    # Service configuration defaults
    service_name_pattern: str = Field(
        ...,
        description="Name pattern for service registration, e.g. 'inventory-checker-{run_id}'.",
    )
    default_action: str = Field("process", description="Maps to Task.action.")
    healthcheck_path: str = "/health"

    # Data contract
    input_schema: TemplateInputSchema = Field(default_factory=TemplateInputSchema)

    # Behavioural metadata — reuses the existing RiskLevel enum
    default_risk_level: RiskLevel = RiskLevel.MEDIUM
    resource_requirements: Dict[str, Any] = Field(
        default_factory=dict,
        description="Resource hints, e.g. {'mem': '512m', 'cpu': '500m'}.",
    )

    # Composability hints
    compatible_predecessors: List[str] = Field(
        default_factory=list,
        description="Template IDs commonly used before this one.",
    )
    compatible_successors: List[str] = Field(
        default_factory=list,
        description="Template IDs commonly used after this one.",
    )

    # Versioning & lifecycle
    version: str = "1.0.0"
    enabled: bool = True
    owner: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("capabilities", "tags", "keywords", mode="before")
    @classmethod
    def strip_whitespace_entries(cls, v: List[str]) -> List[str]:
        """Reject empty / whitespace-only entries."""
        if not isinstance(v, list):
            return v
        cleaned = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        return cleaned
