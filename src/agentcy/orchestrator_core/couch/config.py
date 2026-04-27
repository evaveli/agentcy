from __future__ import annotations

import os

import logging
_log = logging.getLogger("agentcy.couch.config")
_log.setLevel(logging.DEBUG)



# ─────────────────────────────────────────────────────────────────────────────
# Bucket, scope & connection
# ─────────────────────────────────────────────────────────────────────────────
CB_CONN_STR: str = os.getenv("CB_CONN_STR", "couchbase://localhost")
CB_USER:     str = os.getenv("CB_USER",     "Administrator")
CB_PASS:     str = os.getenv("CB_PASS",     "password")
CB_BUCKET:   str = os.getenv("CB_BUCKET",   "agentcy")
CB_BUCKET_EPHEMERAL: str = os.getenv("CB_EPHEMERAL_BUCKET_NAME", "pipeline_runs")
CB_SCOPE:    str = os.getenv("CB_SCOPE",    "_default")


# ─────────────────────────────────────────────────────────────────────────────
# Logical names – used across the code-base
# ─────────────────────────────────────────────────────────────────────────────
class CNames(str):
    """Canonical logical collection names (do *not* change lightly)."""
    AGENTS                  = "agents"
    PIPELINES               = "pipelines"
    PIPELINE_RUNS           = "pipeline_runs"
    PIPELINE_VERSIONING     = "pipeline_versioning"
    PIPELINE_CONFIG         = "pipeline_config"
    PIPELINE_CONFIG_VERSION = "pipeline_config_versioning"
    PIPELINE_RUNS_VERSION   = "pipeline_runs_versioning"

    # Ephemeral
    PIPELINE_RUNS_EPHEMERAL = "pipeline_runs_ephemeral"
    EPHEMERAL_OUTPUTS       = "ephemeral_outputs"
    # NEW: catalog (persistent)
    CATALOG                 = "catalog"
    GRAPH_MARKERS           = "graph_markers"

    # Agent template catalog (persistent)
    AGENT_TEMPLATES         = "agent_templates"

    # Foundational agent state (persistent)
    AGENT_STATE_REGISTRY    = "agent_state_registry"
    AGENT_STATE_PLAN_CACHE  = "agent_state_plan_cache"
    AGENT_STATE_AUDIT_LOGS  = "agent_state_audit_logs"
    AGENT_STATE_PHEROMONES  = "agent_state_pheromones"


# ─────────────────────────────────────────────────────────────────────────────
# Physical collection names (env-overrideable)
# ─────────────────────────────────────────────────────────────────────────────
AGENTS_COLLECTION                   = os.getenv("AGENTS_COLLECTION",                    "agents")
PIPELINE_COLLECTION                 = os.getenv("PIPELINE_COLLECTION",                  "pipelines")
PIPELINE_RUNS_COLLECTION            = os.getenv("PIPELINE_RUNS_COLLECTION",             "pipeline_runs")
PIPELINE_VERSIONING_COLLECTION      = os.getenv("PIPELINE_VERSIONING_COLLECTION",       "pipeline_versioning")
PIPELINE_CONFIG_COLLECTION          = os.getenv("PIPELINE_CONFIG_COLLECTION",           "pipeline_config")
PIPELINE_CONFIG_VERSIONING_COLLECTION = os.getenv("PIPELINE_CONFIG_VERSIONING_COLLECTION",
                                                  "pipeline_config_versioning")
PIPELINE_RUNS_VERSIONING_COLLECTION = os.getenv("PIPELINE_RUNS_VERSIONING_COLLECTION",  "pipeline_runs_versioning")

# Ephemeral physical collections
PIPELINE_RUNS_COLLECTION_EPHEMERAL  = os.getenv("PIPELINE_RUNS_COLLECTION_EPHEMERAL",
                                                 "pipeline_runs_ephemeral")
EPHEMERAL_LARGE_OUTPUTS             = os.getenv("EPHEMERAL_LARGE_OUTPUTS",
                                                 "ephemeral_large_outputs")
CATALOG_COLLECTION                  = os.getenv("CATALOG_COLLECTION",                   "catalog")
GRAPH_MARKERS_COLLECTION            = os.getenv("GRAPH_MARKERS_COLLECTION",             "graph_markers")

# Agent template catalog
AGENT_TEMPLATES_COLLECTION          = os.getenv("AGENT_TEMPLATES_COLLECTION",         "agent_templates")

# Foundational agent state collections
AGENT_STATE_REGISTRY_COLLECTION     = os.getenv("AGENT_STATE_REGISTRY_COLLECTION",      "agent_state_registry")
AGENT_STATE_PLAN_CACHE_COLLECTION   = os.getenv("AGENT_STATE_PLAN_CACHE_COLLECTION",    "agent_state_plan_cache")
AGENT_STATE_AUDIT_LOGS_COLLECTION   = os.getenv("AGENT_STATE_AUDIT_LOGS_COLLECTION",    "agent_state_audit_logs")
AGENT_STATE_PHEROMONES_COLLECTION   = os.getenv("AGENT_STATE_PHEROMONES_COLLECTION",    "agent_state_pheromones")


# ─────────────────────────────────────────────────────────────────────────────
# Logical → physical maps
# ─────────────────────────────────────────────────────────────────────────────
CB_COLLECTIONS: dict[str, str] = {
    CNames.AGENTS:                  AGENTS_COLLECTION,
    CNames.PIPELINES:               PIPELINE_COLLECTION,
    CNames.PIPELINE_RUNS:           PIPELINE_RUNS_COLLECTION,
    CNames.PIPELINE_VERSIONING:     PIPELINE_VERSIONING_COLLECTION,
    CNames.PIPELINE_CONFIG:         PIPELINE_CONFIG_COLLECTION,
    CNames.PIPELINE_CONFIG_VERSION: PIPELINE_CONFIG_VERSIONING_COLLECTION,
    CNames.PIPELINE_RUNS_VERSION:   PIPELINE_RUNS_VERSIONING_COLLECTION,
    CNames.CATALOG:                 CATALOG_COLLECTION,
    CNames.GRAPH_MARKERS:           GRAPH_MARKERS_COLLECTION,
    # Agent template catalog
    CNames.AGENT_TEMPLATES:         AGENT_TEMPLATES_COLLECTION,
    # Foundational agent state
    CNames.AGENT_STATE_REGISTRY:    AGENT_STATE_REGISTRY_COLLECTION,
    CNames.AGENT_STATE_PLAN_CACHE:  AGENT_STATE_PLAN_CACHE_COLLECTION,
    CNames.AGENT_STATE_AUDIT_LOGS:  AGENT_STATE_AUDIT_LOGS_COLLECTION,
    CNames.AGENT_STATE_PHEROMONES:  AGENT_STATE_PHEROMONES_COLLECTION,
}

EPHEMERAL_COLLECTIONS: dict[str, str] = {
    CNames.PIPELINE_RUNS_EPHEMERAL: PIPELINE_RUNS_COLLECTION_EPHEMERAL,
    CNames.EPHEMERAL_OUTPUTS:       EPHEMERAL_LARGE_OUTPUTS,
    
}

_log.debug(
    "CB effective: CONN_STR=%r USER=%r BUCKET=%r EPHEMERAL_BUCKET=%r SCOPE=%r",
    CB_CONN_STR, CB_USER, CB_BUCKET, CB_BUCKET_EPHEMERAL, CB_SCOPE
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper (optional) – one-liner to resolve any logical name
# ─────────────────────────────────────────────────────────────────────────────
def get_collection_name(logical: str) -> str:
    """
    Resolve a logical collection name to its physical Couchbase collection name.
    Raises KeyError if the logical name is unknown.
    """
    return CB_COLLECTIONS.get(logical) or EPHEMERAL_COLLECTIONS[logical]
