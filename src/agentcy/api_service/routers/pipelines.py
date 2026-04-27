#src/agentcy/api_service/routers/pipelines.py

import os
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, status,Query

from agentcy.api_service.dependecies import CommandPublisher, get_publisher, get_rm
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.commands import RegisterPipelineCommand, StartPipelineCommand
from agentcy.pydantic_models.pipeline_validation_models.user_define_pipeline_model import PipelineCreate
from agentcy.rabbitmq_workflow.workflow_config_parser import ConfigParser
from agentcy.pydantic_models.pipeline_validation_models.pipeline_model import PipelineConfig
from agentcy.orchestrator_core.utils import build_pipeline_config


router = APIRouter()
log    = logging.getLogger(__name__)

def _validate_final_leafs(dto: PipelineCreate) -> None:
    # Build a “dry” graph (don’t call PipelineGenerator here)
    wiring = ConfigParser(dto.dag.model_dump(), pipeline_id="dryrun").parse_graph()
    task_dict = wiring.get("task_dict", {})

    bad = []
    for t in dto.dag.tasks:
        if t.is_final_task:
            outs = task_dict.get(t.id, {}).get("inferred_outputs") or []
            if outs:
                bad.append({"task_id": t.id, "downstreams": outs})
    if bad:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Tasks marked is_final_task=True must be leaves.",
                "violations": bad,
            },
        )

@router.post("/pipelines/{username}", status_code=status.HTTP_202_ACCEPTED)
async def create_pipeline(
    username: str,
    payload: PipelineCreate,
    rm:  ResourceManager  = Depends(get_rm),
    pub: CommandPublisher = Depends(get_publisher),
):
    _validate_final_leafs(payload)
    pipeline_id = str(uuid.uuid4())
    store = rm.pipeline_store
    if store is None:
        raise HTTPException(500, "Pipeline store is not configured")
    store.insert_stub(username, pipeline_id, payload)

    if os.getenv("PIPELINE_CREATE_SYNC", "1") != "0":
        full_cfg = build_pipeline_config(dto=payload, pipeline_id=pipeline_id)
        store.update(username, pipeline_id, full_cfg)

    # Use payload_ref pattern: send reference instead of full payload
    # The consumer will fetch the full config from Couchbase
    payload_ref = f"pipeline::{username}::{pipeline_id}"
    cmd = RegisterPipelineCommand(username=username, pipeline_id=pipeline_id, payload_ref=payload_ref)
    await pub.publish("commands.register_pipeline", cmd)
    return {"pipeline_id": pipeline_id, "detail": "queued for processing"}

@router.get("/pipelines/{username}/{pipeline_id}")
async def get_pipeline(
    username: str,
    pipeline_id: str,
    rm: ResourceManager = Depends(get_rm),
):  
    store = rm.pipeline_store
    if store is None:
        raise HTTPException(500, "Pipeline store is not configured")
    doc = store.read(username, pipeline_id)
    if doc is None:
        raise HTTPException(404, "Pipeline not found")
    return doc

@router.get("/pipelines/{username}")
async def list_pipelines(username: str, rm: ResourceManager = Depends(get_rm)):
    store = rm.pipeline_store
    if store is None:
        raise HTTPException(500, "Pipeline store is not configured")
    
    return store.list(username)

@router.put("/pipelines/{username}/{pipeline_id}")
async def update_pipeline(
    username: str,
    pipeline_id: str,
    payload: PipelineCreate,
    rm:  ResourceManager  = Depends(get_rm),
    pub: CommandPublisher = Depends(get_publisher),
):
    store = rm.pipeline_store
    if store is None:
        raise HTTPException(500, "Pipeline store is not configured")
    final_cfg = PipelineConfig.model_validate(payload.model_dump())
    store.update(username, pipeline_id, final_cfg)

    # Use payload_ref pattern: send reference instead of full payload
    payload_ref = f"pipeline::{username}::{pipeline_id}"
    cmd = RegisterPipelineCommand(username=username, pipeline_id=pipeline_id, payload_ref=payload_ref)
    await pub.publish("commands.register_pipeline", cmd)
    return {"detail": "updated & queued"}

@router.delete("/pipelines/{username}/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pipeline(
    username: str,
    pipeline_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    store = rm.pipeline_store
    if store is None:
        raise HTTPException(500, "Pipeline store is not configured")
    store.delete(username, pipeline_id)

@router.get("/pipelines/{username}/{pipeline_id}/runs")
async def list_runs(username: str, pipeline_id: str,latest: bool = Query(False, alias="latest_run"), rm: ResourceManager = Depends(get_rm)):
    """
    Return an array of run-ids (latest last) for a given pipeline.
    Implementation can be as naive as you like for now – read directly from
    `rm.pipeline_store.list_runs`.
    """
    store = rm.pipeline_store
    if store is None:
        raise HTTPException(500, "Pipeline store is not configured")
    runs = store.list_runs(username, pipeline_id)
    if latest:
        if runs:                           # <-- guard!
            return {"run_id": runs[-1]}
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No runs for this pipeline yet",
        )         # newest last
    return {"runs": runs}


@router.get("/pipelines/{username}/{pipeline_id}/{run_id}")
async def get_pipeline_run(
    username: str,
    pipeline_id: str,
    run_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    """
    Fetch a single run document.

    • First look in the durable PipelineStore (runs that have already
      finished are persisted there).

    • While the run is still in-flight it lives in EphemeralPipelineStore,
      so we fall back to that if the durable store doesn’t have it yet.
    """
    store = rm.ephemeral_store
    if store is None:
        raise HTTPException(500, "Pipeline store is not configured")
    doc = store.read_run(username, pipeline_id, run_id)
    if doc is not None:
        graph_store = rm.graph_marker_store
        if graph_store is not None and doc.get("paused"):
            pause_context = doc.get("pause_context") or {}
            suggestion_id = pause_context.get("suggestion_id")
            if suggestion_id:
                suggestion = graph_store.get_plan_suggestion(username=username, suggestion_id=suggestion_id)
                if suggestion:
                    doc["pause_suggestion"] = {
                        "suggestion_id": suggestion.get("suggestion_id"),
                        "status": suggestion.get("status"),
                        "reason": suggestion.get("reason"),
                        "base_revision": suggestion.get("base_revision"),
                        "candidate_revision": suggestion.get("candidate_revision"),
                        "delta": suggestion.get("delta"),
                        "validation": suggestion.get("validation"),
                    }
                    if not doc.get("pause_reason"):
                        doc["pause_reason"] = suggestion.get("reason") or "llm_suggestion_pending"
        return doc

    doc = store.read_run(username, pipeline_id, run_id)
    if doc is not None:
        return doc

    raise HTTPException(status_code=404, detail="Run not found")

@router.post("/pipelines/{username}/{pipeline_id}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_pipeline_run(
    username: str,
    pipeline_id: str,
    pub: CommandPublisher = Depends(get_publisher),
):
    """Publish a StartPipelineCommand so the agent-runtime Runner.launch() kicks off execution."""
    cmd = StartPipelineCommand(
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_config_id=pipeline_id,
    )
    await pub.publish("commands.start_pipeline", cmd)
    return {"status": "accepted", "pipeline_id": pipeline_id}

@router.get("/pipelines/{username}/{pipeline_id}/{run_id}/tasks/{task_id}/output")
async def get_task_output(
    username: str,
    pipeline_id: str,
    run_id: str,
    task_id: str,
    rm: ResourceManager = Depends(get_rm),
):
    """Fetch the actual LLM output for a completed task from the ephemeral store."""
    if rm.ephemeral_store is None:
        raise HTTPException(503, "Ephemeral store not available")
    doc = rm.ephemeral_store.read_task_output(username, task_id, run_id)
    if not doc:
        raise HTTPException(404, f"No output found for task {task_id}")
    return doc

@router.get("/schema/pipeline", response_model=dict)
async def get_pipeline_schema():
    return PipelineConfig.model_json_schema()
