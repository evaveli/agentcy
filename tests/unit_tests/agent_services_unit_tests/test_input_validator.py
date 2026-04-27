import pytest

from src.agentcy.agent_runtime.services.input_validator import run


class _FakeRM:
    pass


@pytest.mark.asyncio
async def test_input_validator_passes_payload():
    message = {
        "username": "alice",
        "pipeline_id": "pipe-1",
        "pipeline_run_id": "run-1",
        "data": {"content_filter_passed": True},
    }

    result = await run(_FakeRM(), "run-1", "input_validator", None, message)
    assert result["validated"] is True
    assert result["blocked"] is False


@pytest.mark.asyncio
async def test_input_validator_blocks_filtered_payload():
    message = {
        "username": "alice",
        "pipeline_id": "pipe-2",
        "pipeline_run_id": "run-2",
        "data": {"content_filter_passed": False, "task_description": "blocked"},
    }

    result = await run(_FakeRM(), "run-2", "input_validator", None, message)
    assert result["validated"] is False
    assert result["blocked"] is True
    assert result["task_ids"] == []
