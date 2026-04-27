# Pipeline Orchestrator: Senior Engineering Analysis

**Author Perspective**: Senior Staff Engineer | Ex-Google, Meta, Anthropic, OpenAI
**Analysis Date**: February 2, 2026
**Repository**: pipeline-orchestrator
**Purpose**: Technical deep-dive for future development reference

---

## Executive Summary

This codebase represents a **demonstration-grade distributed pipeline orchestrator** with impressive architectural discipline. Built on **FastAPI**, **RabbitMQ**, and **Couchbase**, it provides per-run message isolation, transactional pipeline updates, and strict microservice contract enforcement. The system comprises **~146 Python files** organized into clear subsystems with thoughtful separation of concerns.

**Key Strengths:**
- ✅ Transactional guarantees for critical paths (Couchbase transactions)
- ✅ Per-run message isolation preventing concurrent execution conflicts
- ✅ Strict microservice contract: `{"raw_output": "<string>"}` enforced via decorators
- ✅ Comprehensive observability (OpenTelemetry, traces, metrics, logs)
- ✅ Clean code: Pydantic validation, type hints, error handling

**Current Limitations:**
- ❌ Not production-hardened (no chaos testing, lacks Kubernetes deployment)
- ❌ Advanced multi-agent adaptive planning features intentionally omitted (per README)
- ❌ Some LLM-based components optional/stubbed (provider-dependent)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Component Analysis](#2-component-analysis)
3. [Implementation Status Matrix](#3-implementation-status-matrix)
4. [Technology Stack](#4-technology-stack)
5. [Infrastructure & Deployment](#5-infrastructure--deployment)
6. [Data Layer Architecture](#6-data-layer-architecture)
7. [Agent System Deep Dive](#7-agent-system-deep-dive)
8. [API Surface Analysis](#8-api-surface-analysis)
9. [Testing Strategy & Coverage](#9-testing-strategy--coverage)
10. [Technical Debt & TODOs](#10-technical-debt--todos)
11. [Production Readiness Assessment](#11-production-readiness-assessment)
12. [Recommendations](#12-recommendations)

---

## 1. Architecture Overview

### System Design Philosophy

The architecture follows a **distributed microservices pattern** with three primary deployment units:

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   API Service   │────▶│ Orchestrator Core   │────▶│  Agent Runtime  │
│   (Port 8002)   │     │    (Port 8001)      │     │   (Multiple)    │
│                 │     │                     │     │                 │
│  - REST API     │     │  - Consumers        │     │  - Services     │
│  - Validation   │     │  - Stores           │     │  - Execution    │
│  - Routing      │     │  - Orchestration    │     │  - Forwarder    │
└─────────────────┘     └─────────────────────┘     └─────────────────┘
        │                       │                           │
        └───────────────────────┴───────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    │                       │
              ┌─────▼─────┐         ┌──────▼──────┐
              │ RabbitMQ  │         │  Couchbase  │
              │ (Messaging)│        │  (Storage)  │
              └───────────┘         └─────────────┘
```

### Core Design Principles

1. **Per-Run Isolation**: Each pipeline execution gets its own RabbitMQ topology
   - Exchange: `pipeline.{pipeline_id}.run.{run_id}.entry`
   - Routing keys: `{base_key}.{run_id}`
   - Zero cross-talk between concurrent runs

2. **Transactional Consistency**: Couchbase transactions for atomic multi-document updates
   - Pipeline creation: stub + full config + version in one txn
   - Rollback on failure

3. **Contract-First Microservices**: Enforced via decorator
   ```python
   @enforce_raw_output_structure
   def service_logic(msg: dict) -> dict:
       return {"raw_output": "..."}  # Must match this structure
   ```

4. **Eventual Consistency**: Asynchronous pipeline config generation
   - Immediate stub return (sync)
   - Full config built in background (async)
   - Retry-based polling for consistency

5. **Observability-First**: OpenTelemetry instrumentation baked in
   - Distributed tracing
   - Structured logging (JSON)
   - Prometheus metrics

### Directory Structure

```
src/agentcy/
├── api_service/              # REST API layer
│   ├── routers/             # 9 route modules
│   └── services/            # Business logic
├── orchestrator_core/        # Core orchestration engine
│   ├── consumers/           # 4 message consumers
│   ├── stores/              # 7 Couchbase stores
│   ├── executors/           # Container runtime (Docker)
│   └── handlers/            # State transition handlers
├── agent_runtime/           # Per-service agent runtime
│   ├── forwarder.py         # Message forwarding + enrichment
│   ├── tracker.py           # Deduplication (LRU+TTL)
│   ├── runner.py            # Pipeline initialization
│   ├── consumers.py         # Agent-side consumers
│   └── services/            # 23 service implementations
├── pydantic_models/         # Type schemas (21 files)
├── parsing_layer/           # DAG validation & manifest gen
├── semantic/                # Knowledge graph (Fuseki, SHACL)
├── observability/           # OpenTelemetry bootstrap
├── agents/                  # Foundational agents (14 types)
├── shared_lib/              # Shared utilities (backoff, KV)
└── pipeline_orchestrator/   # Resource management
```

**File Count**: ~146 Python files
**Lines of Code**: ~15,000+ LOC (excluding tests)
**Test Files**: 70 (unit, integration, E2E)

---

## 2. Component Analysis

### 2.1 API Service Layer

**Location**: [src/agentcy/api_service/](src/agentcy/api_service/)

**Responsibilities**:
- HTTP request handling (FastAPI)
- Input validation (Pydantic)
- Authentication/authorization (stub)
- Command publishing (RabbitMQ)

**Routers** (9 modules):

| Router | File | Endpoints | Status |
|--------|------|-----------|--------|
| Pipelines | [routers/pipelines.py](src/agentcy/api_service/routers/pipelines.py) | POST, GET, PUT, LIST | ✅ Full |
| Agent Registry | [routers/agent_registry.py](src/agentcy/api_service/routers/agent_registry.py) | POST, GET, LIST, DELETE, Heartbeat | ✅ Full |
| Graph Store | [routers/graph_store.py](src/agentcy/api_service/routers/graph_store.py) | Task specs, bids, drafts | 🛠️ Partial |
| Services | [routers/services.py](src/agentcy/api_service/routers/services.py) | Service registry | ✅ Full |
| Services Create | [routers/services_create_with_artifact.py](src/agentcy/api_service/routers/services_create_with_artifact.py) | Create with artifact | ✅ Full |
| Discovery | [routers/discovery.py](src/agentcy/api_service/routers/discovery.py) | Service discovery | ✅ Full |
| Images | [routers/images.py](src/agentcy/api_service/routers/images.py) | Container images | ✅ Full |
| Health | [routers/healthchecks.py](src/agentcy/api_service/routers/healthchecks.py) | Health checks | ✅ Full |
| CNP | [routers/cnp.py](src/agentcy/api_service/routers/cnp.py) | Contract Net Protocol | ❌ Stub |

**Key Design Patterns**:
- Dependency injection via FastAPI's `Depends()`
- Async request handlers
- Standardized error responses
- Command pattern for RabbitMQ messages

**Example Flow** (Pipeline Creation):
```python
# User POST → API validates → Publishes command → Returns stub immediately
POST /pipelines/{username}
  → PipelinePayloadModel validation
  → Couchbase.upsert(stub)
  → RabbitMQ.publish("commands.register_pipeline")
  → Return stub (201 Created)

# Background consumer processes full config
Consumer listens "commands.register_pipeline"
  → Generate DAG
  → Create RabbitMQ topology
  → Couchbase.transaction([definition, config, version])
  → Emit "pipeline_registered"
```

### 2.2 Orchestrator Core

**Location**: [src/agentcy/orchestrator_core/](src/agentcy/orchestrator_core/)

**Responsibilities**:
- Message consumption (RabbitMQ)
- Pipeline lifecycle management
- Container orchestration (Docker)
- State persistence (Couchbase)

**Consumers** (4 types):

1. **Service Registration Consumer** ([consumers/register_service_consumer.py](src/agentcy/orchestrator_core/consumers/register_service_consumer.py))
   - Listens: `commands.register_service`
   - Action: Persist service metadata to catalog

2. **Pipeline Registration Consumer** ([consumers/register_pipeline_consumer.py](src/agentcy/orchestrator_core/consumers/register_pipeline_consumer.py))
   - Listens: `commands.register_pipeline`
   - Action: Generate full config, validate DAG, create RabbitMQ topology

3. **Pipeline Registered Consumer** ([consumers/pipeline_registered_consumer.py](src/agentcy/orchestrator_core/consumers/pipeline_registered_consumer.py))
   - Listens: `events.pipeline_registered`
   - Action: Setup per-run infrastructure

4. **Task Launcher Consumer** ([consumers/launcher_consumer.py](src/agentcy/orchestrator_core/consumers/launcher_consumer.py))
   - Listens: `commands.start_task`
   - Action: Spawn Docker container, stream logs

**Stores** (7 Couchbase stores):

| Store | File | Collections | Purpose |
|-------|------|-------------|---------|
| PipelineStore | [stores/pipeline_store.py](src/agentcy/orchestrator_core/stores/pipeline_store.py) | pipelines, pipeline_config, pipeline_versioning | Pipeline CRUD + versioning |
| EphemeralPipelineStore | [stores/ephemeral_pipeline_store.py](src/agentcy/orchestrator_core/stores/ephemeral_pipeline_store.py) | pipeline_runs_ephemeral | Hot path run state (TTL) |
| AgentRegistryStore | [stores/agent_registry_store.py](src/agentcy/orchestrator_core/stores/agent_registry_store.py) | agents | Agent registration + heartbeat |
| ServiceStore | [stores/service_store.py](src/agentcy/orchestrator_core/stores/service_store.py) | catalog | Service metadata |
| UserCatalogStore | [stores/user_catalog_store.py](src/agentcy/orchestrator_core/stores/user_catalog_store.py) | catalog | User artifacts (CAS) |
| GraphMarkerStore | [stores/graph_marker_store.py](src/agentcy/orchestrator_core/stores/graph_marker_store.py) | graph_markers | Stigmergic data (bids, specs) |
| LargeOutputStore | [stores/large_output_store.py](src/agentcy/orchestrator_core/stores/large_output_store.py) | ephemeral_large_outputs | Output blobs (`output_ref`) |

**Executors**:
- **Docker Executor** ([executors/docker_exec.py](src/agentcy/orchestrator_core/executors/docker_exec.py))
  - Spawns containers via Docker daemon (HTTP API or Unix socket)
  - Environment variable injection
  - Resource limits (memory, CPU)
  - Log streaming
  - Connection pooling

### 2.3 Agent Runtime

**Location**: [src/agentcy/agent_runtime/](src/agentcy/agent_runtime/)

**Responsibilities**:
- Execute service logic (the actual "work")
- Forward results to downstream agents
- Track message deduplication
- Enrich outputs from blob store

**Core Components**:

1. **Forwarder** ([forwarder.py](src/agentcy/agent_runtime/forwarder.py))
   - Enforces `{"raw_output": "..."}` contract via decorator
   - Fetches large outputs via `output_ref`
   - Publishes to all outgoing edges
   - Retry logic (Tenacity)

2. **Tracker** ([tracker.py](src/agentcy/agent_runtime/tracker.py))
   - LRU+TTL-based deduplication
   - Tracks seen message IDs
   - Forward-only state transitions

3. **Runner** ([runner.py](src/agentcy/agent_runtime/runner.py))
   - Pipeline initialization
   - Creates per-run entry point
   - Seeds initial run document

4. **Consumers** ([consumers.py](src/agentcy/agent_runtime/consumers.py))
   - Listens on `commands.start_pipeline`
   - Consumes pipeline events

**Services** (23 implementations):

| Service | File | Status | Description |
|---------|------|--------|-------------|
| agent_registration | [services/agent_registration.py](src/agentcy/agent_runtime/services/agent_registration.py) | ✅ | Register agents with capabilities |
| input_validator | [services/input_validator.py](src/agentcy/agent_runtime/services/input_validator.py) | ✅ | Validate input risk level |
| path_seeder | [services/path_seeder.py](src/agentcy/agent_runtime/services/path_seeder.py) | ✅ | Seed execution paths |
| blueprint_bidder | [services/blueprint_bidder.py](src/agentcy/agent_runtime/services/blueprint_bidder.py) | ✅ | Score task-agent fit |
| graph_builder | [services/graph_builder.py](src/agentcy/agent_runtime/services/graph_builder.py) | ✅ | Build plan drafts |
| plan_validator | [services/plan_validator.py](src/agentcy/agent_runtime/services/plan_validator.py) | ✅ | Validate plans (SHACL) |
| plan_cache | [services/plan_cache.py](src/agentcy/agent_runtime/services/plan_cache.py) | ✅ | Cache identical plans |
| human_validator | [services/human_validator.py](src/agentcy/agent_runtime/services/human_validator.py) | ✅ | Human approval gates |
| llm_strategist | [services/llm_strategist.py](src/agentcy/agent_runtime/services/llm_strategist.py) | 🛠️ | LLM strategy (provider-dep) |
| llm_strategist_loop | [services/llm_strategist_loop.py](src/agentcy/agent_runtime/services/llm_strategist_loop.py) | 🛠️ | LLM loop handling |
| ethics_checker | [services/ethics_checker.py](src/agentcy/agent_runtime/services/ethics_checker.py) | ✅ | Ethical risk assessment |
| system_executor | [services/system_executor.py](src/agentcy/agent_runtime/services/system_executor.py) | ✅ | Execute tasks (sim/real) |
| pheromone_engine | [services/pheromone_engine.py](src/agentcy/agent_runtime/services/pheromone_engine.py) | ✅ | Stigmergic mark updates |
| failure_escalation | [services/failure_escalation.py](src/agentcy/agent_runtime/services/failure_escalation.py) | ✅ | Escalate failures |
| audit_logger | [services/audit_logger.py](src/agentcy/agent_runtime/services/audit_logger.py) | ✅ | Audit trail |

**Contract Enforcement** (Critical):
```python
# src/agentcy/agent_runtime/forwarder.py:30-51
@enforce_raw_output_structure
def service_logic(msg: dict) -> dict:
    # Must return exactly: {"raw_output": "<non-empty-string>"}
    # Raises ValueError if violated
    pass
```

### 2.4 Parsing Layer

**Location**: [src/agentcy/parsing_layer/](src/agentcy/parsing_layer/)

**Responsibilities**:
- DAG validation (cycle detection, connectivity)
- RabbitMQ manifest generation
- Topological sort
- Fan-in detection

**Key Files**:
- [generate_rabbitmq_manifests.py](src/agentcy/parsing_layer/generate_rabbitmq_manifests.py) - Generates RabbitMQ topology YAML
- [workflow_config_parser.py](src/agentcy/rabbitmq_workflow/workflow_config_parser.py) - Validates DAG via Kahn's algorithm

**Topology Generation Example**:
```python
# Input: DAG with nodes A → B → C
# Output: RabbitMQ exchanges, queues, bindings

exchanges:
  - name: "pipeline.{pid}.run.{rid}.entry"
    type: direct
  - name: "pipeline.{pid}.run.{rid}.A"
    type: topic

queues:
  - name: "pipeline.{pid}.run.{rid}.A.task"
    bindings:
      - exchange: "pipeline.{pid}.run.{rid}.entry"
        routing_key: "entry.{rid}"
```

### 2.5 Semantic Layer

**Location**: [src/agentcy/semantic/](src/agentcy/semantic/)

**Responsibilities**:
- Knowledge graph integration (Fuseki)
- SHACL shape validation
- RDF export
- Provenance tracking

**Components**:
- [fuseki_client.py](src/agentcy/semantic/fuseki_client.py) - SPARQL query client
- [shacl_engine.py](src/agentcy/semantic/shacl_engine.py) - SHACL validation
- [plan_graph.py](src/agentcy/semantic/plan_graph.py) - RDF graph generation

**Status**: 🛠️ **Optional** (behind `FUSEKI_ENABLE=1` flag)

---

## 3. Implementation Status Matrix

### Legend
- ✅ **Full**: Fully implemented, tested, production-ready within demo scope
- 🛠️ **Partial**: Core logic present, some features optional or incomplete
- ❌ **Stub**: Placeholder or not started

### Feature Matrix

| Feature | Status | Wiring | Notes |
|---------|--------|--------|-------|
| **Pipeline CRUD** | ✅ | Full | Transactional updates, versioning |
| **Per-Run Isolation** | ✅ | Full | Dynamic RabbitMQ topology per run |
| **Microservice Contract** | ✅ | Full | Decorator enforces `{"raw_output": "..."}` |
| **Idempotent Processing** | ✅ | Full | LRU+TTL deduplication |
| **Agent Registry** | ✅ | Full | Heartbeat, TTL expiry, policy filtering |
| **Message Forwarder** | ✅ | Full | Output enrichment, retry logic |
| **DAG Validation** | ✅ | Full | Kahn's algorithm, cycle detection |
| **Docker Execution** | ✅ | Full | Container spawning, log streaming |
| **Health Checks** | ✅ | Full | Deep checks (RabbitMQ, Couchbase) |
| **OpenTelemetry** | ✅ | Full | Traces, metrics, logs |
| **Foundational Agents** | 🛠️ | Partial | 14 agents, in-memory state |
| **Graph Store** | 🛠️ | Partial | Schema present, aggregations stub |
| **Semantic Layer** | 🛠️ | Optional | Wired behind feature flag |
| **LLM Strategist** | 🛠️ | Optional | Skeleton present, provider-dependent |
| **CNP** | ❌ | Stub | Contract Net Protocol not implemented |
| **Kubernetes** | ❌ | None | Documented, not implemented |
| **Chaos Testing** | ❌ | None | Test framework defined, tests pending |
| **Multi-Agent Adaptive** | ❌ | Partial | Intentionally omitted (per README) |

### What's Wired vs. Scaffolded

**✅ Fully Wired (End-to-End Working)**:
1. Pipeline creation → registration → execution flow
2. Per-run message isolation (no cross-talk)
3. Microservice execution loop
4. Health/readiness checks
5. Observability (OTLP traces/metrics/logs)

**🛠️ Scaffolded (Framework in place, selective wiring)**:
1. Agent registry (full CRUD, policy filtering optional)
2. Graph store (REST endpoints present, advanced features stub)
3. Semantic layer (Fuseki client available, optional via flag)
4. LLM integration (providers optional, services work in stub mode)

**❌ Not Wired**:
1. Kubernetes deployment
2. Contract Net Protocol
3. Chaos/failure injection tests
4. Advanced multi-agent adaptive planning (per README note)

---

## 4. Technology Stack

### Core Framework
- **FastAPI** 0.111.0 - Async REST API framework
- **Uvicorn** 0.30.1 - ASGI server
- **Pydantic** 2.11.7 - Data validation & settings

### Messaging
- **aio-pika** 9.5.7 - Async RabbitMQ client (AMQP 0-9-1)
- **aiormq** 6.9.0 - Low-level AMQP driver
- **RabbitMQ** 3.13 - Message broker

### Data Persistence
- **Couchbase Python SDK** 4.3.0 - NoSQL database
  - Multi-version concurrency control (MVCC)
  - Transactions (AttemptContext, TransactionFailed)
  - Dual-bucket strategy (persistent + ephemeral with TTL)

### Observability
- **OpenTelemetry** 1.36.0 (SDK) + 0.57b0 (instrumentation)
- **OTLP Exporter** (gRPC/HTTP)
- **python-json-logger** 3.3.0
- **Prometheus** (metrics)
- **Grafana** (dashboards)
- **Loki** (log aggregation)
- **Tempo** (trace storage)

### Knowledge Graph
- **RDFLib** 7.0.0 - RDF processing (Turtle, JSON-LD)
- **pyshacl** 0.26.0 - SHACL validation
- **Apache Jena Fuseki** - SPARQL endpoint

### LLM Integration
- **OpenAI SDK** 1.104.2 - GPT-4, GPT-3.5
- **Ollama** 0.5.3 - Local models (Llama, Mistral)

### Container Orchestration
- **Docker SDK** 7.1.0 - Docker daemon API
- **httpx** 0.27.0 - HTTP client (Docker daemon, Fuseki)

### Utilities
- **Tenacity** 9.1.2 - Retry/backoff
- **PyYAML** 6.0.1 - Configuration
- **Jinja2** 3.1.4 - Template rendering
- **jsonschema** 4.23.0 - Schema validation
- **py-consul** 1.5.1 - Service discovery

### Testing
- **pytest** 8.3.2
- **pytest-asyncio** 0.24.0
- **pytest-mock** 3.14.0

### Infrastructure
- **Traefik** 3.0 - Reverse proxy, SSL/TLS
- **Nexus** 3.82.0 - PyPI mirror, Docker registry

---

## 5. Infrastructure & Deployment

### Docker Compose Architecture

**File**: [docker-compose.yml](docker-compose.yml)

**Services** (11 containers):

```yaml
services:
  traefik:          # SSL/TLS termination, Let's Encrypt
  couchbase:        # NoSQL database (community edition)
  couchbase-setup:  # Bootstrap script (init-couchbase.sh)
  rabbitmq:         # Message broker (3.13-management)
  nexus:            # PyPI mirror + Docker registry
  api:              # FastAPI service (port 8002)
  orchestrator:     # Core orchestration (port 8001)
  agent-runtime:    # Agent execution (optional, --profile agents)
  grafana:          # Dashboards
  loki:             # Log aggregation
  tempo:            # Trace storage
  prometheus:       # Metrics
```

**Network**: Bridge network `agentcy`

**Volumes** (persistent):
- `cb-data` - Couchbase data
- `nexus-data` - Nexus artifacts
- `rabbit-data` - RabbitMQ mnesia
- `grafana-data`, `loki-data`, `tempo-data`, `prometheus-data`

**Health Checks**:
- Couchbase: `curl -f http://localhost:8091/ui/index.html`
- RabbitMQ: `rabbitmq-diagnostics ping`
- API: `curl http://localhost:8002/health`
- Orchestrator: `curl http://localhost:8001/health`

### Environment Configuration

**File**: [.env.example](.env.example)

**Key Variables**:

```bash
# RabbitMQ
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
RABBITMQ_DEFAULT_USER=guest
RABBITMQ_DEFAULT_PASS=guest

# Couchbase
CB_CONN_STR=couchbase://couchbase
CB_USER=Administrator
CB_PASS=password
CB_BUCKET=dev-bucker
CB_BUCKET_EPHEMERAL=pipeline_runs
CB_SCOPE=_default

# Collections (9 persistent + 2 ephemeral)
CB_COLL_AGENTS=agents
CB_COLL_PIPELINES=pipelines
CB_COLL_PIPELINE_RUNS=pipeline_runs
CB_COLL_PIPELINE_VERSIONING=pipeline_versioning
CB_COLL_PIPELINE_CONFIG=pipeline_config
CB_COLL_PIPELINE_CONFIG_VERSIONING=pipeline_config_versioning
CB_COLL_CATALOG=catalog
CB_COLL_GRAPH_MARKERS=graph_markers
CB_COLL_PIPELINE_RUNS_EPHEMERAL=pipeline_runs_ephemeral
CB_COLL_EPHEMERAL_LARGE_OUTPUTS=ephemeral_large_outputs

# Agent Runtime
DOCKER_NETWORK=agentcy_default
AGENT_MEMORY_LIMIT=512m
NEXUS_REGISTRY=host.docker.internal:8083
NEXUS_USER=admin
NEXUS_PASSWORD=admin

# Agent Registry Policy
AGENT_REGISTRY_POLICY_ENABLE=1
AGENT_COVERAGE_TARGET=3
AGENT_FRESHNESS_WARN_S=60
AGENT_FRESHNESS_STALE_S=300
AGENT_FRESHNESS_OFFLINE_S=900
AGENT_DECAY_HALF_LIFE_S=900

# LLM Providers (optional)
LLM_STRATEGIST_PROVIDER=openai|ollama
LLM_OPENAI_API_KEY=sk-...
LLM_OPENAI_MODEL=gpt-4
LLM_OLLAMA_BASE_URL=http://localhost:11434
LLM_OLLAMA_MODEL=llama2

# Observability
OTLP_ENDPOINT=http://tempo:4317
```

### Deployment Profiles

**Development** (minimal):
```bash
docker-compose up api orchestrator
```

**With Agents**:
```bash
docker-compose --profile agents up
```

**Production** (all services):
```bash
docker-compose --profile production up -d
```

### Couchbase Bootstrap

**Script**: [init-couchbase.sh](init-couchbase.sh)

**Actions**:
1. Create buckets (persistent + ephemeral)
2. Create scope `_default`
3. Create 11 collections
4. Set ephemeral bucket TTL (1 hour default)

**Collections**:
- **Persistent**: agents, pipelines, pipeline_runs, pipeline_versioning, pipeline_config, pipeline_config_versioning, catalog, graph_markers
- **Ephemeral**: pipeline_runs_ephemeral, ephemeral_large_outputs

---

## 6. Data Layer Architecture

### Dual-Bucket Strategy

**Rationale**: Separate hot path (ephemeral, high-write) from cold path (persistent, auditable)

```
┌───────────────────────────────────────────┐
│  PERSISTENT BUCKET (dev-bucker)           │
│  - Pipelines                              │
│  - Pipeline configs                       │
│  - Agent registry                         │
│  - Service catalog                        │
│  - Graph markers (bids, specs, drafts)   │
│  - Long-term run records                  │
│  - Versioning history                     │
│  TTL: None (manual cleanup)               │
└───────────────────────────────────────────┘

┌───────────────────────────────────────────┐
│  EPHEMERAL BUCKET (pipeline_runs)         │
│  - Hot path run state                     │
│  - Large outputs (output_ref blobs)       │
│  TTL: 1 hour (auto-cleanup)               │
└───────────────────────────────────────────┘
```

### Store Abstraction Layer

**Protocol**: [src/agentcy/shared_lib/kv/protocols.py](src/agentcy/shared_lib/kv/protocols.py)

```python
class KVCollection(Protocol):
    async def get(self, key: str) -> dict: ...
    async def upsert(self, key: str, doc: dict) -> None: ...
    async def remove(self, key: str) -> None: ...

class KVPool(Protocol):
    def get_collection(self, bucket: str, scope: str, coll: str) -> KVCollection: ...
```

**Benefits**:
- Swappable backends (Redis, DynamoDB)
- Testable (mock KVCollection)
- Centralized retry logic

### Transaction Pattern

**Example** (Pipeline Creation):
```python
# src/agentcy/orchestrator_core/stores/pipeline_store.py
async def create(self, pipeline: Pipeline) -> None:
    async with transaction_context() as txn:
        await txn.insert(
            coll=self.pipelines_coll,
            key=f"pipeline::{pipeline.username}::{pipeline.pipeline_id}",
            doc=pipeline.model_dump()
        )
        await txn.insert(
            coll=self.config_coll,
            key=f"config::{pipeline.username}::{pipeline.pipeline_id}",
            doc=pipeline.config
        )
        await txn.insert(
            coll=self.version_coll,
            key=f"version::{pipeline.username}::{pipeline.pipeline_id}::1",
            doc={"version": 1, "created_at": now()}
        )
    # All-or-nothing commit
```

### Retry & Backoff

**Decorator** ([src/agentcy/shared_lib/kv/backoff.py](src/agentcy/shared_lib/kv/backoff.py)):
```python
@with_backoff(msg="Couchbase get operation")
async def get(self, key: str) -> dict:
    # Exponential backoff with jitter
    # Max attempts: 5
    # Base delay: 0.1s, max: 10s
    return await self.collection.get(key)
```

### Key Naming Conventions

```
# Pipelines
pipeline::{username}::{pipeline_id}
config::{username}::{pipeline_id}
version::{username}::{pipeline_id}::{version_num}

# Runs
pipeline_run::{username}::{pipeline_id}::{run_id}
output_ref::{run_id}::{task_name}

# Agents
agent_registry::{username}::{agent_id}

# Graph Markers
task_spec::{username}::{task_id}
blueprint_bid::{username}::{bid_id}
plan_draft::{username}::{plan_id}
```

### Connection Pooling

**Class**: [DynamicCouchbaseConnectionPool](src/agentcy/orchestrator_core/couch/pool.py)

```python
class DynamicCouchbaseConnectionPool:
    def __init__(self, size: int = 10):
        self.pool: List[Cluster] = []
        self.lock = threading.RLock()

    def acquire(self) -> Cluster:
        with self.lock:
            if self.pool:
                return self.pool.pop()
            return self._create_connection()

    def release(self, conn: Cluster):
        with self.lock:
            if conn.ping():  # Health check
                self.pool.append(conn)
```

**Benefits**:
- Reduced connection overhead
- Health check on release
- Thread-safe

---

## 7. Agent System Deep Dive

### Agent Lifecycle

```
┌─────────────────────────────────────────────────────────┐
│ 1. Bootstrap                                            │
│    - Load ResourceManager (RabbitMQ, Couchbase)         │
│    - Start consumers                                    │
│    - Register with agent registry                       │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Receive Message                                      │
│    - EntryMessage or TaskState from RabbitMQ            │
│    - Dedup check (LRU+TTL)                              │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 3. Enrich                                               │
│    - If output_ref present, fetch blob from store       │
│    - Merge into TaskState.data                          │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Execute Service Logic                                │
│    - Call decorated service function                    │
│    - Enforce {"raw_output": "..."} contract             │
│    - Raise ValueError if violated                       │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│ 5. Persist & Forward                                    │
│    - Update run doc (state transition)                  │
│    - Publish to downstream edges                        │
│    - Track state in dedup set                           │
└─────────────────────────────────────────────────────────┘
```

### Contract Enforcement

**Decorator** ([src/agentcy/agent_runtime/forwarder.py:30-51](src/agentcy/agent_runtime/forwarder.py)):

```python
def enforce_raw_output_structure(func):
    """
    Ensures microservice returns exactly {"raw_output": "<non-empty-string>"}
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)

        # Check structure
        if not isinstance(result, dict):
            raise ValueError(f"Service {func.__name__} must return dict")

        if list(result.keys()) != ["raw_output"]:
            raise ValueError(
                f"Service {func.__name__} must return exactly one key: 'raw_output'. "
                f"Got: {list(result.keys())}"
            )

        if not isinstance(result["raw_output"], str) or not result["raw_output"]:
            raise ValueError(
                f"Service {func.__name__} 'raw_output' must be non-empty string. "
                f"Got: {type(result['raw_output'])}"
            )

        return result
    return wrapper
```

**Why This Matters**:
- Prevents downstream parsing errors
- Simplifies message forwarding logic
- Enforces uniform interface across 23 services
- Fails fast (developer knows immediately if contract violated)

### Deduplication Strategy

**Implementation** ([src/agentcy/agent_runtime/tracker.py](src/agentcy/agent_runtime/tracker.py)):

```python
class _LRUSet:
    """
    LRU + TTL-based deduplication.
    Tracks (pipeline_id, run_id, task_name, state) tuples.
    """
    def __init__(self, max_size: int = 10000, ttl_seconds: int = 3600):
        self.cache: OrderedDict = OrderedDict()
        self.max_size = max_size
        self.ttl = ttl_seconds

    def add(self, key: tuple) -> bool:
        """Returns True if key is new (not seen before)."""
        now = time.time()

        # Check if exists and not expired
        if key in self.cache:
            timestamp = self.cache[key]
            if now - timestamp < self.ttl:
                return False  # Already seen
            else:
                del self.cache[key]  # Expired, treat as new

        # Add to cache
        self.cache[key] = now
        self.cache.move_to_end(key)

        # Evict oldest if over capacity
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

        return True  # New key
```

**Properties**:
- **At-least-once delivery**: Message may arrive multiple times
- **Idempotent**: Only process first occurrence within TTL window
- **Bounded memory**: LRU eviction keeps cache size in check
- **Automatic cleanup**: TTL-based expiry

### Agent Registry & Policy

**Store**: [src/agentcy/orchestrator_core/stores/agent_registry_store.py](src/agentcy/orchestrator_core/stores/agent_registry_store.py)

**Registration**:
```python
@dataclass
class AgentRegistration:
    agent_id: str
    username: str
    service_name: str
    capabilities: List[str]
    tags: List[str]
    status: Literal["active", "idle", "offline"]
    last_heartbeat: datetime
    metadata: dict
```

**Heartbeat Flow**:
```
Agent ──► POST /agent-registry/{username}/{agent_id}/heartbeat
          │
          ├── Update last_heartbeat timestamp
          ├── Reset TTL (45 seconds default)
          └── Update status to "active"

If no heartbeat for >45s → TTL expires → Auto-deleted
```

**Policy Filtering** ([src/agentcy/orchestrator_core/stores/agent_registry_policy.py](src/agentcy/orchestrator_core/stores/agent_registry_policy.py)):

```python
@dataclass
class AgentRegistryPolicy:
    # Coverage: min/target agents per capability
    coverage_target: int = 3
    coverage_min: int = 1

    # Freshness thresholds
    freshness_warn_seconds: int = 60
    freshness_stale_seconds: int = 300
    freshness_offline_seconds: int = 900

    # Decay: exponential drop-off for stale agents
    decay_half_life_seconds: int = 900
    decay_min_factor: float = 0.2

def apply_policy(agents: List[AgentRegistration], policy: AgentRegistryPolicy):
    """
    Filters and ranks agents by:
    1. Coverage: ensures minimum agents per capability
    2. Freshness: prefers recently active agents
    3. Decay: applies exponential penalty to stale agents
    """
    for agent in agents:
        age = (now() - agent.last_heartbeat).total_seconds()

        # Decay factor: exponential with half-life
        decay = max(
            policy.decay_min_factor,
            0.5 ** (age / policy.decay_half_life_seconds)
        )

        # Apply freshness thresholds
        if age > policy.freshness_offline_seconds:
            agent.status = "offline"
        elif age > policy.freshness_stale_seconds:
            agent.status = "stale"
        elif age > policy.freshness_warn_seconds:
            agent.status = "warn"

        agent.score = decay  # Used for ranking

    # Filter out offline agents
    return [a for a in agents if a.status != "offline"]
```

### Foundational Agents

**File**: [src/agentcy/agents/foundational_agents.py](src/agentcy/agents/foundational_agents.py)

**14 Agent Types** (all in-memory state):

1. **agent_registration** - Register agents with capabilities
2. **input_validator** - Validate input risk level (LLM-based)
3. **path_seeder** - Seed initial execution paths
4. **blueprint_bidder** - Score task-agent fit (stimulus model)
5. **graph_builder** - Build plan drafts from tasks
6. **plan_validator** - Validate plans against SHACL shapes
7. **plan_cache** - Cache identical plans (hash-based)
8. **human_validator** - Human approval gates
9. **llm_strategist** - LLM-based strategy suggestions
10. **ethics_checker** - Ethical risk assessment
11. **system_executor** - Execute tasks (simulate or real)
12. **pheromone_engine** - Update stigmergic marks
13. **failure_escalation** - Escalate failures to supervisors
14. **audit_logger** - Audit trail (append-only log)

**Example** (Blueprint Bidder):
```python
# Stimulus scoring for task-agent fit
def blueprint_bidder(msg: dict) -> dict:
    task_spec = msg["task_spec"]
    agent_capabilities = msg["agent_capabilities"]

    # Score based on:
    # 1. Capability overlap
    # 2. Recent success rate (pheromone level)
    # 3. Agent load (current tasks)

    overlap = len(set(task_spec["required_capabilities"]) & set(agent_capabilities))
    pheromone = PHEROMONES.get((task_spec["task_id"], agent_id), 0.5)
    load_factor = 1.0 / (agent_load + 1)

    score = (overlap * 0.5) + (pheromone * 0.3) + (load_factor * 0.2)

    return {"raw_output": json.dumps({"agent_id": agent_id, "score": score})}
```

---

## 8. API Surface Analysis

### REST API Endpoints

**Base URL**: `http://localhost:8002` (API Service)

#### Pipelines

| Method | Endpoint | Description | Status |
|--------|----------|-------------|--------|
| POST | `/pipelines/{username}` | Create pipeline | ✅ |
| GET | `/pipelines/{username}/{pipeline_id}` | Get pipeline | ✅ |
| GET | `/pipelines/{username}` | List pipelines | ✅ |
| PUT | `/pipelines/{username}/{pipeline_id}` | Update pipeline | ✅ |

**Example Request** (Create Pipeline):
```bash
POST /pipelines/alice
Content-Type: application/json

{
  "pipeline_id": "text-processor",
  "name": "Text Processing Pipeline",
  "description": "Tokenize, analyze, summarize text",
  "nodes": [
    {"name": "tokenizer", "service": "tokenizer-v1"},
    {"name": "analyzer", "service": "nlp-analyzer"},
    {"name": "summarizer", "service": "summarizer-gpt"}
  ],
  "edges": [
    {"from": "tokenizer", "to": "analyzer"},
    {"from": "analyzer", "to": "summarizer"}
  ]
}
```

**Response** (Immediate Stub):
```json
{
  "pipeline_id": "text-processor",
  "username": "alice",
  "status": "registering",
  "created_at": "2026-02-02T10:30:00Z",
  "config_status": "pending"
}
```

**Background Processing**:
- Consumer validates DAG
- Generates RabbitMQ topology
- Persists full config (transactional)
- Emits `pipeline_registered` event

#### Agent Registry

| Method | Endpoint | Description | Status |
|--------|----------|-------------|--------|
| POST | `/agent-registry/{username}` | Register agent | ✅ |
| POST | `/agent-registry/{username}/{agent_id}/heartbeat` | Send heartbeat | ✅ |
| GET | `/agent-registry/{username}` | List agents | ✅ |
| GET | `/agent-registry/{username}/{agent_id}` | Get agent | ✅ |
| DELETE | `/agent-registry/{username}/{agent_id}` | Delete agent | ✅ |

**Query Parameters** (List):
- `service_name` - Filter by service
- `capability` - Filter by capability
- `status` - Filter by status (active/idle/offline)
- `tag` - Filter by tag

**Example Request** (Register Agent):
```bash
POST /agent-registry/alice
Content-Type: application/json

{
  "agent_id": "agent-nlp-001",
  "service_name": "nlp-analyzer",
  "capabilities": ["tokenization", "sentiment", "ner"],
  "tags": ["gpu", "high-memory"],
  "metadata": {
    "version": "2.1.0",
    "region": "us-west-2"
  }
}
```

**Heartbeat Flow**:
```bash
# Every 30 seconds (recommended)
POST /agent-registry/alice/agent-nlp-001/heartbeat
→ Updates last_heartbeat timestamp
→ Resets TTL (45 seconds)
→ Status set to "active"
```

#### Graph Store

| Method | Endpoint | Description | Status |
|--------|----------|-------------|--------|
| POST | `/graph-store/{username}/task-specs` | Upsert task spec | ✅ |
| GET | `/graph-store/{username}/task-specs/{task_id}` | Get task spec | ✅ |
| POST | `/graph-store/{username}/blueprint-bids` | Submit bid | ✅ |
| GET | `/graph-store/{username}/plan-drafts/{plan_id}` | Get plan draft | ✅ |
| POST | `/graph-store/{username}/plan-suggestions/{plan_id}/admin-decision` | Admin decision | ✅ |

**Status**: 🛠️ Basic CRUD works; advanced filtering/aggregation not implemented

#### Health & Readiness

| Method | Endpoint | Description | Status |
|--------|----------|-------------|--------|
| GET | `/health` | Basic health | ✅ |
| GET | `/ready` | Deep checks | ✅ |

**Health Check Response**:
```json
{
  "status": "healthy",
  "timestamp": "2026-02-02T10:30:00Z"
}
```

**Readiness Check Response**:
```json
{
  "status": "ready",
  "checks": {
    "rabbitmq": "ok",
    "couchbase_persistent": "ok",
    "couchbase_ephemeral": "ok"
  }
}
```

**Returns**:
- `200 OK` - All checks pass
- `503 Service Unavailable` - One or more checks fail

### Internal Message APIs

**RabbitMQ Exchanges**:

| Exchange | Type | Purpose |
|----------|------|---------|
| `commands.register_service` | direct | Service registration |
| `commands.register_pipeline` | direct | Pipeline registration |
| `commands.start_pipeline` | direct | Pipeline start |
| `commands.start_task` | direct | Task launch |
| `events.pipeline_registered` | fanout | Pipeline ready |
| `events.pipeline_started` | fanout | Run started |
| `events.pipeline_completed` | fanout | Run completed |
| `events.pipeline_failed` | fanout | Run failed |

**Per-Run Exchanges** (dynamic):
- `pipeline.{pipeline_id}.run.{run_id}.entry` - Entry point
- `pipeline.{pipeline_id}.run.{run_id}.{task_name}` - Task-specific

**Routing Key Convention**: `{base_key}.{run_id}`

---

## 9. Testing Strategy & Coverage

### Test Organization

**Directory Structure**:
```
tests/
├── e2e_tests/           # End-to-end integration (10 files)
├── integration_tests/   # Service integration (20 files)
├── unit_tests/          # Isolated logic (40 files)
├── conftest.py          # Shared fixtures
├── data/                # Test data (sample DAGs)
└── docker-compose.ci.yml # CI test environment
```

**Total**: 70 test files

### E2E Tests

**Key Tests**:

1. **test_e2e_pipeline.py**
   - Full pipeline lifecycle: create → register → start → complete
   - Verifies per-run isolation
   - Checks final run state

2. **test_docker_agent_runtime.py**
   - Container spawning
   - Log streaming
   - Resource limits

3. **test_graph_builder_e2e.py**
   - Plan draft generation
   - SHACL validation
   - Graph store persistence

4. **test_fuseki_kg_e2e.py**
   - RDF export
   - SPARQL queries
   - Provenance tracking

**Status**: ✅ Main demo pipeline passes

### Integration Tests

**Coverage**:
- Store operations (CRUD, transactions)
- Consumer message handling
- RabbitMQ topology creation
- Deduplication logic

**Infrastructure**:
- Real RabbitMQ (Docker)
- Real Couchbase (Docker)
- `docker-compose.ci.yml` for fast CI startup

### Unit Tests

**Coverage**:
- Pydantic models
- DAG validators
- Parsing layer
- Retry/backoff logic

**Strategy**: Isolated, mocked dependencies

### Test Infrastructure

**CI Compose** ([docker-compose.ci.yml](docker-compose.ci.yml)):
```yaml
services:
  rabbitmq:
    image: rabbitmq:3.13-management
  couchbase:
    image: couchbase/server:community
```

**Fixtures** ([tests/conftest.py](tests/conftest.py)):
```python
@pytest.fixture
async def resource_manager():
    """Provides RabbitMQ + Couchbase connections"""
    rm = ResourceManager()
    await rm.initialize()
    yield rm
    await rm.close()

@pytest.fixture
def test_client():
    """FastAPI test client"""
    return TestClient(app)
```

### Coverage Gaps

**Missing Tests**:
- ❌ Chaos/failure injection (network partitions, node crashes)
- ❌ Load/stress tests (concurrent pipelines, high throughput)
- ❌ Security tests (auth, injection, RBAC)
- ❌ Kubernetes-specific tests (CRDs, operators)

**Recommendation**: Add chaos testing framework (e.g., Chaos Toolkit) before production

---

## 10. Technical Debt & TODOs

### Active TODOs (from codebase scan)

**High Priority**:

1. **Payload Distribution** ([api_service/routers/pipelines.py:58](src/agentcy/api_service/routers/pipelines.py))
   ```python
   # TODO: Change this so the payload is not sent via the RabbitMQ message
   # but fetched from the store
   ```
   **Impact**: Large payloads can overwhelm RabbitMQ
   **Fix**: Use `payload_ref` pattern (similar to `output_ref`)

2. **RabbitMQ Config Persistence** ([parsing_layer/generate_rabbitmq_manifests.py:10](src/agentcy/parsing_layer/generate_rabbitmq_manifests.py))
   ```python
   # TODO: later this will just be saved into a database
   ```
   **Impact**: Topology lost on file system issues
   **Fix**: Store YAML in Couchbase with versioning

3. **Runner Return Logic** ([agent_runtime/runner.py:229](src/agentcy/agent_runtime/runner.py))
   ```python
   # TODO: fix this return it makes no sense
   ```
   **Impact**: Unclear; needs investigation
   **Fix**: Review control flow, clarify return semantics

**Medium Priority**:

4. **LLM Connector Config** ([llm_utilities/llm_connector.py:22](src/agentcy/llm_utilities/llm_connector.py))
   ```python
   # TODO: Eventually take this out as an environment variable.
   # Perform load testing and adjust accordingly.
   ```
   **Impact**: Hardcoded timeout/retry values
   **Fix**: Add config section for LLM connector tuning

5. **Orchestrator Utils** ([orchestrator_core/utils.py:89](src/agentcy/orchestrator_core/utils.py))
   ```python
   # TODO: add metadata, region, environment if needed
   ```
   **Impact**: Limited context in logs/traces
   **Fix**: Add structured metadata to all operations

6. **Pydantic Model Centralization** ([pydantic_models/pipeline_validation_models/pipeline_payload_model.py:40,45](src/agentcy/pydantic_models/pipeline_validation_models/pipeline_payload_model.py))
   ```python
   # TODO: This will serve as a centralized config for agents in the future
   # TODO: Backoff strategy
   ```
   **Impact**: Decentralized config, inconsistent backoff
   **Fix**: Create centralized agent config model

**Low Priority**:

7. **Pub Wrapper Cleanup** ([pub_sub/pub_wrapper.py:38](src/agentcy/pub_sub/pub_wrapper.py))
   ```python
   # TODO: Delete
   ```
   **Impact**: Dead code
   **Fix**: Remove

### Partial Implementations

**LLM Strategist**:
- **Status**: 🛠️ Skeleton present, provider-dependent
- **Location**: [agent_runtime/services/llm_strategist_loop.py](src/agentcy/agent_runtime/services/llm_strategist_loop.py)
- **Issue**: Requires `LLM_STRATEGIST_PROVIDER` env var; no fallback
- **Fix**: Add stub mode or default provider

**Plan Revision**:
- **Status**: 🛠️ Utilities exist, not integrated
- **Location**: [agent_runtime/services/plan_revision_utils.py](src/agentcy/agent_runtime/services/plan_revision_utils.py)
- **Issue**: Logic present but not wired into main workflow
- **Fix**: Integrate into strategist loop

**CNP (Contract Net Protocol)**:
- **Status**: ❌ Stub
- **Location**: [api_service/routers/cnp.py](src/agentcy/api_service/routers/cnp.py)
- **Issue**: Utilities present but protocol not implemented
- **Fix**: Implement CNP announce/bid/award flow

### Intentionally Omitted

**From README**:
> "This snapshot **omits** the adaptive multi-agent planning track built around a stigmergy/pheromone model."

**Components**:
- LLM Graph Builder + Supervisor (skeleton present)
- Blueprint Bidder stimulus scoring (implemented)
- Path Seeder & Pheromone Engine (implemented)
- Runtime loop with LLM Strategist (loop structure present)
- Contracts (gRPC/HTTP) and service implementations (not started)

**Status**: Design documented in [docs/future_vision.md](docs/future_vision.md)

---

## 11. Production Readiness Assessment

### What's Production-Ready ✅

**Core Pipeline Execution**:
- ✅ Per-run isolation (tested)
- ✅ Transactional updates (Couchbase)
- ✅ Idempotent processing (deduplication)
- ✅ Retry/backoff (Tenacity + custom)
- ✅ Health checks (deep + shallow)
- ✅ Observability (OTLP traces/metrics/logs)

**Data Layer**:
- ✅ Dual-bucket strategy (persistent + ephemeral)
- ✅ Connection pooling
- ✅ TTL-based cleanup
- ✅ Backoff on transient failures

**API Layer**:
- ✅ Input validation (Pydantic)
- ✅ Async request handling
- ✅ Standardized error responses

### What's NOT Production-Ready ❌

**Missing Features**:

1. **Authentication & Authorization**
   - No JWT/OAuth
   - No RBAC
   - Username from URL (trust-based)
   - **Fix**: Add auth middleware, API keys, RBAC

2. **Rate Limiting**
   - No request throttling
   - No backpressure
   - **Fix**: Add token bucket or leaky bucket rate limiter

3. **Multi-Tenancy**
   - Logical isolation only (username prefix)
   - Shared Couchbase bucket
   - Shared RabbitMQ vhost
   - **Fix**: Dedicated buckets/vhosts per tenant, resource quotas

4. **Kubernetes Deployment**
   - Docker daemon only
   - No Helm charts
   - No CRDs/operators
   - **Fix**: See [docs/future_vision.md](docs/future_vision.md) sections 5-6

5. **Secrets Management**
   - Env vars only
   - No rotation
   - No encryption at rest
   - **Fix**: Integrate Vault, AWS Secrets Manager, or Sealed Secrets

6. **Chaos/Resilience Testing**
   - No failure injection
   - No partition tests
   - No latency tests
   - **Fix**: Add Chaos Toolkit or Gremlin

7. **Monitoring & Alerting**
   - Metrics exported but no alerts
   - No SLOs/SLIs defined
   - **Fix**: Add Prometheus alerting rules, PagerDuty integration

8. **Database Migrations**
   - No versioning
   - No rollback
   - **Fix**: Add Alembic-style migration framework for Couchbase schema

9. **Audit Trail**
   - Basic logging only
   - No tamper-proof audit log
   - **Fix**: Add append-only audit store with signatures

10. **Load Balancing**
    - No built-in LB for agent runtime
    - Relies on Docker's round-robin
    - **Fix**: Add Envoy or Traefik for service mesh

### Scalability Assessment

**Current Limits** (estimated):

| Component | Limit | Bottleneck |
|-----------|-------|------------|
| API Service | ~1000 req/s | Couchbase connection pool |
| Orchestrator Core | ~500 pipelines/s | RabbitMQ topology creation |
| Agent Runtime | ~100 tasks/s/agent | Docker container startup |
| Couchbase | ~10k ops/s | Cluster size (single node) |
| RabbitMQ | ~5k msg/s | Single broker |

**Scaling Strategy**:

**Horizontal**:
- ✅ API Service: Add more replicas (stateless)
- ✅ Orchestrator Core: Add more consumers (stateless)
- ✅ Agent Runtime: Add more agents (stateless)
- ❌ Couchbase: Requires cluster setup (not configured)
- ❌ RabbitMQ: Requires cluster + federation (not configured)

**Vertical**:
- ✅ Docker container resources (memory, CPU)
- ✅ Couchbase memory quota
- ❌ RabbitMQ limited by single-node architecture

**Recommendation**: Add Kubernetes + Couchbase Operator + RabbitMQ Cluster Operator

### Security Assessment

**Vulnerabilities**:

1. **Command Injection** (Docker execution)
   - **Risk**: High
   - **Location**: [orchestrator_core/executors/docker_exec.py](src/agentcy/orchestrator_core/executors/docker_exec.py)
   - **Mitigation**: Input validation, container sandboxing

2. **XSS** (API responses)
   - **Risk**: Low (JSON API, no HTML rendering)
   - **Mitigation**: Sanitize error messages

3. **SQL/NoSQL Injection** (Couchbase queries)
   - **Risk**: Low (parameterized queries via SDK)
   - **Mitigation**: Validate user input

4. **Secrets in Logs**
   - **Risk**: Medium
   - **Location**: Env var printing in startup logs
   - **Mitigation**: Redact secrets in logs

5. **Unauthenticated Endpoints**
   - **Risk**: High (all endpoints open)
   - **Mitigation**: Add JWT middleware

**Recommendation**: Security audit before production

---

## 12. Recommendations

### Immediate Actions (Pre-Production)

1. **Add Authentication** (1 week)
   - Implement JWT middleware
   - Add API key support
   - Basic RBAC (admin/user roles)

2. **Fix High-Priority TODOs** (3 days)
   - Payload distribution via `payload_ref`
   - RabbitMQ config persistence
   - Runner return logic

3. **Add Rate Limiting** (2 days)
   - Token bucket algorithm
   - Per-user quotas

4. **Security Audit** (1 week)
   - Penetration testing
   - OWASP Top 10 review
   - Secrets redaction

5. **Chaos Testing** (1 week)
   - Network partition tests
   - Node failure tests
   - RabbitMQ/Couchbase unavailability

### Short-Term (1-3 Months)

1. **Kubernetes Migration** (3 weeks)
   - Helm charts
   - StatefulSets for Couchbase/RabbitMQ
   - HorizontalPodAutoscaler

2. **Multi-Tenancy** (2 weeks)
   - Dedicated buckets per tenant
   - Resource quotas
   - Cost tracking

3. **Monitoring & Alerting** (1 week)
   - Prometheus alerts (error rate, latency, saturation)
   - PagerDuty integration
   - Runbooks

4. **Database Migrations** (1 week)
   - Schema versioning
   - Rollback support
   - Automated testing

5. **Load Testing** (1 week)
   - Baseline: 1000 concurrent pipelines
   - Identify bottlenecks
   - Optimize hot paths

### Long-Term (3-6 Months)

1. **Multi-Agent Adaptive Planning** (6 weeks)
   - Implement stigmergy/pheromone model (per future_vision.md)
   - LLM-based Graph Builder + Supervisor
   - Runtime loop with strategist

2. **Service Mesh** (4 weeks)
   - Envoy or Linkerd
   - mTLS between services
   - Circuit breaking

3. **Advanced Observability** (2 weeks)
   - Distributed tracing (100% sampling → tail-based)
   - Exemplars (link metrics to traces)
   - Log correlation

4. **Disaster Recovery** (3 weeks)
   - Cross-region replication (Couchbase XDCR)
   - Backup/restore procedures
   - Failover testing

5. **Developer Experience** (ongoing)
   - Local development setup (Docker Compose)
   - CLI for common operations
   - Self-service pipeline creation UI

---

## Conclusion

This codebase represents a **solid foundation for a distributed pipeline orchestrator** with impressive architectural discipline. The core execution flow is well-designed, the data layer is thoughtful (dual-bucket strategy, transactions), and observability is baked in from day one.

**Key Strengths**:
- Clean separation of concerns
- Strong typing (Pydantic everywhere)
- Idempotent processing
- Per-run isolation (critical for correctness)
- Contract enforcement (prevents entire classes of bugs)

**Production Gaps**:
- Auth/RBAC missing
- Kubernetes deployment not implemented
- Chaos testing needed
- Some features intentionally omitted (per README)

**Overall Assessment**: **Demo-grade, ready for learning/extension**. With 4-6 weeks of hardening (auth, K8s, chaos testing), this could be production-ready for moderate-scale workloads (100s of pipelines/minute).

**Recommended Next Steps**:
1. Add authentication + rate limiting (critical)
2. Fix high-priority TODOs (payload distribution, runner logic)
3. Kubernetes migration (enables horizontal scaling)
4. Chaos testing (validate resilience claims)
5. Security audit (pen test, OWASP review)

**Final Note**: The deliberate omission of advanced multi-agent features (stigmergy, adaptive planning) is well-documented. The architecture supports adding these later without major refactoring—a sign of good forward-thinking design.

---

**Document Version**: 1.0
**Last Updated**: 2026-02-02
**Maintainer**: Senior Staff Engineer (Ex-FAANG/AI Labs)
**License**: Internal Use Only
