from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from agentcy.agent_runtime.services.plan_utils import load_plan_draft
from agentcy.llm_utilities.llm_connector import LLM_Connector, Provider
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import PlanDraft
from agentcy.semantic.shacl_engine import validate_graph_spec as validate_shacl_graph_spec

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def _load_ruleset() -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    path = os.getenv("PLAN_SHACL_RULESET_PATH", "schemas/plan_draft_shacl.json")
    if not path:
        return None, None, None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None, None, None
    try:
        ruleset = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Plan validator ruleset is not valid JSON: %s", path)
        return None, path, None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return ruleset, path, digest


def _extract_task_ids(tasks: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, Any]]]:
    task_ids = []
    violations: List[Dict[str, Any]] = []
    seen = set()
    for idx, task in enumerate(tasks):
        task_id = task.get("task_id")
        if not task_id:
            violations.append(
                {"code": "missing_task_id", "message": "Task missing task_id", "index": idx}
            )
            continue
        if task_id in seen:
            violations.append(
                {"code": "duplicate_task_id", "message": f"Duplicate task_id '{task_id}'", "task_id": task_id}
            )
            continue
        seen.add(task_id)
        task_ids.append(task_id)
        if task.get("assigned_agent") in (None, ""):
            violations.append(
                {"code": "missing_assignment", "message": f"Task '{task_id}' missing assigned_agent", "task_id": task_id}
            )
    return task_ids, violations


def _detect_cycle(task_ids: List[str], edges: List[Dict[str, Any]]) -> bool:
    adjacency = {tid: [] for tid in task_ids}
    indegree = {tid: 0 for tid in task_ids}
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src in adjacency and dst in adjacency:
            adjacency[src].append(dst)
            indegree[dst] += 1
    queue = [tid for tid, deg in indegree.items() if deg == 0]
    processed = 0
    while queue:
        node = queue.pop()
        processed += 1
        for neighbor in adjacency.get(node, []):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)
    return processed != len(task_ids)

def _parse_ontology(graph_spec: Dict[str, Any]) -> Tuple[Dict[str, set], List[Dict[str, Any]]]:
    violations: List[Dict[str, Any]] = []
    raw = graph_spec.get("ontology")
    if raw is None:
        return {"capabilities": set(), "tags": set(), "task_types": set()}, violations
    if not isinstance(raw, dict):
        violations.append({"code": "invalid_ontology", "message": "Ontology must be an object"})
        return {"capabilities": set(), "tags": set(), "task_types": set()}, violations

    ontology: Dict[str, set] = {"capabilities": set(), "tags": set(), "task_types": set()}
    for key in ("capabilities", "tags", "task_types"):
        values = raw.get(key)
        if values is None:
            continue
        if not isinstance(values, list):
            violations.append(
                {"code": "invalid_ontology_values", "message": f"Ontology '{key}' must be a list"}
            )
            continue
        for value in values:
            if isinstance(value, str) and value.strip():
                ontology[key].add(value.strip())
    return ontology, violations


def _provider_from_env() -> Optional[Provider]:
    raw = os.getenv("LLM_PLAN_VALIDATOR_PROVIDER", "").strip().lower()
    if raw in ("openai", "gpt"):
        return Provider.OPENAI
    if raw in ("llama", "ollama"):
        return Provider.LLAMA
    return None


def _shacl_enabled() -> bool:
    raw = os.getenv("SHACL_ENABLE")
    if raw is None:
        return True
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _extract_json(text: Optional[str]) -> Optional[str]:
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


def _build_llm_prompt(
    *,
    graph_spec: Dict[str, Any],
    violations: List[Dict[str, Any]],
    stats: Dict[str, Any],
) -> List[Dict[str, str]]:
    system = "You are a plan validation assistant. Return ONLY valid JSON. No markdown."
    schema = {
        "approved": False,
        "assessment": "string",
        "risks": ["string"],
        "suggested_fixes": ["string"],
        "confidence": 0.0,
    }
    context = {
        "tasks": graph_spec.get("tasks") or [],
        "edges": graph_spec.get("edges") or [],
        "ontology": graph_spec.get("ontology"),
        "violations": violations,
        "stats": stats,
    }
    user = (
        "Review the plan validation report and return JSON with keys approved "
        "(boolean), assessment, risks, suggested_fixes, and confidence (0-1). "
        "Set approved=false if there are critical violations that should block execution. "
        f"Schema example: {json.dumps(schema, separators=(',', ':'))}\n"
        f"Context: {json.dumps(context, separators=(',', ':'))}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_llm_review(text: Optional[str]) -> Optional[Dict[str, Any]]:
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
    approved = data.get("approved")
    if not isinstance(approved, bool):
        return None
    assessment = data.get("assessment")
    risks_raw = data.get("risks")
    fixes_raw = data.get("suggested_fixes")
    confidence = data.get("confidence")
    risks = [str(item) for item in risks_raw] if isinstance(risks_raw, list) else []
    fixes = [str(item) for item in fixes_raw] if isinstance(fixes_raw, list) else []
    if not isinstance(assessment, str):
        assessment = None
    if not isinstance(confidence, (int, float)):
        confidence = None
    return {
        "approved": approved,
        "assessment": assessment,
        "risks": risks,
        "suggested_fixes": fixes,
        "confidence": confidence,
    }


async def _llm_review(
    *,
    graph_spec: Dict[str, Any],
    violations: List[Dict[str, Any]],
    stats: Dict[str, Any],
    request_id: str,
) -> Optional[Dict[str, Any]]:
    provider = _provider_from_env()
    if not provider:
        return None
    try:
        connector = LLM_Connector(provider=provider)
    except Exception as exc:
        logger.warning("Plan validator LLM disabled (init failed): %s", exc)
        return None

    prompt = _build_llm_prompt(graph_spec=graph_spec, violations=violations, stats=stats)
    await connector.start()
    try:
        responses = await connector.handle_incoming_requests([(request_id, prompt)])
    finally:
        await connector.stop()
    parsed = _parse_llm_review(responses.get(request_id))
    if parsed is None:
        logger.warning("Plan validator LLM response invalid")
        return None
    parsed["provider"] = provider.value
    return parsed


def _validate_graph_spec(graph_spec: Dict[str, Any]) -> Dict[str, Any]:
    tasks = list(graph_spec.get("tasks") or [])
    edges = list(graph_spec.get("edges") or [])
    violations: List[Dict[str, Any]] = []
    ruleset, ruleset_path, ruleset_hash = _load_ruleset()
    ontology, ontology_violations = _parse_ontology(graph_spec)
    violations.extend(ontology_violations)

    if not tasks:
        violations.append({"code": "no_tasks", "message": "Graph has no tasks"})

    task_ids, task_violations = _extract_task_ids(tasks)
    violations.extend(task_violations)

    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src not in task_ids or dst not in task_ids:
            violations.append(
                {
                    "code": "edge_missing_task",
                    "message": f"Edge {src}->{dst} references unknown task",
                    "from": src,
                    "to": dst,
                }
            )

    if task_ids and edges and _detect_cycle(task_ids, edges):
        violations.append({"code": "cycle_detected", "message": "Graph contains a cycle"})

    for task in tasks:
        task_id = task.get("task_id")
        req_caps = task.get("required_capabilities")
        if not isinstance(req_caps, list):
            violations.append(
                {
                    "code": "invalid_required_capabilities",
                    "message": "required_capabilities must be a list",
                    "task_id": task_id,
                }
            )
        elif not req_caps:
            violations.append(
                {
                    "code": "missing_required_capabilities",
                    "message": "Task missing required_capabilities",
                    "task_id": task_id,
                }
            )
        else:
            invalid_caps = [cap for cap in req_caps if not isinstance(cap, str) or not cap.strip()]
            if invalid_caps:
                violations.append(
                    {
                        "code": "invalid_capability",
                        "message": "Task has invalid capability values",
                        "task_id": task_id,
                    }
                )
            if ontology["capabilities"]:
                unknown = [cap for cap in req_caps if cap not in ontology["capabilities"]]
                if unknown:
                    violations.append(
                        {
                            "code": "unknown_capability",
                            "message": "Task references capabilities outside ontology",
                            "task_id": task_id,
                            "unknown": unknown,
                        }
                    )

        tags = task.get("tags")
        if tags is not None and not isinstance(tags, list):
            violations.append(
                {
                    "code": "invalid_tags",
                    "message": "tags must be a list when provided",
                    "task_id": task_id,
                }
            )
        elif isinstance(tags, list) and ontology["tags"]:
            unknown_tags = [tag for tag in tags if tag not in ontology["tags"]]
            if unknown_tags:
                violations.append(
                    {
                        "code": "unknown_tag",
                        "message": "Task references tags outside ontology",
                        "task_id": task_id,
                        "unknown": unknown_tags,
                    }
                )

        task_type = task.get("task_type")
        if ontology["task_types"]:
            if not task_type:
                violations.append(
                    {
                        "code": "missing_task_type",
                        "message": "Task missing task_type required by ontology",
                        "task_id": task_id,
                    }
                )
            elif task_type not in ontology["task_types"]:
                violations.append(
                    {
                        "code": "unknown_task_type",
                        "message": "Task type not defined in ontology",
                        "task_id": task_id,
                        "task_type": task_type,
                    }
                )

    report = {
        "conforms": not violations,
        "checked_at": _now_iso(),
        "stats": {"task_count": len(task_ids), "edge_count": len(edges)},
        "ontology": {k: sorted(v) for k, v in ontology.items() if v},
        "violations": violations,
    }
    if ruleset:
        report["ruleset"] = {
            "path": ruleset_path,
            "version": ruleset.get("version"),
            "hash": ruleset_hash,
        }
    return report


async def validate_plan_draft(
    rm: ResourceManager,
    *,
    username: str,
    pipeline_id: Optional[str] = None,
    pipeline_run_id: Optional[str] = None,
    plan_id: Optional[str] = None,
) -> PlanDraft:
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    draft = load_plan_draft(
        store,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        plan_id=plan_id,
    )
    report = _validate_graph_spec(draft.graph_spec)
    if not _shacl_enabled():
        report["shacl_engine"] = {
            "conforms": None,
            "error": "shacl_disabled",
        }
        shacl_conforms = True
    else:
        shacl_engine = validate_shacl_graph_spec(
            draft.graph_spec,
            plan_id=draft.plan_id,
            pipeline_id=pipeline_id,
            username=username,
        )
        if shacl_engine is None:
            report["shacl_engine"] = {
                "conforms": None,
                "error": "shacl_engine_unavailable",
            }
            shacl_conforms = True
        else:
            report["shacl_engine"] = shacl_engine
            shacl_conforms = bool(shacl_engine.get("conforms"))

    llm_required = _provider_from_env() is not None
    llm_review = await _llm_review(
        graph_spec=draft.graph_spec or {},
        violations=report.get("violations", []),
        stats=report.get("stats", {}),
        request_id=f"{draft.plan_id}:plan_validation",
    )
    llm_approved = False
    if llm_review:
        report["llm_review"] = llm_review
        llm_approved = bool(llm_review.get("approved"))
    else:
        report["llm_review"] = {
            "approved": False,
            "assessment": None,
            "risks": [],
            "suggested_fixes": [],
            "confidence": None,
            "error": "llm_unavailable",
        }
    report["llm_required"] = llm_required
    llm_gate = llm_approved if llm_required else True
    report["final_conforms"] = bool(report.get("conforms")) and shacl_conforms and llm_gate
    updated = draft.model_copy(update={"is_valid": report["final_conforms"], "shacl_report": report})
    store.save_plan_draft(username=username, draft=updated)
    logger.info(
        "Plan validator updated plan %s for %s (conforms=%s)",
        updated.plan_id,
        username,
        report["final_conforms"],
    )
    return updated


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    username = getattr(message, "username", None) or message.get("username")
    pipeline_id = getattr(message, "pipeline_id", None) or message.get("pipeline_id")
    pipeline_run_id = getattr(message, "pipeline_run_id", None) or message.get("pipeline_run_id")
    plan_id = getattr(message, "plan_id", None)
    if isinstance(message, dict):
        plan_id = message.get("plan_id", plan_id)

    if not username:
        raise ValueError("Plan validator requires username")

    draft = await validate_plan_draft(
        rm,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        plan_id=plan_id,
    )
    return {
        "plan_id": draft.plan_id,
        "pipeline_id": draft.pipeline_id,
        "is_valid": draft.is_valid,
        "violations": (draft.shacl_report or {}).get("violations", []),
    }
