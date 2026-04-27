# Agentcy — Distributed Pipeline Orchestrator

Agentcy is a distributed pipeline orchestrator for Python microservices. It runs DAG-shaped workflows where each step is an independently deployable service, with **per-run RabbitMQ topology** for isolation, **ACID-backed configuration storage** in Couchbase, and a deliberately tiny microservice contract that pushes plumbing concerns (persistence, routing, retries, fan-in aggregation, lifecycle tracking) into the runtime.

> **This project is developed as part of a master's thesis.** 
---

## Table of contents

- [What it does](#what-it-does)
- [Why it exists](#why-it-exists)
- [Architecture at a glance](#architecture-at-a-glance)
- [Key design properties](#key-design-properties)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [The microservice contract](#the-microservice-contract)
- [Persistence model](#persistence-model)
- [Testing](#testing)
- [Observability](#observability)
- [Documentation index](#documentation-index)
- [Status & roadmap](#status--roadmap)
- [Redactions & omissions](#redactions--omissions)
- [Disclaimer](#disclaimer)

---

## What it does

Agentcy lets you **declare a pipeline as a DAG** of services, **register** each service with the system, and then **kick off runs** that flow data through the graph. The orchestrator:

1. Validates the DAG (cycle detection, connectivity, fan-in/out inference, parallel-level computation).
2. Generates a final, executable configuration and persists it atomically alongside a version snapshot.
3. Stands up **per-run** RabbitMQ exchanges and queues so concurrent runs cannot cross-talk.
4. Drives messages through the graph, aggregates fan-in points, persists outputs, tracks state, and emits terminal events.
5. Finalizes the run by archiving the ephemeral run document into the persistent store and tearing down per-run resources.

Each service stays small — it implements business logic, returns a single string, and lets the runtime handle everything else.

---

## Why it exists

Pipeline orchestration tools usually force a tradeoff: either you get a powerful but heavy framework (Airflow, Dagster, Argo) with substantial operational footprint, or you build something bespoke that re-invents idempotency, fan-in, and run isolation poorly.

This project is the engineering substrate for thesis research into **how a small, opinionated runtime can absorb the hard parts of distributed orchestration** — at-least-once delivery, per-run isolation, transactional configuration, fan-in semantics — so that domain microservices can stay tiny and testable. The thesis itself studies how this substrate can be extended with adaptive multi-agent planning (kept private for now); the public snapshot here is the deterministic orchestration core that everything else is built on top of.

---

## Architecture at a glance

The system has three main runtime components plus shared infrastructure:

- **API Service** ([src/agentcy/api_service/](src/agentcy/api_service/)) — FastAPI app for pipeline/service CRUD and for publishing `StartPipelineCommand` messages onto `commands.start_pipeline`.
- **Orchestrator Core** ([src/agentcy/orchestrator_core/](src/agentcy/orchestrator_core/)) — owns the persistent stores, transactional pipeline creation/updates, the config parser, and the central run-finalization path.
- **Agent Runtime** ([src/agentcy/agent_runtime/](src/agentcy/agent_runtime/)) — a single async process that hosts a microservice. It wires up consumers, ensures per-run topology, normalizes inbound messages, calls the user's logic, persists outputs, updates run state, and forwards results.

Shared infrastructure:

- **RabbitMQ** for messaging (per-run exchanges and queues; direct + topic exchange types).
- **Couchbase** for persistence — a **persistent** bucket for definitions/configs/history and an **ephemeral** bucket for hot in-flight state and large output blobs.
- **Nexus** for hosting microservice wheels (PyPI) and runtime images (Docker).

For diagrams and the full component breakdown, see [docs/architecture.md](docs/architecture.md) and [docs/runtime.md](docs/runtime.md).

---

## Key design properties

- **Per-run isolation.** Every exchange, queue, and routing key in a run is suffixed with `pipeline_run_id`. Concurrent runs on the same pipeline never share infrastructure objects, so backpressure or failure in one run cannot leak into another.
- **Direct vs. topic exchange mapping.** The config parser suggests one-to-one edges as `direct` and broadcast edges as `fanout`; the runtime maps fanouts to `topic` exchanges so routing keys can carry the `run_id`. Fan-in steps aggregate upstream envelopes before invoking the service.
- **ACID configuration updates.** Pipeline definition, generated final config, and version snapshot are written in a single Couchbase multi-document transaction. The system never observes a half-updated pipeline.
- **Forward-only run state.** The `PipelineRunTracker` enforces forward-only state transitions per task, with an LRU+TTL dedupe layer to make at-least-once delivery effectively-once at the application level.
- **Strict microservice contract.** Services return exactly `{"raw_output": "<non-empty string>"}`. Anything else is a contract violation. The runtime owns persistence, output references, retries, and routing.
- **Small messages, lazy enrichment.** Large task outputs are persisted as blobs and travel as `output_ref`. The forwarder enriches downstream messages from the doc store on demand.
- **Bounded retries.** Tenacity wraps microservice calls, persistence writes, and publishes with small, sane backoffs.

See [docs/lessons_learned.md](docs/lessons_learned.md) for the engineering tradeoffs behind these choices.

---

## Tech stack

| Layer            | Choice                                                          |
| ---------------- | --------------------------------------------------------------- |
| Language         | Python 3.11+                                                    |
| Web framework    | FastAPI (API service), aiohttp (agent runtime `/health`)        |
| Messaging        | RabbitMQ (3.13) — direct and topic exchanges, quorum-ready      |
| Persistence      | Couchbase (Community) — persistent + ephemeral buckets          |
| Schemas          | Pydantic v2 models with JSON-LD export; SHACL/TTL ontology      |
| Artifact hosting | Sonatype Nexus (PyPI repo + Docker registry)                    |
| Observability    | JSON structured logs; Grafana / Loki / Tempo / Prometheus stack |
| Testing          | Pytest (unit, integration, E2E against real RabbitMQ/Couchbase) |
| Container/orch   | Docker Compose for local; Kubernetes is on the roadmap          |

---

## Repository layout

```
agentcy/
├── src/agentcy/
│   ├── api_service/          # FastAPI app — kickoff + CRUD endpoints
│   ├── orchestrator_core/    # Stores, parser, executors, finalizer
│   ├── agent_runtime/        # Per-service async runtime (consumers + forwarder + tracker)
│   ├── pipeline_orchestrator # Pipeline lifecycle glue
│   ├── parsing_layer/        # DAG validation + topology inference
│   ├── pydantic_models/      # Authoritative schemas (commands, events, run state)
│   ├── rabbitmq_workflow/    # Messaging primitives + per-run topology helpers
│   ├── observability/        # Structured logging, tracing hooks
│   ├── llm_utilities/        # LLM client utilities (used by demo agents)
│   ├── semantic/             # Ontology-aware helpers (TTL/SHACL)
│   ├── shared_lib/           # Cross-cutting utilities
│   └── demo_agents/          # Example microservices for the E2E DAG
├── schemas/                  # YAML pipeline templates, ontology, JSON schemas
├── tests/
│   ├── unit_tests/
│   ├── integration_tests/
│   └── e2e_tests/            # Boots real RabbitMQ + Couchbase
├── docs/                     # Architecture, design docs, thesis chapters
├── ci/                       # CI compose stack + bootstrap scripts
├── scripts/                  # Bootstrap + experiment scripts
├── docker/                   # Per-service Dockerfiles
├── observability/            # Loki, Tempo, Prometheus, Grafana configs
├── evaluation/               # Thesis evaluation artifacts
├── docker-compose.yml        # Full local stack
└── setup.py / pytest.ini     # Packaging + test config
```

---

## Quick start

### Prerequisites

- Docker + Docker Compose
- Python 3.11+ (only needed if you want to run scripts or tests outside containers)
- A `.env` file at the repo root with Couchbase / Nexus / RabbitMQ credentials. Templates will land under `env-examples/`.

### 1) Full local stack

Brings up Couchbase (with bootstrap), RabbitMQ, Nexus (PyPI + Docker), the API service, and the orchestrator core:

```bash
docker compose -p agentcy-stack up --build -d
```

Health checks:

```bash
curl -fsS http://localhost:8001/health
curl -fsS http://localhost:8080/openapi.json | head -n 5
```

Dashboards:

- RabbitMQ — http://localhost:15672
- Couchbase — http://localhost:8091
- Nexus — http://localhost:8081

Tear down:

```bash
docker compose -p agentcy-stack down        # keep volumes
docker compose -p agentcy-stack down -v     # wipe data
```

### 2) Minimal stack for tests / CI

```bash
docker compose -f ci/docker-compose.ci.yml up -d
pytest -q tests/e2e_tests
docker compose -f ci/docker-compose.ci.yml down -v
```

The end-to-end demo DAG used by the suite lives at [tests/data/complex-payload](tests/data/complex-payload).

### 3) Kick off a sample run manually

After the pipeline is registered (running the E2E suite once is enough), publish a `StartPipelineCommand`:

```bash
python scripts/start_run.py <username> <pipeline_id> <pipeline_run_config_id>
```

You should see a `pipeline_started` event followed by terminal `COMPLETED` / `FAILED` events.

### 4) Optional: base agent runtime container

```bash
docker compose --profile agents up -d agent_runtime
docker exec -it agent_runtime bash
# inside the container:
python -m agentcy.agent_runtime.runner --service-name echo_task --entry examples.agents.echo:run
```

---

## The microservice contract

Every microservice that runs inside the agent runtime must implement a callable that returns:

```python
{"raw_output": "<non-empty string>"}
```

That's it. The runtime is responsible for:

- Parsing inbound `EntryMessage` / `TaskState` envelopes (including fan-in aggregates).
- Enriching messages by dereferencing `output_ref` blobs from the doc store.
- Persisting the service's output and updating the run document.
- Forwarding the result to **all** downstream edges with per-run routing keys.
- Tracking task state (forward-only) and emitting terminal events when the run reaches its final tasks.

Anything richer (config, dependency injection, retries) layers on top of this contract — but the return shape is non-negotiable, because it's what makes the runtime able to treat services as interchangeable units.

See [docs/runtime.md](docs/runtime.md) for the forwarder/tracker internals and [docs/models.md](docs/models.md) for the message schemas.

---

## Persistence model

Two buckets, deliberately separated by lifecycle:

- **Persistent bucket** (`dev-bucker` by default): pipeline definitions, generated final configs, version snapshots, archived runs, service registrations, user artifact catalog.
- **Ephemeral bucket** (`pipeline_runs` by default): hot in-flight run documents and large task output blobs. TTL-friendly.

Pipeline create/update writes are wrapped in a Couchbase multi-document transaction so that the definition, the parsed/inferred final config, and a version snapshot all commit together — or none of them do. Hot-path writes (run state updates, output persistence) use **CAS + bounded backoff** instead of transactions for throughput.

For collection names, key patterns, and the full transactional flow, see [docs/persistence.md](docs/persistence.md).

---

## Testing

Tests run against **real** infrastructure — no broker/database mocks for anything that crosses a process boundary. The matrix:

| Layer         | What's covered                                                                          |
| ------------- | --------------------------------------------------------------------------------------- |
| Models        | Pydantic schemas, enum strictness, date coercion, JSON-LD export                        |
| Config parser | DAG validity, Kahn cycle detection, connectivity, fan-in/out metadata                   |
| Stores        | Couchbase transactional create/update/version, ephemeral docs, CAS retries              |
| Messaging     | Per-run naming (`.run_id` suffix), idempotent declarations, direct vs. topic mapping    |
| Runtime       | Forwarder + tracker — contract enforcement, output_ref persistence, dedupe              |
| E2E           | Full stack — `StartPipelineCommand` → `pipeline_started` → run reaches `COMPLETED`      |

See [docs/testing.md](docs/testing.md) for fixtures, layout, and how to add a new test.

---

## Observability

The system emits **structured JSON logs** with run/task correlation IDs so a single run can be traced across the API service, orchestrator core, and every agent runtime that touched it. A Grafana / Loki / Tempo / Prometheus stack is configured under [observability/](observability/) for local exploration.




## Status & roadmap

Current state:

- **Done** — One DAG runs end-to-end ([tests/data/complex-payload](tests/data/complex-payload)).
- **Done** — Per-run messaging isolation, transactional pipeline config updates, strict service contract.
- **In progress** — Observability beyond logs (distributed traces, run-level metrics).
- **In progress** — UX for building agents and pipelines without hand-editing YAML.
- **Planned** — Kubernetes migration (per-run Jobs, RabbitMQ + Couchbase operators, Helm charts).
- **Planned** — Reliability / chaos testing suite (broker turbulence, DB failover, network partitions, poison messages).


---

## Redactions & omissions

This snapshot **omits** the adaptive multi-agent planning track built around a stigmergy / pheromone model. That stream is still in flux and tied to private research and environments.

Omitted (high level):

- Agent registry & tags (capability/fit/coverage with freshness/decay)
- LLM Graph Builder + Supervisor (intent → draft graph → validated plan)
- Blueprint Bidder (stimulus scoring, `S > τ`, AffordanceMarks)
- Path Seeder & Pheromone Engine (initial marks + runtime decay)
- Adaptive DAG computation
- Runtime loop with an LLM Strategist proposing graph deltas
- gRPC/HTTP contracts and service implementations for the above


---

## Disclaimer

This is a **research / thesis** project, not production-ready software. It is provided for academic and portfolio purposes and may omit optimizations, hardening, and proprietary components that exist in private branches. Example `.env` templates will be added under `env-examples/` — you can use them as-is or adapt to your environment.
