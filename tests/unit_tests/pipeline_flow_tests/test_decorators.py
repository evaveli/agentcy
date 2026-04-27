# tests/unit_tests/pipeline_flow_tests/test_decorators.py
import pytest

from src.agentcy.agent_runtime.forwarder import enforce_raw_output_structure

@enforce_raw_output_structure
async def dummy_valid_task():
    return {"raw_output": "this is a large output"}

@enforce_raw_output_structure
async def dummy_invalid_task():
    return {"wrong_key": "oops"}

@pytest.mark.asyncio
async def test_valid_raw_output():
    result = await dummy_valid_task()
    assert result == {"raw_output": "this is a large output"}

@pytest.mark.asyncio
async def test_invalid_raw_output():
    with pytest.raises(ValueError):
        await dummy_invalid_task()
