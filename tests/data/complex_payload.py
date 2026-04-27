# tests/data/complex_payload.py
COMPLEX_PIPELINE_PAYLOAD_TEMPLATE = {
    "authors": ["your_name"],
    "name": "pipeline_1",
    "description": "Testing complex entry",
    "pipeline_name": "pipeline_1_complex",
    "vhost": "your_vhost_value",
    "dag": {
        "tasks": [
            {
                "id": "task_1",
                "name": "Task 1",
                "available_services": "service_6",
                "action": "action_1",
                "is_entry": True,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": []}
            },
            {
                "id": "task_2",
                "name": "Task 2",
                "available_services": "service_6",
                "action": "action_5",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_1"]}
            },
            {
                "id": "task_3",
                "name": "Task 3",
                "available_services": "service_1",
                "action": "action_3",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_2"]}
            },
            {
                "id": "task_4",
                "name": "Task 4",
                "available_services": "service_9",
                "action": "action_5",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_1", "task_3"]}
            },
            {
                "id": "task_5",
                "name": "Task 5",
                "available_services": "service_8",
                "action": "action_1",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_2", "task_4"]}
            },
            {
                "id": "task_6",
                "name": "Task 6",
                "available_services": "service_6",
                "action": "action_1",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_5"]}
            },
            {
                "id": "task_7",
                "name": "Task 7",
                "available_services": "service_3",
                "action": "action_2",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_1", "task_2", "task_6"]}
            },
            {
                "id": "task_8",
                "name": "Task 8",
                "available_services": "service_10",
                "action": "action_1",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_7"]}
            },
            {
                "id": "task_9",
                "name": "Task 9",
                "available_services": "service_10",
                "action": "action_5",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_7", "task_4", "task_8"]}
            },
            {
                "id": "task_10",
                "name": "Task 10",
                "available_services": "service_10",
                "action": "action_3",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_9"]}
            },
            {
                "id": "task_11",
                "name": "Task 11",
                "available_services": "service_1",
                "action": "action_4",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_5", "task_10"]}
            },
            {
                "id": "task_12",
                "name": "Task 12",
                "available_services": "service_1",
                "action": "action_1",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_3", "task_11"]}
            },
            {
                "id": "task_13",
                "name": "Task 13",
                "available_services": "service_3",
                "action": "action_4",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_3", "task_2", "task_11", "task_12"]}
            },
            {
                "id": "task_14",
                "name": "Task 14",
                "available_services": "service_4",
                "action": "action_5",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_7", "task_13"]}
            },
            {
                "id": "task_15",
                "name": "Task 15",
                "available_services": "service_4",
                "action": "action_3",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_14"]}
            },
            {
                "id": "task_16",
                "name": "Task 16",
                "available_services": "service_9",
                "action": "action_3",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_10", "task_15"]}
            },
            {
                "id": "task_17",
                "name": "Task 17",
                "available_services": "service_9",
                "action": "action_1",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_16"]}
            },
            {
                "id": "task_18",
                "name": "Task 18",
                "available_services": "service_2",
                "action": "action_3",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_15", "task_8", "task_17"]}
            },
            {
                "id": "task_19",
                "name": "Task 19",
                "available_services": "service_10",
                "action": "action_1",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_18"]}
            },
            {
                "id": "task_20",
                "name": "Task 20",
                "available_services": "service_8",
                "action": "action_2",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_19"]}
            },
            {
                "id": "task_21",
                "name": "Task 21",
                "available_services": "service_3",
                "action": "action_3",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_14", "task_20"]}
            },
            {
                "id": "task_22",
                "name": "Task 22",
                "available_services": "service_3",
                "action": "action_2",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_8", "task_21"]}
            },
            {
                "id": "task_23",
                "name": "Task 23",
                "available_services": "service_3",
                "action": "action_3",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_9", "task_22"]}
            },
            {
                "id": "task_24",
                "name": "Task 24",
                "available_services": "service_4",
                "action": "action_3",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_23"]}
            },
            {
                "id": "task_25",
                "name": "Task 25",
                "available_services": "service_1",
                "action": "action_1",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_24"]}
            },
            {
                "id": "task_26",
                "name": "Task 26",
                "available_services": "service_3",
                "action": "action_5",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_8", "task_25"]}
            },
            {
                "id": "task_27",
                "name": "Task 27",
                "available_services": "service_10",
                "action": "action_2",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_26"]}
            },
            {
                "id": "task_28",
                "name": "Task 28",
                "available_services": "service_10",
                "action": "action_4",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_16", "task_27"]}
            },
            {
                "id": "task_29",
                "name": "Task 29",
                "available_services": "service_9",
                "action": "action_2",
                "is_entry": False,
                "is_final_task": False,
                "description": "...",
                "inputs": {"dependencies": ["task_28"]}
            },
            {
                "id": "task_30",
                "name": "Task 30",
                "available_services": "service_1",
                "action": "action_5",
                "is_entry": False,
                "is_final_task": True,
                "description": "...",
                "inputs": {"dependencies": ["task_29"]}
            }
        ]
    },
    "error_handling": {
        "retry_policy": {
            "max_retries": 2,
            "backoff_strategy": "Rolling"
        },
        "on_failure": "Stop"
    }
}
