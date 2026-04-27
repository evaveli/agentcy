# tests/conftest.py
from __future__ import annotations
from curses.ascii import US
import subprocess
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
import os
import socket



PROJECT_ROOT = Path(__file__).resolve().parents[1]
dotenv_path: Path = Path(__file__).parent / ".env.test"

# Load the project-wide .env (for Nexus creds, etc.) before overriding with test values
load_dotenv(PROJECT_ROOT / ".env", override=False)
load_dotenv(dotenv_path, override=True)


def _ensure_registry_env() -> None:
    registry = os.environ.get("NEXUS_REGISTRY", "").strip()
    fallback = os.environ.get("E2E_NEXUS_REGISTRY", "localhost:5001")

    def _host_resolves(host: str) -> bool:
        try:
            socket.getaddrinfo(host, None)
            return True
        except socket.gaierror:
            return False

    def _strip_host(ref: str) -> str:
        ref = ref.replace("http://", "").replace("https://", "")
        return ref.split("/", 1)[0]

    if registry:
        host_only = _strip_host(registry).split(":", 1)[0]
        if not _host_resolves(host_only):
            os.environ["NEXUS_REGISTRY"] = fallback
    else:
        os.environ["NEXUS_REGISTRY"] = fallback


_ensure_registry_env()

# Normalize core service envs for local pytest runs before importing app modules.
os.environ.setdefault("AMQP_URI", "amqp://guest:guest@localhost:5672/")
os.environ.setdefault("RABBITMQ_URL", os.environ["AMQP_URI"])
os.environ.setdefault("START_TASK_QUEUE", "commands.start_task.test")
os.environ["CB_CONN_STR"] = "couchbase://localhost"
os.environ["CB_USER"]     = "Administrator"
os.environ["CB_PASS"]     = "secret-password"
os.environ["CB_BUCKET"]   = "dev-bucker"
os.environ["CB_SCOPE"]    = "_default"
os.environ["CB_EPHEMERAL_BUCKET_NAME"] = "pipeline_runs"
# Always force the compose network so docker containers can reach rabbit/cb
os.environ["AGENT_DOCKER_NETWORK"] = "agentcy-stack_agentcy"

print("AMQP_URI     =", os.getenv("AMQP_URI"))
print("RABBITMQ_URL =", os.getenv("RABBITMQ_URL"))

# Dump the exact envs your code uses
AMQP_KEYS = ["AMQP_URI", "RABBITMQ_URL", "RABBITMQ_HOST", "RABBITMQ_PORT", "RABBITMQ_USER", "RABBITMQ_PASS", "RABBITMQ_VHOST"]
CB_KEYS   = ["CB_CONN_STR", "CB_USER", "CB_PASS", "CB_BUCKET", "CB_EPHEMERAL_BUCKET_NAME", "CB_SCOPE"]

print("[ENV] Rabbit vars:")
for k in AMQP_KEYS: print(f"   {k}={os.getenv(k)!r}")
print("[ENV] Couchbase vars:")
for k in CB_KEYS: print(f"   {k}={os.getenv(k)!r}")

from src.agentcy.agent_runtime.bootstrap_agent import serve
from src.agentcy.pydantic_models.pipeline_validation_models.pipeline_run_model import EntryMessage, TaskState

from src.agentcy.agent_runtime.consumers import _start_pipeline_consumer
from tests.data.complex_payload import COMPLEX_PIPELINE_PAYLOAD_TEMPLATE
from tests.e2e_tests.helpers_e2e import publish_start_pipeline, wait_until_registered

import asyncio, os, sys, socket, time
from contextlib import asynccontextmanager, suppress
from src.agentcy.agent_runtime.event_handler import make_on_pipeline_event
import aio_pika
import httpx
import pytest
import pytest_asyncio

from tests.utils import safe_started, safe_finish # noqa: F401
from src.agentcy.orchestrator_core.consumers.pipeline import register_pipeline_consumer
from src.agentcy.orchestrator_core.consumers.launcher_consumer import start_task_consumer
from src.agentcy.pipeline_orchestrator.resource_manager import resource_manager_context
from src.agentcy.api_service.main import create_app
from src.agentcy.pipeline_orchestrator.resource_manager import resource_manager_context

import logging


fastapi_app = create_app()

import importlib

# Reload the config module to re-read environment into its constants
import src.agentcy.orchestrator_core.couch.config as cb_config
importlib.reload(cb_config)

print("[FORCE] CB_CONN_STR =", cb_config.CB_CONN_STR)
print("[FORCE] CB_BUCKET   =", cb_config.CB_BUCKET)

TEST_USER = "e2e_tester"
RUN_CONFIG = "e2e_run_cfg"
POLL_INTERVAL = 0.5          # seconds
POLL_TIMEOUT  = 15.0         # seconds  (30 × 0.5 s)
SRC_DIR = os.path.abspath(os.path.join(__file__, "..", "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

AMQP_URI    = os.environ["AMQP_URI"]       # e.g. amqp://guest:guest@localhost:5672/
CB_CONN_STR = os.environ["CB_CONN_STR"]    # e.g. couchbase://localhost





def pytest_configure(config):
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d  %(message)s",
    )

    logging.getLogger("aio_pika").setLevel(logging.INFO)


# ─────────────────────────────────────────────────────────────────────────────
# 1) wait until RabbitMQ & Couchbase are reachable (once per session)
# ─────────────────────────────────────────────────────────────────────────────
def _wait(host: str, port: int, timeout: float = 60.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), 1):
                return
        except OSError:
            time.sleep(2)
    raise RuntimeError(f"{host}:{port} not ready within {timeout}s")

_wait("localhost", 5672)   # Rabbit
_wait("localhost", 8091)   # Couchbase
_wait("localhost", 11210)  # Couchbase KV


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


@pytest.fixture(autouse=True)
def _restore_event_loop(event_loop):
    asyncio.set_event_loop(event_loop)

# ─────────────────────────────────────────────────────────────────────────────
# 2) raw aio-pika connection (shared)
# ─────────────────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture(scope="session")
async def rabbitmq_connection():
    conn = await aio_pika.connect_robust(AMQP_URI, timeout=15)
    yield conn
    await conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# 3) real ResourceManager (uses live Couchbase + Rabbit)
# ─────────────────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_rm(rabbitmq_connection):                  # depend on Rabbit
    global _rm
    async with resource_manager_context(rmq=True, cb=True, ephemeral=True) as rm:
        _rm = rm
        fastapi_app.state.rm = rm                         # for non-Depends paths
        yield
    _rm = None

# ─────────────────────────────────────────────────────────────────────────────
# 4) attach a connection wrapper exposing BOTH .channel() and get_channel()
# ─────────────────────────────────────────────────────────────────────────────
class _ChanWrap:
    """
    Gives CommandPublisher its `.channel()` *and*
    the e2e listener helper its `get_channel()`.
    """
    def __init__(self, conn: aio_pika.RobustConnection):
        self._conn = conn

    # ---- used by CommandPublisher -----------------------------------------
    async def channel(self, *args, **kw):
        return await self._conn.channel(*args, **kw)

    # ---- used by tests/helpers (context-manager style) --------------------
    @asynccontextmanager
    async def get_channel(self, *args, **kw):
        ch = await self._conn.channel(*args, **kw)
        try:
            yield ch
        finally:
            await ch.close()

@pytest_asyncio.fixture(scope="session", autouse=True)
async def _attach_channel_wrapper(_init_rm, rabbitmq_connection):
    _rm.rabbit_conn = _ChanWrap(rabbitmq_connection)      # type: ignore[attr-defined]
    yield


@pytest_asyncio.fixture(scope="session", autouse=True)
async def start_register_pipeline_consumer(_init_rm):
    """Run the consumer that processes RegisterPipelineCommand for the tests."""
    stop_event = asyncio.Event()

    async def _runner():
        try:
            await register_pipeline_consumer(_rm)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_runner(), name="register_pipeline_consumer")

    # give it a moment to declare queue & start consuming
    await asyncio.sleep(1.0)
    yield

    # graceful shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

@pytest_asyncio.fixture(scope="session")
async def start_task_consumer_fixture(_init_rm):
    """Ensure the Docker start-task consumer is running for tests that need it."""
    task = asyncio.create_task(start_task_consumer(_rm), name="start_task_consumer")
    await asyncio.sleep(1.0)
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# ─────────────────────────────────────────────────────────────────────────────
# 6) HTTPX client for tests
# ─────────────────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture(scope="session")
async def async_client():
    transport = httpx.ASGITransport(app=fastapi_app)  #type: ignore
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

# ─────────────────────────────────────────────────────────────────────────────
# 7) expose the (already-built) RM to e2e helpers
# ─────────────────────────────────────────────────────────────────────────────
@pytest_asyncio.fixture(scope="session")
async def resource_manager_fixture():        # <- new name keeps it short
    yield _rm# same instance

from src.agentcy.agent_runtime.services import graph_builder as graph_builder_service
from src.agentcy.agent_runtime.services import plan_cache as plan_cache_service
from src.agentcy.agent_runtime.services import plan_validator as plan_validator_service
from src.agentcy.agent_runtime.services import input_validator as input_validator_service
from src.agentcy.agent_runtime.services import supervisor_agent as supervisor_agent_service
from src.agentcy.agent_runtime.services import agent_registration as agent_registration_service
from src.agentcy.agent_runtime.services import path_seeder as path_seeder_service
from src.agentcy.agent_runtime.services import blueprint_bidder as blueprint_bidder_service
from src.agentcy.agent_runtime.services import pheromone_engine as pheromone_engine_service
from src.agentcy.agent_runtime.services import human_validator as human_validator_service
from src.agentcy.agent_runtime.services import llm_strategist as llm_strategist_service
from src.agentcy.agent_runtime.services import ethics_checker as ethics_checker_service
from src.agentcy.agent_runtime.services import system_executor as system_executor_service
from src.agentcy.agent_runtime.services import failure_escalation as failure_escalation_service
from src.agentcy.agent_runtime.services import audit_logger as audit_logger_service


# CNP failure injection: keyed by (username, service_name) → remaining failure count.
# Tests set entries before starting a pipeline; the hook decrements and raises on match.
_FAILURE_INJECTIONS: dict = {}


class MultiAgentLogic:
    async def __call__(
        self,
        rm_or_message,
        run_id: str | None = None,
        to_task: str | None = None,
        triggered_by=None,
        message=None,
    ):
        if message is None:
            message = rm_or_message
            rm = None
        else:
            rm = rm_or_message

        service_name = getattr(message, "service_name", None) or to_task or ""

        # CNP failure injection hook (keyed by unique username — no cross-test contamination)
        _inj_key = (getattr(message, "username", ""), service_name)
        if _inj_key in _FAILURE_INJECTIONS and _FAILURE_INJECTIONS[_inj_key] > 0:
            _FAILURE_INJECTIONS[_inj_key] -= 1
            raise RuntimeError(f"[CNP_FAILURE_INJECTION] {_inj_key}")

        if rm is not None:
            if service_name == "graph_builder":
                return await graph_builder_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "plan_validator":
                return await plan_validator_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "plan_cache":
                return await plan_cache_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "input_validator":
                return await input_validator_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "supervisor_agent":
                return await supervisor_agent_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "agent_registration":
                return await agent_registration_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "path_seeder":
                return await path_seeder_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "blueprint_bidder":
                return await blueprint_bidder_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "pheromone_engine":
                return await pheromone_engine_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "human_validator":
                return await human_validator_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "llm_strategist":
                return await llm_strategist_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "ethics_checker":
                return await ethics_checker_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "system_executor":
                return await system_executor_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "failure_escalation":
                return await failure_escalation_service.run(rm, run_id or "", to_task or "", triggered_by, message)
            if service_name == "audit_logger":
                return await audit_logger_service.run(rm, run_id or "", to_task or "", triggered_by, message)

        await safe_started(message)  # optional
        await safe_finish(message, output={"echo": "ok"}, error=None)
        return {"raw_output": {"echo": "ok"}}

# -----------------------------------
# 8) http_client alias
# -----------------------------------
@pytest_asyncio.fixture(scope="session", name="http_client")
async def http_client_alias(async_client: httpx.AsyncClient):
    return async_client

@pytest_asyncio.fixture(scope="session")
async def created_pipeline_id(async_client: httpx.AsyncClient) -> str:
    """Create the complex pipeline once and return its id."""
    payload = COMPLEX_PIPELINE_PAYLOAD_TEMPLATE.copy()
    payload["authors"] = [TEST_USER]

    resp = await async_client.post(f"/pipelines/{TEST_USER}", json=payload)
    resp.raise_for_status()

    return resp.json()["pipeline_id"]


@pytest_asyncio.fixture
async def kickoff_response(
    created_pipeline_id,
    resource_manager_fixture,     # still injected – might be useful later
    async_client):
    """
    • Fire-and-forget a StartPipelineCommand.
    • Poll until the first run document exists.
    • Return run_id, pipeline_id, pipeline_config_id for the tests.
    """
    pipeline_id = created_pipeline_id

    # 1) fire-and-forget command
    await publish_start_pipeline(TEST_USER, pipeline_id, RUN_CONFIG)

    # 2) wait until the pipeline itself is registered
    await wait_until_registered(async_client, TEST_USER, pipeline_id)

    # 3) poll the helper route until it returns a non-empty run_id
    runs_url = f"/pipelines/{TEST_USER}/{pipeline_id}/runs?latest_run=true"
    run_id: str | None = None
    for _ in range(int(POLL_TIMEOUT / POLL_INTERVAL)):
        resp = await async_client.get(runs_url)
        if resp.status_code == 200:
            run_id = resp.json().get("run_id")
            if run_id:                   # non-empty → we’re done
                break
        await asyncio.sleep(POLL_INTERVAL)
    else:
        raise TimeoutError("Run document never appeared within the timeout window.")

    # 4) fetch the full run doc – we need pipeline_config_id, status, …
    run_doc_resp = await async_client.get(
        f"/pipelines/{TEST_USER}/{pipeline_id}/{run_id}"
    )
    run_doc_resp.raise_for_status()
    run_doc = run_doc_resp.json()

    return {
        "run_id":             run_id,
        "pipeline_id":        pipeline_id,
        "pipeline_config_id": run_doc["pipeline_config_id"],
    }


@pytest_asyncio.fixture(scope="session", autouse=True)
async def start_start_pipeline_consumer(_init_rm):
    """Keep the StartPipelineCommand consumer alive for the whole session."""
    logic    = MultiAgentLogic()
    on_event = make_on_pipeline_event(
        rm=_rm, microservice_logic=logic, service_name="entry",
    )

    task = asyncio.create_task(
        _start_pipeline_consumer(_rm, on_event), # type: ignore[arg-type]
        name="start_pipeline_consumer",
    )

    try:
        yield                          # ← keep it running until session ends
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


# ---------- OTel test fixtures ------------------------------------------------
@pytest.fixture(autouse=True)
def otel_pipeline(monkeypatch):
    from types import SimpleNamespace
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    # InMemoryMetricReader import path varies by version
    try:
        from opentelemetry.sdk.metrics._internal.export import InMemoryMetricReader
    except Exception:
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # type: ignore

    # ---------- tracing ----------
    span_exporter = InMemorySpanExporter()
    tp = trace.get_tracer_provider()
    if not isinstance(tp, TracerProvider):
        tp = TracerProvider(resource=Resource.create({"service.name": "test-service"}))
        trace.set_tracer_provider(tp)
    tp.add_span_processor(SimpleSpanProcessor(span_exporter))

    # ---------- metrics (force a single provider everyone uses) ----------
    metric_reader = InMemoryMetricReader()

    from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider

    # Prefer attaching the reader via the constructor to support older SDKs
    try:
        sdk_mp = SDKMeterProvider(
            resource=Resource.create({"service.name": "test-service"}),
            metric_readers=[metric_reader],
        )
    except TypeError:
        # Fallbacks for older variants
        sdk_mp = SDKMeterProvider(resource=Resource.create({"service.name": "test-service"}))
        if hasattr(sdk_mp, "register_metric_reader"):
            sdk_mp.register_metric_reader(metric_reader)  # type: ignore
        elif hasattr(sdk_mp, "add_metric_reader"):
            sdk_mp.add_metric_reader(metric_reader) # type: ignore

    # 1) Make all future get_meter() calls return meters from OUR provider
    monkeypatch.setattr(metrics, "get_meter", lambda *a, **k: sdk_mp.get_meter(*a, **k), raising=False)

    # 2) Make get_meter_provider() report OUR provider
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: sdk_mp, raising=False)

    # 3) Make set_meter_provider() a no-op (but if someone passes an SDK provider, attach our reader to it too)
    def _noop_set_meter_provider(mp):
        try:
            if hasattr(mp, "register_metric_reader"):
                mp.register_metric_reader(metric_reader)
            elif hasattr(mp, "add_metric_reader"):
                mp.add_metric_reader(metric_reader)
        except Exception:
            pass
        # do NOT replace our forced provider
        return None

    monkeypatch.setattr(metrics, "set_meter_provider", _noop_set_meter_provider, raising=False)

    # 4) Belt-and-suspenders: point internal API global (if present) to OUR provider
    try:
        import opentelemetry.metrics._internal as api_metrics_internal
        monkeypatch.setattr(api_metrics_internal, "_METER_PROVIDER", sdk_mp, raising=False)
    except Exception:
        pass

    yield SimpleNamespace(spans=span_exporter, metric_reader=metric_reader)

    try:
        tp.force_flush()
    except Exception:
        pass

# --------------------------------------------------------------------------- DUMMY AGENT

def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def _rm_image(image: str) -> None:
    subprocess.run(["docker", "image", "rm", "-f", image], text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _image_exists(image: str) -> bool:
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0


def _image_created_at(image: str) -> datetime | None:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.Created}}", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return None
    created_raw = result.stdout.strip()
    if not created_raw:
        return None
    try:
        created_raw = created_raw.replace("Z", "+00:00")
        return datetime.fromisoformat(created_raw)
    except ValueError:
        return None


def _latest_mtime(paths: list[Path]) -> float:
    latest = 0.0
    for path in paths:
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
            continue
        if path.is_dir():
            for entry in path.rglob("*.py"):
                try:
                    latest = max(latest, entry.stat().st_mtime)
                except FileNotFoundError:
                    continue
    return latest


def _run_build(cmd: list[str]) -> None:
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode == 0:
        return
    output = f"{result.stdout}\n{result.stderr}".lower()
    if "no space left on device" in output:
        pytest.skip("docker build skipped: no space left on device")
    raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr or result.stdout}")


@pytest.fixture(scope="session", autouse=True)
def e2e_dummy_agent() -> str:
    """
    Make sure the dummy agent image exists for e2e tests.

    - Builds from tests/e2e_tests/docker_agent
    - Tags it using NEXUS_REGISTRY (if set) or local name
    - Optionally pushes when E2E_PUSH_IMAGE=1
    """
    if os.getenv("E2E_SKIP_DOCKER", "0") == "1":
        pytest.skip("E2E_SKIP_DOCKER=1; skipping docker-based e2e agent setup")

    docker_dir = Path(__file__).parent / "e2e_tests" / "docker_agent"

    if not docker_dir.exists():
        raise RuntimeError(f"e2e agent Docker context not found: {docker_dir}")

    registry = os.getenv("NEXUS_REGISTRY", "").rstrip("/")
    image_repo = os.getenv("E2E_AGENT_REPO", "agentcy/e2e-agent")
    tag = os.getenv("E2E_AGENT_TAG", "latest")

    image = f"{registry}/{image_repo}:{tag}" if registry else f"{image_repo}:{tag}"

    try:
        subprocess.run([
            "docker",
            "version",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except FileNotFoundError:
        pytest.skip("docker CLI not available; skipping e2e docker tests")
    except subprocess.CalledProcessError:
        pytest.skip("docker daemon not reachable; skipping e2e docker tests")

    if _image_exists(image):
        return image

    _run_build(["docker", "build", "-t", image, str(docker_dir)])

    # Default to not pushing during local/CI runs unless explicitly enabled.
    push_required = os.getenv("E2E_PUSH_IMAGE", "0") != "0"
    if push_required and registry:
        username = os.getenv("NEXUS_USERNAME") or os.getenv("REGISTRY_USERNAME")
        password = os.getenv("NEXUS_PASSWORD") or os.getenv("REGISTRY_PASSWORD")
        if not (username and password):
            raise RuntimeError("Registry creds missing; cannot push e2e image")

        registry_host = registry.replace("http://", "").replace("https://", "").split("/", 1)[0]
        login_proc = subprocess.run(
            [
                "docker",
                "login",
                "--username",
                username,
                "--password-stdin",
                registry_host,
            ],
            input=f"{password}\n",
            text=True,
            capture_output=True,
        )
        if login_proc.returncode != 0:
            # If registry isn't reachable, proceed without pushing/pulling.
            print(f"[e2e] docker login failed ({login_proc.stderr or login_proc.stdout}); skipping push/pull")
            return image

        try:
            _run(["docker", "push", image])

            # Verify registry serves the image, then drop local copy to force runtime pulls
            _rm_image(image)
            _run(["docker", "pull", image])
            _rm_image(image)
        except Exception as exc:
            print(f"[e2e] registry push/pull failed ({exc}); continuing with local image")

    return image


@pytest.fixture(scope="session")
def e2e_runtime_image() -> str:
    """
    Build the agent runtime image used by entry-based services.
    """
    if os.getenv("E2E_SKIP_DOCKER", "0") == "1":
        pytest.skip("E2E_SKIP_DOCKER=1; skipping docker-based runtime image setup")

    try:
        subprocess.run(
            ["docker", "version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except FileNotFoundError:
        pytest.skip("docker CLI not available; skipping entry runtime tests")
    except subprocess.CalledProcessError:
        pytest.skip("docker daemon not reachable; skipping entry runtime tests")

    image = os.getenv("AGENT_RUNTIME_IMAGE", "agentcy/agent_runtime:base")
    dockerfile = Path(__file__).parents[1] / "docker" / "Dockerfile.runtime"
    if not dockerfile.exists():
        raise RuntimeError(f"runtime Dockerfile not found: {dockerfile}")

    force_rebuild = os.getenv("E2E_FORCE_REBUILD", "0") == "1"
    exists = _image_exists(image)
    if exists and not force_rebuild:
        created_at = _image_created_at(image)
        src_root = PROJECT_ROOT / "src" / "agentcy" / "agent_runtime"
        latest_src = _latest_mtime([dockerfile, src_root])
        if created_at is None or latest_src > created_at.timestamp():
            force_rebuild = True

    if force_rebuild or not exists:
        _run_build(["docker", "build", "-f", str(dockerfile), "-t", image, str(PROJECT_ROOT)])
    os.environ["AGENT_RUNTIME_IMAGE"] = image
    return image

    
