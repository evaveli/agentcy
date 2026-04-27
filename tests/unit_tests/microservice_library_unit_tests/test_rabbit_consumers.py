#tests/unit_tests/microservice_library_unit_tests/test_rabbit_consumers.py
import pytest
from src.agentcy.pipeline_orchestrator.pub_sub.consumer_wrapper import (
    get_tasks_for_service_name,
    get_subscribe_queues_for_task,
    generate_final_config_for,
)

# For unit tests we use a simplified version of the base config.
SAMPLE_CONFIG = {
    "task_dict": {
        "task_1": {"service_name": "service_a"},
        "task_2": {"service_name": "service_b"},
        "task_3": {"service_name": "service_a"}
    },
    "queues": {
        "queue_1": {"queue_name": "queue_1", "to_task": "task_1"},
        "queue_2": {"queue_name": "queue_2", "to_task": "task_2"},
        "queue_3": {"queue_name": "queue_3", "to_task": "task_1"}
    },
    "rabbitmq_configs": [
        {
            "rabbitmq": {
                "queue": "queue_1",
                "exchange": "ex_1",
                "exchange_type": "direct",
                "routing_key": "rk1"
            },
            "task_id": "task_1"
        },
        {
            "rabbitmq": {
                "queue": "queue_2",
                "exchange": "ex_2",
                "exchange_type": "fanout",
                "routing_key": ""
            },
            "task_id": "task_2"
        }
    ],
    "fan_in_metadata": {
        "task_1": {"required_steps": ["task_0"]},
        "task_2": {"required_steps": []}
    }
}

def test_get_tasks_for_service_name():
    tasks = get_tasks_for_service_name("service_a", SAMPLE_CONFIG)
    assert set(tasks) == {"task_1", "task_3"}

def test_get_subscribe_queues_for_task():
    queues = get_subscribe_queues_for_task("task_1", SAMPLE_CONFIG)
    assert set(queues) == {"queue_1", "queue_3"}

def test_generate_final_config_for():
    mini_config = generate_final_config_for("service_a", ["queue_1"], SAMPLE_CONFIG)
    # We expect the minimal config to include the selected queue, its rabbitmq config, and fan_in_metadata for task_1.
    assert "queue_1" in mini_config["queues"]
    # Since only the config for task_1 should be relevant.
    assert len(mini_config["rabbitmq_configs"]) == 1
    assert "task_1" in mini_config.get("fan_in_metadata", {})
