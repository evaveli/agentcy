#src/agentcy/agent_runtime/forwarder.py

from abc import ABC, abstractmethod
import functools
import asyncio
from datetime import datetime
import importlib
import logging
import os
import random
from typing import Any, Awaitable, Dict, List, Union, Callable, Optional
import sys

import aio_pika
import aiormq
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from tenacity import RetryError, retry, retry_if_exception, retry_if_exception_type, stop_after_attempt, wait_fixed
try:
    from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import EntryMessage, TaskState, TaskStatus
except ImportError:
    from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import EntryMessage, TaskState, TaskStatus
from agentcy.agent_runtime.parser import AbstractMessageParser
from agentcy.agent_runtime.services.bandit_learner import (
    BanditLearner,
    _bandit_enabled,
    compute_reward,
)
from agentcy.pydantic_models.multi_agent_pipeline import (
    BidFeatures,
    ExecutionOutcomeBandit,
)
from agentcy.pydantic_models.multi_agent_pipeline import EvaluationSequence
from agentcy.pipeline_orchestrator.pub_sub.publisher_utilities.publishing_utilites import persist_output_and_update_task


_AGENT_ENTRY_MAP = {
    "warehouse-north": "agentcy.demo_agents.warehouse_agent_north:run",
    "warehouse-central": "agentcy.demo_agents.warehouse_agent_central:run",
    "warehouse-south": "agentcy.demo_agents.warehouse_agent_south:run",
    "cost-estimator": "agentcy.demo_agents.cost_estimator:run",
    "speed-estimator": "agentcy.demo_agents.speed_estimator:run",
}

_ROUND_ROBIN_INDEX: Dict[tuple[str, str], int] = {}


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _find_shared_capability(registry, *, username: str, service_name: str) -> tuple[Optional[str], list[str]]:
    try:
        entries = registry.list(username=username, service_name=service_name)
    except Exception:
        return None, []
    if not entries:
        return None, []

    capabilities = entries[0].get("capabilities", [])
    for capability in capabilities:
        try:
            competing = registry.list(username=username, capability=capability)
        except Exception:
            continue
        service_names = sorted({
            str(agent.get("service_name"))
            for agent in competing
            if agent.get("service_name")
        })
        if len(service_names) > 1:
            return str(capability), service_names
    return None, []


def _select_round_robin_service(*, username: str, capability: str, service_names: list[str]) -> Optional[str]:
    if not service_names:
        return None
    key = (username, capability)
    current_index = _ROUND_ROBIN_INDEX.get(key, -1)
    next_index = (current_index + 1) % len(service_names)
    _ROUND_ROBIN_INDEX[key] = next_index
    return service_names[next_index]


def _load_agent_entry(service_name: str):
    entry = _AGENT_ENTRY_MAP.get(service_name)
    if not entry:
        return None
    mod_path, func_name = entry.rsplit(":", 1)
    mod = importlib.import_module(mod_path)
    return getattr(mod, func_name)


# ── CNP inline resolution for competing agents ──────────────────────────────
async def _resolve_competing_service(
    rm,
    task_id: str,
    available_services: str,
    username: str,
    pipeline_id: str,
    pipeline_run_id: str,
    task_data: dict,
) -> tuple[str, Optional[EvaluationSequence]]:
    """Check if available_services is a capability shared by multiple agents.

    If so, run inline CNP bidding and return (winner_service_name, eval_sequence).
    If not (single agent or direct match), return (available_services, None).
    """
    registry = getattr(rm, "agent_registry_store", None)
    if registry is None:
        return available_services, None

    # Check if available_services matches a registered service name directly
    try:
        direct = registry.list(username=username, service_name=available_services)
        if direct:
            return available_services, None  # exact service match, no bidding needed
    except Exception:
        return available_services, None

    # Check if it's a capability with multiple agents
    try:
        candidates = registry.list(username=username, capability=available_services)
    except Exception:
        return available_services, None

    if len(candidates) <= 1:
        if candidates:
            return candidates[0].get("service_name", available_services), None
        return available_services, None

    # Multiple agents share this capability — run CNP bidding
    logger.info(
        "[CNP-resolve] Task '%s' has %d competing agents for capability '%s': %s",
        task_id, len(candidates), available_services,
        [c.get("service_name") for c in candidates],
    )

    from agentcy.agent_runtime.services.agent_utils import normalize_agent, score_agent_for_task
    from agentcy.agent_runtime.services.cnp_utils import (
        agent_cnp_state, capability_value, estimate_cost, score_bid,
        trust_score, response_threshold, task_params, failure_surface_penalty,
    )
    from agentcy.pydantic_models.multi_agent_pipeline import (
        BlueprintBid, TaskSpec, RiskLevel,
    )

    # Build a TaskSpec from the task data, extracting CNP metadata
    description = ""
    metadata = {}
    cnp_metadata = {}
    if isinstance(task_data, dict):
        description = task_data.get("description", "")
        metadata = task_data.get("metadata", {})

    # Also search for description in nested payload paths
    if not description:
        payload = task_data.get("payload", {}) if isinstance(task_data, dict) else {}
        if isinstance(payload, dict):
            inner = payload.get("payload", {})
            if isinstance(inner, dict):
                inner_data = inner.get("data", {})
                if isinstance(inner_data, dict):
                    description = inner_data.get("description", "")

    # Parse CNP metadata from [CNP:{...}] prefix in description
    import re as _re
    import json as _json
    cnp_match = _re.match(r'\[CNP:(\{.*?\})\]\s*', description or "")
    if cnp_match:
        try:
            cnp_metadata = _json.loads(cnp_match.group(1))
            description = description[cnp_match.end():]  # strip prefix for agent
        except Exception:
            pass

    # Merge CNP metadata into metadata dict for task_params() to read
    merged_metadata = {**metadata, **cnp_metadata}

    # Extract risk_level from metadata
    risk_str = merged_metadata.get("risk_level", "medium")
    risk_map = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM, "high": RiskLevel.HIGH}
    risk_level = risk_map.get(str(risk_str).lower(), RiskLevel.MEDIUM)

    # Extract preferred_tags for tag matching in score_agent_for_task
    preferred_tags = merged_metadata.get("preferred_tags", [])

    spec = TaskSpec(
        task_id=task_id,
        username=username,
        description=description,
        required_capabilities=[available_services],
        risk_level=risk_level,
        tags=preferred_tags,
        metadata=merged_metadata,
    )
    params = task_params(spec)

    graph_store = getattr(rm, "graph_marker_store", None)
    pheromone_enabled = _env_enabled("PHEROMONE_ENABLE", True)

    # Score all candidates
    scored = []
    for raw_agent in candidates:
        agent = normalize_agent(raw_agent)
        state = agent_cnp_state(agent)
        cap_val = capability_value(agent, spec)
        cost = estimate_cost(
            reward=params.get("reward", 1.0),
            capability_value=cap_val,
            load=state.get("load", 0),
            max_load=state.get("max_load", 1),
        )
        t_score = trust_score(state)

        # Fetch pheromone intensity if available
        pheromone_intensity = 0.0
        if pheromone_enabled and graph_store and hasattr(graph_store, "list_affordance_markers"):
            try:
                agent_id_str = str(agent.get("agent_id", ""))
                all_markers, _ = graph_store.list_affordance_markers(
                    username=username,
                    agent_id=agent_id_str,
                )
                # Filter by capability manually
                for m in (all_markers or []):
                    m_cap = m.get("capability", "") if isinstance(m, dict) else getattr(m, "capability", "")
                    m_intensity = m.get("intensity", 0) if isinstance(m, dict) else getattr(m, "intensity", 0)
                    if m_cap == available_services:
                        pheromone_intensity = max(pheromone_intensity, float(m_intensity))
            except Exception:
                pass

        # Fetch failure surface penalty
        fp = 0.0
        if graph_store and hasattr(graph_store, "list_failure_markers"):
            try:
                fm = graph_store.list_failure_markers(
                    username=username,
                    agent_id=str(agent.get("agent_id", "")),
                    task_type=params.get("task_type"),
                )
                fp = failure_surface_penalty(fm)
            except Exception:
                pass

        scored.append({
            "agent": agent,
            "trust_score": t_score,
            "cost_estimate": cost,
            "load": state.get("load", 0),
            "max_load": max(state.get("max_load", 1), 1),
            "capability_score": score_agent_for_task(agent, spec),
            "pheromone_intensity": pheromone_intensity,
            "failure_penalty": fp,
        })

    # Two-pass scoring: compute actual tmin/tmax across all candidates
    if not scored:
        return available_services, None

    all_costs = [s["cost_estimate"] for s in scored]
    all_loads = [s["load"] for s in scored]
    tmin = min(all_costs)
    tmax = max(all_costs) if max(all_costs) > min(all_costs) else min(all_costs) + 0.01
    lmin = min(all_loads)
    lmax = max(all_loads) if max(all_loads) > min(all_loads) else max(all_loads) + 1

    pheromone_weight = float(os.getenv("CNP_PHEROMONE_WEIGHT", "0.15"))
    if not pheromone_enabled:
        pheromone_weight = 0.0

    cap_weight = float(os.getenv("CNP_CAPABILITY_WEIGHT", "0.25"))

    for entry in scored:
        bid_score_val = score_bid(
            trust=entry["trust_score"],
            cost=entry["cost_estimate"],
            load=entry["load"],
            tmin=tmin, tmax=tmax,
            lmin=lmin, lmax=lmax,
            failure_penalty_score=entry["failure_penalty"],
        )
        # Blend capability_score (includes tag matching) into bid
        cap_score = entry.get("capability_score", 0.5)
        bid_score_val = bid_score_val * (1.0 - cap_weight - pheromone_weight) + \
                        cap_score * cap_weight + \
                        entry["pheromone_intensity"] * pheromone_weight
        entry["bid_score"] = bid_score_val

        logger.debug(
            "[CNP-score] %s: base=%.3f cap=%.3f(w=%.2f) phero=%.3f(w=%.2f) -> final=%.4f",
            entry["agent"].get("service_name"), bid_score_val,
            cap_score, cap_weight, entry["pheromone_intensity"], pheromone_weight,
            entry["bid_score"],
        )

    scored.sort(key=lambda x: x["bid_score"], reverse=True)

    # Epsilon-greedy exploration: occasionally pick a random agent
    _epsilon = float(os.getenv("CNP_EPSILON_GREEDY", "0.05"))
    if len(scored) > 1 and random.random() < _epsilon:
        _swap_idx = random.randint(1, len(scored) - 1)
        scored[0], scored[_swap_idx] = scored[_swap_idx], scored[0]
        logger.info(
            "[CNP-resolve] Epsilon-greedy exploration: swapped rank-1 with %s",
            scored[0]["agent"].get("service_name"),
        )

    bid_ttl_seconds = int(os.getenv("CNP_BID_TTL_SECONDS", "86400"))
    persisted_bid_ids: Dict[str, str] = {}

    # Persist bids to graph store before building the evaluation sequence.
    if graph_store:
        for e in scored:
            try:
                bidder_id = str(e["agent"].get("agent_id", ""))
                bid = BlueprintBid(
                    task_id=task_id,
                    bidder_id=bidder_id,
                    bid_score=e["bid_score"],
                    rationale="inline-cnp-resolve",
                    pipeline_id=pipeline_id,
                    pipeline_run_id=pipeline_run_id,
                    ttl_seconds=bid_ttl_seconds,
                    task_priority=params.get("priority"),
                    task_stimulus=params.get("stimulus"),
                    task_reward=params.get("reward"),
                    capability_score=e.get("capability_score"),
                    cost_estimate=e.get("cost_estimate"),
                    agent_load=e.get("load"),
                    trust_score=e.get("trust_score"),
                )
                persisted_bid_ids[bidder_id] = graph_store.add_bid(username=username, bid=bid)
            except Exception:
                logger.debug("Failed to persist bid", exc_info=True)

    eval_seq = EvaluationSequence(
        task_id=task_id,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
        plan_id=pipeline_id,
        candidates=[
            {
                "bidder_id": str(e["agent"].get("agent_id", "")),
                "service_name": e["agent"].get("service_name", ""),
                "bid_score": round(e["bid_score"], 4),
                "bid_id": persisted_bid_ids.get(str(e["agent"].get("agent_id", ""))),
                "trust_score": round(e["trust_score"], 4),
                "cost_estimate": round(e["cost_estimate"], 4),
                "capability_score": round(e.get("capability_score", 0), 4),
                "pheromone_intensity": round(e.get("pheromone_intensity", 0), 4),
            }
            for e in scored
        ],
    )

    if graph_store:
        try:
            graph_store.save_evaluation_sequence(username=username, seq=eval_seq)
        except Exception:
            logger.debug("Failed to persist EvaluationSequence", exc_info=True)

    winner = scored[0]
    winner_agent_id = str(winner["agent"].get("agent_id", ""))
    winner_service = winner["agent"].get("service_name", available_services)
    if graph_store:
        try:
            from agentcy.pydantic_models.multi_agent_pipeline import ContractAward

            award = ContractAward(
                task_id=task_id,
                bidder_id=winner_agent_id,
                bid_id=persisted_bid_ids.get(winner_agent_id),
                cfp_id=eval_seq.cfp_id,
                pipeline_id=pipeline_id,
                pipeline_run_id=pipeline_run_id,
            )
            graph_store.add_contract_award(username=username, award=award)
        except Exception:
            logger.debug("Failed to persist initial contract award", exc_info=True)
    logger.info(
        "[CNP-resolve] Winner for '%s': %s (score=%.4f, trust=%.4f, pheromone=%.4f) "
        "over %d candidates",
        task_id, winner_service, winner["bid_score"],
        winner["trust_score"], winner.get("pheromone_intensity", 0),
        len(scored),
    )

    return winner_service, eval_seq
from agentcy.pipeline_orchestrator.pub_sub.pub_wrapper import get_dynamic_names_from_config
from agentcy.agent_runtime.tracker import PipelineRunTracker
from agentcy.agent_runtime.registry_client import get_registry_client
from agentcy.pipeline_orchestrator.ports import MessageBus
from agentcy.semantic.execution_recorder import record_execution, record_data_flow

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

def enforce_raw_output_structure(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> Dict[str, Any]:
        result = await func(*args, **kwargs)
        if not isinstance(result, dict):
            logger.error("Function %s did not return a dict, got %s", func.__name__, type(result))
            raise ValueError("Business logic function must return a dictionary.")
        
        # Check that the dict has exactly one key: "raw_output"
        if set(result.keys()) != {"raw_output"}:
            logger.error("Returned dict keys from %s are %s; expected exactly {'raw_output'}", func.__name__, result.keys())
            raise ValueError("Returned dictionary must have exactly one key: 'raw_output'.")
        
        # Ensure that the value of "raw_output" is a non-empty string.
        raw_output_value = result["raw_output"]
        if not isinstance(raw_output_value, str) or not raw_output_value.strip():
            logger.error("The value for 'raw_output' must be a non-empty string, got: %s", raw_output_value)
            raise ValueError("The 'raw_output' key must have a non-empty string value.")
        
        logger.info("Function %s returned valid raw_output: %s", func.__name__, result)
        return result
    return wrapper
    
class ForwarderInterface(ABC):
    @abstractmethod
    async def forward(self, message_data: Any, triggered_by: Union[str, List[str]], to_task: str) -> None:
        """
        Forward the given message_data to the appropriate next step.
        
        :param message_data: The message or aggregated data to forward.
        :param triggered_by: A string or list of strings indicating which upstream tasks triggered this event.
        :param to_task: The task id for which this forwarding is intended.
        """
        pass




@retry(stop=stop_after_attempt(3), wait=wait_fixed(1),
       retry=retry_if_exception_type(Exception), reraise=True)
async def enrich_task_state(rm, task: TaskState) -> TaskState:
    """
    Fetch the large‐output blob via DocStore (using output_ref) and
    merge it into the TaskState.data payload.
    """
    logger.info("Enriching task '%s' (run=%s)", task.task_id, task.pipeline_run_id)

    # nothing to do for entry‐messages or if no blob was ever stored
    if not getattr(task, "output_ref", None):
        return task

    # load the blob
    try:
        fetched_output = await rm.doc_store.load(task.output_ref)
    except Exception as e:
        logger.warning("Could not fetch large output for %s: %s", task.output_ref, e)
        return task

    # merge and return a new TaskState
    merged = {**task.data, **fetched_output}
    enriched = task.model_copy(update={"data": merged})
    logger.info("Enriched TaskState: %s", enriched)
    return enriched
enrich_task_state.__module__ = sys.modules[__name__]

@retry(stop=stop_after_attempt(3), wait=wait_fixed(1.0), retry=retry_if_exception_type(Exception))
async def call_persistence_with_retry(
    rm,
    base_state: TaskState,
    raw_output: dict
) -> TaskState:
    """
    Call persist_output_and_update_task with retries.
    """
    return await persist_output_and_update_task(rm, base_state, raw_output)
persist_output_and_update_task.__module__ = sys.modules[__name__]

@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1.0),
    retry=retry_if_exception_type(
        (aio_pika.exceptions.AMQPError, aiormq.exceptions.ChannelInvalidStateError)
    ),
)
async def call_publish_with_retry(
    rm,
    service_name: str,
    pipeline_run_id: str,
    username: str,
    pipeline_id: str,
    payload: Any
) -> None:
    try:
        final_cfg = await asyncio.to_thread(
            rm.pipeline_store.get_final_config, username, pipeline_id
        )
    except Exception:
        logging.error("Failed to retrieve pipeline configuration.", exc_info=True)
        raise

    # Collect ALL outgoing edges for this task (not just the first one)
    edge_rbs = [
        c["rabbitmq"]
        for c in final_cfg.get("rabbitmq_configs", [])
        if c.get("task_id") == service_name and "rabbitmq" in c
    ]
    if not edge_rbs:
        msg = f"RabbitMQ configuration not found for service '{service_name}'."
        logging.error("Configuration error: %s", msg)
        raise KeyError(msg)

    # Normalize payload to plain dict once
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()

    envelope = {
        "pipeline_run_id": pipeline_run_id,
        "username":        username,
        "pipeline_id":     pipeline_id,
        "from_task":       service_name,
        "payload":         payload,
    }

    bus: MessageBus = rm.message_bus
    if bus is None:
        raise RuntimeError("ResourceManager has no message_bus attached")

    # Publish one message per edge with per-run routing keys
    for rb in edge_rbs:
        run_queue, routing_key, mapped_type = get_dynamic_names_from_config(rb, pipeline_run_id)
        exchange_name = rb["exchange"]
        exchange_type = mapped_type  # 'topic' (for fanout/topic) or 'direct'

        logger.info("Publish exch=%s type=%s rk=%s", exchange_name, exchange_type, routing_key)
        await bus.publish(
            exchange_name,
            routing_key,
            envelope,
            exchange_type=exchange_type,
        )

# Legacy alias for tests expecting publish_message in this module.
call_publish_with_retry.__module__ = sys.modules[__name__]
publish_message = call_publish_with_retry
publish_message.__module__ = sys.modules[__name__]



from typing import TypedDict

class MicroserviceOutput(TypedDict):
    raw_output: str

MicroserviceLogicFunc = Callable[[TaskState], Awaitable[MicroserviceOutput]]

def _retry_microservice_logic(exc: BaseException) -> bool:
    return not isinstance(exc, (TypeError, asyncio.CancelledError))

@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1.0),
    retry=retry_if_exception(_retry_microservice_logic),
    reraise=True,
)
async def call_microservice_logic_with_retry(
    microservice_logic: MicroserviceLogicFunc,
    message: Any
) -> Any:
    """
    Calls the injected microservice logic with retries. If an exception is raised,
    it will retry up to 3 times waiting 1 second between attempts.
    """
    return await microservice_logic(
        message
    )



async def _update_bandit_decision(
    store: Any,
    username: str,
    task_id: str,
    agent_id: str,
    success: bool,
    duration: Optional[float],
    retries: int,
    error: Optional[str],
) -> None:
    """Fire-and-forget: find the matching DecisionRecord, compute reward, update bandit."""
    try:
        if not hasattr(store, "list_decision_records"):
            return
        records, _ = store.list_decision_records(username=username, task_id=task_id)
        # Find most recent record for this agent with no outcome yet
        target = None
        for rec in records:
            if rec.get("chosen_bidder_id") == agent_id and rec.get("outcome") is None:
                target = rec
                break
        if target is None:
            return

        outcome = ExecutionOutcomeBandit(
            success=success,
            latency_seconds=duration,
            retries=retries,
        )
        reward = compute_reward(outcome)

        decision_id = target.get("decision_id", "")
        store.update_decision_outcome(
            username=username,
            task_id=task_id,
            decision_id=decision_id,
            outcome=outcome,
            reward=reward,
        )

        # Update the bandit model
        chosen_features_raw = target.get("chosen_features")
        task_type = target.get("task_type", "general")
        if chosen_features_raw:
            features = BidFeatures.model_validate(chosen_features_raw)
            learner = BanditLearner(store, username)
            learner.record_reward(task_type, features, reward)
    except Exception:
        logger.debug("_update_bandit_decision failed", exc_info=True)


class DefaultForwarder(ForwarderInterface):
    def __init__(self, rm, microservice_logic: Optional[MicroserviceLogicFunc] = None):
        self.rm = rm
        self.run_microservice_logic = microservice_logic
        try:
            self.tracker = PipelineRunTracker(resource_manager=self.rm)
        except AttributeError:
            self.tracker = None

    async def forward(self, message_data: Any, triggered_by: Union[str, List[str]], to_task: str) -> None:
        """
        This default forwarder is automatically called by the consumer manager when fan-in is complete.
        It now supports three inputs:
        • TaskState (normal)
        • EntryMessage (entry fan-out)
        • Aggregated dict-of-envelopes from the fan-in aggregator
        """
        logger.info("→ forward() called: to_task=%s, triggered_by=%s", to_task, triggered_by)

        base_state: Optional[TaskState] = None

        # ─────────────────────────────────────────────────────────────────────────
        # A) Fan-in aggregated payload (dict-of-envelopes)
        #    Expect shape: { "<from_task>": {"pipeline_run_id":..., "username":..., "pipeline_id":..., "payload": {...}}, ... }
        # ─────────────────────────────────────────────────────────────────────────
        if (
            isinstance(message_data, dict)
            and message_data
            and all(isinstance(v, dict) for v in message_data.values())
            and "pipeline_run_id" in next(iter(message_data.values()), {})
        ):
            logger.info("Detected aggregated payload for to_task=%s", to_task)
            any_env = next(iter(message_data.values()))
            run_id   = any_env.get("pipeline_run_id")
            username = any_env.get("username")
            pipeline_id = any_env.get("pipeline_id")

            if not (run_id and username and pipeline_id):
                logger.error("Aggregated payload missing run context (run_id/username/pipeline_id). Dropping.")
                return

            try:
                final_cfg = await asyncio.to_thread(
                    self.rm.pipeline_store.get_final_config, username, pipeline_id
                )
                tmeta = final_cfg["task_dict"].get(to_task)
                if not tmeta:
                    logger.error("Unknown to_task '%s' for pipeline %s", to_task, pipeline_id)
                    return
            except Exception as e:
                logger.exception("Failed loading pipeline config for aggregated forward: %s", e)
                return

            # Optional: recover pipeline_config_id from the ephemeral run doc
            cfg_id = None
            try:
                run_doc = self.rm.ephemeral_store.read_run(username, pipeline_id, run_id)
                if run_doc:
                    cfg_id = run_doc.get("pipeline_config_id")
            except Exception as e:
                logger.warning("Could not fetch pipeline_config_id from run doc: %s", e)

            aggregated_payloads = {k: v.get("payload") for k, v in message_data.items()}
            base_state = TaskState(
                status=TaskStatus.RUNNING,
                attempts=0,
                task_id=to_task,
                username=username,
                pipeline_id=pipeline_id,
                pipeline_run_id=run_id,
                pipeline_config_id=cfg_id,
                service_name=tmeta["available_services"],
                is_final_task=not tmeta.get("inferred_outputs"),
                data={"aggregated": aggregated_payloads, "triggered_by": triggered_by},
                error=None,
                result=None,
                output_ref=None,
                last_updated=datetime.now(),
            )

        # ─────────────────────────────────────────────────────────────────────────
        # B) Not aggregated → use the existing parser (TaskState or EntryMessage)
        # ─────────────────────────────────────────────────────────────────────────
        else:
            try:
                parsed = AbstractMessageParser.parse(message_data)
                logger.info("Parsed message_data into %s", type(parsed).__name__)
                #logger.info("Parsed content: %s", parsed)
            except Exception as e:
                logger.error("Message_data must be a BaseModel: %s", e)
                return

            if isinstance(parsed, TaskState) or getattr(parsed, "__class__", None).__name__ == "TaskState":
                if not isinstance(parsed, TaskState):
                    try:
                        data = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed.dict()
                        parsed = TaskState.model_validate(data)
                    except Exception:
                        logger.error("Unsupported TaskState type in forward(): %s", type(parsed))
                        return
                logger.info("Enriching task message for task '%s', run '%s'", parsed.task_id, parsed.pipeline_run_id)
                #logger.info("Raw TaskState before enrichment: %s", parsed)
                try:
                    enriched = await enrich_task_state(self.rm, parsed)
                    #logger.info("Enrichment succeeded for %s: %s", parsed.task_id, enriched)
                    base_state = enriched
                except Exception as enrich_error:
                    # Short-circuit on enrichment failure
                    logger.error("❌ Error during enrichment for task '%s': %s", parsed.task_id, enrich_error, exc_info=True)
                    failed = parsed.model_copy(update={
                        "error": str(enrich_error),
                        "status": TaskStatus.FAILED,
                        "last_updated": datetime.now()
                    })
                    # Persist and publish FAILED immediately, then return.
                    try:
                        final_task_state = await call_persistence_with_retry(self.rm, failed, {"raw_output": "error"})
                    except Exception as persist_error:
                        logger.error("Error during persistence for failed task '%s': %s", failed.task_id, persist_error)
                        final_task_state = failed
                    try:
                        self.tracker.on_task_done(final_task_state)
                    except Exception as tracker_error:
                        logger.error("Error triggering PipelineRunTracker for failed task '%s': %s", final_task_state.task_id, tracker_error)
                    try:
                        await call_publish_with_retry(
                            rm=self.rm,
                            service_name=to_task,
                            pipeline_run_id=final_task_state.pipeline_run_id,
                            username=final_task_state.username,
                            pipeline_id=final_task_state.pipeline_id,
                            payload=final_task_state
                        )
                        logger.info("✅ Published FAILED TaskState for task '%s' in run '%s'", final_task_state.task_id, final_task_state.pipeline_run_id)
                    except RetryError as publish_error:
                        logger.error("Publishing FAILED TaskState failed after retries for task '%s': %s", final_task_state.task_id, publish_error)
                    return

            elif isinstance(parsed, EntryMessage) or getattr(parsed, "__class__", None).__name__ == "EntryMessage":
                if not isinstance(parsed, EntryMessage):
                    try:
                        data = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed.dict()
                        parsed = EntryMessage.model_validate(data)
                    except Exception:
                        logger.error("Unsupported EntryMessage type in forward(): %s", type(parsed))
                        return
                logger.info("Bootstrapping TaskState for entry task '%s' (run=%s)", to_task, parsed.pipeline_run_id)
                tmeta = None
                if getattr(self.rm, "pipeline_store", None):
                    try:
                        final_cfg = await asyncio.to_thread(
                            self.rm.pipeline_store.get_final_config, parsed.username, parsed.pipeline_id
                        )
                        tmeta = final_cfg["task_dict"].get(to_task)
                    except Exception as e:
                        logger.exception("Failed to load pipeline config to bootstrap entry TaskState: %s", e)
                if tmeta is None:
                    tmeta = {"available_services": to_task, "inferred_outputs": []}

                # Populate entry task data with the task description from the
                # pipeline config so agents receive a meaningful prompt.
                entry_data = {}
                if tmeta.get("description"):
                    entry_data["description"] = tmeta["description"]

                base_state = TaskState(
                    status=TaskStatus.RUNNING,
                    attempts=0,
                    task_id=to_task,
                    username=parsed.username,
                    pipeline_id=parsed.pipeline_id,
                    pipeline_run_id=parsed.pipeline_run_id,
                    pipeline_config_id=getattr(parsed, "pipeline_config_id", None),
                    service_name=tmeta["available_services"],
                    is_final_task=not tmeta.get("inferred_outputs"),
                    data=entry_data,
                    error=None,
                    result=None,
                    output_ref=None,
                    last_updated=datetime.now(),
                )

            else:
                logger.error("Unsupported message type in forward(): %s", type(parsed))
                return

        # ─────────────────────────────────────────────────────────────────────────
        # Common TaskState path (aggregated or parsed)
        # ─────────────────────────────────────────────────────────────────────────
        if isinstance(base_state, TaskState):
            base_state = base_state.model_copy(update={"status": TaskStatus.RUNNING, "last_updated": datetime.now()})

        final_payload = base_state
        logger.info("Final payload ready for microservice logic: task='%s', run='%s'", to_task, final_payload.pipeline_run_id)
        #logger.info("Final payload details: %s", final_payload)

        # Persist task input for observability and potential cross-service re-dispatch
        if isinstance(final_payload, TaskState) and getattr(self.rm, 'ephemeral_store', None):
            try:
                self.rm.ephemeral_store.store_task_output(
                    username=final_payload.username,
                    task_id=f"input_{to_task}",
                    run_id=final_payload.pipeline_run_id,
                    data=final_payload.model_dump(),
                )
            except Exception:
                logger.debug("Failed to persist task input for %s", to_task, exc_info=True)

        # ── Data Flow Recorder (fire-and-forget) ────────────────────────────
        if isinstance(base_state, TaskState) and triggered_by:
            try:
                _graph_store = getattr(self.rm, 'graph_marker_store', None)
                _upstream_tasks = [triggered_by] if isinstance(triggered_by, str) else list(triggered_by)
                _payload_size = None
                _payload_fields = None
                if base_state.data and isinstance(base_state.data, dict):
                    import json as _json
                    try:
                        _payload_size = len(_json.dumps(base_state.data))
                        _payload_fields = list(base_state.data.keys())
                    except Exception:
                        pass
                for _upstream in _upstream_tasks:
                    if _upstream and _upstream not in ("api", "entry", "unit"):
                        asyncio.ensure_future(record_data_flow(
                            from_task=_upstream,
                            to_task=to_task,
                            plan_id=getattr(base_state, 'pipeline_id', ''),
                            pipeline_run_id=base_state.pipeline_run_id,
                            username=base_state.username,
                            payload_size_bytes=_payload_size,
                            payload_fields=_payload_fields,
                            graph_marker_store=_graph_store,
                        ))
            except Exception:
                logger.debug("Data flow recorder call failed", exc_info=True)

        # ── CNP inline resolution / baseline routing for competing tasks ──────
        _cnp_eval_seq = None
        _cnp_winner_entry = None
        if isinstance(final_payload, TaskState):
            _current_svc = final_payload.service_name or to_task
            _registry = getattr(self.rm, "agent_registry_store", None)
            _cnp_enabled = os.getenv("CNP_MANAGER_ENABLE", "0") == "1"
            _assignment_baseline = (os.getenv("AGENT_ASSIGNMENT_BASELINE", "direct") or "direct").strip().lower()
            _cnp_cap = None
            _competing_services: list[str] = []
            if _registry:
                _cnp_cap, _competing_services = _find_shared_capability(
                    _registry,
                    username=final_payload.username,
                    service_name=_current_svc,
                )

            if _cnp_cap and _cnp_enabled:
                _task_data = final_payload.data if isinstance(final_payload.data, dict) else {}
                try:
                    _pcfg2 = await asyncio.to_thread(
                        self.rm.pipeline_store.get_final_config,
                        final_payload.username, final_payload.pipeline_id,
                    )
                    _task_desc = _pcfg2.get("task_dict", {}).get(to_task, {}).get("description", "")
                    if _task_desc:
                        _task_data = {**_task_data, "description": _task_desc}
                        logger.info("[CNP-metadata] Found task description for '%s': %s",
                                    to_task, _task_desc[:100])
                    else:
                        dag_tasks = _pcfg2.get("dag", {}).get("tasks", [])
                        for _dt in dag_tasks:
                            if _dt.get("id") == to_task and _dt.get("description"):
                                _task_data = {**_task_data, "description": _dt["description"]}
                                logger.info("[CNP-metadata] Found description in dag.tasks for '%s': %s",
                                            to_task, _dt["description"][:100])
                                break
                        else:
                            logger.info("[CNP-metadata] No description found for '%s'", to_task)
                except Exception as _e:
                    logger.warning("[CNP-metadata] Failed to fetch pipeline config: %s", _e)

                if "[CNP:" not in _task_data.get("description", ""):
                    try:
                        _pipe_name = _pcfg2.get("name", _pcfg2.get("pipeline_name", ""))
                    except Exception:
                        _pipe_name = ""
                    _client_key = ""
                    for _ck in ["freshco", "techparts", "greenleaf", "quickship", "nordicsteel"]:
                        if _ck in _pipe_name.lower():
                            _client_key = _ck
                            break
                    if _client_key:
                        from evaluation.pipeline_templates import CLIENT_CNP_METADATA
                        import json as _json2
                        _cnp_for_task = CLIENT_CNP_METADATA.get(_client_key, {}).get(to_task, {})
                        if _cnp_for_task:
                            _existing_desc = _task_data.get("description", "")
                            _task_data["description"] = f"[CNP:{_json2.dumps(_cnp_for_task)}] {_existing_desc}"
                            logger.info("[CNP-metadata] Injected metadata from pipeline_templates for '%s/%s'",
                                        _client_key, to_task)
                try:
                    resolved_service, _cnp_eval_seq = await _resolve_competing_service(
                        rm=self.rm,
                        task_id=to_task,
                        available_services=_cnp_cap,
                        username=final_payload.username,
                        pipeline_id=final_payload.pipeline_id,
                        pipeline_run_id=final_payload.pipeline_run_id,
                        task_data=_task_data,
                    )
                    if resolved_service != _current_svc:
                        logger.info(
                            "[CNP-resolve] Routing task '%s': %s -> %s (capability=%s)",
                            to_task, _current_svc, resolved_service, _cnp_cap,
                        )
                        final_payload = final_payload.model_copy(update={"service_name": resolved_service})
                        _cnp_winner_entry = _load_agent_entry(resolved_service)
                        if _cnp_winner_entry is not None:
                            logger.info(
                                "[CNP-resolve] Loaded entry point override for %s",
                                resolved_service,
                            )
                    else:
                        logger.info("[CNP-resolve] Task '%s': %s won (stays as default)", to_task, _current_svc)
                except Exception:
                    logger.exception("[CNP-resolve] Failed for task '%s', using default routing", to_task)
            elif _cnp_cap and _assignment_baseline == "round_robin":
                try:
                    resolved_service = _select_round_robin_service(
                        username=final_payload.username,
                        capability=_cnp_cap,
                        service_names=_competing_services,
                    )
                    if resolved_service and resolved_service != _current_svc:
                        logger.info(
                            "[Baseline-route] Task '%s': %s -> %s via round-robin (capability=%s)",
                            to_task, _current_svc, resolved_service, _cnp_cap,
                        )
                        final_payload = final_payload.model_copy(update={"service_name": resolved_service})
                        _cnp_winner_entry = _load_agent_entry(resolved_service)
                except Exception:
                    logger.exception("[Baseline-route] Failed for task '%s', using default routing", to_task)

        # ── CNP re-dispatch loop ──────────────────────────────────────────────
        max_reforwards = int(os.getenv("CNP_MAX_REFORWARDS", "3"))
        reforward_count = 0

        while True:
            registry_client = get_registry_client()
            if registry_client is not None and isinstance(final_payload, TaskState):
                try:
                    await registry_client.mark_task_started(
                        task_id=final_payload.task_id,
                        pipeline_run_id=final_payload.pipeline_run_id,
                        service_name=to_task,
                    )
                except Exception:
                    logger.debug("Agent registry task-start update failed", exc_info=True)

            span_attrs = {
                "agentcy.task_id": getattr(final_payload, "task_id", None),
                "agentcy.pipeline_run_id": getattr(final_payload, "pipeline_run_id", None),
                "agentcy.pipeline_id": getattr(final_payload, "pipeline_id", None),
                "agentcy.username": getattr(final_payload, "username", None),
                "agentcy.service_name": to_task,
                "agentcy.triggered_by": str(triggered_by),
                "agentcy.reforward_count": reforward_count,
            }
            span_attrs = {k: v for k, v in span_attrs.items() if v is not None}
            with _tracer.start_as_current_span("task.execute", attributes=span_attrs) as span:
                try:
                    # Failure injection for experiment 3.4
                    _failure_inject = os.getenv("FAILURE_INJECT_SERVICE", "")
                    _svc_label = final_payload.service_name or to_task
                    if _failure_inject and _svc_label in _failure_inject.split(","):
                        import random as _fi_rand
                        _inject_rate = float(os.getenv("FAILURE_INJECT_RATE", "1.0"))
                        if _fi_rand.random() < _inject_rate:
                            _failure_ts = datetime.utcnow().isoformat() + "Z"
                            logger.warning(
                                "[FailureInject] Injecting failure for service=%s task=%s at=%s",
                                _svc_label, to_task, _failure_ts,
                            )
                            # Persist failure timestamp for RT metric
                            try:
                                self.rm.ephemeral_store.upsert_sub_key(
                                    f"pipeline_run::default::{final_payload.pipeline_id}::{final_payload.pipeline_run_id}",
                                    "failure_injected_at", _failure_ts,
                                )
                            except Exception:
                                logger.debug("Could not persist failure_injected_at")
                            raise RuntimeError(f"INJECTED_FAILURE: service={_svc_label} at={_failure_ts}")

                    # Use CNP-resolved agent entry point if available
                    logic = _cnp_winner_entry if _cnp_winner_entry else self.run_microservice_logic
                    if logic is None:
                        raise RuntimeError("DefaultForwarder has no microservice_logic configured")
                    logger.info("Calling microservice logic for task '%s' (service=%s, cnp_override=%s)",
                                to_task, _svc_label, _cnp_winner_entry is not None)
                    try:
                        raw_output = await call_microservice_logic_with_retry(
                            lambda msg: logic(self.rm, final_payload.pipeline_run_id, to_task, triggered_by, msg),
                            final_payload,
                        )
                    except TypeError:
                        raw_output = await call_microservice_logic_with_retry(
                            lambda msg: logic(msg),
                            final_payload,
                        )
                    _state_update = {
                        "status": TaskStatus.COMPLETED,
                        "last_updated": datetime.now(),
                    }
                    # Persist CNP winner's service_name so it's captured in run data
                    if _cnp_winner_entry and hasattr(final_payload, "service_name"):
                        _state_update["service_name"] = final_payload.service_name
                    base_state = base_state.model_copy(update=_state_update)
                    span.set_attribute("agentcy.task_success", True)
                    span.set_status(Status(StatusCode.OK))

                    # Deposit pheromone marker for the winning agent after success
                    if _cnp_eval_seq and hasattr(final_payload, "service_name") and _env_enabled("PHEROMONE_ENABLE", True):
                        try:
                            from agentcy.pydantic_models.multi_agent_pipeline import AffordanceMarker
                            _graph_store = getattr(self.rm, "graph_marker_store", None)
                            if _graph_store:
                                _winner_svc = final_payload.service_name
                                # Find the winner's agent_id from the eval sequence
                                _winner_entry = next(
                                    (c for c in _cnp_eval_seq.candidates
                                     if c.get("service_name") == _winner_svc),
                                    None,
                                )
                                _winner_agent_id = _winner_entry.get("bidder_id", _winner_svc) if _winner_entry else _winner_svc
                                # Determine capability from the agent type
                                _cap = to_task  # default
                                if any("warehouse" in str(c.get("service_name", "")) for c in _cnp_eval_seq.candidates):
                                    _cap = "warehouse_matching"
                                elif any(str(c.get("service_name", "")) in ("cost-estimator", "speed-estimator")
                                         for c in _cnp_eval_seq.candidates):
                                    _cap = "deal_estimation"
                                # Additive pheromone: read existing, add delta, cap at 1.0
                                _existing_intensity = 0.0
                                try:
                                    _existing_markers, _ = _graph_store.list_affordance_markers(
                                        username=final_payload.username,
                                        agent_id=_winner_agent_id,
                                        task_id=to_task,
                                    )
                                    for _em in (_existing_markers or []):
                                        _ei = _em.get("intensity", 0) if isinstance(_em, dict) else getattr(_em, "intensity", 0)
                                        _existing_intensity = max(_existing_intensity, float(_ei))
                                except Exception:
                                    pass

                                _deposit_delta = float(os.getenv("PHEROMONE_DEPOSIT_DELTA", "0.2"))
                                _new_intensity = min(_existing_intensity + _deposit_delta, 1.0)

                                marker = AffordanceMarker(
                                    task_id=to_task,
                                    agent_id=_winner_agent_id,
                                    capability=_cap,
                                    intensity=_new_intensity,
                                    rationale=f"success:{to_task}:{final_payload.pipeline_run_id[:8]}",
                                    pipeline_id=final_payload.pipeline_id,
                                    pipeline_run_id=final_payload.pipeline_run_id,
                                    ttl_seconds=86400,
                                )
                                _graph_store.add_affordance_marker(
                                    username=final_payload.username,
                                    marker=marker,
                                )
                                logger.info(
                                    "[Pheromone] Deposited marker: agent=%s cap=%s intensity=%.2f (was %.2f +%.2f) task=%s",
                                    _winner_agent_id, _cap, _new_intensity,
                                    _existing_intensity, _deposit_delta, to_task,
                                )
                        except Exception:
                            logger.debug("[Pheromone] Failed to deposit marker", exc_info=True)

                    logger.info("Microservice logic returned raw_output: %s", raw_output)
                except Exception as e:
                    logger.error("Error in microservice logic …: %s", e)
                    span.record_exception(e)
                    span.set_attribute("agentcy.task_success", False)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    # failure → minimal payload and FAILED status
                    raw_output = {"raw_output": "error"}
                    base_state = base_state.model_copy(update={
                        "status": TaskStatus.FAILED,
                        "last_updated": datetime.now()
                    })

            if registry_client is not None and isinstance(final_payload, TaskState):
                try:
                    await registry_client.mark_task_finished(
                        task_id=final_payload.task_id,
                        pipeline_run_id=final_payload.pipeline_run_id,
                        service_name=to_task,
                        success=base_state.status == TaskStatus.COMPLETED,
                    )
                except Exception:
                    logger.debug("Agent registry task-finish update failed", exc_info=True)

            if isinstance(base_state, TaskState):
                logger.info("Persisting output for task '%s'", base_state.task_id)
                try:
                    final_task_state = await call_persistence_with_retry(self.rm, base_state, raw_output)
                except Exception as persist_error:
                    final_task_state = base_state.model_copy(update={
                        "error": f"Persistence error: {persist_error}",
                        "status": TaskStatus.FAILED,
                        "last_updated": datetime.now()
                    })

                # ── Execution Recorder (fire-and-forget) ──
                try:
                    _duration = None
                    if hasattr(final_task_state, 'started_at') and final_task_state.started_at:
                        _duration = (datetime.now() - final_task_state.started_at).total_seconds()
                    _graph_store = getattr(self.rm, 'graph_marker_store', None)
                    asyncio.ensure_future(record_execution(
                        task_id=final_task_state.task_id,
                        agent_id=getattr(final_task_state, 'service_name', to_task),
                        plan_id=getattr(final_task_state, 'pipeline_id', ''),
                        pipeline_run_id=final_task_state.pipeline_run_id,
                        username=final_task_state.username,
                        status="completed" if final_task_state.status == TaskStatus.COMPLETED else "failed",
                        attempt_number=reforward_count + 1,
                        duration_seconds=_duration,
                        error=final_task_state.error,
                        graph_marker_store=_graph_store,
                    ))
                except Exception:
                    logger.debug("Execution recorder call failed", exc_info=True)

                # ── Bandit decision update (fire-and-forget) ──
                if _bandit_enabled() and _graph_store is not None:
                    try:
                        asyncio.ensure_future(_update_bandit_decision(
                            store=_graph_store,
                            username=final_task_state.username,
                            task_id=final_task_state.task_id,
                            agent_id=getattr(final_task_state, 'service_name', to_task),
                            success=final_task_state.status == TaskStatus.COMPLETED,
                            duration=_duration,
                            retries=reforward_count,
                            error=final_task_state.error,
                        ))
                    except Exception:
                        logger.debug("Bandit decision update call failed", exc_info=True)

                # Check for CNP re-forward on failure
                reforward_info = None
                if self.tracker and final_task_state.status == TaskStatus.FAILED:
                    try:
                        reforward_info = self.tracker.on_task_done(final_task_state)
                    except Exception as tracker_error:
                        logger.error("Error triggering PipelineRunTracker for task '%s': %s",
                                     final_task_state.task_id, tracker_error)
                elif self.tracker:
                    try:
                        self.tracker.on_task_done(final_task_state)
                    except Exception as tracker_error:
                        logger.error("Error triggering PipelineRunTracker for task '%s': %s",
                                     final_task_state.task_id, tracker_error)

                # Re-dispatch if tracker returned re-forward info
                if reforward_info is not None and reforward_count < max_reforwards:
                    reforward_count += 1
                    logger.info(
                        "[Forwarder] CNP re-dispatch #%d for task %s: agent %s -> %s (score=%.3f, seq=%d)",
                        reforward_count, to_task,
                        getattr(final_task_state, 'service_name', '?'),
                        reforward_info.new_agent_id,
                        reforward_info.bid_score,
                        reforward_info.sequence_index,
                    )
                    # Reset base_state for retry (original input stays in final_payload)
                    base_state = final_payload.model_copy(update={
                        "status": TaskStatus.RUNNING,
                        "error": None,
                        "service_name": reforward_info.new_service,
                        "last_updated": datetime.now(),
                    })
                    continue  # retry microservice_logic with new agent

                if reforward_info is not None:
                    logger.warning(
                        "[Forwarder] CNP re-forward cap reached (%d) for task %s; publishing FAILED downstream",
                        max_reforwards, to_task,
                    )
            else:
                final_task_state = base_state

            break  # exit re-dispatch loop: success, exhausted, or cap reached
        # ── end CNP re-dispatch loop ──────────────────────────────────────────

        try:
            if not isinstance(final_task_state, TaskState):
                logger.error("final_task_state is not TaskState (%s); cannot publish", type(final_task_state).__name__)
                return
            logger.info("Publishing final state for task='%s'", getattr(final_task_state, "task_id", "entry"))

            try:
                await publish_message(
                    self.rm,
                    to_task,
                    final_task_state.pipeline_run_id,
                    final_task_state.username,
                    final_task_state.pipeline_id,
                    final_task_state,
                )
            except TypeError:
                cfg_id = getattr(final_task_state, "pipeline_config_id", None) or ""
                await publish_message(
                    self.rm,
                    to_task,
                    final_task_state.pipeline_run_id,
                    cfg_id,
                    final_task_state,
                )
            logger.info("Published TaskState for task '%s' in run '%s'",
                        getattr(final_task_state, "task_id", "entry"),
                        final_task_state.pipeline_run_id)
        except RetryError as publish_error:
            logger.error("Publishing failed after retries for task '%s': %s",
                        getattr(final_task_state, "task_id", "entry"), publish_error)
