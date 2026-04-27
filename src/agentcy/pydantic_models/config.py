#src/agentcy/pydantic_models/config.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """
    All agent‐runtime processes *must* configure this.
    If SERVICE_NAME is missing, this will fail immediately.
    """
    service_name: str = Field(
        ...,
        description="Unique name of this microservice; used to route RabbitMQ exchanges/queues.",
        validation_alias="SERVICE_NAME",
    )

    # replace inner Config with model_config
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )