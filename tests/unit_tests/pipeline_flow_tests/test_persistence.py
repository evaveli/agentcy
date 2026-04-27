# tests/unit_tests/pipeline_flow_tests/test_persistence.py
import pytest
from datetime import datetime, timezone
from typing import Dict, Any

# ──────────────────────────────────────────────────────────────────────────────
#  Production helper under test
# ──────────────────────────────────────────────────────────────────────────────
from src.agentcy.pipeline_orchestrator.pub_sub.publisher_utilities.publishing_utilites import (
    persist_output_and_update_task,
)

from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import (
    TaskState,
    TaskStatus,
)

# ──────────────────────────────────────────────────────────────────────────────
#  Dummy infra                                                                  
# ──────────────────────────────────────────────────────────────────────────────
class _DummyDocStore:
    """Async stub mimicking the new CouchbaseDocStore interface."""

    def __init__(self, fail: bool = False):
        self._fail = fail

    async def save(
        self,
        username: str,
        run_id: str,
        task_id: str,
        raw_output: Dict[str, Any],
    ) -> str:
        if self._fail:
            raise Exception("Simulated store error")
        return f"task_output::{username}::{task_id}::{run_id}"


class _LegacyEphemeralStore:
    """Legacy sync API used in older implementation branches."""

    def __init__(self, fail: bool = False):
        self._fail = fail

    def store_task_output(self, run_id, task_id, raw):
        if self._fail:
            raise Exception("Simulated store error")
        # username isn’t available here, but legacy helper ignored it
        return


class DummyResourceManager:
    """
    Minimal resource-manager stub: exposes *both* .doc_store (newer code path)
    and .ephemeral_pipeline_doc_manager (older code path).
    """

    def __init__(self, fail: bool = False):
        self.rabbit_conn = None  # not needed
        self.doc_store = _DummyDocStore(fail)
        self.ephemeral_pipeline_doc_manager = _LegacyEphemeralStore(fail)


# ──────────────────────────────────────────────────────────────────────────────
#  Shared fixture                                                               
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def base_task() -> TaskState:
    return TaskState(
        # the run and task identifiers
        pipeline_run_id="run_003",
        task_id="task_456",

        # **new**: which pipeline and which user
        pipeline_id="my_pipeline",
        username="xhemil",

        # **new**: which pipeline‐config and which service this task belongs to
        pipeline_config_id="cfg-888",
        service_name="dummy_service",

        # task state fields
        status=TaskStatus.COMPLETED,
        attempts=0,
        # error/result are optional and will default to None
        output_ref="",
        final_task=False,
        last_updated=None,
        data={},
    )



# ──────────────────────────────────────────────────────────────────────────────
#  Tests                                                                        
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_persist_success(base_task):
    rm = DummyResourceManager(fail=False)
    out = await persist_output_and_update_task(rm, base_task, {"raw_output": "data"})

    expected = f"task_output::{base_task.username}::{base_task.task_id}::{base_task.pipeline_run_id}"
    assert out.output_ref == expected
    assert out.last_updated is not None
    assert out.last_updated.tzinfo is not None  # should be timezone-aware


@pytest.mark.asyncio
async def test_persist_failure(base_task):
    rm = DummyResourceManager(fail=True)
    with pytest.raises(Exception, match="Simulated store error"):
        await persist_output_and_update_task(rm, base_task, {"raw_output": "data"})


@pytest.mark.asyncio
async def test_persist_empty_raw_output(base_task):
    rm = DummyResourceManager(fail=False)
    out = await persist_output_and_update_task(rm, base_task, {})  # empty raw

    expected = f"task_output::{base_task.username}::{base_task.task_id}::{base_task.pipeline_run_id}"
    assert out.output_ref == expected
    assert out.last_updated is not None
