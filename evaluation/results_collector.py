"""Results collector — extracts structured metrics from completed pipeline runs.

Queries the API for pipeline runs, CNP bid data, pheromone markers, and compliance
output, then produces a structured JSON/CSV with all metrics needed for Chapter 5.
"""

import csv
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional
from urllib import parse as urllib_parse
from urllib import request as urllib_request

logger = logging.getLogger(__name__)

API_BASE = os.getenv("EVAL_API_BASE", "http://127.0.0.1:8082")

# Ground truth from experimental design
WAREHOUSE_GROUND_TRUTH = {
    "freshco": "warehouse-south",
    "techparts": "warehouse-central",
    "greenleaf": "warehouse-central",
    "quickship": "warehouse-south",
    "nordicsteel": "warehouse-north",
}

ESTIMATOR_GROUND_TRUTH = {
    "freshco": "cost-estimator",
    "techparts": "cost-estimator",
    "greenleaf": "speed-estimator",
    "quickship": "cost-estimator",
    "nordicsteel": "speed-estimator",
}


@dataclass
class RunResult:
    config: str
    client: str
    run_number: int
    username: str
    pipeline_id: str
    run_id: str
    status: str
    # Assignment accuracy
    warehouse_winner: str = ""
    warehouse_expected: str = ""
    warehouse_correct: bool = False
    estimator_winner: str = ""
    estimator_expected: str = ""
    estimator_correct: bool = False
    assignment_accuracy: float = 0.0  # 0, 0.5, or 1.0
    # Compliance
    compliance_passed: bool = False
    compliance_blocks: int = 0
    compliance_warnings: int = 0
    compliance_scoped: bool = False
    plan_id: str = ""
    plan_valid: bool = False
    shacl_conforms: Optional[bool] = None
    shacl_disabled: bool = False
    strategy_present: bool = False
    ethics_present: bool = False
    # Latency
    latency_seconds: float = 0.0
    started_at: str = ""
    finished_at: str = ""
    # Task statuses
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_pending: int = 0
    tasks_total: int = 0
    # Bid scores (warehouse competition)
    warehouse_bid_scores: dict = field(default_factory=dict)
    warehouse_bid_delta: float = 0.0
    # Bid scores (estimator competition)
    estimator_bid_scores: dict = field(default_factory=dict)
    estimator_bid_delta: float = 0.0
    # Pheromone intensities
    pheromone_intensities: dict = field(default_factory=dict)
    # Token consumption (TOK metric)
    token_input: int = 0
    token_output: int = 0
    token_total: int = 0
    # Recovery time (RT metric) — seconds from failure injection to pipeline completion
    failure_injected_at: str = ""
    recovery_time: float = 0.0
    recovery_success: bool = False
    # Error info
    error: str = ""


def _get_json(path: str, params: Optional[dict[str, Any]] = None) -> Any:
    query = ""
    if params:
        clean = {k: v for k, v in params.items() if v not in (None, "", [])}
        if clean:
            query = "?" + urllib_parse.urlencode(clean)
    url = f"{API_BASE}{path}{query}"
    req = urllib_request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.debug("GET %s failed: %s", url, e)
        return None


def _detect_client(pipeline_name: str) -> str:
    """Extract client key from pipeline name like 'ablation-freshco'."""
    for key in WAREHOUSE_GROUND_TRUTH:
        if key in pipeline_name.lower():
            return key
    return "unknown"


def _parse_compliance(username: str, run_data: dict, pipeline_id: str, run_id: str) -> dict:
    """Extract compliance metrics from task output."""
    result = {
        "passed": False, "blocks": 0, "warnings": 0, "scoped": False,
    }
    try:
        output = _get_json(
            f"/pipelines/{username}/{pipeline_id}/{run_id}/tasks/compliance-check/output"
        )
        if output and "raw_output" in output:
            parsed = json.loads(output["raw_output"])
            result["passed"] = parsed.get("passed", False)
            result["blocks"] = parsed.get("blocks", 0)
            result["warnings"] = parsed.get("warnings", 0)
            result["scoped"] = parsed.get("scoped", False)
    except Exception as e:
        logger.debug("Failed to parse compliance output: %s", e)
    return result


def _extract_cnp_winner(run_data: dict, task_key: str) -> str:
    """Get the service_name that actually executed a task.

    The service_name in the task state reflects the CNP winner
    (updated by the forwarder's CNP-resolve).
    """
    tasks = run_data.get("tasks", {})
    task = tasks.get(task_key, {})
    if isinstance(task, dict):
        return task.get("service_name", "")
    return ""


def _service_name_from_agent_ref(value: str) -> str:
    if not value:
        return ""
    for known_svc in [
        "warehouse-north",
        "warehouse-central",
        "warehouse-south",
        "cost-estimator",
        "speed-estimator",
    ]:
        if known_svc in value:
            return known_svc
    if "-" in value:
        return value.split("-")[0]
    return value


def _latest_award_winner(username: str, pipeline_id: str, run_id: str, task_id: str) -> str:
    awards = _get_json(
        f"/graph-store/{username}/awards",
        {
            "pipeline_id": pipeline_id,
            "pipeline_run_id": run_id,
            "task_id": task_id,
        },
    )
    if not isinstance(awards, list) or not awards:
        return ""
    latest = max(awards, key=lambda award: award.get("awarded_at", ""))
    return _service_name_from_agent_ref(str(latest.get("bidder_id", "")))


def _load_plan_artifacts(username: str, pipeline_id: str, run_id: str) -> dict:
    details = {
        "plan_id": "",
        "plan_valid": False,
        "shacl_conforms": None,
        "shacl_disabled": False,
        "strategy_present": False,
        "ethics_present": False,
    }
    drafts = _get_json(
        f"/graph-store/{username}/plan-drafts",
        {
            "pipeline_id": pipeline_id,
            "pipeline_run_id": run_id,
        },
    )
    if not isinstance(drafts, list) or not drafts:
        return details
    latest = max(drafts, key=lambda draft: draft.get("created_at", ""))
    details["plan_id"] = str(latest.get("plan_id", ""))
    details["plan_valid"] = bool(latest.get("is_valid"))
    report = latest.get("shacl_report") if isinstance(latest.get("shacl_report"), dict) else {}
    shacl_engine = report.get("shacl_engine") if isinstance(report, dict) else {}
    if isinstance(shacl_engine, dict):
        details["shacl_conforms"] = shacl_engine.get("conforms")
        details["shacl_disabled"] = shacl_engine.get("error") == "shacl_disabled"
    plan_ids = {
        str(draft.get("plan_id", ""))
        for draft in drafts
        if draft.get("plan_id")
    }
    for plan_id in plan_ids:
        strategies = _get_json(f"/graph-store/{username}/strategy-plans", {"plan_id": plan_id})
        ethics = _get_json(f"/graph-store/{username}/ethics-checks", {"plan_id": plan_id})
        details["strategy_present"] = details["strategy_present"] or (
            isinstance(strategies, list) and len(strategies) > 0
        )
        details["ethics_present"] = details["ethics_present"] or (
            isinstance(ethics, list) and len(ethics) > 0
        )
    return details


def collect_run(
    config: str,
    client: str,
    run_number: int,
    username: str,
    pipeline_id: str,
    run_id: str,
) -> RunResult:
    """Collect all metrics for a single pipeline run."""
    result = RunResult(
        config=config,
        client=client,
        run_number=run_number,
        username=username,
        pipeline_id=pipeline_id,
        run_id=run_id,
        status="unknown",
    )

    # Fetch run data
    run_data = _get_json(f"/pipelines/{username}/{pipeline_id}/{run_id}")
    if not run_data or "detail" in run_data:
        result.status = "not_found"
        result.error = str(run_data)
        return result

    result.status = run_data.get("status", "unknown")

    # Timing
    started = run_data.get("started_at", "")
    finished = run_data.get("finished_at", "")
    result.started_at = started or ""
    result.finished_at = finished or ""
    if started and finished:
        try:
            s = datetime.fromisoformat(started.replace("Z", "+00:00"))
            f = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            result.latency_seconds = round((f - s).total_seconds(), 1)
        except Exception:
            pass

    # Task statuses
    tasks = run_data.get("tasks", {})
    result.tasks_total = len(tasks)
    for tid, ts in tasks.items():
        if isinstance(ts, dict):
            s = (ts.get("status") or "").upper()
            if s == "COMPLETED":
                result.tasks_completed += 1
            elif s == "FAILED":
                result.tasks_failed += 1
            elif s == "PENDING":
                result.tasks_pending += 1

    # Assignment accuracy — warehouse
    result.warehouse_winner = _latest_award_winner(username, pipeline_id, run_id, "warehouse-match") or _extract_cnp_winner(run_data, "warehouse-match")
    result.warehouse_expected = WAREHOUSE_GROUND_TRUTH.get(client, "")
    result.warehouse_correct = result.warehouse_winner == result.warehouse_expected

    # Assignment accuracy — estimator
    result.estimator_winner = _latest_award_winner(username, pipeline_id, run_id, "deal-estimation") or _extract_cnp_winner(run_data, "deal-estimation")
    result.estimator_expected = ESTIMATOR_GROUND_TRUTH.get(client, "")
    result.estimator_correct = result.estimator_winner == result.estimator_expected

    # Combined accuracy (average of warehouse + estimator correctness)
    correct_count = int(result.warehouse_correct) + int(result.estimator_correct)
    result.assignment_accuracy = correct_count / 2.0

    # Compliance
    compliance = _parse_compliance(username, run_data, pipeline_id, run_id)
    result.compliance_passed = compliance["passed"]
    result.compliance_blocks = compliance["blocks"]
    result.compliance_warnings = compliance["warnings"]
    result.compliance_scoped = compliance["scoped"]
    plan_artifacts = _load_plan_artifacts(username, pipeline_id, run_id)
    result.plan_id = plan_artifacts["plan_id"]
    result.plan_valid = plan_artifacts["plan_valid"]
    result.shacl_conforms = plan_artifacts["shacl_conforms"]
    result.shacl_disabled = plan_artifacts["shacl_disabled"]
    result.strategy_present = plan_artifacts["strategy_present"]
    result.ethics_present = plan_artifacts["ethics_present"]

    # Bid scores (from graph store)
    try:
        bids_data = _get_json(
            f"/graph-store/{username}/bids",
            {
                "pipeline_id": pipeline_id,
                "pipeline_run_id": run_id,
            },
        )
        if isinstance(bids_data, list):
            for bid in bids_data:
                task_id = bid.get("task_id", "")
                bidder = bid.get("bidder_id", "")
                score = bid.get("bid_score", 0)
                svc = _service_name_from_agent_ref(str(bidder))
                if "warehouse" in task_id:
                    result.warehouse_bid_scores[svc] = max(
                        result.warehouse_bid_scores.get(svc, 0), score
                    )
                elif "estimation" in task_id or "deal" in task_id:
                    result.estimator_bid_scores[svc] = max(
                        result.estimator_bid_scores.get(svc, 0), score
                    )
    except Exception:
        pass

    # Bid deltas
    if len(result.warehouse_bid_scores) >= 2:
        sorted_scores = sorted(result.warehouse_bid_scores.values(), reverse=True)
        result.warehouse_bid_delta = round(sorted_scores[0] - sorted_scores[1], 4)
    if len(result.estimator_bid_scores) >= 2:
        sorted_scores = sorted(result.estimator_bid_scores.values(), reverse=True)
        result.estimator_bid_delta = round(sorted_scores[0] - sorted_scores[1], 4)

    # Token estimation (TOK metric) — estimate from task output sizes
    # Each task output is stored; we estimate ~4 chars per token (GPT standard)
    try:
        for tid in tasks:
            task_output = _get_json(f"/pipelines/{username}/{pipeline_id}/{run_id}/tasks/{tid}/output")
            if task_output and "raw_output" in task_output:
                output_len = len(task_output["raw_output"])
                est_output_tokens = output_len // 4
                result.token_output += est_output_tokens
                # Estimate input as ~2x output for these agents
                result.token_input += est_output_tokens * 2
        result.token_total = result.token_input + result.token_output
    except Exception:
        pass

    # Pheromone intensities
    try:
        markers = _get_json(
            f"/graph-store/{username}/markers/affordance",
            {
                "pipeline_id": pipeline_id,
                "pipeline_run_id": run_id,
            },
        )
        if isinstance(markers, list):
            for m in markers:
                agent_id = m.get("agent_id", "")
                intensity = m.get("intensity", 0)
                cap = m.get("capability", "")
                if "warehouse" in cap or "warehouse" in agent_id:
                    key = agent_id
                    for known in ["warehouse-north", "warehouse-central", "warehouse-south"]:
                        if known in agent_id:
                            key = known
                            break
                    result.pheromone_intensities[key] = max(
                        result.pheromone_intensities.get(key, 0), intensity
                    )
    except Exception:
        pass

    # Recovery time (RT metric)
    # Check if failure was injected by looking for failure_injected_at in run data
    failure_at = run_data.get("failure_injected_at", "")
    if failure_at:
        result.failure_injected_at = failure_at
        result.recovery_success = result.status == "COMPLETED"
        if result.recovery_success and finished:
            try:
                f_ts = datetime.fromisoformat(failure_at.replace("Z", "+00:00"))
                fin_ts = datetime.fromisoformat(finished.replace("Z", "+00:00"))
                result.recovery_time = round((fin_ts - f_ts).total_seconds(), 1)
            except Exception:
                pass
    # Also check if any task failed (indicates failure injection even if field missing)
    if result.tasks_failed > 0 and not failure_at:
        result.recovery_success = result.status == "COMPLETED"

    return result


def collect_all_completed(username: str = "default") -> list[RunResult]:
    """Scan all ablation pipelines and collect metrics for completed runs."""
    pipes = _get_json(f"/pipelines/{username}")
    if not pipes:
        return []

    results = []
    run_counter: dict[str, int] = {}  # client -> count

    for pipe in pipes:
        name = pipe.get("name", pipe.get("pipeline_name", ""))
        if not name.startswith("ablation-"):
            continue
        pid = pipe["pipeline_id"]
        client = _detect_client(name)

        runs_data = _get_json(f"/pipelines/{username}/{pid}/runs")
        run_ids = (runs_data or {}).get("runs", [])

        for rid in run_ids:
            run_data = _get_json(f"/pipelines/{username}/{pid}/{rid}")
            if not run_data or run_data.get("detail"):
                continue
            if run_data.get("status") != "COMPLETED":
                continue

            counter_key = f"C0:{client}"
            run_counter[counter_key] = run_counter.get(counter_key, 0) + 1

            result = collect_run(
                config="C0",
                client=client,
                run_number=run_counter[counter_key],
                username=username,
                pipeline_id=pid,
                run_id=rid,
            )
            results.append(result)

    return results


def collect_from_manifest(manifest_path: str, config_label: str | None = None) -> list[RunResult]:
    """Collect metrics from runs listed in a batch manifest."""
    with open(manifest_path) as f:
        manifest = json.load(f)

    results = []
    for i, run_info in enumerate(manifest.get("runs", [])):
        if run_info.get("status") != "COMPLETED":
            continue
        client = run_info.get("client", "unknown")
        username = run_info.get("username", "default")
        pid = run_info.get("pipeline_id", "")
        rid = run_info.get("run_id", "")
        rep = run_info.get("rep", i + 1)

        if not pid or not rid:
            continue

        run_config = run_info.get("config") or config_label or "C0"
        result = collect_run(
            config=run_config,
            client=client,
            run_number=rep,
            username=username,
            pipeline_id=pid,
            run_id=rid,
        )
        results.append(result)

    return results


def merge_results(*result_lists: list[RunResult]) -> list[RunResult]:
    """Merge multiple result lists, deduplicating by (config, pipeline_id, run_id)."""
    seen = set()
    merged = []
    for results in result_lists:
        for r in results:
            key = (r.config, r.pipeline_id, r.run_id)
            if key not in seen:
                seen.add(key)
                merged.append(r)
    return merged


def save_results_json(results: list[RunResult], path: str):
    """Save results to JSON file."""
    data = [asdict(r) for r in results]
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Saved {len(results)} results to {path}")


def save_results_csv(results: list[RunResult], path: str):
    """Save results to CSV file."""
    if not results:
        print("No results to save")
        return
    flat = []
    for r in results:
        d = asdict(r)
        # Flatten dicts to strings for CSV
        for key in ["warehouse_bid_scores", "estimator_bid_scores", "pheromone_intensities"]:
            d[key] = json.dumps(d[key])
        flat.append(d)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=flat[0].keys())
        writer.writeheader()
        writer.writerows(flat)
    print(f"Saved {len(results)} results to {path}")


def print_summary(results: list[RunResult]):
    """Print a human-readable summary of collected results."""
    if not results:
        print("No results collected")
        return

    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY ({len(results)} completed runs)")
    print(f"{'='*70}\n")

    # Group by client
    by_client: dict[str, list[RunResult]] = {}
    for r in results:
        by_client.setdefault(r.client, []).append(r)

    # Assignment accuracy table
    print("Assignment Accuracy:")
    print(f"  {'Client':<15} {'Runs':>5} {'WH Correct':>12} {'Est Correct':>12} {'ACC':>8}")
    print(f"  {'-'*52}")
    total_wh = total_est = total_runs = 0
    for client in sorted(by_client):
        runs = by_client[client]
        wh_correct = sum(1 for r in runs if r.warehouse_correct)
        est_correct = sum(1 for r in runs if r.estimator_correct)
        acc = sum(r.assignment_accuracy for r in runs) / len(runs)
        print(f"  {client:<15} {len(runs):>5} {wh_correct:>8}/{len(runs):<3} {est_correct:>8}/{len(runs):<3} {acc:>7.1%}")
        total_wh += wh_correct
        total_est += est_correct
        total_runs += len(runs)

    if total_runs:
        print(f"  {'-'*52}")
        print(f"  {'TOTAL':<15} {total_runs:>5} {total_wh:>8}/{total_runs:<3} {total_est:>8}/{total_runs:<3} {(total_wh+total_est)/(2*total_runs):>7.1%}")

    # Compliance summary
    print(f"\nCompliance:")
    print(f"  {'Client':<15} {'Passed':>8} {'Blocks':>8} {'Scoped':>8}")
    print(f"  {'-'*39}")
    for client in sorted(by_client):
        runs = by_client[client]
        passed = sum(1 for r in runs if r.compliance_passed)
        avg_blocks = sum(r.compliance_blocks for r in runs) / len(runs)
        scoped = sum(1 for r in runs if r.compliance_scoped)
        print(f"  {client:<15} {passed:>5}/{len(runs):<2} {avg_blocks:>7.1f} {scoped:>5}/{len(runs):<2}")

    # Latency summary
    print(f"\nLatency (seconds):")
    print(f"  {'Client':<15} {'Min':>8} {'Mean':>8} {'Max':>8}")
    print(f"  {'-'*39}")
    for client in sorted(by_client):
        runs = [r for r in by_client[client] if r.latency_seconds > 0]
        if runs:
            lats = [r.latency_seconds for r in runs]
            print(f"  {client:<15} {min(lats):>7.0f}s {sum(lats)/len(lats):>7.0f}s {max(lats):>7.0f}s")

    # Winners breakdown
    print(f"\nWarehouse Winners:")
    wh_counts: dict[str, int] = {}
    for r in results:
        wh_counts[r.warehouse_winner] = wh_counts.get(r.warehouse_winner, 0) + 1
    for svc, count in sorted(wh_counts.items(), key=lambda x: -x[1]):
        print(f"  {svc:<25} {count:>3} wins ({count/len(results)*100:.0f}%)")

    print(f"\nEstimator Winners:")
    est_counts: dict[str, int] = {}
    for r in results:
        est_counts[r.estimator_winner] = est_counts.get(r.estimator_winner, 0) + 1
    for svc, count in sorted(est_counts.items(), key=lambda x: -x[1]):
        print(f"  {svc:<25} {count:>3} wins ({count/len(results)*100:.0f}%)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect metrics from completed pipeline runs")
    parser.add_argument("--api-url", default="http://127.0.0.1:8082")
    parser.add_argument("--output-json", default="evaluation/results/run_results.json")
    parser.add_argument("--output-csv", default="evaluation/results/run_results.csv")
    parser.add_argument("--username", default="default")
    parser.add_argument("--manifest", help="Batch manifest JSON to collect from")
    parser.add_argument("--config-label", default=None, help="Optional override for manifest run config labels")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing results file instead of overwriting")
    args = parser.parse_args()

    API_BASE = args.api_url
    os.makedirs(os.path.dirname(args.output_json), exist_ok=True)

    if args.manifest:
        label_msg = args.config_label if args.config_label else "manifest-native labels"
        print(f"Collecting from manifest {args.manifest} as config={label_msg}...")
        new_results = collect_from_manifest(args.manifest, args.config_label)
    else:
        print(f"Collecting all completed runs from {API_BASE}...")
        new_results = collect_all_completed(args.username)

    if args.append and os.path.exists(args.output_json):
        with open(args.output_json) as f:
            existing = [RunResult(**r) for r in json.load(f)]
        results = merge_results(existing, new_results)
        print(f"Merged {len(new_results)} new + {len(existing)} existing = {len(results)} total")
    else:
        results = new_results

    print_summary(results)
    save_results_json(results, args.output_json)
    save_results_csv(results, args.output_csv)
