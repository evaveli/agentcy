#src/agentcy/agent_runtime/cli.py
from __future__ import annotations
import argparse
import importlib
import importlib.metadata as ilmd
import json
import os
import re
import subprocess
import sys
import time
from typing import Callable, Dict, Any, List
from urllib.parse import quote, urlparse

from agentcy.agent_runtime.bootstrap_agent import serve

# ----------------------------- utils ---------------------------------

def _log(msg: str) -> None:
    print(f"[runner] {msg}", flush=True)

def _auth_index(url: str) -> str:
    """
    Injects basic auth creds into index URL if provided via env and not already present.
    """
    if not url:
        return url
    u, p = os.getenv("NEXUS_USERNAME"), os.getenv("NEXUS_PASSWORD")
    if not (u and p):
        return url
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        return url  # already has creds
    return f"{parsed.scheme}://{quote(u)}:{quote(p)}@{parsed.netloc}{parsed.path or ''}"

def _trusted_host_args(url: str) -> List[str]:
    """
    If index URL is plain HTTP (dev Nexus) or TRUSTED_HOSTS is set, tell pip to trust host(s).
    """
    args: List[str] = []
    if not url:
        return args
    host = urlparse(url).hostname
    if host:
        if url.lower().startswith("http://"):
            args += ["--trusted-host", host]
    extra = os.getenv("PIP_TRUSTED_HOSTS", "")
    for h in filter(None, re.split(r"[,\s]+", extra)):
        args += ["--trusted-host", h]
    return args

def _already_installed(name: str, version: str) -> bool:
    """
    Best-effort check if exact version already present.
    Normalizes name (PEP 503).
    """
    norm = re.sub(r"[-_.]+", "-", name).lower()
    try:
        v = ilmd.version(name)
    except ilmd.PackageNotFoundError:
        # Try normalized name
        try:
            v = ilmd.version(norm)
        except ilmd.PackageNotFoundError:
            return False
    return v == version

def _pip_install(name: str, version: str, index_url: str, *, retries: int = 3, timeout: int = 300) -> None:
    """
    Install the exact wheel from the given index. Retries with backoff.
    """
    if _already_installed(name, version):
        _log(f"package {name}=={version} already installed; skipping pip")
        return

    idx = _auth_index(index_url)
    base = [
        sys.executable, "-m", "pip", "install",
        "--no-input", "--disable-pip-version-check", "--no-cache-dir",
        "--index-url", idx, f"{name}=={version}"
    ]
    base += _trusted_host_args(idx)

    # Optional extras from env (e.g., "--extra-index-url https://…" or "--proxy http://…")
    extra_args = os.getenv("AGENT_PIP_EXTRA_ARGS", "")
    if extra_args.strip():
        base += extra_args.split()

    # Honor a global pip timeout if provided
    if timeout and "--timeout" not in extra_args:
        base += ["--timeout", str(timeout)]

    delay = 1.0
    for attempt in range(1, max(1, retries) + 1):
        _log(f"pip install attempt {attempt}/{retries}: {name}=={version}")
        try:
            subprocess.run(base, check=True)
            _log("pip install succeeded")
            return
        except subprocess.CalledProcessError as e:
            if attempt >= retries:
                raise
            _log(f"pip failed (rc={e.returncode}); retrying in {delay:.1f}s…")
            time.sleep(delay)
            delay *= 2

def _load_entry(entry: str) -> Callable:
    """
    entry format: 'module[:callable]' (callable defaults to 'run')
    """
    mod, func = (entry.split(":", 1) + ["run"])[:2]
    m = importlib.import_module(mod)
    if not hasattr(m, func):
        raise RuntimeError(f"Entry callable '{func}' not found in module '{mod}'")
    return getattr(m, func)

def _validate_wheel_artifact(art: Dict[str, Any]) -> None:
    required = ["kind", "name", "version", "index_url", "entry"]
    missing = [k for k in required if not art.get(k)]
    if missing:
        raise SystemExit(f"artifact-json missing required fields: {', '.join(missing)}")
    if art.get("kind") != "wheel":
        raise SystemExit("artifact-json 'kind' must be 'wheel'")

# ----------------------------- CLI -----------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Agent runtime runner")
    p.add_argument("--service-name", required=True, help="Service name to report in events/health")
    p.add_argument("--artifact-json", required=False, help="WheelArtifact JSON payload")
    p.add_argument("--entry", required=False, help="module[:callable], default callable is 'run'")
    p.add_argument("--index-url", required=False, help="Override index URL (dev/tools)")
    p.add_argument("--pip-retries", type=int, default=int(os.getenv("AGENT_PIP_RETRIES", "3")))
    p.add_argument("--pip-timeout", type=int, default=int(os.getenv("AGENT_PIP_TIMEOUT", "300")))
    args = p.parse_args()

    # Resolve entry + (optional) install
    if args.artifact_json:
        try:
            art: Dict[str, Any] = json.loads(args.artifact_json)
        except json.JSONDecodeError as e:
            raise SystemExit(f"--artifact-json is not valid JSON: {e}") from e

        _validate_wheel_artifact(art)

        name    = str(art["name"])
        version = str(art["version"])
        # Allow CLI override (useful in testing)
        index_url = args.index_url or str(art.get("index_url") or os.getenv("NEXUS_PYPI_URL") or "")

        if not index_url:
            raise SystemExit("No index URL provided (artifact.index_url missing and NEXUS_PYPI_URL not set).")

        _log(f"installing {name}=={version} from {urlparse(index_url).netloc}")
        _pip_install(name, version, index_url, retries=args.pip_retries, timeout=args.pip_timeout)

        entry = str(art["entry"])
    else:
        # Manual mode (preinstalled deps)
        if not args.entry:
            raise SystemExit("Provide --artifact-json or --entry")
        entry = args.entry

    logic_fn = _load_entry(entry)

    # Hand off to the generic bootstrap (starts RM, consumers, /health, graceful shutdown)
    _log(f"starting service '{args.service_name}' with entry '{entry}'")
    serve(service_name=args.service_name, logic_fn=logic_fn)

if __name__ == "__main__":
    main()
