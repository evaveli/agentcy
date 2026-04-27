"""API router for end-to-end pipeline generation from business templates.

POST /generate/{username}  — accepts a BusinessTemplate, returns a compiled
PipelineCreate with full traceability (policy decisions, skeleton used,
mutations applied, performance data).

Optionally creates the pipeline directly if ``auto_create=true``.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from agentcy.api_service.dependecies import CommandPublisher, get_publisher, get_rm
from agentcy.cognitive.topology.generation_service import GenerationResult, generate_system
from agentcy.cognitive.topology.orchestrator import topology_prior_enabled
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.runtime_policy_models import HealthSignals
from agentcy.pydantic_models.topology_models import BusinessTemplate
from agentcy.pydantic_models.pipeline_validation_models.user_define_pipeline_model import PipelineCreate
from agentcy.pydantic_models.commands import RegisterPipelineCommand
from agentcy.orchestrator_core.utils import build_pipeline_config

router = APIRouter()
log = logging.getLogger(__name__)


class GenerateRequest(BusinessTemplate):
    """Request body for pipeline generation.

    Extends BusinessTemplate with optional health signals and control flags.
    """
    health_signals: Optional[HealthSignals] = None
    auto_create: bool = False
    vhost: str = "/"

    class Config:
        extra = "allow"


@router.post("/generate/{username}", status_code=status.HTTP_200_OK)
async def generate_pipeline(
    username: str,
    request: GenerateRequest,
    rm: ResourceManager = Depends(get_rm),
    pub: CommandPublisher = Depends(get_publisher),
):
    """Generate a pipeline from a structured business template.

    The platform evaluates current health signals, loads historical topology
    performance, and selects + mutates the best-matching topology skeleton
    to produce a compiled PipelineCreate.

    If ``auto_create`` is True, the pipeline is also persisted and queued
    for registration (same as POST /pipelines/{username}).

    Returns the compiled pipeline, topology metadata, policy decisions,
    and historical performance data for full transparency.
    """
    if not topology_prior_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Topology prior library is not enabled. Set TOPOLOGY_PRIOR_ENABLE=1.",
        )

    # Build BusinessTemplate from request (strip extra fields)
    business_template = BusinessTemplate(
        workflow_class=request.workflow_class,
        decision_criticality=request.decision_criticality,
        compliance_strictness=request.compliance_strictness,
        human_approval_required=request.human_approval_required,
        throughput_priority=request.throughput_priority,
        integration_types=request.integration_types,
        volume_per_day=request.volume_per_day,
        industry=request.industry,
        description=request.description,
        experiment_mode=request.experiment_mode,
    )

    # Get stores from ResourceManager
    graph_store = getattr(rm, "graph_marker_store", None)
    template_store = getattr(rm, "template_store", None)

    # Generate
    result = generate_system(
        business_template=business_template,
        username=username,
        graph_marker_store=graph_store,
        template_store=template_store,
        health_signals=request.health_signals,
        vhost=request.vhost,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.error or "Failed to generate pipeline.",
        )

    response = result.to_dict()

    # Optionally create the pipeline
    if request.auto_create and result.pipeline_create:
        try:
            pipeline_create = PipelineCreate.model_validate(result.pipeline_create)
            pipeline_id = str(uuid.uuid4())

            store = rm.pipeline_store
            if store is None:
                raise HTTPException(500, "Pipeline store is not configured")

            store.insert_stub(username, pipeline_id, pipeline_create)

            if os.getenv("PIPELINE_CREATE_SYNC", "1") != "0":
                full_cfg = build_pipeline_config(dto=pipeline_create, pipeline_id=pipeline_id)
                store.update(username, pipeline_id, full_cfg)

            payload_ref = f"pipeline::{username}::{pipeline_id}"
            cmd = RegisterPipelineCommand(
                username=username,
                pipeline_id=pipeline_id,
                payload_ref=payload_ref,
            )
            await pub.publish("commands.register_pipeline", cmd)

            response["pipeline_id"] = pipeline_id
            response["pipeline_status"] = "created_and_queued"
            log.info("Auto-created pipeline %s from generated topology", pipeline_id)

        except HTTPException:
            raise
        except Exception as exc:
            log.error("Auto-create pipeline failed: %s", exc, exc_info=True)
            response["pipeline_status"] = "generation_only"
            response["auto_create_error"] = str(exc)

    return response


@router.post("/generate/{username}/preview", status_code=status.HTTP_200_OK)
async def preview_pipeline(
    username: str,
    request: GenerateRequest,
    rm: ResourceManager = Depends(get_rm),
):
    """Preview a generated pipeline without creating it.

    Same as /generate but never persists or queues the pipeline.
    Useful for operators to inspect what the platform would generate
    before committing.
    """
    if not topology_prior_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Topology prior library is not enabled.",
        )

    business_template = BusinessTemplate(
        workflow_class=request.workflow_class,
        decision_criticality=request.decision_criticality,
        compliance_strictness=request.compliance_strictness,
        human_approval_required=request.human_approval_required,
        throughput_priority=request.throughput_priority,
        integration_types=request.integration_types,
        volume_per_day=request.volume_per_day,
        industry=request.industry,
        description=request.description,
        experiment_mode=request.experiment_mode,
    )

    graph_store = getattr(rm, "graph_marker_store", None)
    template_store = getattr(rm, "template_store", None)

    result = generate_system(
        business_template=business_template,
        username=username,
        graph_marker_store=graph_store,
        template_store=template_store,
        health_signals=request.health_signals,
        vhost=request.vhost,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result.error or "Failed to generate pipeline.",
        )

    return result.to_dict()
