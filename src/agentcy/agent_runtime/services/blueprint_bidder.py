from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from agentcy.agent_runtime.services.agent_utils import normalize_agent, score_agent_for_task
from agentcy.agent_runtime.services.bandit_learner import BanditLearner, _bandit_enabled
from agentcy.agent_runtime.services.cnp_utils import (
    agent_cnp_state,
    capability_value,
    estimate_cost,
    failure_surface_penalty,
    response_threshold,
    score_bid,
    task_params,
    trust_score,
)
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import (
    BidFeatures,
    BlueprintBid,
    CallForProposal,
    CandidateSnapshot,
    DecisionRecord,
    TaskSpec,
)
from agentcy.semantic.execution_recorder import _recorder_enabled

logger = logging.getLogger(__name__)


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _get_env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _fetch_historical_stats(
    agent_id: str,
    capabilities: List[str],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Fetch historical success rate and avg duration from the KG.

    Returns (success_rate, agent_avg_duration, capability_baseline_duration).
    All values are None when stats are unavailable or on any error.
    """
    try:
        from agentcy.semantic.queries import get_agent_success_rate, get_task_avg_duration

        success_rate: Optional[float] = None
        avg_dur: Optional[float] = None
        baseline: Optional[float] = None

        rows = await get_agent_success_rate(agent_id)
        if rows and len(rows) > 0:
            total = int(rows[0].get("total", 0))
            successes = int(rows[0].get("successes", 0))
            if total > 0:
                success_rate = successes / total

        if capabilities:
            cap = capabilities[0]
            dur_rows = await get_task_avg_duration(cap)
            if dur_rows and len(dur_rows) > 0:
                raw_avg = dur_rows[0].get("avgDuration")
                if raw_avg is not None:
                    baseline = float(raw_avg)
                    avg_dur = baseline  # agent-specific would need a separate query

        return success_rate, avg_dur, baseline
    except Exception:
        logger.debug("Failed to fetch historical stats for %s", agent_id, exc_info=True)
        return None, None, None


def _cfp_key(value: Optional[str]) -> str:
    return str(value) if value else "__none__"


def _existing_bid_state(
    store,
    *,
    username: str,
    task_id: str,
) -> Tuple[Dict[str, set[str]], Dict[str, int]]:
    if not hasattr(store, "list_bids"):
        return {}, {}
    existing, _ = store.list_bids(username=username, task_id=task_id)
    by_cfp: Dict[str, set[str]] = {}
    counts: Dict[str, int] = {}
    for bid in existing:
        bidder_id = bid.get("bidder_id")
        if not bidder_id:
            continue
        key = _cfp_key(bid.get("cfp_id"))
        by_cfp.setdefault(key, set()).add(str(bidder_id))
        counts[key] = counts.get(key, 0) + 1
    return by_cfp, counts


async def _delegate_to_http(
    *,
    username: str,
    payload: Dict[str, Any],
    run_id: str,
    to_task: str,
    triggered_by: Any,
) -> Dict[str, Any]:
    base_url = os.getenv("CNP_BIDDER_URL")
    if not base_url:
        raise RuntimeError("CNP_BIDDER_URL is not configured for HTTP mode")
    timeout = _get_env_float("CNP_BIDDER_TIMEOUT_SECONDS", 10.0)
    endpoint = f"{base_url.rstrip('/')}/cnp/{username}/bid"
    body = {
        "username": username,
        "data": dict(payload),
        "run_id": run_id,
        "to_task": to_task,
        "triggered_by": triggered_by,
    }
    body["data"]["cnp_force_local"] = True
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint, json=body)
        response.raise_for_status()
        return response.json()


def _open_cfps(
    store,
    *,
    username: str,
    spec: TaskSpec,
    pipeline_id: Optional[str],
    params: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], int]:
    open_cfps, _ = store.list_cfps(username=username, task_id=spec.task_id, status="open")
    if open_cfps:
        still_open: List[Dict[str, Any]] = []
        now = _now_utc()
        for cfp_doc in open_cfps:
            closes_at = _parse_dt(cfp_doc.get("closes_at"))
            if closes_at and closes_at <= now:
                cfp = CallForProposal.model_validate(cfp_doc)
                closed = cfp.model_copy(
                    update={
                        "status": "closed",
                        "status_reason": "expired",
                        "closes_at": now,
                    }
                )
                store.add_cfp(username=username, cfp=closed)
            else:
                still_open.append(cfp_doc)
        if still_open:
            return still_open, 0

    all_cfps, _ = store.list_cfps(username=username, task_id=spec.task_id)
    round_num = 1
    if all_cfps:
        round_num = max(int(c.get("round") or 0) for c in all_cfps) + 1

    ttl_seconds = _get_env_int("CNP_CFP_TTL_SECONDS", 300)
    stimulus_step = float(os.getenv("CNP_STIMULUS_STEP", "0.1"))
    stimulus_max = float(os.getenv("CNP_STIMULUS_MAX", "1.0"))
    base_stimulus = params.get("stimulus", 0.5)
    stimulus = min(base_stimulus + ((round_num - 1) * stimulus_step), stimulus_max)
    closes_at = _now_utc() + timedelta(seconds=ttl_seconds)
    max_bids = _get_env_int("CNP_MAX_BIDS_PER_TASK", _get_env_int("BLUEPRINT_BIDS_PER_TASK", 1))
    lmax = _get_env_int("CNP_LMAX_DEFAULT", 3)
    min_score = params.get("min_score")
    if min_score is None:
        min_score = _get_env_float("CNP_MIN_BID_SCORE", 0.0)
    min_score = _coerce_float(min_score, 0.0)
    if min_score <= 0.0:
        min_score = None
    cfp = CallForProposal(
        task_id=spec.task_id,
        pipeline_id=pipeline_id,
        required_capabilities=spec.required_capabilities,
        round=round_num,
        priority=params.get("priority"),
        stimulus=stimulus,
        reward=params.get("reward"),
        min_score=min_score,
        max_bids=max_bids,
        lmax=lmax,
        closes_at=closes_at,
    )
    store.add_cfp(username=username, cfp=cfp)
    return [cfp.model_dump(mode="json")], 1


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
    Generate bids for TaskSpecs using the agent registry and write them to the graph store.
    """
    store = rm.graph_marker_store
    registry = rm.agent_registry_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    if not username:
        raise ValueError("blueprint_bidder requires username")

    payload: Dict[str, Any] = {}
    if isinstance(message, dict):
        payload = dict(message.get("data") or {})
        for key in ("cnp_force_local", "pipeline_id", "task_ids"):
            if key in message and key not in payload:
                payload[key] = message[key]
        if not payload:
            payload = dict(message)
    else:
        payload = dict(getattr(message, "data", {}) or {})

    if os.getenv("CNP_BIDDER_MODE", "local").lower() == "http" and not payload.get("cnp_force_local"):
        try:
            return await _delegate_to_http(
                username=username,
                payload=payload,
                run_id=_run_id,
                to_task=_to_task,
                triggered_by=_triggered_by,
            )
        except Exception:
            if os.getenv("CNP_BIDDER_HTTP_FALLBACK", "1") != "0":
                logger.exception("Blueprint bidder HTTP mode failed; falling back to local bidding")
            else:
                raise

    task_ids = payload.get("task_ids")
    if isinstance(task_ids, str):
        task_ids = [task_ids]

    specs = _load_task_specs(store, username=username, task_ids=task_ids)
    if not specs:
        logger.warning("Blueprint bidder found no TaskSpecs for %s", username)
        return {"bids_created": 0, "task_count": 0}

    max_bids_per_task = _get_env_int("BLUEPRINT_BIDS_PER_TASK", 1)
    agents = registry.list(username=username) if registry is not None else []
    bids_created = 0
    cfps_created = 0

    pipeline_id = payload.get("pipeline_id")
    max_rounds = _get_env_int("CNP_MAX_ROUNDS", 3)
    allow_system_fallback = os.getenv("CNP_FALLBACK_TO_SYSTEM", "1") != "0"
    open_next_round_on_empty = os.getenv("CNP_OPEN_NEXT_ROUND_ON_EMPTY", "1") != "0"
    min_score_default = _get_env_float("CNP_MIN_BID_SCORE", 0.0)

    for spec in specs:
        pipeline_id = pipeline_id or (spec.metadata.get("pipeline_id") if isinstance(spec.metadata, dict) else None)
        params = task_params(spec)
        existing_by_cfp, existing_counts = _existing_bid_state(
            store,
            username=username,
            task_id=spec.task_id,
        )
        open_cfps, created = _open_cfps(
            store,
            username=username,
            spec=spec,
            pipeline_id=pipeline_id,
            params=params,
        )
        cfps_created += created

        active_stimulus = params.get("stimulus", 0.5)
        if open_cfps:
            active_stimulus = max(
                [cfp.get("stimulus") for cfp in open_cfps if cfp.get("stimulus") is not None] or [active_stimulus]
            )

        candidate_entries: List[Dict[str, Any]] = []
        for raw_agent in agents:
            agent = normalize_agent(raw_agent)
            if not agent.get("agent_id"):
                continue
            state = agent_cnp_state(agent)
            threshold = response_threshold(task_type=params["task_type"], state=state)
            if active_stimulus < threshold:
                continue
            cap_value = capability_value(agent, spec)
            cost = estimate_cost(
                reward=params["reward"],
                capability_value=cap_value,
                load=state.get("load", 0),
                max_load=state.get("max_load", 1),
            )
            if cap_value < cost:
                continue
            candidate_entries.append(
                {
                    "agent": agent,
                    "capability_score": score_agent_for_task(agent, spec),
                    "capability_value": cap_value,
                    "cost": cost,
                    "load": state.get("load", 0),
                    "max_load": state.get("max_load", 1),
                    "threshold": threshold,
                    "trust": trust_score(state),
                }
            )

        if not candidate_entries and open_next_round_on_empty:
            all_cfps, _ = store.list_cfps(username=username, task_id=spec.task_id)
            current_round = max(int(c.get("round") or 0) for c in all_cfps) if all_cfps else 1
            if current_round < max_rounds:
                now = _now_utc()
                for cfp_doc in open_cfps:
                    if cfp_doc.get("status") != "open":
                        continue
                    cfp = CallForProposal.model_validate(cfp_doc)
                    closed = cfp.model_copy(
                        update={
                            "status": "closed",
                            "status_reason": "no_bids",
                            "closes_at": now,
                        }
                    )
                    store.add_cfp(username=username, cfp=closed)
                open_cfps, created = _open_cfps(
                    store,
                    username=username,
                    spec=spec,
                    pipeline_id=pipeline_id,
                    params=params,
                )
                cfps_created += created

        if not candidate_entries and allow_system_fallback:
            candidate_entries = [
                {
                    "agent": {"agent_id": "system"},
                    "capability_score": 0.5,
                    "capability_value": 1.0,
                    "cost": params.get("reward", 1.0),
                    "load": 0,
                    "max_load": _get_env_int("CNP_LMAX_DEFAULT", 3),
                    "threshold": 0.0,
                    "trust": 0.0,
                }
            ]

        if not candidate_entries:
            continue

        costs = [entry["cost"] for entry in candidate_entries]
        loads = [entry["load"] for entry in candidate_entries]
        tmin, tmax = min(costs), max(costs)
        lmin, lmax = min(loads), max(loads)
        max_bids_per_task = max_bids_per_task or 1

        # Fetch historical stats when execution recording is enabled
        stats_cache: Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]] = {}
        if _recorder_enabled():
            for entry in candidate_entries:
                aid = str(entry["agent"].get("agent_id", ""))
                if aid and aid not in stats_cache:
                    stats_cache[aid] = await _fetch_historical_stats(
                        aid, list(spec.required_capabilities or [])
                    )

        # Fetch failure surface markers for each candidate agent
        failure_cache: Dict[str, float] = {}
        task_type = params.get("task_type")
        if hasattr(store, "list_failure_markers"):
            for entry in candidate_entries:
                aid = str(entry["agent"].get("agent_id", ""))
                if aid and aid not in failure_cache:
                    try:
                        fm = store.list_failure_markers(
                            username=username, agent_id=aid, task_type=task_type,
                        )
                        failure_cache[aid] = failure_surface_penalty(fm)
                    except Exception:
                        logger.debug("Failed to fetch failure markers for %s", aid, exc_info=True)
                        failure_cache[aid] = 0.0

        # Optionally instantiate bandit learner
        learner: Optional[BanditLearner] = None
        if _bandit_enabled():
            try:
                learner = BanditLearner(store, username)
            except Exception:
                logger.debug("Failed to instantiate BanditLearner", exc_info=True)

        scored_entries = []
        for entry in candidate_entries:
            aid = str(entry["agent"].get("agent_id", ""))
            sr, avg_dur, baseline = stats_cache.get(aid, (None, None, None))
            fp = failure_cache.get(aid)

            # Build normalised feature vector for this candidate
            cost_norm = (entry["cost"] - tmin) / (tmax - tmin) if tmax > tmin else 0.0
            load_norm = (entry["load"] - lmin) / (lmax - lmin) if lmax > lmin else 0.0
            speed_val = 0.0
            if avg_dur is not None and baseline and baseline > 0:
                speed_val = max(1.0 - min(avg_dur / baseline, 1.0), 0.0)
            bid_features = BidFeatures(
                trust=entry["trust"],
                cost_norm=cost_norm,
                load_norm=load_norm,
                failure_penalty=fp or 0.0,
                hist_success=sr or 0.0,
                speed=speed_val,
            )

            # Query bandit for learned bias
            lcb: Optional[float] = None
            if learner is not None:
                try:
                    lcb = learner.get_bias(task_type, bid_features)
                except Exception:
                    logger.debug("Bandit get_bias failed for %s", aid, exc_info=True)

            bid_score_val = score_bid(
                trust=entry["trust"],
                cost=entry["cost"],
                load=entry["load"],
                tmin=tmin,
                tmax=tmax,
                lmin=lmin,
                lmax=lmax,
                historical_success_rate=sr,
                historical_avg_duration=avg_dur,
                duration_baseline=baseline,
                failure_penalty_score=fp,
                learned_context_bias=lcb,
            )
            scored_entries.append({
                **entry,
                "bid_score": bid_score_val,
                "bid_features": bid_features,
            })
        scored_entries.sort(key=lambda item: item["bid_score"], reverse=True)

        # Epsilon-greedy exploration: swap rank-1 with a random lower-ranked candidate
        if learner is not None and len(scored_entries) > 1 and learner.should_explore():
            import random as _rand
            swap_idx = _rand.randint(1, len(scored_entries) - 1)
            scored_entries[0], scored_entries[swap_idx] = scored_entries[swap_idx], scored_entries[0]

        # Log DecisionRecord for bandit learning
        if learner is not None and scored_entries and hasattr(store, "save_decision_record"):
            try:
                chosen = scored_entries[0]
                snapshots = [
                    CandidateSnapshot(
                        bidder_id=str(e["agent"].get("agent_id", "")),
                        bid_score=e["bid_score"],
                        features=e.get("bid_features", BidFeatures()),
                    )
                    for e in scored_entries
                ]
                dr = DecisionRecord(
                    task_id=spec.task_id,
                    task_type=task_type,
                    required_capabilities=list(spec.required_capabilities or []),
                    pipeline_id=pipeline_id,
                    cnp_round=open_cfps[0].get("round", 1) if open_cfps else 1,
                    candidate_bidders=snapshots,
                    chosen_bidder_id=str(chosen["agent"].get("agent_id", "")),
                    chosen_features=chosen.get("bid_features"),
                )
                store.save_decision_record(username=username, record=dr)
            except Exception:
                logger.debug("Failed to save DecisionRecord", exc_info=True)

        for cfp_doc in open_cfps or [{}]:
            cfp_id = cfp_doc.get("cfp_id") if isinstance(cfp_doc, dict) else None
            max_bids_override = _coerce_int(
                cfp_doc.get("max_bids") if isinstance(cfp_doc, dict) else None,
                0,
            )
            max_bids = max_bids_override or max_bids_per_task or 1
            min_score = min_score_default
            if isinstance(cfp_doc, dict) and cfp_doc.get("min_score") is not None:
                min_score = _coerce_float(cfp_doc.get("min_score"), min_score_default)
            eligible = [entry for entry in scored_entries if entry["bid_score"] >= min_score]
            if not eligible:
                continue

            key = _cfp_key(cfp_id)
            existing_bidders = existing_by_cfp.get(key, set())
            existing_count = existing_counts.get(key, 0)
            if max_bids and existing_count >= max_bids:
                continue
            eligible = [
                entry for entry in eligible if str(entry["agent"].get("agent_id")) not in existing_bidders
            ]
            if not eligible:
                continue
            slots = max_bids - existing_count if max_bids else len(eligible)
            entries = eligible[: max(0, slots)]
            for entry in entries:
                agent = entry["agent"]
                score = entry["bid_score"]
                bid_ttl_seconds = _get_env_int("CNP_BID_TTL_SECONDS", 86400)
                bid = BlueprintBid(
                    task_id=spec.task_id,
                    bidder_id=str(agent.get("agent_id")),
                    bid_score=score,
                    rationale="icnp-bid",
                    cfp_id=cfp_id,
                    ttl_seconds=bid_ttl_seconds,
                    task_priority=params.get("priority"),
                    task_stimulus=active_stimulus,
                    task_reward=params.get("reward"),
                    capability_score=entry.get("capability_score"),
                    cost_estimate=entry.get("cost"),
                    agent_load=entry.get("load"),
                    response_threshold=entry.get("threshold"),
                    trust_score=entry.get("trust"),
                    cnp_round=cfp_doc.get("round") if isinstance(cfp_doc, dict) else None,
                )
                store.add_bid(username=username, bid=bid)
                bids_created += 1

    logger.info("Blueprint bidder wrote %d bids for %s (cfps_created=%d)", bids_created, username, cfps_created)
    return {"bids_created": bids_created, "cfps_created": cfps_created, "task_count": len(specs)}
