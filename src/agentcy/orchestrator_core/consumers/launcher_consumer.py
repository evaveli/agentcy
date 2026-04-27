from __future__ import annotations
import asyncio, json, logging, os
from typing import Any, Dict

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.orchestrator_core.executors import docker_exec  # use Docker for both kinds

log = logging.getLogger(__name__)

QUEUE = os.getenv("START_TASK_QUEUE", "commands.start_task")


async def start_task_consumer(rm: ResourceManager) -> None:
    # match the pattern in agent_runtime.consumers
    await rm.ready_event.wait()
    log.info("StartTask consumer: ResourceManager ready; proceeding to bind queue.")

    rabbit_mgr = rm.rabbit_mgr
    if rabbit_mgr is None:
        log.warning("StartTask consumer skipped: no RabbitMQ manager.")
        return

    async with rabbit_mgr.get_channel() as ch:
        await ch.set_qos(prefetch_count=4)
        log.info("Setting up StartTask consumer…")

        queue = await ch.declare_queue(QUEUE, durable=True)
        log.info("StartTask queue declared: %s", queue.name)
        log.info("Listening for StartTask on '%s'", QUEUE)

        async with queue.iterator() as it:
            async for msg in it:
                async with msg.process():  # mirrors your pattern: ack on normal exit
                    try:
                        body = msg.body.decode("utf-8", "replace")
                        log.debug("Received start_task body: %s", body)
                        payload = json.loads(body)
                        await _handle_start_task(payload)
                        log.info(
                            "Handled start_task service=%s runtime=%s",
                            payload.get("service_name"), payload.get("runtime")
                        )
                    except Exception:
                        # keep the pattern: log and let msg.process() ack
                        log.exception("Error in StartTask handler")


def _merge_env(task_env: Dict[str, str]) -> Dict[str, str]:
    """Base infra env + task overrides; provide AMQP_URI alias for legacy code."""
    base = {
        "RABBITMQ_URL": os.getenv("RABBITMQ_URL", ""),
        "AMQP_URI":     os.getenv("RABBITMQ_URL", ""),  # alias
        "RABBITMQ_HOST": os.getenv("RABBITMQ_HOST", ""),
        "RABBITMQ_PORT": os.getenv("RABBITMQ_PORT", ""),
        "RABBITMQ_DEFAULT_USER": os.getenv("RABBITMQ_DEFAULT_USER", ""),
        "RABBITMQ_DEFAULT_PASS": os.getenv("RABBITMQ_DEFAULT_PASS", ""),
        "RABBITMQ_DEFAULT_VHOST": os.getenv("RABBITMQ_DEFAULT_VHOST", ""),
        "CB_CONN_STR":  os.getenv("CB_CONN_STR", ""),
        "CB_USER":      os.getenv("CB_USER", ""),
        "CB_PASS":      os.getenv("CB_PASS", ""),
        "CB_BUCKET":    os.getenv("CB_BUCKET", ""),
        "CB_SCOPE":     os.getenv("CB_SCOPE", ""),
        "AGENT_RUNTIME_CB": os.getenv("AGENT_RUNTIME_CB", ""),
        "AGENT_RUNTIME_EPHEMERAL_CB": os.getenv("AGENT_RUNTIME_EPHEMERAL_CB", ""),
        "SYSTEM_EXECUTION_MODE": os.getenv("SYSTEM_EXECUTION_MODE", ""),
        # wheel installs (Nexus PyPI auth)
        "NEXUS_USERNAME": os.getenv("NEXUS_USERNAME", ""),
        "NEXUS_PASSWORD": os.getenv("NEXUS_PASSWORD", ""),
        "NEXUS_PYPI_URL": os.getenv("NEXUS_PYPI_URL", ""),
        # agent registry (runtime heartbeat)
        "AGENT_REGISTRY_URL": os.getenv("AGENT_REGISTRY_URL", ""),
        "AGENT_REGISTRY_USERNAME": os.getenv("AGENT_REGISTRY_USERNAME", ""),
        "AGENT_CAPABILITIES": os.getenv("AGENT_CAPABILITIES", ""),
        "AGENT_TAGS": os.getenv("AGENT_TAGS", ""),
        "AGENT_DESCRIPTION": os.getenv("AGENT_DESCRIPTION", ""),
        "AGENT_REGISTRY_HEARTBEAT_SECONDS": os.getenv("AGENT_REGISTRY_HEARTBEAT_SECONDS", ""),
        "AGENT_REGISTRY_TTL_SECONDS": os.getenv("AGENT_REGISTRY_TTL_SECONDS", ""),
        "AGENT_REGISTRY_TIMEOUT_SECONDS": os.getenv("AGENT_REGISTRY_TIMEOUT_SECONDS", ""),
        "AGENT_REGISTRY_FAILURE_THRESHOLD": os.getenv("AGENT_REGISTRY_FAILURE_THRESHOLD", ""),
        # Semantic/KG integrations
        "FUSEKI_URL": os.getenv("FUSEKI_URL", ""),
        "FUSEKI_DATASET": os.getenv("FUSEKI_DATASET", ""),
        "FUSEKI_ENABLE": os.getenv("FUSEKI_ENABLE", ""),
        "FUSEKI_TIMEOUT": os.getenv("FUSEKI_TIMEOUT", ""),
        "FUSEKI_USER": os.getenv("FUSEKI_USER", ""),
        "FUSEKI_PASSWORD": os.getenv("FUSEKI_PASSWORD", ""),
        "AGENTCY_BASE_URI": os.getenv("AGENTCY_BASE_URI", ""),
        "SEMANTIC_RDF_EXPORT": os.getenv("SEMANTIC_RDF_EXPORT", ""),
        "SHACL_SHAPES_PATH": os.getenv("SHACL_SHAPES_PATH", ""),
    }
    merged = {**base, **(task_env or {})}
    # ensure AMQP_URI is set if caller only provided RABBITMQ_URL in overrides
    merged.setdefault("AMQP_URI", merged.get("RABBITMQ_URL", ""))
    return merged


async def _handle_start_task(p: Dict[str, Any]) -> None:
    # minimal validation
    runtime  = str(p["runtime"])
    artifact_raw = p["artifact"]
    if not isinstance(artifact_raw, dict):
        raise ValueError("artifact must be a JSON object")
    artifact = dict(artifact_raw)
    svc_name = str(p["service_name"])
    run_env  = _merge_env(p.get("task_environ", {}))

    if runtime == "python_plugin":
        kind = artifact.get("kind") or ("entry" if artifact.get("entry") else None)
        if kind == "entry":
            await docker_exec.launch_entry(service_name=svc_name, artifact=artifact, env=run_env)
        else:
            await docker_exec.launch_wheel(service_name=svc_name, artifact=artifact, env=run_env)
    elif runtime == "container":
        await docker_exec.launch_oci(service_name=svc_name, artifact=artifact, env=run_env)
    else:
        raise ValueError(f"Unknown runtime: {runtime!r}")
