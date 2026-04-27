# src/agentcy/api_service/services/registry.py
from __future__ import annotations

import asyncio, os, json, contextlib
from typing import Tuple, List

NEXUS_REGISTRY = os.getenv("NEXUS_REGISTRY")  # e.g. nexus.example.com
if not NEXUS_REGISTRY:
    raise RuntimeError("NEXUS_REGISTRY is not configured")
REGISTRY_HOST = NEXUS_REGISTRY.replace("http://", "").replace("https://", "")  # e.g. "nexus:5001"
REGISTRY_USER  = os.getenv("REGISTRY_USERNAME")
REGISTRY_PASS  = os.getenv("REGISTRY_PASSWORD")

class RegistryError(Exception): ...

def _copy_auth_args_dest() -> List[str]:
    if not (REGISTRY_USER and REGISTRY_PASS and REGISTRY_HOST):
        raise RegistryError("Registry credentials/URL missing")
    return ["--dest-username", REGISTRY_USER, "--dest-password", REGISTRY_PASS]

def _inspect_auth_args() -> List[str]:
    if not (REGISTRY_USER and REGISTRY_PASS and REGISTRY_HOST):
        raise RegistryError("Registry credentials/URL missing")
    # skopeo inspect uses --creds USER:PASS
    return ["--creds", f"{REGISTRY_USER}:{REGISTRY_PASS}"]

async def _run(cmd: List[str], *, timeout: float | None = None) -> Tuple[int, str]:
    """
    Run a command, capture combined stdout/stderr. No Optional stdout types -> no Pylance warning.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        rc: int = await proc.wait()
        raise RegistryError(f"Command timed out (rc={rc}): {' '.join(cmd[:3])} …")
    # get an int (not int|None) for type checkers
    rc: int = await proc.wait()
    return rc, (out or b"").decode(errors="replace")

async def skopeo_copy_docker_archive(
    archive_path: str, repo: str, tag: str, *, timeout: float | None = None
) -> Tuple[int, str]:
    if not REGISTRY_HOST:
        raise RegistryError("NEXUS_REGISTRY is not configured")
    dest = f"docker://{REGISTRY_HOST}/{repo}:{tag}"
    cmd = ["skopeo", "copy"] + _copy_auth_args_dest() + ["--dest-tls-verify=false", f"docker-archive:{archive_path}", dest]
    return await _run(cmd, timeout=timeout)

async def skopeo_inspect_digest(
    repo: str, tag: str, *, timeout: float | None = None
) -> str:
    if not REGISTRY_HOST:
        raise RegistryError("NEXUS_REGISTRY is not configured")
    ref = f"docker://{REGISTRY_HOST}/{repo}:{tag}"

    # 1) Raw check (optional)
    cmd_raw = ["skopeo", "inspect", "--raw", "--tls-verify=false"] + _inspect_auth_args() + [ref]
    rc_raw, out_raw = await _run(cmd_raw, timeout=timeout)
    if rc_raw != 0:
        raise RegistryError(out_raw)

    # 2) JSON with Digest
    cmd = ["skopeo", "inspect", "--tls-verify=false"] + _inspect_auth_args() + [ref]
    rc, out = await _run(cmd, timeout=timeout)
    if rc != 0:
        raise RegistryError(out)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise RegistryError(f"skopeo inspect returned non-JSON output: {e}\n{out[:300]}") from e
    digest = data.get("Digest")
    if not digest or not isinstance(digest, str):
        raise RegistryError(f"Digest field missing in inspect output for {repo}:{tag}")
    return digest