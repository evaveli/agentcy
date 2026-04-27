"""Tests for AgentTemplate model validation."""
import pytest
from pydantic import ValidationError

from src.agentcy.pydantic_models.agent_template_model import (
    AgentTemplate,
    ArtifactKind,
    TemplateArtifact,
    TemplateCategory,
    TemplateRuntime,
)
from src.agentcy.pydantic_models.multi_agent_pipeline import RiskLevel


def _valid_kwargs(**overrides):
    base = dict(
        name="test_agent",
        display_name="Test Agent",
        description="A test agent for unit tests",
        service_name_pattern="test-agent-{run_id}",
    )
    base.update(overrides)
    return base


def test_minimal_valid_template():
    tmpl = AgentTemplate(**_valid_kwargs())
    assert tmpl.name == "test_agent"
    assert tmpl.category == TemplateCategory.CUSTOM
    assert tmpl.default_risk_level == RiskLevel.MEDIUM
    assert tmpl.enabled is True
    assert tmpl.template_id  # auto-generated UUID


def test_risk_level_uses_enum():
    tmpl = AgentTemplate(**_valid_kwargs(default_risk_level="high"))
    assert tmpl.default_risk_level == RiskLevel.HIGH


def test_invalid_risk_level_rejected():
    with pytest.raises(ValidationError):
        AgentTemplate(**_valid_kwargs(default_risk_level="extreme"))


def test_artifact_kind_enum():
    art = TemplateArtifact(kind="wheel", ref={"path": "agent-1.0.whl"})
    assert art.kind == ArtifactKind.WHEEL


def test_invalid_artifact_kind_rejected():
    with pytest.raises(ValidationError):
        TemplateArtifact(kind="zip", ref={})


def test_empty_name_rejected():
    with pytest.raises(ValidationError):
        AgentTemplate(**_valid_kwargs(name=""))


def test_name_too_long_rejected():
    with pytest.raises(ValidationError):
        AgentTemplate(**_valid_kwargs(name="x" * 200))


def test_whitespace_capabilities_stripped():
    tmpl = AgentTemplate(**_valid_kwargs(capabilities=["  data_read  ", "", "  ", "transform"]))
    assert tmpl.capabilities == ["data_read", "transform"]


def test_whitespace_tags_stripped():
    tmpl = AgentTemplate(**_valid_kwargs(tags=["  etl  ", "", "batch"]))
    assert tmpl.tags == ["etl", "batch"]


def test_whitespace_keywords_stripped():
    tmpl = AgentTemplate(**_valid_kwargs(keywords=["stock", "  ", "warehouse  "]))
    assert tmpl.keywords == ["stock", "warehouse"]


def test_category_enum():
    tmpl = AgentTemplate(**_valid_kwargs(category="payment"))
    assert tmpl.category == TemplateCategory.PAYMENT


def test_invalid_category_rejected():
    with pytest.raises(ValidationError):
        AgentTemplate(**_valid_kwargs(category="blockchain"))


def test_runtime_enum():
    tmpl = AgentTemplate(**_valid_kwargs(runtime="container"))
    assert tmpl.runtime == TemplateRuntime.CONTAINER


def test_template_id_auto_generated():
    t1 = AgentTemplate(**_valid_kwargs())
    t2 = AgentTemplate(**_valid_kwargs())
    assert t1.template_id != t2.template_id


def test_serialization_roundtrip():
    tmpl = AgentTemplate(
        **_valid_kwargs(
            capabilities=["data_read"],
            tags=["etl"],
            default_risk_level="high",
            category="analytics",
            artifact={"kind": "oci", "ref": {"image": "agent:latest"}},
        )
    )
    data = tmpl.model_dump(mode="json")
    restored = AgentTemplate(**data)
    assert restored.name == tmpl.name
    assert restored.default_risk_level == RiskLevel.HIGH
    assert restored.artifact.kind == ArtifactKind.OCI
