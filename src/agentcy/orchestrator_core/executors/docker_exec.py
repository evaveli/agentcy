from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from agentcy.orchestrator_core.executors import container_runtime

# ---- Config via env ----------------------------------------------------------
DOCKER_NETWORK = os.getenv("AGENT_DOCKER_NETWORK")            # e.g. "yourstack_default"
MEM_LIMIT     = os.getenv("AGENT_MEM_LIMIT")                  # e.g. "512m"
NANO_CPUS     = os.getenv("AGENT_NANOCPUS")                   # e.g. "500000000" (0.5 CPU)
FOLLOW_LOGS   = os.getenv("AGENT_FOLLOW_LOGS", "1") != "0"    # set to "0" to disable

# Optional registry auth (for private Nexus registry pulls)
REGISTRY_USERNAME = os.getenv("REGISTRY_USERNAME")
REGISTRY_PASSWORD = os.getenv("REGISTRY_PASSWORD")
REGISTRY_EMAIL    = os.getenv("REGISTRY_EMAIL")  # optional

log = logging.getLogger(__name__)

# ---- Helpers ----------------------------------------------------------------
_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_.-]+")

def _runtime_image() -> str:
    return os.getenv("AGENT_RUNTIME_IMAGE", "agentcy/agent_runtime:base")

def _sanitize_name(name: str, *, maxlen: int = 63) -> str:
    s = _NAME_SAFE.sub("-", name).strip("-.")
    return s[:maxlen] or "agent"

def _env(env: Dict[str, str]) -> Dict[str, str]:
    # Ensure everything is str (docker-py requirement)
    return {str(k): ("" if v is None else str(v)) for k, v in env.items()}

def _res_limits() -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    if MEM_LIMIT:
        d["mem_limit"] = MEM_LIMIT
    if NANO_CPUS and NANO_CPUS.isdigit() and int(NANO_CPUS) > 0:
        d["nano_cpus"] = int(NANO_CPUS)
    return d

def _registry_auth() -> Optional[Dict[str, str]]:
    if REGISTRY_USERNAME and REGISTRY_PASSWORD:
        auth = {"username": REGISTRY_USERNAME, "password": REGISTRY_PASSWORD}
        if REGISTRY_EMAIL:
            auth["email"] = REGISTRY_EMAIL
        return auth
    return None

def _ensure_image(image: str) -> None:
    auth = _registry_auth()
    container_runtime.ensure_image(image, auth=auth, logger=log)

async def _follow_logs(container_id: str, container_name: str) -> None:
    # Move the blocking log stream to a thread to avoid blocking the event loop
    def _stream() -> None:
        try:
            for chunk in container_runtime.stream_logs(container_id, logger=log):
                try:
                    print(f"[{container_name}] {chunk.decode(errors='replace').rstrip()}", flush=True)
                except Exception:
                    # best-effort logging
                    pass
        except Exception:
            pass
    await asyncio.to_thread(_stream)

def _create_and_start_container(
    *,
    image: str,
    name: str,
    environment: Dict[str, str],
    command: Optional[List[str]],
) -> str:
    limits = _res_limits()
    network = DOCKER_NETWORK if container_runtime._network_exists(DOCKER_NETWORK) else None
    try:
        container_id = container_runtime.create_container(
            image=image,
            name=name,
            environment=environment,
            command=command,
            mem_limit=limits.get("mem_limit"),
            nano_cpus=limits.get("nano_cpus"),
            network=network,
            auto_remove=True,
            logger=log,
        )
        try:
            container_runtime.start_container(container_id, logger=log, name=name)
            return container_id
        except Exception as exc:
            log.warning("API start failed (%s); attempting docker run fallback", exc)
    except Exception as exc:
        log.warning("API create failed (%s); attempting docker run fallback", exc)

    return container_runtime.run_container_cli(
        image=image,
        name=name,
        environment=environment,
        command=command,
        network=network,
        logger=log,
    )

# ---- Launchers ---------------------------------------------------------------
async def launch_wheel(*, service_name: str, artifact: Dict[str, Any], env: Dict[str, str]) -> None:
    """
    Run a wheel-based agent inside the generic agent_runtime image.
    The runner CLI inside that image will pip-install the wheel from Nexus
    using the fields in `artifact` (name/version/index_url/entry).
    """
    try:
        runtime_image = _runtime_image()
        _ensure_image(runtime_image)
        safe_name = _sanitize_name(f"agent-{service_name}-{artifact.get('version', '')}")
        cmd = [
            "python", "-m", "agentcy.agent_runtime.cli",
            "--service-name", service_name,
            "--artifact-json", json.dumps(artifact, separators=(",", ":")),
        ]
        container_id = _create_and_start_container(
            image=runtime_image,
            name=safe_name,
            environment=_env(env),
            command=cmd,
        )
        if FOLLOW_LOGS:
            asyncio.create_task(_follow_logs(container_id, safe_name))
    except Exception as e:
        raise RuntimeError(f"Docker launch_wheel failed: {e}") from e

async def launch_entry(*, service_name: str, artifact: Dict[str, Any], env: Dict[str, str]) -> None:
    """
    Run an entry-based agent inside the generic agent_runtime image.
    The runner CLI will import the entrypoint and serve it.
    """
    entry = artifact.get("entry")
    if not entry:
        raise ValueError("Entry artifact requires 'entry'")
    try:
        runtime_image = _runtime_image()
        _ensure_image(runtime_image)
        safe_name = _sanitize_name(f"agent-{service_name}-entry")
        cmd = [
            "python", "-m", "agentcy.agent_runtime.cli",
            "--service-name", service_name,
            "--entry", str(entry),
        ]
        container_id = _create_and_start_container(
            image=runtime_image,
            name=safe_name,
            environment=_env(env),
            command=cmd,
        )
        if FOLLOW_LOGS:
            asyncio.create_task(_follow_logs(container_id, safe_name))
    except Exception as e:
        raise RuntimeError(f"Docker launch_entry failed: {e}") from e

async def launch_oci(*, service_name: str, artifact: Dict[str, Any], env: Dict[str, str]) -> None:
    """
    Run a container-based agent (already built & pushed to Nexus registry).
    Assumes the image entrypoint/CMD starts the agent logic.
    `artifact` must contain at least: repo, tag (and typically registry ref).
    """
    try:
        image_ref = f"{artifact['repo']}:{artifact['tag']}"
        _ensure_image(image_ref)

        safe_name = _sanitize_name(f"agent-{service_name}-{artifact['tag']}")
        container_id = _create_and_start_container(
            image=image_ref,
            name=safe_name,
            environment=_env(env),
            command=None,
        )
        if FOLLOW_LOGS:
            asyncio.create_task(_follow_logs(container_id, safe_name))
    except Exception as e:
        raise RuntimeError(f"Docker launch_oci failed: {e}") from e
