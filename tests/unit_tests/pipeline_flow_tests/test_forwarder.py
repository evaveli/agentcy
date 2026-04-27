# tests/unit_tests/pipeline_flow_tests/test_forwarder.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Union

import pytest
from pydantic import BaseModel

from src.agentcy.agent_runtime.forwarder import (
    DefaultForwarder,
    publish_message as _real_publish,          # <- grabbed for patching path
    enrich_task_state as _real_enrich,
    persist_output_and_update_task as _real_persist,
)
from src.agentcy.agent_runtime.parser import AbstractMessageParser
from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import (
    EntryMessage,
    TaskState,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Dummy infrastructure                                                       #
# --------------------------------------------------------------------------- #
class _DummyChannelCtx:
    async def __aenter__(self):     # pragma: no cover
        return self
    async def __aexit__(self, *a):  # pragma: no cover
        pass
    async def declare_exchange(self, *a, **kw):   # pragma: no cover
        class _X:
            async def publish(self, *a, **kw): pass
        return _X()
    async def declare_queue(self, *a, **kw):      # pragma: no cover
        class _Q:
            async def bind(self, *a, **kw): pass
            async def consume(self, *a, **kw): pass
        return _Q()


class _DummyRabbitConn:
    """Mimics rm.rabbit_conn without hitting the network."""
    def get_channel(self) -> _DummyChannelCtx:
        return _DummyChannelCtx()


class DummyEphemeralManager:
    def store_task_output(self, run_id: str, task_id: str, raw: Dict[str, Any]) -> str:
        return f"task_output::{task_id}::{run_id}"

    def read_task_output(self, doc_id: str, run_id: str, task_id: str) -> Dict[str, Any]:
        return {"fetched_output": "dummy"}


class DummyResourceManager:  # keeps type-checking happy without heavy base-class
    def __init__(self):
        self.rabbit_conn = _DummyRabbitConn()
        self.ephemeral_pipeline_doc_manager = DummyEphemeralManager()


# --------------------------------------------------------------------------- #
#  small dummy helpers used by forwarder                                      #
# --------------------------------------------------------------------------- #
async def _dummy_process(
    rm: DummyResourceManager,
    run_id: str,
    to_task: str,
    triggered_by: Any,
    message: Union[EntryMessage, TaskState],
) -> Dict[str, Any]:
    data_in = getattr(message, "data", {})
    return {
        "raw_output": "dummy_raw",
        "result": "ok",
        "data": {**data_in, "added": 1},
        "status_update": TaskStatus.COMPLETED,
    }


_published: list[Dict[str, Any]] = []             # capture in-memory


async def _dummy_publish(rm, svc, run_id, cfg_id, payload):
    body = payload.model_dump() if isinstance(payload, BaseModel) else payload
    _published.append(
        {"service": svc, "run": run_id, "cfg": cfg_id, "payload": body}
    )


async def _dummy_enrich(rm, task: TaskState) -> TaskState:
    extra = rm.ephemeral_pipeline_doc_manager.read_task_output(
        "dummy", task.pipeline_run_id, task.task_id
    )
    return task.model_copy(update={"data": {**task.data, **extra}})


async def _dummy_persist(rm, base: TaskState, raw: Dict[str, Any]) -> TaskState:
    ref = rm.ephemeral_pipeline_doc_manager.store_task_output(
        base.pipeline_run_id, base.task_id, raw.get("raw_output", {})
    )
    return base.model_copy(
        update={
            "status": raw.get("status_update", TaskStatus.FAILED),
            "result": raw.get("result"),
            "output_ref": ref,
            "last_updated": datetime.now(timezone.utc),
            "data": {**base.data, **raw.get("data", {})},
        }
    )


# --------------------------------------------------------------------------- #
#  Automatic patch of the forwarder helpers                                   #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _patch_forwarder(monkeypatch):
    """
    Patch the helper functions *inside the same module* that DefaultForwarder
    imports from (src.agentcy.agent_runtime.forwarder).
    """
    monkeypatch.setattr(_real_publish.__module__, "publish_message", _dummy_publish)
    monkeypatch.setattr(_real_enrich.__module__, "enrich_task_state", _dummy_enrich)
    monkeypatch.setattr(_real_persist.__module__, "persist_output_and_update_task", _dummy_persist)
    _published.clear()


# --------------------------------------------------------------------------- #
#  Parser smoke tests                                                         #
# --------------------------------------------------------------------------- #
def test_parser_entry_message():
    msg = EntryMessage(pipeline_id="p1", username="u", pipeline_run_id="r")
    assert AbstractMessageParser.parse(msg) == msg


def test_parser_task_message():
    msg = TaskState(
        username="u",
        pipeline_run_id="r2",
        task_id="t",
        status=TaskStatus.PENDING,
        pipeline_config_id="cfg",
        attempts=0,
        output_ref="x",
        data={},
    )
    assert AbstractMessageParser.parse(msg) == msg


# --------------------------------------------------------------------------- #
#  Forwarder integration tests                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_forwarder_task_flow():
    rm = DummyResourceManager()
    fwd = DefaultForwarder(rm, _dummy_process)

    payload = TaskState(
        username="tester",
        pipeline_run_id="run1",
        task_id="taskA",
        status=TaskStatus.PENDING,
        pipeline_config_id="cfg1",
        attempts=0,
        output_ref="",
        data={"start": 1},
    )

    await fwd.forward(payload, triggered_by="unit", to_task="svcA")

    assert len(_published) == 1
    out = _published[0]["payload"]
    assert out["status"] == TaskStatus.COMPLETED.value
    assert out["data"]["added"] == 1
    assert out["output_ref"].startswith("task_output::taskA::run1")


@pytest.mark.asyncio
async def test_forwarder_entry_flow():
    rm = DummyResourceManager()
    fwd = DefaultForwarder(rm, _dummy_process)

    payload = EntryMessage(
        pipeline_id="pipeX",
        username="alice",
        pipeline_run_id="runE",
        pipeline_config_id="cfgE",
    )

    await fwd.forward(payload, triggered_by="api", to_task="svcStart")

    assert len(_published) == 1
    out = _published[0]
    assert out["service"] == "svcStart"
    assert out["payload"]["pipeline_id"] == "pipeX"


@pytest.mark.asyncio
async def test_forwarder_parse_error(caplog):
    rm = DummyResourceManager()
    fwd = DefaultForwarder(rm, _dummy_process)

    with caplog.at_level(logging.ERROR):
        await fwd.forward({"not": "a model"}, triggered_by="oops", to_task="svc")
    # no publish
    assert not _published
    assert "Message_data must be a BaseModel" in caplog.text


@pytest.mark.asyncio
async def test_forwarder_updates_agent_registry(monkeypatch):
    rm = DummyResourceManager()
    fwd = DefaultForwarder(rm, _dummy_process)
    events = []

    class DummyRegistryClient:
        async def mark_task_started(self, *, task_id, pipeline_run_id, service_name):
            events.append(("start", task_id, pipeline_run_id, service_name))

        async def mark_task_finished(self, *, task_id, pipeline_run_id, service_name, success):
            events.append(("finish", task_id, pipeline_run_id, service_name, success))

    monkeypatch.setattr(
        "src.agentcy.agent_runtime.forwarder.get_registry_client",
        lambda: DummyRegistryClient(),
    )

    payload = TaskState(
        username="tester",
        pipeline_run_id="run2",
        task_id="taskB",
        status=TaskStatus.PENDING,
        pipeline_config_id="cfg2",
        attempts=0,
        output_ref="",
        data={},
    )

    await fwd.forward(payload, triggered_by="unit", to_task="svcB")

    assert events[0][:3] == ("start", "taskB", "run2")
    assert events[-1][0] == "finish"
