# src/agentcy/settings.py
from functools import lru_cache
from typing import Literal, Optional
from unittest.mock import Base
from pydantic  import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 4.1  Logical keys -------------------------------------------------------
class CollKey(str):
    AGENTS                  = "agents"
    PIPELINES               = "pipelines"
    PIPELINE_RUNS           = "pipeline_runs"
    PIPELINE_RUNS_VERSIONED = "pipeline_runs_versioning"
    PIPELINE_RUNS_EPHEMERAL = "pipeline_runs_ephemeral"
    PIPELINE_VERSIONING     = "pipeline_versioning"
    PIPELINE_CONFIG         = "pipeline_config"
    PIPELINE_CONFIG_VERSION = "pipeline_config_versioning"
    EPHEMERAL_OUTPUTS       = "ephemeral_outputs"
    GRAPH_MARKERS           = "graph_markers"

# 4.2  Env-backed settings -----------------------------------------------
class Settings(BaseModel):
    # Couchbase
    cb_conn_str: str = Field("couchbase://localhost")
    cb_bucket:   str = Field("dev-bucker")
    cb_scope:    str = Field("_default")

    # Physical collection names
    agents_collection:                   str = Field("agents")
    pipelines_collection:                str = Field("pipelines")
    pipeline_runs_collection:            str = Field("pipeline_runs")
    pipeline_versioning_collection:      str = Field("pipeline_versioning")
    pipeline_config_collection:          str = Field("pipeline_config")
    pipeline_config_versioning_collection: str = Field("pipeline_config_versioning")
    pipeline_runs_versioning_collection: str = Field("pipeline_runs_versioning")
    pipeline_runs_collection_ephemeral:  str = Field("pipeline_runs_ephemeral")
    ephemeral_large_outputs:             str = Field("ephemeral_large_outputs")
    catalog_collection:                  str = Field("catalog")
    graph_markers_collection:           str = Field("graph_markers")

    class Config:
        env_file = ".env"
        case_sensitive = True

    # Logical → physical maps --------------------------------------------
    @property
    def collections(self) -> dict[CollKey, str]:
        return {
            CollKey(CollKey.AGENTS):                   self.agents_collection,
            CollKey(CollKey.PIPELINES):                self.pipelines_collection,
            CollKey(CollKey.PIPELINE_RUNS):            self.pipeline_runs_collection,
            CollKey(CollKey.PIPELINE_VERSIONING):      self.pipeline_versioning_collection,
            CollKey(CollKey.PIPELINE_CONFIG):          self.pipeline_config_collection,
            CollKey(CollKey.PIPELINE_CONFIG_VERSION):  self.pipeline_config_versioning_collection,
            CollKey(CollKey.PIPELINE_RUNS_VERSIONED):  self.pipeline_runs_versioning_collection,
            CollKey("catalog"):                        self.catalog_collection,
            CollKey(CollKey.GRAPH_MARKERS):            self.graph_markers_collection,
        }

    @property
    def ephemeral_collections(self) -> dict[CollKey, str]:
        return {
            CollKey(CollKey.PIPELINE_RUNS_EPHEMERAL): self.pipeline_runs_collection_ephemeral,
            CollKey(CollKey.EPHEMERAL_OUTPUTS):       self.ephemeral_large_outputs,
        }
    
class RuntimeSettings(BaseSettings):
    # Deduper / tracker tuning
    lru_event_max_items: int = Field(4096, ge=256, le=1_000_000)
    lru_event_ttl_seconds: float = Field(3600, ge=0)  # 0 disables TTL expiry

    # Stop-broadcast after terminal state
    stop_broadcast_enabled: bool = True
    stop_broadcast_backoff_ms: int = Field(0, ge=0)

    # Optional cross-process dedupe backend (future-ready)
    dedupe_backend: Literal["none", "redis", "couchbase"] = "none"
    redis_url: Optional[str] = None

    model_config = SettingsConfigDict(
        env_prefix="ORCH_",      # e.g., ORCH_LRU_EVENT_MAX_ITEMS=200000
        env_file=".env",
        extra="ignore",
    )

@lru_cache
def get_settings() -> Settings:
    return Settings() #type: ignore
