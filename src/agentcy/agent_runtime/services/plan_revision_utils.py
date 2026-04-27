from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from agentcy.agent_runtime.services.plan_validator import _validate_graph_spec
from agentcy.semantic.shacl_engine import validate_graph_spec as validate_shacl_graph_spec
from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskStatus


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_delta(
    graph_spec: Dict[str, Any],
    delta: Dict[str, Any],
) -> Tuple[Dict[str, Any], int]:
    tasks = [dict(task) for task in (graph_spec.get("tasks") or [])]
    edges = [dict(edge) for edge in (graph_spec.get("edges") or [])]
    applied = 0

    overrides = delta.get("task_overrides")
    if isinstance(overrides, dict):
        for task in tasks:
            task_id = task.get("task_id")
            if not task_id:
                continue
            patch = overrides.get(task_id)
            if isinstance(patch, dict):
                task.update(patch)
                applied += 1

    remove_ids = delta.get("remove_tasks")
    if isinstance(remove_ids, list):
        remove_set = {str(tid) for tid in remove_ids}
        if remove_set:
            tasks = [task for task in tasks if str(task.get("task_id")) not in remove_set]
            edges = [
                edge
                for edge in edges
                if edge.get("from") not in remove_set and edge.get("to") not in remove_set
            ]
            applied += len(remove_set)

    add_tasks = delta.get("add_tasks")
    if isinstance(add_tasks, list):
        for task in add_tasks:
            if isinstance(task, dict) and task.get("task_id"):
                tasks.append(task)
                applied += 1

    add_edges = delta.get("add_edges")
    if isinstance(add_edges, list):
        for edge in add_edges:
            if isinstance(edge, dict) and edge.get("from") and edge.get("to"):
                edges.append(edge)
                applied += 1

    remove_edges = delta.get("remove_edges")
    if isinstance(remove_edges, list):
        remove_pairs = {(edge.get("from"), edge.get("to")) for edge in remove_edges if isinstance(edge, dict)}
        if remove_pairs:
            edges = [edge for edge in edges if (edge.get("from"), edge.get("to")) not in remove_pairs]
            applied += len(remove_pairs)

    updated = dict(graph_spec)
    updated["tasks"] = tasks
    updated["edges"] = edges
    return updated, applied


def _task_status(value: Any) -> Optional[str]:
    if isinstance(value, TaskStatus):
        return value.value
    if value is None:
        return None
    return str(value).upper()


def _task_map(tasks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    for task in tasks:
        task_id = task.get("task_id")
        if task_id:
            mapping[str(task_id)] = task
    return mapping


def validate_runtime_constraints(
    *,
    base_graph: Dict[str, Any],
    candidate_graph: Dict[str, Any],
    run_doc: Dict[str, Any],
) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    run_tasks = run_doc.get("tasks") or {}
    if not isinstance(run_tasks, dict):
        return violations

    completed: set[str] = set()
    running: set[str] = set()
    failed: set[str] = set()
    for task_id, entry in run_tasks.items():
        status = _task_status(entry.get("status"))
        if status == TaskStatus.COMPLETED.value:
            completed.add(str(task_id))
        elif status == TaskStatus.RUNNING.value:
            running.add(str(task_id))
        elif status == TaskStatus.FAILED.value:
            failed.add(str(task_id))

    started = completed | running | failed

    base_tasks = _task_map(list(base_graph.get("tasks") or []))
    cand_tasks = _task_map(list(candidate_graph.get("tasks") or []))

    for task_id in sorted(started):
        if task_id not in cand_tasks:
            violations.append(
                {
                    "code": "remove_started_task",
                    "message": f"Task '{task_id}' already started and cannot be removed",
                    "task_id": task_id,
                }
            )

    for task_id in sorted(running):
        base_task = base_tasks.get(task_id) or {}
        cand_task = cand_tasks.get(task_id) or {}
        if base_task and cand_task:
            base_agent = base_task.get("assigned_agent")
            cand_agent = cand_task.get("assigned_agent")
            if base_agent and cand_agent and base_agent != cand_agent:
                violations.append(
                    {
                        "code": "running_assignment_change",
                        "message": f"Task '{task_id}' is running; assigned_agent cannot change",
                        "task_id": task_id,
                        "from": base_agent,
                        "to": cand_agent,
                    }
                )

    # Reject new prerequisites for already-completed/running tasks.
    candidate_edges = list(candidate_graph.get("edges") or [])
    predecessors: Dict[str, set[str]] = {}
    for edge in candidate_edges:
        src = edge.get("from")
        dst = edge.get("to")
        if not src or not dst:
            continue
        predecessors.setdefault(str(dst), set()).add(str(src))

    for task_id in sorted(completed | running):
        preds = predecessors.get(task_id, set())
        invalid = [pred for pred in preds if pred not in completed]
        if invalid:
            violations.append(
                {
                    "code": "invalid_runtime_dependency",
                    "message": f"Task '{task_id}' already started; new dependencies are not allowed",
                    "task_id": task_id,
                    "invalid_predecessors": invalid,
                }
            )

    return violations


def validate_candidate_graph(
    *,
    candidate_graph: Dict[str, Any],
    base_graph: Dict[str, Any],
    run_doc: Dict[str, Any],
    plan_id: str,
    pipeline_id: Optional[str],
    username: Optional[str],
) -> Dict[str, Any]:
    static_report = _validate_graph_spec(candidate_graph)
    shacl_engine = validate_shacl_graph_spec(
        candidate_graph,
        plan_id=plan_id,
        pipeline_id=pipeline_id,
        username=username,
    )
    if shacl_engine is None:
        shacl_report = {"conforms": None, "error": "shacl_engine_unavailable"}
        shacl_ok = True
    else:
        shacl_report = shacl_engine
        shacl_ok = bool(shacl_engine.get("conforms"))

    runtime_violations = validate_runtime_constraints(
        base_graph=base_graph,
        candidate_graph=candidate_graph,
        run_doc=run_doc,
    )

    report = {
        "checked_at": _now_iso(),
        "static": static_report,
        "shacl_engine": shacl_report,
        "runtime": {
            "conforms": not runtime_violations,
            "violations": runtime_violations,
        },
    }
    report["conforms"] = bool(static_report.get("conforms")) and shacl_ok and not runtime_violations
    return report
