# tests/utils.py
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import textwrap
import types
from pathlib import Path
from typing import Iterable
from datetime import datetime, timezone

from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import (
    TaskState,
    TaskStatus,
)

# ────────────────────────────────────── test helpers ──────────────────────────
class SpyLogic:
    """Collect every TaskState that hits the forwarder so tests can assert on them."""

    def __init__(self) -> None:
        self.received: list[TaskState] = []

    async def __call__(self, msg: TaskState, qinfo: dict, rm):
        self.received.append(msg)
        return {"raw_output": ""}


async def wait_for_consumers(rm, expected_queues: Iterable[str], timeout: float = 5.0):
    """Poll RabbitMQ until every *expected_queues* has at least one consumer."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with rm.rabbit_conn.get_channel() as ch:
        while True:
            for qname in expected_queues:
                q = await ch.declare_queue(qname, passive=True)
                if not q.consumer_count:
                    break
            else:  # ← loop **not** broken → all queues ready
                return
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("Consumers never attached")
            await asyncio.sleep(0.1)


# ───────────────────────────── dynamic stub service factory ───────────────────
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="stub_services_"))
(_TMP_ROOT / "services").mkdir()
(_TMP_ROOT / "services" / "__init__.py").touch()  # namespace marker
sys.path.insert(0, str(_TMP_ROOT))  # make importable from every process


def _ensure_namespace(package_name: str) -> types.ModuleType:
    """Return a namespace package (create if missing) and keep it in sys.modules."""
    if package_name in sys.modules:
        return sys.modules[package_name]

    mod = types.ModuleType(package_name)
    mod.__path__ = []  # namespace package per PEP-420
    sys.modules[package_name] = mod
    return mod


_RUN_SOURCE_TEMPLATE = textwrap.dedent(
    """
    from __future__ import annotations
    import json
    import logging
    from aio_pika import Message, ExchangeType
    from agentcy.pipeline_orchestrator.resource_manager import resource_manager_context
    from agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskState
    logger = logging.getLogger(__name__)

    async def run(message: dict | TaskState):  # noqa: ANN001
        \"\"\"Auto-generated stub for {svc}.\"\"\"
        if isinstance(message, TaskState):
            inbound = message.model_dump()
        else:
            inbound = message
            logger.info("🛠  {svc} received %s", inbound)

        payload = {{"handled_by": "{svc}", "ok": True}}

        rm = resource_manager_context.get_current()
        if rm is not None:
            ch = await rm.rabbit_conn.get_channel()
            body = json.dumps(
                {{
                    "event":           "task_completed",
                    "pipeline_id":     inbound.get("pipeline_id"),
                    "pipeline_run_id": inbound.get("pipeline_run_id"),
                    "task_id":         inbound.get("task_id") or inbound.get("id"),
                    "status":          "SUCCESS",
                    "output":          payload,
                }}
            ).encode()

            # Publish to the same durable, named exchange the app uses
            events_ex = await ch.declare_exchange(
                "pipeline_events_exchange",
                ExchangeType.TOPIC,
                durable=True,
            )
            await events_ex.publish(
                Message(body=body),
                routing_key="pipeline.events",
            )
        logger.info("✉️  {svc} sent task_completed for %s", inbound.get("task_id"))
        return {{"raw_output": json.dumps(payload)}}
    """
)


def _create_handler(full_pkg: str) -> None:
    """
    Create ``<full_pkg>.handler`` both **in-memory** and **on disk**
    so every new interpreter spawned by multiprocessing can import it.
    """
    handler_mod_name = f"{full_pkg}.handler"
    if handler_mod_name in sys.modules:  # idempotent
        return

    # ––––– in-memory namespace –––––
    parent_pkg = _ensure_namespace(full_pkg)

    mod = types.ModuleType(handler_mod_name)
    svc_short = full_pkg.rpartition(".")[2]
    exec(_RUN_SOURCE_TEMPLATE.format(svc=svc_short), mod.__dict__)
    sys.modules[handler_mod_name] = mod
    parent_pkg.handler = mod  # type: ignore

    # ––––– on-disk module –––––
    disk_dir = _TMP_ROOT / "services" / svc_short
    disk_dir.mkdir(exist_ok=True)
    (disk_dir / "__init__.py").touch()
    with (disk_dir / "handler.py").open("w") as f:
        f.write(_RUN_SOURCE_TEMPLATE.format(svc=svc_short))


def install_stub_services(service_names: Iterable[str]) -> None:
    """Call once during test bootstrap."""
    _ensure_namespace("services")
    for svc in service_names:
        _create_handler(f"services.{svc}")


# ─────────────────────────── auto-install for complex DAG ─────────────────────
from tests.data.complex_payload import (
    COMPLEX_PIPELINE_PAYLOAD_TEMPLATE,  # noqa: E402  (import late)
)

install_stub_services(
    {t["available_services"] for t in COMPLEX_PIPELINE_PAYLOAD_TEMPLATE["dag"]["tasks"]}
)


async def publish_dummy_task_state(rm, username, pipeline_id, run_id):
    await rm.publish_task_state(
        TaskState(
            username=username,
            pipeline_id=pipeline_id,
            pipeline_run_id=run_id,
            task_id="task_1",          # first task is good enough
            status=TaskStatus.COMPLETED
        ) # type: ignore
    )  



# tests/utils/ctx_compat.py

def _utcnow():
    return datetime.now(timezone.utc)

async def safe_started(task_ctx):
    meth = getattr(task_ctx, "started", None)
    if callable(meth):
        await meth() # type: ignore
        return
    state = getattr(task_ctx, "state", None) or getattr(task_ctx, "task_state", None)
    if state is not None:
        t = _utcnow()
        # support either alias; set both if present
        if hasattr(state, "started_at") and getattr(state, "started_at", None) is None:
            state.started_at = t
        if hasattr(state, "started") and getattr(state, "started", None) is None:
            state.started = t
        if hasattr(state, "last_updated"):
            state.last_updated = t

async def safe_finish(task_ctx, *, output=None, error=None):
    # prefer a real method if the runtime exposes one
    for name in ("finish", "complete", "done", "succeed", "mark_completed"):
        m = getattr(task_ctx, name, None)
        if callable(m):
            # try common signatures
            try:
                return await m(output=output, error=error) # type: ignore
            except TypeError:
                if error is not None:
                    return await m(error=error) # type: ignore
                return await m(output=output) # type: ignore

    # fall back: mutate state directly (tests only)
    state = getattr(task_ctx, "state", None) or getattr(task_ctx, "task_state", None)
    if state is None:
        return
    t = _utcnow()
    # set status/result/error fields if they exist
    if error is None:
        if hasattr(state, "status"):
            from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskStatus
            state.status = TaskStatus.COMPLETED
        if hasattr(state, "result"):
            state.result = output
        if hasattr(state, "error"):
            state.error = None
    else:
        if hasattr(state, "status"):
            from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import TaskStatus
            state.status = TaskStatus.FAILED
        if hasattr(state, "error"):
            state.error = str(error)
    if hasattr(state, "last_updated"):
        state.last_updated = t
