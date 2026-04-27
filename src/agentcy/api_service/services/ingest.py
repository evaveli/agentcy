# src/agentcy/api_service/services/ingest.py
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shlex
import tarfile
import tempfile
from typing import Any, Dict, Optional, List, TYPE_CHECKING
import uuid

from pydantic import AnyHttpUrl
from agentcy.pydantic_models.service_registration_model import WheelArtifact
from agentcy.api_service.services.nexus_pypi import twine_upload, UploadError

# Type-only import to avoid heavy runtime deps
if TYPE_CHECKING:
    from agentcy.orchestrator_core.stores.catalog_store import UserCatalogStore

from agentcy.orchestrator_core.stores.catalog_store import CatalogConflict, _doc_id

# Pydantic v2 / v1 AnyHttpUrl adapter
try:
    from pydantic import TypeAdapter
    _HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
    def _as_http_url(s: str) -> AnyHttpUrl:
        return _HTTP_URL_ADAPTER.validate_python(s)  # type: ignore[return-value]
except Exception:
    from pydantic.tools import parse_obj_as
    def _as_http_url(s: str) -> AnyHttpUrl:
        return parse_obj_as(AnyHttpUrl, s)

log = logging.getLogger(__name__)

NEXUS_PYPI_URL = os.getenv("NEXUS_PYPI_URL", "")
NEXUS_REGISTRY = os.getenv("NEXUS_REGISTRY", "nexus:5001")  # docker-internal registry host:port

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

async def _run(cmd: list[str]) -> str:
    # run skopeo quietly unless explicitly debugging
    quiet = os.getenv("INGEST_DEBUG", "0") != "1"
    if quiet and "copy" in cmd and "--quiet" not in cmd:
        i = cmd.index("copy") + 1
        cmd.insert(i, "--quiet")

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()

    if proc.returncode != 0:
        # save tiny capped raw logs to files for manual inspection ONLY
        tempdir = tempfile.mkdtemp(prefix="skopeo_fail_")
        ts = uuid.uuid4().hex[:8]
        out_p = os.path.join(tempdir, f"stdout.{ts}.log")
        err_p = os.path.join(tempdir, f"stderr.{ts}.log")
        with open(out_p, "wb") as f: f.write(out[:1<<20])   # cap 1 MB
        with open(err_p, "wb") as f: f.write(err[:1<<20])

        def summarize(b: bytes, max_chars=2000) -> str:
            s = b.decode(errors="ignore")
            # keep only human-useful lines; strip binary noise
            lines = [ln for ln in s.splitlines()
                     if re.search(r'(?i)error|denied|unauthorized|forbidden|tls|certificate|manifest|name|invalid|not found', ln)]
            if not lines:
                lines = s.splitlines()
            s = "\n".join(lines[-20:])  # last few relevant lines
            return (s[:max_chars] + " … [truncated]") if len(s) > max_chars else s

        msg = (
            f"skopeo failed rc={proc.returncode}\n"
            f"CMD: {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"stderr (summary):\n{summarize(err)}\n"
            f"(raw logs capped & saved in {tempdir})"
        )
        raise RuntimeError(msg)

    return out.decode(errors="ignore")

def _is_docker_archive(path: str) -> bool:
    try:
        with tarfile.open(path, "r:*") as tf:
            return any(m.name == "manifest.json" for m in tf.getmembers())
    except Exception:
        return False

async def _catalog_upsert(store: "UserCatalogStore", username: str, env: str, entry: Dict[str, Any]):
    """
    Be tolerant to different store signatures:
    - upsert(env=..., username=..., entry=...)
    - upsert(username, entry)
    (sync or async)
    Return (doc_id, state_str)
    """
    fn = getattr(store, "upsert", None)
    if fn is None:
        raise RuntimeError("Catalog store has no 'upsert'")

    # Try keyword signature first
    try:
        if asyncio.iscoroutinefunction(fn):
            res = await fn(env=env, username=username, entry=entry)
        else:
            res = fn(env=env, username=username, entry=entry)
    except TypeError:
        # Fallback positional
        if asyncio.iscoroutinefunction(fn):
            res = await fn(username, entry)
        else:
            res = fn(username, entry)

    # Normalize result
    if isinstance(res, tuple) and len(res) == 2:
        return str(res[0]), str(res[1])
    return str(res), "upserted"

async def ingest_wheel(
    wheel_path: str,
    *,
    name: str,
    version: str,
    entry: str,
    status: str,
    requires_python: str = "~=3.11",
    agentcy_abi: str = "1",
    signatures: Optional[List[str]] = None,
    env: str,
    username: str,
    user_catalog_store: "UserCatalogStore",
) -> Dict[str, Any]:
    if not NEXUS_PYPI_URL:
        raise RuntimeError("NEXUS_PYPI_URL is not configured")

    digest = _sha256(wheel_path)

    try:
        rc, out = await twine_upload(wheel_path)
    except UploadError as e:
        raise RuntimeError(f"Twine upload error: {e}") from e
    if rc != 0:
        lowered = (out or "").lower()
        if "already exists" in lowered or "409" in lowered or "conflict" in lowered:
            log.warning("Twine upload reported existing artifact; continuing.")
        else:
            raise RuntimeError(f"Twine upload failed: {out[-500:]}")

    index_url: AnyHttpUrl = _as_http_url(NEXUS_PYPI_URL)

    wheel = WheelArtifact(
        name=name,
        version=version,
        sha256=digest,
        index_url=index_url,
        entry=entry,
        requires_python=requires_python,
        agentcy_abi=agentcy_abi,
    )
    doc: Dict[str, Any] = wheel.model_dump()
    doc.update({"kind": "wheel", "status": status})
    if signatures:
        doc["signatures"] = signatures

    try:
        doc_id, state = await _catalog_upsert(user_catalog_store, username, env, doc)
    except CatalogConflict:
        # allow multiple service registrations to reference the same wheel
        log.warning("Catalog immutable conflict for %s/%s; keeping existing entry.", username, env)
        doc_id, state = _doc_id(env, username), "unchanged"

    artifact_ref = {
        "kind": "wheel",
        "name": name,
        "version": version,
        "sha256": digest,
        "index_url": str(index_url),
        "entry": entry,
        "requires_python": requires_python,
        "agentcy_abi": agentcy_abi,
    }
    return {"catalog_doc_id": doc_id, "state": state, "artifact": artifact_ref}

async def ingest_oci(
    path: Optional[str],  # docker save tar
    repo: str,
    tag: str,
    status: str,
    entry: Optional[str],
    env: str,
    username: str,
    user_catalog_store: "UserCatalogStore",
) -> Dict[str, str]:
    if not path:
        raise ValueError("path to docker-archive tar is required for this flow")
    if not _is_docker_archive(path):
        raise ValueError("Uploaded file is not a docker-archive (missing manifest.json). Use `docker save`.")

    dest_registry = NEXUS_REGISTRY                  # e.g. nexus:5001
    dest_user     = os.getenv("NEXUS_USERNAME")
    dest_pass     = os.getenv("NEXUS_PASSWORD")

    src_ref  = f"docker-archive:{path}"
    dest_img = f"{dest_registry}/{repo}:{tag}"
    dest_ref = f"docker://{dest_img}"

    tls_flag = "--dest-tls-verify=false" if os.getenv("REGISTRY_INSECURE", "1") == "1" else "--dest-tls-verify=true"

    cmd = ["skopeo", "copy", "--insecure-policy", tls_flag]
    if dest_user and dest_pass:
        cmd += ["--dest-creds", f"{dest_user}:{dest_pass}"]
    cmd += [src_ref, dest_ref]

    log.info("skopeo copy → %s", dest_img)
    await _run(cmd)

    catalog_doc = {
        "kind": "oci",
        # canonical keys expected by catalog_store._path()
        "name": repo,
        "version": tag,
        # keep these if other code/UX likes them
        "repo": repo,
        "tag": tag,
        "image": dest_img,
        "status": status,
        "entry": entry,
        "env": env,
        "username": username,
    }
    doc_id, _state = await _catalog_upsert(user_catalog_store, username, env, catalog_doc)
    return {"artifact": dest_img, "catalog_doc_id": doc_id}
