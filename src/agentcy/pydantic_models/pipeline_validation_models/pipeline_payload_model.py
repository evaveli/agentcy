#src/agentcy/pydantic_models/pipeline_validation_models/pipeline_payload_model.py
from enum import Enum
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv
import os

load_dotenv(override=False)

response_time = os.getenv("AGENT_RESPONSE_TIME", 30)

# Define the trigger protocol enum.
class TriggerProtocol(Enum):
    HTTP = "HTTP"
    RABBITMQ = "RABBITMQ"
    GRPC = "GRPC"


class BackoffStrategy(Enum):
    FIXED = "Low-latency retries, predictable failures"
    LINEAR = "Gradual failure recovery"
    EXPONENTIAL = "API rate limits, network failures"
    EXPONENTIAL_WITH_JITTER = "Distributed systems, avoid synchronized retries"
    FULL_JITTER = "Large-scale distributed retries"
    FIBONACCI = "Slower growth alternative to exponential"
    POLYNOMIAL = "Controllable growth in retry intervals"
    ADAPTIVE = "API with rate limits, dynamic workloads"
    TOKEN_BUCKET = "Enforce retry rate limits, burst protection"
    HYBRID = "Complex systems requiring adaptive behavior"

    def description(self):
        """Returns the human-readable description of the backoff strategy."""
        return self.value


class ConfigOverride(BaseModel):
    max_retries: int
    timeout: int

#TODO: Thois will serve as a centralized config for agents in the future
class Parameters(BaseModel):
    priority: str
    config_override: ConfigOverride

#TODO: Backoff strategy needs considering
class RetryPolicy(BaseModel):
    max_retries: int
    backoff_strategy: str

class Tasks(BaseModel):
    task_id: str
    service: str
    action: str
    expected_response_time: int
    retry_policy: RetryPolicy
    is_final_task: bool

class Security(BaseModel):
    access_token: str

class Orchestration(BaseModel):
    tasks: List[Tasks]
    security: Security

class PipelinePayload(BaseModel):
    schema_version: str
    pipeline_run_config_id: str
    origin: str
    trigger_protocol: TriggerProtocol
    timestamp: str
    orchestration: Orchestration

    class Config:
        json_encoders = {
            Enum: lambda v: v.value  # This is equivalent to your enum_to_value function.
        }


# Example usage:
if __name__ == "__main__":
    example_payload = {
    "schema_version": "1.0",
    "pipeline_run_config_id": "pipeline_run_config_id",
    "origin": "kickoff_service",
    "trigger_protocol": "HTTP",
    "timestamp": "timestamp",
    "orchestration": {
        "schema_version": "1.0",
        "tasks": [
            {
                "task_id": "fetch_output",
                "service": "data_processor",
                "action": "fetch_output",
                "expected_response_time": 300,
                "retry_policy": {
                    "max_retries": 3,
                    "backoff_strategy": "exponential"
                }
            }
        ],
        "security": {
            "access_token": "REPLACE_WITH_SECURE_TOKEN"
        }
    }
}

    payload_obj = PipelinePayload(**example_payload)
    print(payload_obj.model_dump_json(indent=4))
