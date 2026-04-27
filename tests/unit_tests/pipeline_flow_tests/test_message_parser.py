#tests/unit_tests/pipeline_flow_tests/test_message_parser.py
from src.agentcy.agent_runtime.parser import AbstractMessageParser
from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import EntryMessage, TaskState



def test_parse_entry_message():
    # Build the payload with exactly the fields required for an EntryMessage.
    payload = {
        "pipeline_id": "pipeline_001",
        "username": "alice",
        "pipeline_run_id": "run_001"
    }
    # Create an EntryMessage instance from the payload.
    entry_instance = EntryMessage(**payload)
    # Pass the model instance to the parser.
    result = AbstractMessageParser.parse(entry_instance)
    # Check that the result is an EntryMessage and fields match.
    assert isinstance(result, EntryMessage)
    assert result.pipeline_id == "pipeline_001"
    assert result.username == "alice"
    assert result.pipeline_run_id == "run_001"

def test_parse_task_message():
    # Build the payload with exactly the fields required for a TaskState.
    payload = {
        "status": "PENDING",
        "attempts": 0,
        "error": None,
        "result": None,
        "output_ref": "some_ref",  # Non-empty output_ref indicates a TaskState.
        "final_task": False,
        "last_updated": None,
        "pipeline_run_id": "run_002",
        "task_id": "task_123",
        "username": "bob",
        "pipeline_config_id": "config_001",
        "data": {"key": "value"}
    }
    # Create a TaskState instance from the payload.
    task_instance = TaskState(**payload)
    # Pass the TaskState instance to the parser.
    result = AbstractMessageParser.parse(task_instance)
    # Check that the result is a TaskState and fields match.
    assert isinstance(result, TaskState)
    assert result.pipeline_run_id == "run_002"
    assert result.task_id == "task_123"
    assert result.output_ref == "some_ref"
    assert result.username == "bob"
    assert result.pipeline_config_id == "config_001"
    assert result.data == {"key": "value"}
