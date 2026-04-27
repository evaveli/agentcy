#src/agentcy/pydantic_models/pipeline_validation_models/pipeline_model.py
from enum import Enum
from typing import List, Optional, Dict, Any, Set
from pydantic import BaseModel, Field
from datetime import datetime



# Task RabbitMQ Configuration
class RabbitMQConfig(BaseModel):
    publisher: Optional[str] = None
    subscriber: Optional[str] = None


# Task Model
class Task(BaseModel):
    id: str
    name: str
    available_services: str
    action: str
    is_entry: bool
    description: Optional[str]
    inputs: Optional[Dict[str, Any]]
    is_final_task: bool

# Retry Policy Model
class RetryPolicy(BaseModel):
    max_retries: int
    backoff_strategy: str


# Error Handling Model
class ErrorHandling(BaseModel):
    retry_policy: RetryPolicy
    on_failure: str


# RabbitMQ Queue Configuration
class QueueConfig(BaseModel):
    name: str
    durable: bool
    auto_delete: bool


# RabbitMQ Publisher Configuration
class PublisherConfig(BaseModel):
    name: str
    routing_key: str


# RabbitMQ Subscriber Configuration
class SubscriberConfig(BaseModel):
    name: str
    queue: str
    consumer_tag: str


# RabbitMQ Section
class RabbitMQSection(BaseModel):
    cluster: Dict[str, Any]
    queues: List[QueueConfig]
    publishers: List[PublisherConfig]
    subscribers: List[SubscriberConfig]

# DAG Configuration
class DAGConfig(BaseModel):
    tasks: List[Task]


# Main Pipeline Configuration
class PipelineConfig(BaseModel):
    pipeline_id: str
    authors: Optional[List[str]] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime]= None
    vhost: str
    name: str
    description: Optional[str]
    pipeline_name: str
    last_updated: datetime
    version: int = Field(1, description="Version number of the pipeline.")
    dag: DAGConfig
    error_handling: ErrorHandling
    final_task_ids: List[str] = Field(default_factory=list)

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }