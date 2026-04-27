from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.llm_utilities.llm_connector import LLM_Connector, Provider
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import StrategyPlan

logger = logging.getLogger(__name__)


def _payload_from_message(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message.get("data") or message)
    return dict(getattr(message, "data", {}) or {})


def _build_adjacency(tasks: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    adjacency: Dict[str, List[str]] = {task["task_id"]: [] for task in tasks if task.get("task_id")}
    indegree: Dict[str, int] = {task_id: 0 for task_id in adjacency}
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src in adjacency and dst in adjacency:
            adjacency[src].append(dst)
            indegree[dst] += 1
    return adjacency, indegree


def _topological_phases(tasks: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[List[str]]:
    adjacency, indegree = _build_adjacency(tasks, edges)
    phases: List[List[str]] = []
    queue = [task_id for task_id, deg in indegree.items() if deg == 0]
    while queue:
        phase = list(queue)
        phases.append(phase)
        next_queue: List[str] = []
        for node in queue:
            for neighbor in adjacency.get(node, []):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    next_queue.append(neighbor)
        queue = next_queue
    return phases


def _critical_path(tasks: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> List[str]:
    adjacency, indegree = _build_adjacency(tasks, edges)
    queue = [task_id for task_id, deg in indegree.items() if deg == 0]
    order: List[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for neighbor in adjacency.get(node, []):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    dist: Dict[str, int] = {task_id: 0 for task_id in adjacency}
    prev: Dict[str, Optional[str]] = {task_id: None for task_id in adjacency}
    for node in order:
        for neighbor in adjacency.get(node, []):
            if dist[node] + 1 > dist[neighbor]:
                dist[neighbor] = dist[node] + 1
                prev[neighbor] = node

    if not dist:
        return []
    end = max(dist, key=dist.get)
    path = []
    while end is not None:
        path.append(end)
        end = prev[end]
    return list(reversed(path))


def _provider_from_env() -> Optional[Provider]:
    raw = os.getenv("LLM_STRATEGIST_PROVIDER", "").strip().lower()
    if raw in ("openai", "gpt"):
        return Provider.OPENAI
    if raw in ("llama", "ollama"):
        return Provider.LLAMA
    return None


def _build_prompt(
    *,
    tasks: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    baseline_phases: List[Dict[str, Any]],
    baseline_critical_path: List[str],
) -> List[Dict[str, str]]:
    system = (
        "You are a planning strategist. Return ONLY valid JSON. "
        "No markdown, no commentary."
    )
    schema = {
        "summary": "string",
        "phases": [{"phase": 1, "tasks": ["task_id"]}],
        "critical_path": ["task_id"],
    }
    context = {
        "tasks": [
            {
                "task_id": task.get("task_id"),
                "required_capabilities": task.get("required_capabilities"),
                "tags": task.get("tags"),
                "task_type": task.get("task_type"),
            }
            for task in tasks
        ],
        "edges": edges,
        "baseline": {
            "phases": baseline_phases,
            "critical_path": baseline_critical_path,
        },
    }
    user = (
        "Given the task graph, produce a strategy plan JSON with keys "
        "summary, phases, critical_path. "
        "Use ONLY task_ids from the input and include all tasks exactly once "
        "across phases. Ensure dependencies are respected (a task appears in "
        "a later phase than its prerequisites). "
        f"Schema example: {json.dumps(schema, separators=(',', ':'))}\n"
        f"Context: {json.dumps(context, separators=(',', ':'))}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json(text: str) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return stripped[start : end + 1]


def _normalize_phases(raw: Any, task_ids: List[str], edges: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(raw, list) or not raw:
        return None
    phases: List[List[str]] = []
    if all(isinstance(item, dict) for item in raw):
        for item in raw:
            tasks = item.get("tasks")
            if not isinstance(tasks, list):
                return None
            phases.append([str(task) for task in tasks if isinstance(task, str)])
    elif all(isinstance(item, list) for item in raw):
        phases = [[str(task) for task in item if isinstance(task, str)] for item in raw]
    else:
        return None

    all_tasks: List[str] = []
    for phase in phases:
        for task_id in phase:
            if task_id not in all_tasks:
                all_tasks.append(task_id)

    task_set = set(task_ids)
    if not all_tasks or any(task_id not in task_set for task_id in all_tasks):
        return None
    if set(all_tasks) != task_set:
        return None

    phase_index = {}
    for idx, phase in enumerate(phases):
        for task_id in phase:
            phase_index[task_id] = idx
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src in phase_index and dst in phase_index:
            if phase_index[src] >= phase_index[dst]:
                return None

    return [{"phase": idx + 1, "tasks": phase} for idx, phase in enumerate(phases)]


def _parse_strategy_response(
    text: Optional[str],
    *,
    task_ids: List[str],
    edges: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not text or text == "Error":
        return None
    payload = _extract_json(text)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    summary = data.get("summary")
    phases = _normalize_phases(data.get("phases"), task_ids, edges)
    critical_path = data.get("critical_path")
    if not isinstance(critical_path, list) or any(item not in task_ids for item in critical_path):
        critical_path = None
    return {
        "summary": summary if isinstance(summary, str) and summary.strip() else None,
        "phases": phases,
        "critical_path": critical_path,
    }


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    pipeline_id = getattr(message, "pipeline_id", None) or (message.get("pipeline_id") if isinstance(message, dict) else None)
    pipeline_run_id = getattr(message, "pipeline_run_id", None) or (message.get("pipeline_run_id") if isinstance(message, dict) else None)
    plan_id = getattr(message, "plan_id", None)
    if isinstance(message, dict):
        plan_id = message.get("plan_id", plan_id)

    if not username:
        raise ValueError("llm_strategist requires username")

    draft = load_plan_draft(
        store,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        plan_id=plan_id,
    )
    graph_spec = draft.graph_spec or {}
    tasks = list(graph_spec.get("tasks") or [])
    edges = list(graph_spec.get("edges") or [])

    phases_raw = _topological_phases(tasks, edges)
    baseline_phases = [
        {"phase": idx + 1, "tasks": phase}
        for idx, phase in enumerate(phases_raw)
    ]
    baseline_critical_path = _critical_path(tasks, edges)
    summary = f"Plan {draft.plan_id} executes in {len(baseline_phases)} phases with {len(tasks)} tasks."
    phases = baseline_phases
    critical_path = baseline_critical_path

    provider = _provider_from_env()
    task_ids = [task.get("task_id") for task in tasks if task.get("task_id")]
    if provider and task_ids:
        try:
            connector = LLM_Connector(provider=provider)
        except Exception as exc:
            logger.warning("LLM strategist disabled (init failed): %s", exc)
            connector = None
        if connector is not None:
            prompt = _build_prompt(
                tasks=tasks,
                edges=edges,
                baseline_phases=baseline_phases,
                baseline_critical_path=baseline_critical_path,
            )
            await connector.start()
            try:
                responses = await connector.handle_incoming_requests([(draft.plan_id, prompt)])
            finally:
                await connector.stop()
            parsed = _parse_strategy_response(
                responses.get(draft.plan_id),
                task_ids=task_ids,
                edges=edges,
            )
            if parsed:
                summary = parsed.get("summary") or summary
                phases = parsed.get("phases") or phases
                critical_path = parsed.get("critical_path") or critical_path
            else:
                logger.warning("LLM strategist response invalid; using baseline strategy.")

    strategy = StrategyPlan(
        plan_id=draft.plan_id,
        pipeline_id=pipeline_id,
        summary=summary,
        phases=phases,
        critical_path=critical_path,
    )
    store.save_strategy_plan(username=username, strategy=strategy)

    logger.info("LLM strategist stored strategy %s for %s", strategy.strategy_id, username)
    return {
        "plan_id": draft.plan_id,
        "strategy_id": strategy.strategy_id,
        "phase_count": len(phases),
        "critical_path": critical_path,
        "summary": summary,
    }
