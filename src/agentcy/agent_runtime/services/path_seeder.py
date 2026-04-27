from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional

from agentcy.agent_runtime.services.agent_utils import rank_agents_for_task
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import AffordanceMarker, TaskSpec

logger = logging.getLogger(__name__)


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _load_task_specs(
    store,
    *,
    username: str,
    task_ids: Optional[Iterable[str]] = None,
) -> List[TaskSpec]:
    raw, _ = store.list_task_specs(username=username)
    if task_ids:
        task_id_set = set(task_ids)
        raw = [spec for spec in raw if spec.get("task_id") in task_id_set]
    return [TaskSpec.model_validate(spec) for spec in raw]


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    """
    Seed affordance markers in the graph store to prime the planning path.
    """
    store = rm.graph_marker_store
    registry = rm.agent_registry_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    if not username:
        raise ValueError("path_seeder requires username")

    payload: Dict[str, Any] = {}
    if isinstance(message, dict):
        payload = dict(message.get("data") or message)
    else:
        payload = dict(getattr(message, "data", {}) or {})

    task_ids = payload.get("task_ids")
    if isinstance(task_ids, str):
        task_ids = [task_ids]

    specs = _load_task_specs(store, username=username, task_ids=task_ids)
    if not specs:
        logger.warning("Path seeder found no TaskSpecs for %s", username)
        return {"markers_created": 0, "task_count": 0}

    agents = registry.list(username=username) if registry is not None else []
    ttl_seconds = _get_env_int("PATH_SEED_TTL_SECONDS", 300)
    created = 0

    for spec in specs:
        ranked = rank_agents_for_task(agents, spec, limit=1)
        if ranked:
            agent = ranked[0]["agent"]
            intensity = ranked[0]["score"]
        else:
            agent = {"agent_id": "system"}
            intensity = 0.5

        marker = AffordanceMarker(
            task_id=spec.task_id,
            agent_id=str(agent.get("agent_id")),
            capability=(spec.required_capabilities[0] if spec.required_capabilities else None),
            intensity=float(intensity),
            rationale="path_seed",
        )
        store.add_affordance_marker(username=username, marker=marker, ttl_seconds=ttl_seconds)
        created += 1

    logger.info("Path seeder created %d affordance markers for %s", created, username)
    return {"markers_created": created, "task_count": len(specs)}
