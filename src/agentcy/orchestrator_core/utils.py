# src/agentcy/orchestrator_core/utils.py
from datetime import datetime, timezone
import logging
from math import pi
from typing import Optional

from pydantic import BaseModel
from aio_pika import Channel, ExchangeType

from agentcy.pydantic_models.pipeline_validation_models.pipeline_model import DAGConfig, ErrorHandling, PipelineConfig
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import PipelineRun, PipelineStatus, TaskState, TaskStatus

logger = logging.getLogger(__name__)

def _coerce(model_cls, obj):
    if isinstance(obj, model_cls):
        return obj
    if isinstance(obj, BaseModel):
        obj = obj.model_dump()          # neutralize cross-module class issues
    return model_cls.model_validate(obj)




def build_pipeline_config(
    dto,  # PipelineCreate
    pipeline_id: str,
) -> PipelineConfig:
    
    """
    Produces a fully-populated PipelineConfig (typed).
    RabbitMQ wiring/final dict is generated later by the store.
    """
    now = datetime.now(timezone.utc)

    # Ensure nested models are coerced correctly (dict -> model is fine)
    dag = _coerce(DAGConfig, dto.dag)
    err = _coerce(ErrorHandling, dto.error_handling)

    full = PipelineConfig(
        pipeline_id   = pipeline_id,
        authors       = list(dto.authors or []),
        vhost         = dto.vhost,
        name          = dto.name,
        description   = dto.description,
        pipeline_name = dto.pipeline_name,
        dag           = dag,
        error_handling= err,
        created_at    = now,
        updated_at    = now,
        last_updated  = now,
        version       = 1,
        final_task_ids=[],  # keep it JSON-safe
    )

    # (Optional) log a flattened view for debugging
    logger.info("Built PipelineConfig(pipeline_id=%s, name=%s)", full.pipeline_id, full.name)
    return full





def seed_initial_run(
    *, username: str, pipeline_id: str, run_id: str, cfg: dict, pipeline_config_id: Optional[str] = None
) -> PipelineRun:
    """
    Build a fully-populated PipelineRun from the final pipeline_config
    (the one that already contains `task_dict`).
    """
    tasks_dict = {}

    for tid, tmeta in cfg["task_dict"].items():
        is_final = bool(tmeta.get("is_final_task"))
        tasks_dict[tid] = TaskState(
            status=TaskStatus.PENDING,
            attempts=0,
            task_id=tid,
            username=username,
            pipeline_id=pipeline_id,
            pipeline_run_id=run_id,
            pipeline_config_id=pipeline_config_id,
            service_name=tmeta["available_services"],
            is_final_task=is_final,
            data={},
            error=None,
            result=None
        )
    #TODO: add metadata, region, environment if needed
    # metadata = Metadata(region=..., environment=..., extra_info=...)
    final_ids = {tid for tid, tmeta in cfg["task_dict"].items() if tmeta.get("is_final_task")}

    return PipelineRun(
        pipeline_run_id = run_id,
        pipeline_id     = pipeline_id,
        pipeline_config_id = pipeline_config_id,
        status          = PipelineStatus.RUNNING,
        tasks           = tasks_dict,
        started_at      = datetime.now(timezone.utc),
        finished_at     = None,
        triggered_by    = username,
        metadata        = None,
        final_task_ids  = final_ids,
    )

async def ensure_topology(final_cfg: dict, ch: Channel) -> None:
    """
    Registration-time topology:
      - Declare shared exchanges only.
      - Do NOT declare queues or bindings here (those are per-run).
    Idempotent: safe to call repeatedly.
    """
    count = 0
    for item in final_cfg.get("rabbitmq_configs", []):
        rabbit = item.get("rabbitmq") or {}
        x_name = rabbit.get("exchange")
        if not x_name:
            continue

        x_type_raw = (rabbit.get("exchange_type") or "direct").lower()
        x_type = getattr(ExchangeType, x_type_raw.upper(), ExchangeType.DIRECT)

        # Ensure stale exchanges with a different type are removed before declaring.
        try:
            await ch.exchange_delete(x_name, if_unused=False, if_empty=False)
        except Exception:
            # If the exchange does not exist or cannot be deleted, continue with declare.
            pass

        await ch.declare_exchange(x_name, x_type, durable=True)
        count += 1

    logger.info("Exchanges ready: %d  (pipeline=%s)", count, final_cfg.get("pipeline_id"))
