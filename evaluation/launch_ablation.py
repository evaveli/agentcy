#!/usr/bin/env python3
"""Register and launch ablation study pipelines for all 5 client scenarios.

Usage:
    python -m evaluation.launch_ablation --api-url http://localhost:8082 --username default
    python -m evaluation.launch_ablation --client freshco          # single client
    python -m evaluation.launch_ablation --dry-run                 # print payloads only
"""

import argparse
import json
import sys
import time
from urllib import request, error

from evaluation.pipeline_templates import ALL_CLIENT_KEYS, build_pipeline_payload


def post_json(url: str, data: dict) -> dict:
    """POST JSON to a URL and return the parsed response."""
    body = json.dumps(data).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"  HTTP {e.code}: {err_body[:500]}", file=sys.stderr)
        raise


def get_json(url: str) -> dict:
    """GET JSON from a URL."""
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Launch ablation study pipelines")
    parser.add_argument("--api-url", default="http://localhost:8082",
                        help="API service base URL (default: http://localhost:8082)")
    parser.add_argument("--username", default="default",
                        help="Username for pipeline ownership (default: default)")
    parser.add_argument("--client", choices=ALL_CLIENT_KEYS,
                        help="Run single client only (default: all 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print payloads without registering")
    parser.add_argument("--launch", action="store_true",
                        help="Also launch pipeline runs after registration")
    args = parser.parse_args()

    clients = [args.client] if args.client else ALL_CLIENT_KEYS

    # Check API health
    if not args.dry_run:
        try:
            health = get_json(f"{args.api_url}/health")
            print(f"API healthy: {health}")
        except Exception as e:
            print(f"ERROR: Cannot reach API at {args.api_url}: {e}", file=sys.stderr)
            sys.exit(1)

    results = []
    for client_key in clients:
        payload = build_pipeline_payload(client_key)
        print(f"\n{'='*60}")
        print(f"Client: {client_key} -> Pipeline: {payload['name']}")
        print(f"{'='*60}")

        if args.dry_run:
            print(json.dumps(payload, indent=2))
            continue

        # Register pipeline
        url = f"{args.api_url}/pipelines/{args.username}"
        print(f"  POST {url}")
        try:
            resp = post_json(url, payload)
            pipeline_id = resp.get("pipeline_id", resp.get("id", "unknown"))
            print(f"  Registered: pipeline_id={pipeline_id}")
            results.append({"client": client_key, "pipeline_id": pipeline_id, "status": "registered"})

            # Launch if requested
            if args.launch:
                launch_url = f"{args.api_url}/pipelines/{args.username}/{pipeline_id}/launch"
                print(f"  POST {launch_url}")
                try:
                    launch_resp = post_json(launch_url, {})
                    run_id = launch_resp.get("pipeline_run_id", launch_resp.get("run_id", "unknown"))
                    print(f"  Launched: run_id={run_id}")
                    results[-1]["run_id"] = run_id
                    results[-1]["status"] = "launched"
                except Exception as e:
                    print(f"  Launch failed: {e}", file=sys.stderr)
                    results[-1]["status"] = "registered_but_launch_failed"

                # Brief pause between launches to avoid overwhelming the system
                time.sleep(2)

        except Exception as e:
            print(f"  Registration failed: {e}", file=sys.stderr)
            results.append({"client": client_key, "status": "failed", "error": str(e)})

    # Summary
    if not args.dry_run:
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        for r in results:
            print(f"  {r['client']:12s} -> {r['status']:30s} {r.get('pipeline_id', '')}")


if __name__ == "__main__":
    main()
