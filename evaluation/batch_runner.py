#!/usr/bin/env python3
"""Batch runner for ablation study — launches pipelines across configs and clients.

Usage:
    python -m evaluation.batch_runner --reps 5                        # C0 only (default)
    python -m evaluation.batch_runner --reps 5 --configs C0 C2 C7     # specific configs
    python -m evaluation.batch_runner --reps 5 --configs ALL          # all 9 configs
    python -m evaluation.batch_runner --convergence --reps 15         # convergence mode
    python -m evaluation.batch_runner --failure-test                  # failure injection
    python -m evaluation.batch_runner --configs C0 C1 --resume       # continue from manifest
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib import request as urllib_request, error as urllib_error, parse as urllib_parse

API_BASE = os.getenv("EVAL_API_BASE", "http://127.0.0.1:8082")
RABBITMQ_API_BASE = os.getenv("EVAL_RABBITMQ_API", "http://127.0.0.1:15673/api")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

ALL_CLIENTS = ["freshco", "techparts", "greenleaf", "quickship", "nordicsteel"]

# Services that need restart when env vars change
AFFECTED_SERVICES = [
    "call-transcription-agent",
    "deal-summary-agent",
    "client-necessity-agent",
    "proposal-template-agent",
    "warehouse-agent-south", "warehouse-agent-north", "warehouse-agent-central",
    "cost-estimator-agent", "speed-estimator-agent", "compliance-agent",
    "graph-builder-agent", "plan-validator-agent", "plan-cache-agent",
    "llm-strategist-agent", "ethics-checker-agent",
    "api_service", "orchestrator_core",
]

EXPECTED_AGENT_REGISTRY_COUNT = sum(
    1 for service in AFFECTED_SERVICES
    if service not in {"api_service", "orchestrator_core"}
)
EXPECTED_PIPELINE_EVENT_QUEUES = [
    "pipeline_events_call_transcription",
    "pipeline_events_deal_summary",
    "pipeline_events_client_necessity",
    "pipeline_events_proposal_template",
    "pipeline_events_warehouse_north",
    "pipeline_events_warehouse_central",
    "pipeline_events_warehouse_south",
    "pipeline_events_cost_estimator",
    "pipeline_events_speed_estimator",
    "pipeline_events_compliance",
    "pipeline_events_graph_builder",
    "pipeline_events_plan_validator",
    "pipeline_events_plan_cache",
    "pipeline_events_llm_strategist",
    "pipeline_events_ethics_checker",
]
QUEUE_SERVICE_MAP = {
    "commands.start_pipeline": "call-transcription-agent",
    "pipeline_events_call_transcription": "call-transcription-agent",
    "pipeline_events_deal_summary": "deal-summary-agent",
    "pipeline_events_client_necessity": "client-necessity-agent",
    "pipeline_events_proposal_template": "proposal-template-agent",
    "pipeline_events_warehouse_north": "warehouse-agent-north",
    "pipeline_events_warehouse_central": "warehouse-agent-central",
    "pipeline_events_warehouse_south": "warehouse-agent-south",
    "pipeline_events_cost_estimator": "cost-estimator-agent",
    "pipeline_events_speed_estimator": "speed-estimator-agent",
    "pipeline_events_compliance": "compliance-agent",
    "pipeline_events_graph_builder": "graph-builder-agent",
    "pipeline_events_plan_validator": "plan-validator-agent",
    "pipeline_events_plan_cache": "plan-cache-agent",
    "pipeline_events_llm_strategist": "llm-strategist-agent",
    "pipeline_events_ethics_checker": "ethics-checker-agent",
}

ABLATION_CONFIGS = {
    "C0": {
        "description": "Full framework (control)",
        "env": {},  # defaults
    },
    "C1": {
        "description": "No pheromone adaptation",
        "env": {"PHEROMONE_ENABLE": "0"},
    },
    "C2": {
        "description": "No CNP bidding (round-robin)",
        "env": {
            "CNP_MANAGER_ENABLE": "0",
            "AGENT_ASSIGNMENT_BASELINE": "round_robin",
        },
    },
    "C3": {
        "description": "No SHACL validation",
        "env": {"SHACL_ENABLE": "0"},
    },
    "C4": {
        "description": "No compliance agent (pass-through)",
        "env": {"COMPLIANCE_AGENT_ENABLE": "0"},
    },
    "C3+C4": {
        "description": "No SHACL + no compliance",
        "env": {"SHACL_ENABLE": "0", "COMPLIANCE_AGENT_ENABLE": "0"},
    },
    "C5": {
        "description": "No LLM strategist",
        "env": {"LLM_STRATEGIST_ENABLE": "0", "LLM_STRATEGIST_PROVIDER": ""},
    },
    "C6": {
        "description": "No ethics checker",
        "env": {
            "ETHICS_CHECK_ENABLE": "0",
            "EXECUTION_REQUIRE_ETHICS": "0",
            "LLM_ETHICS_PROVIDER": "",
        },
    },
    "C7": {
        "description": "Minimal baseline (all coordination off)",
        "env": {
            "CNP_MANAGER_ENABLE": "0", "AGENT_ASSIGNMENT_BASELINE": "direct",
            "PHEROMONE_ENABLE": "0", "SHACL_ENABLE": "0",
            "COMPLIANCE_AGENT_ENABLE": "0", "EVAL_PIPELINE_MODE": "minimal",
            "LLM_STRATEGIST_ENABLE": "0", "ETHICS_CHECK_ENABLE": "0",
            "LLM_STRATEGIST_PROVIDER": "", "EXECUTION_REQUIRE_ETHICS": "0",
            "LLM_ETHICS_PROVIDER": "",
        },
    },
}

# Default env values to restore after each config
DEFAULT_ENV_VALUES = {
    "CNP_MANAGER_ENABLE": "1",
    "AGENT_ASSIGNMENT_BASELINE": "direct",
    "PHEROMONE_ENABLE": "1",
    "SHACL_ENABLE": "1",
    "SHACL_SHAPES_PATH": "schemas/plan_draft_shapes.ttl",
    "PLAN_SHACL_RULESET_PATH": "schemas/plan_draft_shacl.json",
    "COMPLIANCE_AGENT_ENABLE": "1",
    "LLM_STRATEGIST_ENABLE": "1",
    "ETHICS_CHECK_ENABLE": "1",
    "EVAL_PIPELINE_MODE": "full",
    "LLM_STRATEGIST_PROVIDER": "openai",
    "EXECUTION_REQUIRE_ETHICS": "1",
    "LLM_ETHICS_PROVIDER": "openai",
    "AGENT_REGISTRY_USERNAME": "default",
    "FAILURE_INJECT_SERVICE": "",
    "FAILURE_INJECT_RATE": "0",
}


def _manifest_path(configs: list[str]) -> Path:
    return PROJECT_ROOT / "evaluation" / "results" / f"batch_manifest_{'_'.join(configs)}.json"


def _run_key(config: str, client: str, rep: int) -> tuple[str, str, int]:
    return (config, client, rep)


def _summarize_manifest_runs(runs: list[dict]) -> tuple[int, int]:
    completed = sum(1 for r in runs if r.get("status") == "COMPLETED")
    failed = sum(1 for r in runs if r.get("status") != "COMPLETED")
    return completed, failed


def _save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    completed, failed = _summarize_manifest_runs(manifest.get("runs", []))
    manifest["total_runs"] = len(manifest.get("runs", []))
    manifest["completed"] = completed
    manifest["failed"] = failed
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(manifest, indent=2) + "\n")


def _load_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        print(f"WARNING: failed to load manifest {path}: {exc}", flush=True)
        return None


def _upsert_manifest_run(manifest: dict, run_record: dict) -> None:
    runs = manifest.setdefault("runs", [])
    key = _run_key(
        str(run_record.get("config", "")),
        str(run_record.get("client", "")),
        int(run_record.get("rep", 0)),
    )
    for idx, existing in enumerate(runs):
        existing_key = _run_key(
            str(existing.get("config", "")),
            str(existing.get("client", "")),
            int(existing.get("rep", 0)),
        )
        if existing_key == key:
            runs[idx] = run_record
            return
    runs.append(run_record)


def _completed_run_keys(manifest: dict) -> set[tuple[str, str, int]]:
    keys: set[tuple[str, str, int]] = set()
    for run in manifest.get("runs", []):
        if run.get("status") == "COMPLETED":
            try:
                keys.add(
                    _run_key(
                        str(run.get("config", "")),
                        str(run.get("client", "")),
                        int(run.get("rep", 0)),
                    )
                )
            except Exception:
                continue
    return keys


def _post_json(path: str, data: dict) -> dict:
    url = f"{API_BASE}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib_request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib_request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(path: str) -> dict:
    url = f"{API_BASE}{path}"
    req = urllib_request.Request(url, headers={"Accept": "application/json"})
    with urllib_request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wait_for_agent_registry(
    username: str,
    min_agents: int = EXPECTED_AGENT_REGISTRY_COUNT,
    timeout_seconds: int = 90,
) -> None:
    """Wait for agent registry to repopulate after service restarts."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            agents = _get_json(f"/agent-registry/{username}")
            if isinstance(agents, list) and len(agents) >= min_agents:
                return
        except Exception:
            pass
        time.sleep(2)
    print("  WARNING: agent registry not fully repopulated before launch", flush=True)


def _wait_for_runtime_listeners(timeout_seconds: int = 90) -> None:
    """Wait until launch and pipeline-event queues are actually being consumed."""
    required_queues = {"commands.start_pipeline", *EXPECTED_PIPELINE_EVENT_QUEUES}
    last_restart: dict[str, float] = {}
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            raw = _rabbitmq_request("GET", "/queues/%2F")
            queues = json.loads(raw.decode("utf-8"))
            if isinstance(queues, list):
                queue_map = {
                    str(item.get("name")): int(item.get("consumers") or 0)
                    for item in queues
                    if isinstance(item, dict) and item.get("name")
                }
                if all(queue_map.get(name, 0) >= 1 for name in required_queues):
                    return
                for queue_name in required_queues:
                    if queue_map.get(queue_name, 0) >= 1:
                        continue
                    service = QUEUE_SERVICE_MAP.get(queue_name)
                    if not service:
                        continue
                    now = time.time()
                    if now - last_restart.get(service, 0) < 10:
                        continue
                    try:
                        state = subprocess.run(
                            ["docker", "inspect", service, "--format", "{{.State.Status}}"],
                            cwd=str(PROJECT_ROOT),
                            capture_output=True,
                            text=True,
                            timeout=10,
                            check=False,
                        )
                        status = (state.stdout or "").strip()
                        if status and status != "running":
                            subprocess.run(
                                ["docker", "compose", "up", "-d", "--no-deps", service],
                                cwd=str(PROJECT_ROOT),
                                capture_output=True,
                                timeout=60,
                                check=False,
                            )
                            last_restart[service] = now
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(2)
    print("  WARNING: runtime listeners not fully ready before launch", flush=True)


def _wait_for_api_ready(*, timeout_seconds: int = 60, announce: bool = True) -> bool:
    deadline = time.time() + timeout_seconds
    last_error = None
    if announce:
        print("  Waiting for API...", flush=True)
    while time.time() < deadline:
        try:
            _get_json("/health")
            if announce:
                print("  API ready.", flush=True)
            return True
        except Exception as exc:
            last_error = exc
            time.sleep(2)
    if announce:
        print(f"  WARNING: API not ready after {timeout_seconds}s ({last_error})", flush=True)
    return False


def _read_env() -> dict[str, str]:
    """Read current .env file into a dict."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip()
    return env


def _rabbitmq_request(method: str, path: str) -> bytes:
    env = _read_env()
    user = os.getenv("RABBITMQ_DEFAULT_USER") or env.get("RABBITMQ_DEFAULT_USER", "guest")
    password = os.getenv("RABBITMQ_DEFAULT_PASS") or env.get("RABBITMQ_DEFAULT_PASS", "guest")
    url = f"{RABBITMQ_API_BASE}{path}"
    req = urllib_request.Request(url, method=method)
    auth = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {auth}")
    with urllib_request.urlopen(req, timeout=15) as resp:
        return resp.read()


def _purge_dynamic_exchanges() -> None:
    """Delete per-run experiment exchanges so launches start cleanly."""
    try:
        raw = _rabbitmq_request("GET", "/exchanges/%2F")
        exchanges = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        print(f"  WARNING: RabbitMQ exchange cleanup skipped: {exc}", flush=True)
        return

    if not isinstance(exchanges, list):
        return

    candidates = []
    for item in exchanges:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        if name.startswith("exchange_") or name.startswith("pipeline_entry_"):
            candidates.append(name)

    for name in sorted(set(candidates)):
        try:
            encoded = urllib_parse.quote(name, safe="")
            _rabbitmq_request("DELETE", f"/exchanges/%2F/{encoded}")
        except urllib_error.HTTPError as exc:
            if exc.code != 404:
                print(f"  WARNING: failed to delete exchange {name}: {exc}", flush=True)
        except Exception as exc:
            print(f"  WARNING: failed to delete exchange {name}: {exc}", flush=True)


def _write_env(env: dict[str, str]):
    """Write env dict back to .env file, preserving comments."""
    lines = []
    if ENV_FILE.exists():
        existing_keys = set()
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in env:
                    lines.append(f"{key}={env[key]}")
                    existing_keys.add(key)
                else:
                    lines.append(line)
            else:
                lines.append(line)
        # Add any new keys not in original file
        for key, val in env.items():
            if key not in existing_keys:
                lines.append(f"{key}={val}")
    else:
        for key, val in env.items():
            lines.append(f"{key}={val}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def apply_config(config_name: str, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Apply ablation config env vars and restart affected services. Returns original env."""
    cfg = ABLATION_CONFIGS.get(config_name, {})
    overrides = {**DEFAULT_ENV_VALUES, **cfg.get("env", {}), **(extra_env or {})}

    # Save original values
    current_env = _read_env()
    original = {k: current_env.get(k, DEFAULT_ENV_VALUES.get(k, "")) for k in overrides}

    # Apply overrides
    new_env = {**current_env, **overrides}
    _write_env(new_env)
    _purge_dynamic_exchanges()

    # Restart affected services
    print(f"  Applying config {config_name}: {overrides}", flush=True)
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "--no-deps"] + AFFECTED_SERVICES,
            cwd=str(PROJECT_ROOT),
            capture_output=True, timeout=60,
        )
    except Exception as e:
        print(f"  WARNING: docker restart failed: {e}", flush=True)

    _wait_for_api_ready(timeout_seconds=60)

    _wait_for_agent_registry(overrides.get("AGENT_REGISTRY_USERNAME", "default"))
    _wait_for_runtime_listeners()
    time.sleep(2)
    return original


def restore_config(original_env: dict[str, str]):
    """Restore original env vars and restart services."""
    if not original_env:
        return

    current_env = _read_env()
    restored = {**current_env, **original_env}
    # Also clear failure injection
    restored["FAILURE_INJECT_SERVICE"] = ""
    restored["FAILURE_INJECT_RATE"] = "0"
    _write_env(restored)
    _purge_dynamic_exchanges()

    print(f"  Restoring default config...", flush=True)
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "--no-deps"] + AFFECTED_SERVICES,
            cwd=str(PROJECT_ROOT),
            capture_output=True, timeout=60,
        )
    except Exception as e:
        print(f"  WARNING: docker restart failed: {e}", flush=True)

    _wait_for_api_ready(timeout_seconds=60, announce=False)
    _wait_for_agent_registry(restored.get("AGENT_REGISTRY_USERNAME", "default"))
    _wait_for_runtime_listeners()
    time.sleep(2)


def wait_for_completion(
    pipeline_id: str,
    *,
    username: str,
    timeout: int = 600,
    poll_interval: int = 10,
) -> dict:
    """Poll until a pipeline run completes or times out."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            runs_data = _get_json(f"/pipelines/{username}/{pipeline_id}/runs")
            run_ids = runs_data.get("runs", [])
            if not run_ids:
                time.sleep(poll_interval)
                continue

            rid = run_ids[-1]
            run_data = _get_json(f"/pipelines/{username}/{pipeline_id}/{rid}")
            status = run_data.get("status", "")

            if status == "COMPLETED":
                return {"status": "COMPLETED", "pipeline_id": pipeline_id, "run_id": rid}
            elif status == "FAILED":
                return {"status": "FAILED", "pipeline_id": pipeline_id, "run_id": rid}

            tasks = run_data.get("tasks", {})
            completed = sum(1 for t in tasks.values()
                          if isinstance(t, dict) and t.get("status") == "COMPLETED")
            total = len(tasks)
            elapsed = int(time.time() - start)
            print(f"    [{elapsed}s] {completed}/{total} tasks completed...", flush=True)

        except Exception as e:
            print(f"    Poll error: {e}", flush=True)

        time.sleep(poll_interval)

    return {"status": "TIMEOUT", "pipeline_id": pipeline_id, "run_id": ""}


def launch_and_wait(client: str, config: str, rep: int, total_reps: int, *, username: str, timeout: int = 600) -> dict:
    """Launch a single pipeline run and wait for completion."""
    print(f"  [{config}] {client} rep {rep}/{total_reps} — launching...", flush=True)

    try:
        result = _post_json("/api/evaluation/pipeline/launch", {
            "client": client, "username": username,
        })
        pipeline_id = result.get("pipeline_id", "")
        print(f"    pipeline={pipeline_id[:12]}", flush=True)

        completion = wait_for_completion(pipeline_id, username=username, timeout=timeout)
        status = completion["status"]
        run_id = completion.get("run_id", "")

        if status == "COMPLETED":
            print(f"    COMPLETED (run={run_id[:12]})", flush=True)
        else:
            print(f"    {status}", flush=True)

        return {
            "config": config,
            "client": client,
            "username": username,
            "rep": rep,
            "pipeline_id": pipeline_id,
            "run_id": run_id,
            "status": status,
        }

    except Exception as e:
        print(f"    FAILED: {e}", flush=True)
        return {
            "config": config, "client": client, "username": username, "rep": rep,
            "pipeline_id": "", "run_id": "", "status": "ERROR",
            "error": str(e),
        }


def _config_username(config: str, batch_id: str) -> str:
    # Use 'default' because agents register under 'default' username.
    # Per-config usernames break agent discovery since the registry is
    # namespace-scoped and agents don't re-register per experiment config.
    return "default"


def run_batch(
    configs: list[str],
    clients: list[str],
    reps: int,
    convergence: bool = False,
    failure_test: bool = False,
    resume: bool = False,
):
    """Run the full batch experiment."""
    total = len(configs) * len(clients) * reps
    manifest_path = _manifest_path(configs)
    print(f"\n{'='*60}")
    print(f"ABLATION BATCH RUN")
    print(f"  Configs: {configs}")
    print(f"  Clients: {clients}")
    print(f"  Reps per client per config: {reps}")
    print(f"  Total pipeline runs: {total}")
    if convergence:
        print(f"  Mode: CONVERGENCE (decay between runs)")
    if failure_test:
        print(f"  Mode: FAILURE INJECTION")
    if resume:
        print(f"  Mode: RESUME")
    print(f"{'='*60}\n")

    # Check API health
    if not _wait_for_api_ready(timeout_seconds=60, announce=False):
        print(f"ERROR: API not reachable at {API_BASE}")
        sys.exit(1)
    health = _get_json("/health")
    print(f"API healthy: {health}\n")

    batch_id = time.strftime("%Y%m%d%H%M%S")
    manifest = None
    if resume:
        manifest = _load_manifest(manifest_path)
        if manifest:
            print(f"Resuming from existing manifest: {manifest_path}", flush=True)
        else:
            print(f"No manifest found at {manifest_path}; starting fresh.", flush=True)
    if not manifest:
        manifest = {
            "configs": configs,
            "clients": clients,
            "reps": reps,
            "convergence": convergence,
            "failure_test": failure_test,
            "batch_id": batch_id,
            "status": "RUNNING",
            "config_usernames": {},
            "runs": [],
        }
        _save_manifest(manifest_path, manifest)
    else:
        manifest["configs"] = configs
        manifest["clients"] = clients
        manifest["reps"] = reps
        manifest["convergence"] = convergence
        manifest["failure_test"] = failure_test
        manifest["status"] = "RUNNING"
        manifest.setdefault("batch_id", batch_id)
        manifest.setdefault("config_usernames", {})
        manifest.setdefault("runs", [])
        _save_manifest(manifest_path, manifest)

    completed_keys = _completed_run_keys(manifest)
    run_count = len(completed_keys)
    interrupted = False

    try:
        for config in configs:
            cfg = ABLATION_CONFIGS.get(config, {})
            config_usernames = manifest.setdefault("config_usernames", {})
            config_username = config_usernames.get(config) or _config_username(
                config,
                str(manifest.get("batch_id") or batch_id),
            )
            config_usernames[config] = config_username
            _save_manifest(manifest_path, manifest)

            pending_for_config = [
                _run_key(config, client, rep)
                for client in clients
                for rep in range(1, reps + 1)
                if _run_key(config, client, rep) not in completed_keys
            ]
            if not pending_for_config:
                print(f"\n{'─'*60}")
                print(f"Config: {config} — {cfg.get('description', '?')}")
                print(f"Username: {config_username}")
                print("All requested runs already completed; skipping.")
                print(f"{'─'*60}")
                continue

            print(f"\n{'─'*60}")
            print(f"Config: {config} — {cfg.get('description', '?')}")
            print(f"Username: {config_username}")
            print(f"{'─'*60}")

            extra_env = {"AGENT_REGISTRY_USERNAME": config_username}
            if failure_test and config in {"C0", "C1", "C2", "C7"}:
                extra_env.update({
                    "FAILURE_INJECT_SERVICE": "warehouse-north,warehouse-central,warehouse-south",
                    "FAILURE_INJECT_RATE": "1.0",
                })
            original_env = apply_config(config, extra_env=extra_env)

            try:
                for client in clients:
                    for rep in range(1, reps + 1):
                        run_key = _run_key(config, client, rep)
                        if run_key in completed_keys:
                            print(
                                f"\n[{run_count}/{total}]",
                                flush=True,
                            )
                            print(
                                f"  [{config}] {client} rep {rep}/{reps} — already completed, skipping.",
                                flush=True,
                            )
                            continue

                        run_count += 1
                        print(f"\n[{run_count}/{total}]", flush=True)
                        _purge_dynamic_exchanges()
                        timeout = 300 if failure_test else 600
                        result = launch_and_wait(
                            client,
                            config,
                            rep,
                            reps,
                            username=config_username,
                            timeout=timeout,
                        )
                        _upsert_manifest_run(manifest, result)
                        _save_manifest(manifest_path, manifest)
                        if result.get("status") == "COMPLETED":
                            completed_keys.add(run_key)
                            # Incremental CSV export — collect and append after every completed run
                            try:
                                from evaluation.results_collector import collect_run, RunResult
                                from dataclasses import asdict
                                import csv as _csv
                                _csv_path = os.path.join(os.path.dirname(manifest_path), "run_results.csv")
                                _run_result = collect_run(
                                    config=config,
                                    client=client,
                                    run_number=rep,
                                    username=config_username,
                                    pipeline_id=result.get("pipeline_id", ""),
                                    run_id=result.get("run_id", ""),
                                )
                                _row = asdict(_run_result)
                                _file_exists = os.path.exists(_csv_path)
                                with open(_csv_path, "a", newline="") as _f:
                                    _w = _csv.DictWriter(_f, fieldnames=_row.keys())
                                    if not _file_exists:
                                        _w.writeheader()
                                    _w.writerow(_row)
                                # Also append to JSON
                                _json_path = _csv_path.replace(".csv", ".json")
                                _existing = []
                                if os.path.exists(_json_path):
                                    with open(_json_path) as _jf:
                                        _existing = json.load(_jf)
                                _existing.append(_row)
                                with open(_json_path, "w") as _jf:
                                    json.dump(_existing, _jf, indent=2, default=str)
                                print(f"    → saved to CSV ({len(_existing)} total)", flush=True)
                            except Exception as _csv_err:
                                print(f"    → CSV export failed: {_csv_err}", flush=True)

                        if rep < reps or client != clients[-1]:
                            time.sleep(3)
            finally:
                if original_env:
                    restore_config(original_env)
    except KeyboardInterrupt:
        interrupted = True
        manifest["status"] = "INTERRUPTED"
        _save_manifest(manifest_path, manifest)
        print(f"\nInterrupted. Partial manifest saved to {manifest_path}", flush=True)
    else:
        manifest["status"] = "COMPLETED"
        _save_manifest(manifest_path, manifest)
    finally:
        if manifest.get("status") not in {"COMPLETED", "INTERRUPTED"}:
            manifest["status"] = "COMPLETED"
            _save_manifest(manifest_path, manifest)

    print(f"\nBatch manifest saved to {manifest_path}")

    runs = manifest.get("runs", [])
    completed = sum(1 for r in runs if r.get("status") == "COMPLETED")
    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE: {completed}/{len(runs)} runs succeeded")
    for config in configs:
        config_runs = [r for r in runs if r.get("config") == config]
        config_ok = sum(1 for r in config_runs if r["status"] == "COMPLETED")
        print(f"  {config}: {config_ok}/{len(config_runs)}")
    print(f"{'='*60}")
    print(f"\nNext steps:")
    mpath = manifest_path.relative_to(PROJECT_ROOT)
    print(f"  python -m evaluation.results_collector --manifest {mpath} --append")
    print(f"  python -m evaluation.report_generator")

    if interrupted:
        raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description="Run ablation study batch")
    parser.add_argument("--api-url", default="http://127.0.0.1:8082")
    parser.add_argument("--configs", nargs="+", default=["C0"],
                        help="Ablation configs (use ALL for all 9)")
    parser.add_argument("--clients", nargs="+", default=ALL_CLIENTS,
                        choices=ALL_CLIENTS)
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--convergence", action="store_true",
                        help="Convergence mode: decay pheromones between runs")
    parser.add_argument("--failure-test", action="store_true",
                        help="Run failure injection experiment")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from the batch manifest for this config set")
    args = parser.parse_args()

    global API_BASE
    API_BASE = args.api_url

    configs = args.configs
    if configs == ["ALL"]:
        configs = list(ABLATION_CONFIGS.keys())

    run_batch(
        configs,
        args.clients,
        args.reps,
        args.convergence,
        args.failure_test,
        args.resume,
    )


if __name__ == "__main__":
    main()
