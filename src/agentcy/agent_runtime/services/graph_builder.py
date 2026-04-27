from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any, Dict, Iterable, Optional, Tuple, List

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.agent_runtime.services.cnp_utils import agent_cnp_state
from agentcy.pydantic_models.multi_agent_pipeline import (
    CallForProposal,
    ContractAward,
    EvaluationSequence,
    PlanDraft,
    PlanRevision,
    ReservationMarker,
    RiskLevel,
    TaskSpec,
)

logger = logging.getLogger(__name__)
_CNP_PREFIX = re.compile(r"\[CNP:(\{.*?\})\]\s*")


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_cnp_metadata(description: Any) -> tuple[str, Dict[str, Any]]:
    text = str(description or "")
    match = _CNP_PREFIX.match(text)
    if not match:
        return text, {}
    try:
        payload = json.loads(match.group(1))
    except Exception:
        return text, {}
    return text[match.end():], payload if isinstance(payload, dict) else {}


def _risk_level_from_metadata(metadata: Dict[str, Any]) -> RiskLevel:
    raw = str(metadata.get("risk_level", "medium")).strip().lower()
    if raw == "high":
        return RiskLevel.HIGH
    if raw == "low":
        return RiskLevel.LOW
    return RiskLevel.MEDIUM


def _service_name_from_run(run_doc: Optional[Dict[str, Any]], task_id: str) -> Optional[str]:
    if not isinstance(run_doc, dict):
        return None
    tasks = run_doc.get("tasks")
    if not isinstance(tasks, dict):
        return None
    task_state = tasks.get(task_id)
    if not isinstance(task_state, dict):
        return None
    service_name = task_state.get("service_name")
    if not service_name:
        return None
    return str(service_name)


def _load_pipeline_specs(
    rm: ResourceManager,
    *,
    username: str,
    pipeline_id: str,
    pipeline_run_id: Optional[str],
    include_task_ids: Optional[set[str]] = None,
) -> list[TaskSpec]:
    pipeline_store = getattr(rm, "pipeline_store", None)
    if pipeline_store is None:
        return []
    try:
        final_cfg = pipeline_store.get_final_config(username, pipeline_id)
    except Exception:
        logger.exception("Graph builder failed loading pipeline config for %s", pipeline_id)
        return []

    task_dict = final_cfg.get("task_dict") if isinstance(final_cfg, dict) else None
    if not isinstance(task_dict, dict):
        task_dict = {
            str(task.get("id")): dict(task)
            for task in ((final_cfg or {}).get("dag", {}) or {}).get("tasks", [])
            if isinstance(task, dict) and task.get("id")
        }

    run_doc = None
    if pipeline_run_id and getattr(rm, "ephemeral_store", None) is not None:
        try:
            run_doc = rm.ephemeral_store.read_run(username, pipeline_id, pipeline_run_id)
        except Exception:
            logger.debug("Graph builder could not read run doc for %s/%s", pipeline_id, pipeline_run_id, exc_info=True)

    specs: list[TaskSpec] = []
    for task_id, task_meta in task_dict.items():
        if include_task_ids and task_id not in include_task_ids:
            continue
        if not isinstance(task_meta, dict):
            continue
        description, cnp_metadata = _extract_cnp_metadata(task_meta.get("description", ""))
        metadata = dict(task_meta.get("metadata") or {})
        metadata.update(cnp_metadata)
        dependencies = list(
            task_meta.get("dependencies")
            or (task_meta.get("inputs") or {}).get("dependencies", [])
            or []
        )
        metadata.setdefault("depends_on", dependencies)
        metadata.setdefault("pipeline_id", pipeline_id)
        if pipeline_run_id:
            metadata.setdefault("pipeline_run_id", pipeline_run_id)
        resolved_service = _service_name_from_run(run_doc, task_id) or task_meta.get("available_services")
        if resolved_service:
            metadata.setdefault("service_name", str(resolved_service))
        if task_meta.get("action") and "task_type" not in metadata:
            metadata["task_type"] = str(task_meta["action"])
        required_capability = metadata.get("cnp_capability") or resolved_service or task_meta.get("available_services") or task_id
        preferred_tags = metadata.get("preferred_tags") or task_meta.get("tags") or []
        if not isinstance(preferred_tags, list):
            preferred_tags = []

        specs.append(
            TaskSpec(
                task_id=str(task_id),
                username=username,
                description=description or str(task_meta.get("name") or task_id),
                required_capabilities=[str(required_capability)],
                tags=[str(tag) for tag in preferred_tags if str(tag).strip()],
                risk_level=_risk_level_from_metadata(metadata),
                requires_human_approval=bool(metadata.get("requires_human_approval", False)),
                metadata=metadata,
            )
        )
    return specs


def _best_bids(
    bids: list[Dict[str, Any]],
    *,
    allowed_cfp_ids: Optional[set[str]] = None,
    min_score_by_cfp: Optional[Dict[str, float]] = None,
    min_score_default: float = 0.0,
) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for bid in bids:
        task_id = bid.get("task_id")
        if not task_id:
            continue
        if allowed_cfp_ids is not None:
            cfp_id = bid.get("cfp_id")
            if cfp_id not in allowed_cfp_ids:
                continue
        cfp_id = bid.get("cfp_id")
        min_score = min_score_default
        if min_score_by_cfp and cfp_id in min_score_by_cfp:
            min_score = min_score_by_cfp[cfp_id]
        try:
            bid_score = float(bid.get("bid_score", 0))
        except (TypeError, ValueError):
            bid_score = 0.0
        if bid_score < min_score:
            continue
        score = bid_score
        current = best.get(task_id)
        if current is None or score > current.get("bid_score", 0):
            best[task_id] = bid
    return best


def _ranked_bids(
    bids: list[Dict[str, Any]],
    *,
    allowed_cfp_ids: Optional[set[str]] = None,
    min_score_by_cfp: Optional[Dict[str, float]] = None,
    min_score_default: float = 0.0,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return ALL qualifying bids per task, sorted by bid_score descending.

    Same filtering as ``_best_bids`` but retains every candidate so the
    tracker can fall back to the next one on contractor failure
    (paper §3.4 evaluation sequence table).
    """
    by_task: Dict[str, List[Dict[str, Any]]] = {}
    for bid in bids:
        task_id = bid.get("task_id")
        if not task_id:
            continue
        if allowed_cfp_ids is not None:
            cfp_id = bid.get("cfp_id")
            if cfp_id not in allowed_cfp_ids:
                continue
        cfp_id = bid.get("cfp_id")
        min_score = min_score_default
        if min_score_by_cfp and cfp_id in min_score_by_cfp:
            min_score = min_score_by_cfp[cfp_id]
        try:
            bid_score = float(bid.get("bid_score", 0))
        except (TypeError, ValueError):
            bid_score = 0.0
        if bid_score < min_score:
            continue
        by_task.setdefault(task_id, []).append(bid)
    for task_id in by_task:
        by_task[task_id].sort(key=lambda b: float(b.get("bid_score", 0)), reverse=True)
    return by_task


def _increment_agent_load(
    registry: Any,
    *,
    username: str,
    agent_id: str,
    task_id: str,
) -> None:
    if registry is None or not hasattr(registry, "get") or not hasattr(registry, "heartbeat"):
        return
    try:
        doc = registry.get(username=username, agent_id=agent_id)
    except Exception:
        logger.debug("Graph builder failed to read registry entry for %s", agent_id, exc_info=True)
        return
    if not doc:
        return
    state = agent_cnp_state(doc)
    max_load = state.get("max_load", 1)
    load = min(state.get("load", 0) + 1, max_load)
    received = state.get("tasks_received", 0) + 1
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    cnp = metadata.get("cnp") if isinstance(metadata.get("cnp"), dict) else {}
    cnp.update(
        {
            "tasks_received": received,
            "load": load,
            "max_load": max_load,
            "last_award": {"task_id": task_id},
        }
    )
    metadata["cnp"] = cnp
    try:
        registry.heartbeat(username=username, agent_id=agent_id, metadata=metadata)
    except Exception:
        logger.debug("Graph builder failed updating registry for %s", agent_id, exc_info=True)


def _build_ontology(specs: Iterable[TaskSpec]) -> Dict[str, Any]:
    capabilities: set[str] = set()
    tags: set[str] = set()
    task_types: set[str] = set()
    for spec in specs:
        capabilities.update(spec.required_capabilities or [])
        tags.update(spec.tags or [])
        if isinstance(spec.metadata, dict):
            task_type = spec.metadata.get("task_type")
            if task_type:
                task_types.add(str(task_type))
    ontology: Dict[str, Any] = {}
    if capabilities:
        ontology["capabilities"] = sorted(capabilities)
    if tags:
        ontology["tags"] = sorted(tags)
    if task_types:
        ontology["task_types"] = sorted(task_types)
    return ontology


def _resolve_service_name(
    *,
    spec: TaskSpec,
    bid: Optional[Dict[str, Any]],
    assigned_agent: Optional[str],
    agent_service_map: Dict[str, str],
    service_names: set[str],
) -> Optional[str]:
    if isinstance(spec.metadata, dict):
        service_name = spec.metadata.get("service_name")
        if service_name:
            return str(service_name)
    if bid:
        service_name = bid.get("service_name")
        if service_name:
            return str(service_name)
    if assigned_agent:
        if assigned_agent in agent_service_map:
            return agent_service_map[assigned_agent]
        if assigned_agent in service_names:
            return assigned_agent
    return None


def _build_graph_spec(
    specs: Iterable[TaskSpec],
    best_bids: Dict[str, Dict[str, Any]],
    awards: Dict[str, ContractAward],
    *,
    agent_service_map: Optional[Dict[str, str]] = None,
    service_names: Optional[set[str]] = None,
    run_tasks: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], int]:
    tasks = []
    edges = []
    agent_service_map = agent_service_map or {}
    service_names = service_names or set()
    run_tasks = run_tasks or {}
    for spec in specs:
        bid = best_bids.get(spec.task_id)
        award = awards.get(spec.task_id)
        run_task = run_tasks.get(spec.task_id) if isinstance(run_tasks, dict) else None
        run_service_name = run_task.get("service_name") if isinstance(run_task, dict) else None
        assigned_agent = bid.get("bidder_id") if bid else (run_service_name or None)
        bid_score = bid.get("bid_score") if bid else None
        bid_id = bid.get("bid_id") if bid else None
        service_name = _resolve_service_name(
            spec=spec,
            bid=bid,
            assigned_agent=assigned_agent,
            agent_service_map=agent_service_map,
            service_names=service_names,
        )
        tasks.append(
            {
                "task_id": spec.task_id,
                "description": spec.description,
                "required_capabilities": spec.required_capabilities,
                "tags": spec.tags,
                "assigned_agent": assigned_agent,
                "bid_score": bid_score,
                "bid_id": bid_id,
                "service_name": service_name,
                "cfp_id": bid.get("cfp_id") if bid else (award.cfp_id if award else None),
                "award_id": award.award_id if award else None,
                "task_type": (
                    spec.metadata.get("task_type") if isinstance(spec.metadata, dict) else None
                ),
                "risk_level": (
                    spec.risk_level.value if hasattr(spec.risk_level, "value") else str(spec.risk_level)
                ),
                "requires_human_approval": bool(spec.requires_human_approval),
                "metadata": dict(spec.metadata or {}),
                "status": run_task.get("status") if isinstance(run_task, dict) else None,
            }
        )
        depends_on = spec.metadata.get("depends_on") if isinstance(spec.metadata, dict) else None
        if depends_on:
            for dep in depends_on:
                edges.append({"from": dep, "to": spec.task_id})
    graph_spec: Dict[str, Any] = {"tasks": tasks, "edges": edges}
    ontology = _build_ontology(specs)
    if ontology:
        graph_spec["ontology"] = ontology
    return graph_spec, len(tasks)


async def build_plan_draft(
    rm: ResourceManager,
    *,
    username: str,
    pipeline_id: str,
    pipeline_run_id: Optional[str] = None,
    task_ids: Optional[Iterable[str]] = None,
) -> PlanDraft:
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    specs_raw, _ = store.list_task_specs(username=username)
    task_id_set = set(task_ids) if task_ids else None
    filtered_specs_raw = []
    for spec in specs_raw:
        if task_id_set and spec.get("task_id") not in task_id_set:
            continue
        metadata = spec.get("metadata") if isinstance(spec, dict) else {}
        if isinstance(metadata, dict):
            meta_pipeline = metadata.get("pipeline_id")
            meta_run = metadata.get("pipeline_run_id")
            if meta_pipeline and meta_pipeline != pipeline_id:
                continue
            if pipeline_run_id and meta_run and meta_run != pipeline_run_id:
                continue
        filtered_specs_raw.append(spec)

    specs_by_id: Dict[str, TaskSpec] = {}
    for spec in filtered_specs_raw:
        try:
            validated = TaskSpec.model_validate(spec)
        except Exception:
            continue
        specs_by_id[validated.task_id] = validated

    for spec in _load_pipeline_specs(
        rm,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        include_task_ids=task_id_set,
    ):
        specs_by_id.setdefault(spec.task_id, spec)

    specs = list(specs_by_id.values())

    bids, _ = store.list_bids(
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
    )
    open_cfps, _ = store.list_cfps(username=username, status="open")
    if task_id_set:
        open_cfps = [cfp for cfp in open_cfps if cfp.get("task_id") in task_id_set]
    allowed_cfp_ids = {cfp.get("cfp_id") for cfp in open_cfps if cfp.get("cfp_id")}
    min_score_default = _coerce_float(os.getenv("CNP_MIN_BID_SCORE", "0"), 0.0)
    min_score_by_cfp = {
        str(cfp.get("cfp_id")): _coerce_float(cfp.get("min_score"), 0.0)
        for cfp in open_cfps
        if cfp.get("cfp_id") and cfp.get("min_score") is not None
    }
    best = _best_bids(
        bids,
        allowed_cfp_ids=allowed_cfp_ids or None,
        min_score_by_cfp=min_score_by_cfp,
        min_score_default=min_score_default,
    )

    existing_award_docs, _ = store.list_contract_awards(
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
    )
    awards: Dict[str, ContractAward] = {}
    for award_doc in existing_award_docs:
        task_id = award_doc.get("task_id")
        if not task_id:
            continue
        try:
            award = ContractAward.model_validate(award_doc)
        except Exception:
            continue
        current = awards.get(str(task_id))
        if current is None or str(award.awarded_at) > str(current.awarded_at):
            awards[str(task_id)] = award

    registry = getattr(rm, "agent_registry_store", None)
    for spec in specs:
        bid = best.get(spec.task_id)
        if not bid or spec.task_id in awards:
            continue
        award = ContractAward(
            task_id=spec.task_id,
            bidder_id=str(bid.get("bidder_id")),
            bid_id=bid.get("bid_id"),
            cfp_id=bid.get("cfp_id"),
            pipeline_id=pipeline_id,
            pipeline_run_id=pipeline_run_id,
        )
        store.add_contract_award(username=username, award=award)
        awards[spec.task_id] = award

        marker = ReservationMarker(task_id=spec.task_id, agent_id=str(bid.get("bidder_id")))
        store.add_reservation_marker(username=username, marker=marker)
        _increment_agent_load(
            registry,
            username=username,
            agent_id=str(bid.get("bidder_id")),
            task_id=spec.task_id,
        )

    if open_cfps:
        bids_by_cfp = {bid.get("cfp_id") for bid in bids if bid.get("cfp_id")}
        now = datetime.now(timezone.utc)
        for cfp_doc in open_cfps:
            if cfp_doc.get("status") != "open":
                continue
            cfp_id = cfp_doc.get("cfp_id")
            closes_at = cfp_doc.get("closes_at")
            expired = False
            if closes_at:
                try:
                    expired = datetime.fromisoformat(str(closes_at)) <= now
                except ValueError:
                    expired = False
            if cfp_id not in bids_by_cfp and not expired:
                continue
            cfp = CallForProposal.model_validate(cfp_doc)
            if cfp.task_id not in {spec.task_id for spec in specs}:
                continue
            reason = "expired" if expired else "awarded"
            closed = cfp.model_copy(
                update={"status": "closed", "status_reason": reason, "closes_at": now}
            )
            store.add_cfp(username=username, cfp=closed)

    agent_service_map: Dict[str, str] = {}
    if registry is not None:
        try:
            for entry in registry.list(username=username):
                agent_id = entry.get("agent_id")
                service_name = entry.get("service_name")
                if agent_id and service_name:
                    agent_service_map[str(agent_id)] = str(service_name)
        except Exception:
            logger.exception("Graph builder failed listing registry entries for %s", username)

    service_names: set[str] = set()
    service_store = getattr(rm, "service_store", None)
    if service_store is not None:
        try:
            for entry in service_store.list_all(username):
                name = entry.get("service_name")
                if name:
                    service_names.add(str(name))
        except Exception:
            logger.exception("Graph builder failed listing services for %s", username)

    run_tasks: Dict[str, Dict[str, Any]] = {}
    if pipeline_run_id and getattr(rm, "ephemeral_store", None) is not None:
        try:
            run_doc = rm.ephemeral_store.read_run(username, pipeline_id, pipeline_run_id) or {}
            raw_tasks = run_doc.get("tasks") if isinstance(run_doc, dict) else {}
            if isinstance(raw_tasks, dict):
                run_tasks = {
                    str(task_id): dict(task_state)
                    for task_id, task_state in raw_tasks.items()
                    if isinstance(task_state, dict)
                }
        except Exception:
            logger.debug("Graph builder failed to load run tasks", exc_info=True)

    plan_id = str(uuid4())

    # Store evaluation sequences for failure re-forwarding (paper §3.4).
    ranked = _ranked_bids(
        bids,
        allowed_cfp_ids=allowed_cfp_ids or None,
        min_score_by_cfp=min_score_by_cfp,
        min_score_default=min_score_default,
    )
    for tid, candidates in ranked.items():
        if len(candidates) > 1:
            seq = EvaluationSequence(
                task_id=tid,
                pipeline_id=pipeline_id,
                pipeline_run_id=pipeline_run_id,
                plan_id=plan_id,
                cfp_id=candidates[0].get("cfp_id"),
                candidates=[
                    {
                        "bidder_id": c.get("bidder_id"),
                        "bid_score": float(c.get("bid_score", 0)),
                        "bid_id": c.get("bid_id"),
                        "trust_score": c.get("trust_score"),
                        "cost_estimate": c.get("cost_estimate"),
                        "agent_load": c.get("agent_load"),
                        "cfp_id": c.get("cfp_id"),
                    }
                    for c in candidates
                ],
                current_index=0,
            )
            store.save_evaluation_sequence(username=username, seq=seq)

    graph_spec, task_count = _build_graph_spec(
        specs,
        best,
        awards,
        agent_service_map=agent_service_map,
        service_names=service_names,
        run_tasks=run_tasks,
    )
    if task_count == 0:
        graph_spec["warnings"] = ["no_task_specs"]

    if os.getenv("SEMANTIC_RDF_EXPORT", "1") != "0":
        try:
            from agentcy.semantic.plan_graph import build_plan_graph, serialize_graph
            from agentcy.semantic.fuseki_client import ingest_turtle
            from agentcy.semantic.namespaces import RESOURCE
            rdf_graph = build_plan_graph(
                graph_spec,
                plan_id=plan_id,
                pipeline_id=pipeline_id,
                username=username,
                include_prov=False,
            )
            turtle = serialize_graph(rdf_graph)
            graph_spec["semantic_graph"] = {
                "format": "turtle",
                "data": turtle,
            }
            graph_uri = f"{RESOURCE}graph/plan/{plan_id}"
            await ingest_turtle(turtle, graph_uri=graph_uri)
        except Exception:
            logger.exception("Graph builder failed to serialize semantic graph for %s", username)

    # Fire-and-forget domain knowledge extraction from task descriptions
    try:
        import asyncio
        from agentcy.semantic.domain_extractor import extract_domain_knowledge

        nl_text = " ".join(spec.description for spec in specs if getattr(spec, "description", ""))
        if nl_text.strip():
            asyncio.ensure_future(extract_domain_knowledge(
                text=nl_text,
                plan_id=plan_id,
                username=username,
                graph_marker_store=store,
            ))
    except Exception:
        logger.debug("Domain extraction fire-and-forget setup failed", exc_info=True)

    draft = PlanDraft(
        plan_id=plan_id,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        revision=1,
        graph_spec=graph_spec,
        is_valid=False,
        cached=False,
    )
    store.save_plan_draft(username=username, draft=draft)
    try:
        revision = PlanRevision(
            plan_id=plan_id,
            username=username,
            pipeline_id=pipeline_id,
            pipeline_run_id=pipeline_run_id,
            revision=1,
            parent_revision=None,
            graph_spec=graph_spec,
            status="APPLIED",
            created_by="graph_builder",
            reason="initial_plan",
        )
        store.save_plan_revision(username=username, revision=revision)
    except Exception:
        logger.exception("Graph builder failed to save plan revision for %s", plan_id)
    if pipeline_run_id and getattr(rm, "ephemeral_store", None) is not None:
        try:
            run_doc = rm.ephemeral_store.read_run(username, pipeline_id, pipeline_run_id)
            if isinstance(run_doc, dict):
                run_doc["plan_id"] = plan_id
                run_doc["plan_revision"] = 1
                rm.ephemeral_store.update_run(username, pipeline_id, pipeline_run_id, run_doc)
        except Exception:
            logger.debug("Graph builder failed to update run doc with plan_id", exc_info=True)
    logger.info("Graph builder saved plan draft %s for %s", draft.plan_id, username)
    return draft


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    """
    Agent runtime entrypoint for the graph builder.
    Reads TaskSpecs + bids and stores a PlanDraft in the graph marker store.
    """
    username = getattr(message, "username", None) or message.get("username")
    pipeline_id = getattr(message, "pipeline_id", None) or message.get("pipeline_id")
    pipeline_run_id = getattr(message, "pipeline_run_id", None) or message.get("pipeline_run_id")
    if not username or not pipeline_id:
        raise ValueError("Graph builder requires username and pipeline_id")

    task_ids = None
    if isinstance(message, dict):
        task_ids = message.get("task_ids")

    draft = await build_plan_draft(
        rm,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        task_ids=task_ids,
    )
    return {
        "plan_id": draft.plan_id,
        "pipeline_id": draft.pipeline_id,
        "task_count": len(draft.graph_spec.get("tasks", [])),
    }
