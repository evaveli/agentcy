# Agentcy Pipeline Orchestrator

Distributed pipeline runner for Python microservices with per-run RabbitMQ topology, ACID pipeline config updates (Couchbase), and a strict, testable microservice contract.

* **Per-run isolation:** exchanges/queues suffixed with `pipeline_run_id`
* **Transactional config:** pipeline definition + final config + version history written atomically
* **Microservice contract:** business logic returns exactly `{"raw_output": "<non-empty string>"}` — runtime handles persistence, tracking, and routing

See the **[docs/](docs)** folder for architecture, design decisions, and lessons learned.

---

## Portfolio Snapshot Notice

This repository is a **curated, public snapshot** of a larger private project. It’s intended for portfolio review and demonstration. Some advanced work (multi-agent planning, adaptive routing) is intentionally **excluded** here.

---

## Redactions & Omissions

This snapshot **omits** the adaptive multi-agent planning track built around a stigmergy/pheromone model. That stream is still in flux and tied to private research and environments.

Omitted (high level):

* Agent registry & tags (capability/fit/coverage + freshness/decay)
* LLM Graph Builder + Supervisor (intent → draft graph → validated plan)
* Blueprint Bidder (stimulus scoring, `S > τ`, AffordanceMarks)
* Path Seeder & Pheromone Engine (initial marks + runtime decay)
* DAG computation from the adaptive plan
* Runtime loop with an LLM Strategist proposing graph deltas
* Contracts (gRPC/HTTP) and service implementations for the above

> For the conceptual outlook, see **`docs/future_vision.md`**.

---

## Quick-Start

### 1) Full local stack

Starts Couchbase (+ bootstrap), RabbitMQ, Nexus (PyPI & Docker), `api_service`, `orchestrator_core`.

```bash
docker compose -p agentcy-stack up --build -d
```

Health checks:

```bash
curl -fsS http://localhost:8001/health
curl -fsS http://localhost:8080/openapi.json | head -n 5
```

Dashboards: RabbitMQ [http://localhost:15672](http://localhost:15672) • Couchbase [http://localhost:8091](http://localhost:8091) • Nexus [http://localhost:8081](http://localhost:8081)

Stop:

```bash
docker compose -p agentcy-stack down        # keep volumes
# docker compose -p agentcy-stack down -v   # wipe data
```

### 2) Minimal stack for tests / CI

```bash
docker compose -f docker-compose.ci.yml up -d
pytest -q tests/e2e_tests
docker compose -f docker-compose.ci.yml down -v
```

> The end-to-end demo DAG lives at **`tests/data/complex-payload`** and is exercised by the test suite.

### 3) Kick off the sample DAG manually (optional)

After the pipeline is registered (running `pytest` once will do this E2E), publish a `StartPipelineCommand`:

```bash
python scripts/start_run.py <username> <pipeline_id> <pipeline_run_config_id>
```

You should see `pipeline_started` and the run complete in logs.

### 4) Optional: base agent runtime container

```bash
docker compose --profile agents up -d agent_runtime
docker exec -it agent_runtime bash
# Inside the container:
python -m agentcy.agent_runtime.runner --service-name echo_task --entry examples.agents.echo:run
```

---

## Docs

Everything lives in **[`docs/`](docs)**:

* [architecture.md](docs/architecture.md)
* [config-parser.md](docs/config-parser.md)
* [models.md](docs/models.md)
* [persistence.md](docs/persistence.md)
* [runtime.md](docs/runtime.md)
* [testing.md](docs/testing.md)
* [the\_resource\_manager.md](docs/the_resource_manager.md)
* [logging.md](docs/logging.md)
* [lessons\_learned.md](docs/lessons_learned.md)
* [future\_vision.md](docs/future_vision.md)

Assets: [`docs/assets/`](docs/assets)

---

## Status

* ✅ One DAG runs end-to-end (`tests/data/complex-payload`)
* ✅ Per-run messaging, transactional pipeline updates, strict service contract
* 🛠️ Observability WIP (JSON logs now; traces/metrics next)
* 🛠️ UX for building agents/pipelines in progress

---

## Disclaimer

This is a **demo/learning** project, not production-ready software. It’s provided for educational and portfolio purposes and may omit optimizations, hardening, and proprietary components.

> Example `.env` templates will be added under `env-examples/`. You can use them as-is or customize.

---