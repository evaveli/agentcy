"""E3 CLI runner — ablation study across system configurations.

Usage:
    python -m evaluation.e3_ablation.run_e3 --configs all --deals 1,2,3,4,5
    python -m evaluation.e3_ablation.run_e3 --configs full_system,no_ethics --deals 1,2,3
    python -m evaluation.e3_ablation.run_e3 --no-violations --no-failures
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.e3_ablation.ablation_runner import AblationResult, run_ablation
from evaluation.e3_ablation.config_profiles import ABLATION_CONFIGS, list_configs
from evaluation.e3_ablation.result_collector import (
    export_json,
    generate_full_report,
)

logger = logging.getLogger(__name__)

RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"


async def run_evaluation(
    configs: list[str],
    deal_ids: list[int],
    agents: list[str] | None,
    inject_violations: bool,
    inject_failures: bool,
    failure_rate: float,
    output_path: str,
) -> dict[str, list[AblationResult]]:
    """Run ablation study across all specified configurations."""
    all_results: dict[str, list[AblationResult]] = {}

    for config_name in configs:
        print(f"\n{'='*60}")
        print(f"Running config: {config_name}")
        print(f"{'='*60}")

        results = await run_ablation(
            config_name=config_name,
            deal_ids=deal_ids,
            agents=agents,
            inject_violations=inject_violations,
            inject_failures=inject_failures,
            failure_rate=failure_rate,
        )
        all_results[config_name] = results

        # Print quick summary
        for r in results:
            status = "OK" if r.error is None else f"ERROR: {r.error}"
            print(f"  Deal {r.deal_id}: {r.end_to_end_latency_ms:.0f}ms, "
                  f"violations={r.ethics_violations_reaching_output}, "
                  f"corrections={r.manual_corrections}, "
                  f"recovery={r.recovery_rate:.0%}, {status}")

    # Generate reports
    report_md = generate_full_report(all_results)
    report_json = export_json(all_results)

    out_path = Path(output_path) if output_path else RESULTS_DIR / "e3_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_md)
    out_path.with_suffix(".json").write_text(report_json)

    print(f"\n{'='*60}")
    print(f"Report written to {out_path}")
    print(f"JSON written to {out_path.with_suffix('.json')}")
    print(f"{'='*60}\n")
    print(report_md)

    return all_results


def main():
    parser = argparse.ArgumentParser(description="E3 Ablation Study")
    parser.add_argument(
        "--configs", default="all",
        help=f"Comma-separated configs: {','.join(list_configs())} (or 'all')",
    )
    parser.add_argument(
        "--deals", default="1,2,3,4,5",
        help="Comma-separated deal IDs",
    )
    parser.add_argument(
        "--agents", default=None,
        help="Comma-separated agent names (default: all)",
    )
    parser.add_argument("--no-violations", action="store_true", help="Disable violation injection")
    parser.add_argument("--no-failures", action="store_true", help="Disable failure injection")
    parser.add_argument("--failure-rate", type=float, default=0.2, help="Fraction of runs to inject failures")
    parser.add_argument("--output", default="", help="Output file path")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    configs = list_configs() if args.configs == "all" else [c.strip() for c in args.configs.split(",")]
    deal_ids = [int(d.strip()) for d in args.deals.split(",")]
    agents = [a.strip() for a in args.agents.split(",")] if args.agents else None

    asyncio.run(run_evaluation(
        configs=configs,
        deal_ids=deal_ids,
        agents=agents,
        inject_violations=not args.no_violations,
        inject_failures=not args.no_failures,
        failure_rate=args.failure_rate,
        output_path=args.output,
    ))


if __name__ == "__main__":
    main()
