#src/agentcy/agent_runtime/tracker.py

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional, Set


from aio_pika import ExchangeType, Message
from agentcy.pipeline_orchestrator.pub_sub.control_channels import control_channel_names
from jsonschema import ValidationError
try:
    from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import PipelineRun, TaskState, TaskStatus, PipelineStatus
except ImportError:
    from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import PipelineRun, TaskState, TaskStatus, PipelineStatus
from datetime import datetime, timezone

from collections import OrderedDict
from threading import RLock
from typing import Dict
import time


@dataclass
class ReforwardInfo:
    """Returned by on_task_done when a failed task was re-assigned to a new agent."""
    task_id: str
    new_service: str
    new_agent_id: str
    sequence_index: int
    bid_score: float

logger = logging.getLogger(__name__)



# 1) Reuse or define your 'send_stop_command' function
async def send_stop_command(
    rm,
    service_name: str,
    username: str,
    pipeline_id: str,
    pipeline_run_id: str,
) -> None:
    """
    Publish a 'stop' control message for a specific service/pipeline run.
    Uses ResourceManager.rabbit_mgr to acquire an aio-pika channel.
    """
    exchange_name, _, routing_key = control_channel_names(
        service_name=service_name,
        username=username,
        pipeline_id=pipeline_id,
        pipeline_run_id=pipeline_run_id,
    )
    payload       = {"command": "stop"}

    async with rm.rabbit_mgr.get_channel() as channel:
        exchange = await channel.declare_exchange(
            exchange_name,
            ExchangeType.DIRECT,
            durable=True,
        )
        await exchange.publish(
            Message(
                body=json.dumps(payload).encode("utf-8"),
                content_type="application/json",
            ),
            routing_key=routing_key,
        )

    logger.info(
        "Stop-command sent   run=%s   svc=%s   exch=%s",
        pipeline_run_id,
        service_name,
        exchange_name,
    )

def _now() -> float:
    # Prefer a monotonic clock; fall back if a weird environment lacks it
    if hasattr(time, "monotonic"):
        return time.monotonic()
    if hasattr(time, "perf_counter"):
        return time.perf_counter()
    # Last resort: wall clock (not ideal, but preserves functionality)
    return time.time()

class _LRUSet:
    """
    Thread-safe LRU+TTL set for deduping event keys.
    Uses a monotonic-like clock for TTL so it’s immune to wall-clock jumps.
    Stores only relative times; no timezone concerns here.
    """
    def __init__(self, max_items: int = 4096, ttl_seconds: int = 3600):
        self._max = int(max_items)
        self._ttl = float(ttl_seconds)
        self._data: "OrderedDict[str, float]" = OrderedDict()
        self._lock = RLock()

    def _purge_expired(self, now: float) -> None:
        if self._ttl <= 0:
            return
        # Expire from LRU side
        while self._data:
            _, ts = next(iter(self._data.items()))
            if (now - ts) <= self._ttl:
                break
            self._data.popitem(last=False)

    def contains(self, key: str) -> bool:
        now = _now()
        with self._lock:
            self._purge_expired(now)
            if key in self._data:
                # refresh recency and timestamp
                self._data.move_to_end(key)
                self._data[key] = now
                return True
            return False

    def add(self, key: str) -> None:
        now = _now()
        with self._lock:
            self._purge_expired(now)
            self._data[key] = now
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class PipelineRunTracker:
    def __init__(self, resource_manager, max_retries=3):
        self.resource_manager = resource_manager
        self.max_retries = max_retries
        mgr = (
            getattr(self.resource_manager, "ephemeral_store", None)
            or getattr(self.resource_manager, "ephemeral_pipeline_doc_manager", None)
        )
        # must support read_run/write_run for tracker logic
        if mgr and hasattr(mgr, "read_run"):
            self.pipeline_doc_manager = mgr
        else:
            raise AttributeError("ResourceManager missing ephemeral_store/ephemeral_pipeline_doc_manager")
        # single-process safety
        self._run_locks: Dict[str, RLock] = {}
        # dedupe cache per run
        self._dedupe: Dict[str, _LRUSet] = {}
    
    _ORDER = {
        TaskStatus.PENDING: 0,
        TaskStatus.RUNNING: 1,
        TaskStatus.COMPLETED: 2,
        TaskStatus.FAILED: 3,
        # Unknowns map to 0
    }
    
    @staticmethod
    def _to_enum(val) -> Optional[TaskStatus]:
        """Convert any value to TaskStatus; None if unknown/invalid."""
        if isinstance(val, TaskStatus):
            return val

        # Handle enum from a different import path (src.agentcy vs agentcy)
        raw = getattr(val, "value", None) or str(val)
        for cand in (raw, raw.capitalize(), raw.upper()):
            try:
                return TaskStatus(cand)
            except ValueError:
                continue
        return None
    
    @staticmethod
    def _is_terminal(status: TaskStatus) -> bool:
        return status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
    
    def _forward_only(self, prev: TaskStatus, new: TaskStatus) -> bool:
        a = self._ORDER.get(prev, 0)
        b = self._ORDER.get(new, 0)
        return b >= a
    
    def _event_key(self, t: TaskState) -> str:
        # Robust key: task_id + status + attempt + (optional) seq/updated_at
        att = getattr(t, "attempts", getattr(t, "attempt", 0))
        seq = getattr(t, "sequence", 0)
        uat = getattr(t, "updated_at", None)
        base = f"{t.task_id}:{self._to_enum(t.status)}:{att}:{seq}"
        if uat:
            return base + f":{uat}"
        return base
    
    def _get_lock(self, run_id: str) -> RLock:
        lock = self._run_locks.get(run_id)
        if lock is None:
            lock = RLock()
            self._run_locks[run_id] = lock
        return lock
    

    def _deduper(self, run_id: str) -> _LRUSet:
        d = self._dedupe.get(run_id)
        if d is None:
            d = _LRUSet()
            self._dedupe[run_id] = d
        return d
    
    def _derive_finals_from_cfg(
        self, username: str, pipeline_id: str
    ) -> Set[str]:
        """
        Try to derive finals (zero out-degree) from the persisted pipeline config.
        We look (in order) for 'task_outputs', 'task_graph', then 'execution_order'.
        """
        try:
            # Prefer a proper store if ResourceManager exposes one.
            cfg = None
            ps = getattr(self.resource_manager, "pipeline_store", None) or getattr(
                self.resource_manager, "persistent_store", None
            )
            if ps:
                # common method names across codebases
                for meth in (
                    "get_pipeline_config",
                    "read_pipeline_config",
                    "get",
                    "read",
                ):
                    if hasattr(ps, meth):
                        cfg = getattr(ps, meth)(username=username, pipeline_id=pipeline_id)  # type: ignore
                        break

            # Some codebases keep the cfg under a known doc key; tolerate dict or object
            if not cfg:
                return set()

            if isinstance(cfg, dict):
                data = cfg
            else:
                # pydantic / attr models – try attr / dict()
                data = getattr(cfg, "model_dump", lambda: {})()

            # 1) explicit task_outputs: {task_id: set/list of downstream tasks}
            task_outputs = data.get("task_outputs")
            if isinstance(task_outputs, dict):
                finals = {tid for tid, outs in task_outputs.items() if not outs}
                if finals:
                    return finals
                logger.info("[Tracker] finals candidates for %s", sorted(finals))
            # 2) task_graph: {task_id: [downstreams]}
            task_graph = data.get("task_graph")
            if isinstance(task_graph, dict):
                finals = {tid for tid, outs in task_graph.items() if not outs}
                logger.info("[Tracker] finals candidates for %s", sorted(finals))
                if finals:
                    return finals

            # 3) execution_order: assume the last is a final
            eo = data.get("execution_order")
            if isinstance(eo, (list, tuple)) and eo:
                return {eo[-1]}

        except Exception as exc:
            logger.warning("[Tracker] unable to derive finals from cfg: %s", exc)

        return set()

  

    def _finals(self, pipeline_run: PipelineRun, username: str, pipeline_id: str) -> Set[str]:
        finals = set(getattr(pipeline_run, "final_task_ids", []) or [])
        if finals:
            logger.info("[Tracker] finals (from run): %s", sorted(finals))
            return finals

        # fallback (older runs / safety)
        hinted = {tid for tid, t in pipeline_run.tasks.items() if getattr(t, "is_final_task", False)}
        if hinted:
            logger.info("[Tracker] finals (from hints): %s", sorted(hinted))
            return hinted

        derived = self._derive_finals_from_cfg(username, pipeline_id)
        if derived:
            logger.info("[Tracker] finals (derived): %s", sorted(derived))
            return derived

        return set()



    def on_task_done(self, updated_task: TaskState) -> Optional[ReforwardInfo]:
        """
        Handle a TaskCompleted / TaskFailed event.

        Steps:
        1) Validate, normalize status to enum, dedupe events
        2) Replace stored TaskState (forward-only)
        3) If any task FAILS → attempt re-forwarding, then fail run
           Else if *all finals* COMPLETE → run COMPLETED
        4) Persist run
        5) If RUNNING → terminal transition happened, emit stop per service

        Returns:
            ReforwardInfo if the task was re-forwarded to a new agent
            (caller should retry execution), None otherwise.
        """
        run_id = updated_task.pipeline_run_id
        task_id = updated_task.task_id
        username = updated_task.username
        pipeline_id = updated_task.pipeline_id
        reforward_result: Optional[ReforwardInfo] = None

        enum_status = self._to_enum(updated_task.status)
        if enum_status is None:
            logger.error(
                "[Tracker] unknown status '%s' on task '%s' – dropping",
                updated_task.status, task_id
            )
            return None

        if enum_status is not updated_task.status:
            updated_task = updated_task.model_copy(update={"status": enum_status})

        # Dedupe (best-effort)
        ev_key = self._event_key(updated_task)
        d = self._deduper(run_id)
        if d.contains(ev_key):
            logger.info("[Tracker] duplicate event dropped run=%s task=%s key=%s", run_id, task_id, ev_key)
            return None

        lock = self._get_lock(run_id)
        with lock:
            # fetch current run
            raw_doc = self.pipeline_doc_manager.read_run(
                username=username, pipeline_id=pipeline_id, run_id=run_id
            )
            if not raw_doc:
                logger.error("[Tracker] run doc not found for %s", run_id)
                return None

            try:
                pipeline_run = PipelineRun.model_validate(raw_doc)
            except ValidationError as err:
                logger.error("[Tracker] invalid pipeline-run doc: %s", err)
                return None

            logger.info(
                "[Tracker] event: run=%s task=%s status=%s",
                run_id, task_id, enum_status
            )

            # sanity
            if task_id not in pipeline_run.tasks:
                logger.warning("[Tracker] task %s missing in run %s – ignoring", task_id, run_id)
                return None

            prev_task = pipeline_run.tasks[task_id]
            prev_status = self._to_enum(prev_task.status) or TaskStatus.PENDING

            # forward-only
            if not self._forward_only(prev_status, enum_status):
                logger.info(
                    "[Tracker] regressive transition dropped run=%s task=%s %s→%s",
                    run_id, task_id, prev_status, enum_status
                )
                return None


            # apply
            pipeline_run.tasks[task_id] = updated_task

            # decide new run status
            old_run_status = pipeline_run.status

            # fail → attempt re-forwarding, then fall back to fail-fast
            if enum_status is TaskStatus.FAILED:
                reforward_result = self._try_reforward_sync(
                    username=username,
                    pipeline_id=pipeline_id,
                    run_id=run_id,
                    task_id=task_id,
                    pipeline_run=pipeline_run,
                )
                if reforward_result is None:
                    pipeline_run.status = PipelineStatus.FAILED
                    pipeline_run.finished_at = datetime.now(timezone.utc)
            else:
                finals = self._finals(pipeline_run, username, pipeline_id)
                logger.info("[Tracker] finals candidates for %s", sorted(finals))
                if finals:
                    all_done = True
                    for fid in finals:
                        t = pipeline_run.tasks.get(fid)
                        if not t or self._to_enum(t.status) is not TaskStatus.COMPLETED:
                            all_done = False
                            break
                    if all_done:
                        pipeline_run.status = PipelineStatus.COMPLETED
                        pipeline_run.finished_at = datetime.now(timezone.utc)

            prev_final = bool(getattr(prev_task, "is_final_task", False))
            incoming_final = bool(getattr(updated_task, "is_final_task", None))
            merged_final = prev_final or bool(incoming_final)
            updated_task = updated_task.model_copy(update={"is_final_task": merged_final})
            pipeline_run.tasks[task_id] = updated_task 

            # persist
            self.pipeline_doc_manager.update_run(
                username=username,
                pipeline_id=pipeline_id,
                run_id=run_id,
                updated_state=pipeline_run.model_dump(),
            )

            logger.info(
                "[Tracker] run %s status=%s persisted",
                run_id, pipeline_run.status
            )

            # mark event as processed for dedupe
            d.add(ev_key)

            # trigger strategist loop (best-effort, async)
            try:
                from agentcy.agent_runtime.services.llm_strategist_loop import handle_task_event
                event = {
                    "event": "task_state_changed",
                    "username": username,
                    "pipeline_id": pipeline_id,
                    "pipeline_run_id": run_id,
                    "task_id": task_id,
                    "status": enum_status.value,
                    "error": getattr(updated_task, "error", None),
                }
                asyncio.create_task(handle_task_event(self.resource_manager, event))
            except Exception:
                logger.debug("[Tracker] strategist loop trigger failed", exc_info=True)

            # emit stops exactly once per run (on the transition)
            if old_run_status == PipelineStatus.RUNNING and pipeline_run.status in (
                PipelineStatus.COMPLETED, PipelineStatus.FAILED
            ):
                # determine services (prefer TaskState.service_name, else derive from config if needed)
                services: Set[str] = set()
                for tid, ts in pipeline_run.tasks.items():
                    svc = getattr(ts, "service_name", None)
                    if not svc:
                        # derive from pipeline config if possible
                        svc = self._service_for_task(username, pipeline_id, tid)
                    if svc:
                        services.add(str(svc))

                if not services:
                    logger.warning(
                        "[Tracker] no services resolved for stop; falling back to unique services in config"
                    )
                    services = self._services_from_cfg(username, pipeline_id)

                for svc in services:
                    try:
                        asyncio.create_task(
                            send_stop_command(self.resource_manager, svc, username, pipeline_id, run_id)
                        )
                    except Exception as exc:
                        logger.error("[Tracker] failed sending stop to %s: %s", svc, exc)

        return reforward_result

    def _service_for_task(self, username: str, pipeline_id: str, task_id: str) -> Optional[str]:
        """
        Resolve a task → service name using pipeline config if TaskState doesn't carry it.
        We try 'task_dict[task_id].available_services' (string) or similar.
        """
        try:
            ps = getattr(self.resource_manager, "pipeline_store", None) or getattr(
                self.resource_manager, "persistent_store", None
            )
            if not ps:
                return None

            # tolerant method lookup
            cfg = None
            for meth in ("get_pipeline_config", "read_pipeline_config", "get", "read"):
                if hasattr(ps, meth):
                    cfg = getattr(ps, meth)(username=username, pipeline_id=pipeline_id)  # type: ignore
                    break
            if not cfg:
                return None

            data = cfg if isinstance(cfg, dict) else getattr(cfg, "model_dump", lambda: {})()
            task_dict = data.get("task_dict") or {}
            tmeta = task_dict.get(task_id) or {}
            svc = tmeta.get("available_services") or tmeta.get("service") or None
            return str(svc) if svc else None
        except Exception as exc:
            logger.debug("[Tracker] _service_for_task error: %s", exc)
            return None

    def _services_from_cfg(self, username: str, pipeline_id: str) -> Set[str]:
        try:
            ps = getattr(self.resource_manager, "pipeline_store", None) or getattr(
                self.resource_manager, "persistent_store", None
            )
            if not ps:
                return set()
            cfg = None
            for meth in ("get_pipeline_config", "read_pipeline_config", "get", "read"):
                if hasattr(ps, meth):
                    cfg = getattr(ps, meth)(username=username, pipeline_id=pipeline_id)  # type: ignore
                    break
            if not cfg:
                return set()
            data = cfg if isinstance(cfg, dict) else getattr(cfg, "model_dump", lambda: {})()
            task_dict = data.get("task_dict") or {}
            svcs = set()
            for tmeta in task_dict.values():
                svc = tmeta.get("available_services") or tmeta.get("service")
                if svc:
                    svcs.add(str(svc))
            return svcs
        except Exception:
            return set()

    # ──────────────────────────────────────────────────────────────────────
    # CNP failure re-forwarding (paper §3.5)
    # ──────────────────────────────────────────────────────────────────────
    def _try_reforward_sync(
        self,
        *,
        username: str,
        pipeline_id: str,
        run_id: str,
        task_id: str,
        pipeline_run: PipelineRun,
    ) -> Optional[ReforwardInfo]:
        """Attempt to re-assign a failed task to the next evaluation-sequence
        candidate.  Returns a :class:`ReforwardInfo` if re-forwarded (run stays
        RUNNING), ``None`` if no candidates remain (caller should fail the run).

        Disabled when ``CNP_FAILURE_REFORWARD=0``.
        """
        import os
        if os.getenv("CNP_FAILURE_REFORWARD", "1") == "0":
            return None

        store = getattr(self.resource_manager, "graph_marker_store", None)
        if store is None:
            return None

        # Resolve plan_id from run doc or task graph_spec
        plan_id = None
        raw_doc = None
        if hasattr(self, "pipeline_doc_manager"):
            raw_doc = self.pipeline_doc_manager.read_run(
                username=username, pipeline_id=pipeline_id, run_id=run_id,
            )
        if isinstance(raw_doc, dict):
            plan_id = raw_doc.get("plan_id")
        if not plan_id:
            return None

        task_state = pipeline_run.tasks.get(task_id)
        if task_state is None:
            return None
        failed_agent = getattr(task_state, "service_name", None) or "unknown"

        next_candidate = store.advance_evaluation_sequence(username, task_id, plan_id)
        if not next_candidate:
            logger.info(
                "[Tracker] no more candidates for task %s – failing run", task_id,
            )
            return None

        new_agent = next_candidate["bidder_id"]
        new_score = float(next_candidate.get("bid_score", 0))
        seq_idx = int(next_candidate.get("sequence_index", -1))

        # Create new award
        try:
            from agentcy.pydantic_models.multi_agent_pipeline import ContractAward
            award = ContractAward(
                task_id=task_id,
                bidder_id=new_agent,
                bid_id=next_candidate.get("bid_id"),
                cfp_id=next_candidate.get("cfp_id"),
                pipeline_id=pipeline_id,
                pipeline_run_id=run_id,
            )
            store.add_contract_award(username=username, award=award)
        except Exception:
            logger.debug("[Tracker] failed creating re-forward award", exc_info=True)

        # Reset task to PENDING with new assignment
        svc = self._service_for_task(username, pipeline_id, task_id) or new_agent
        updated = task_state.model_copy(update={
            "status": TaskStatus.PENDING,
            "error": None,
            "service_name": svc,
        })
        pipeline_run.tasks[task_id] = updated

        logger.info(
            "[Tracker] Re-forwarded task %s from %s to %s (seq_idx=%d, score=%.3f)",
            task_id, failed_agent, new_agent, seq_idx, new_score,
        )

        # Best-effort async publish of ReassignTaskCommand
        try:
            asyncio.create_task(
                self._publish_reassign(
                    username=username,
                    pipeline_id=pipeline_id,
                    pipeline_run_id=run_id,
                    plan_id=plan_id,
                    task_id=task_id,
                    failed_agent_id=str(failed_agent),
                )
            )
        except Exception:
            logger.debug("[Tracker] failed publishing ReassignTaskCommand", exc_info=True)

        return ReforwardInfo(
            task_id=task_id,
            new_service=svc,
            new_agent_id=new_agent,
            sequence_index=seq_idx,
            bid_score=new_score,
        )

    async def _publish_reassign(
        self,
        *,
        username: str,
        pipeline_id: str,
        pipeline_run_id: str,
        plan_id: str,
        task_id: str,
        failed_agent_id: str,
    ) -> None:
        """Publish a ``ReassignTaskCommand`` to the message bus."""
        try:
            from agentcy.pydantic_models.commands import ReassignTaskCommand
            from agentcy.api_service.dependecies import CommandPublisher

            rabbit_mgr = getattr(self.resource_manager, "rabbit_mgr", None)
            if rabbit_mgr is None:
                return
            cmd = ReassignTaskCommand(
                username=username,
                pipeline_id=pipeline_id,
                pipeline_run_id=pipeline_run_id,
                plan_id=plan_id,
                task_id=task_id,
                failed_agent_id=failed_agent_id,
            )
            pub = CommandPublisher(rabbit_mgr)
            await pub.publish("commands.reassign_task", cmd)
        except Exception:
            logger.debug("[Tracker] _publish_reassign error", exc_info=True)
