"""E4 CLI runner — evaluate ethics detection (stub vs LLM mode).

Usage:
    python -m evaluation.e4_ethics.run_e4 --modes stub
    python -m evaluation.e4_ethics.run_e4 --modes stub,llm
    python -m evaluation.e4_ethics.run_e4 --output evaluation/results/e4_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.e4_ethics.confusion_matrix import (
    compute_confusion_matrix,
    generate_full_report,
)
from evaluation.e4_ethics.ethics_test_runner import run_all_llm, run_all_stub
from evaluation.e4_ethics.synthetic_dataset import build_synthetic_dataset, dataset_summary

logger = logging.getLogger(__name__)

RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"


async def run_evaluation(
    modes: list[str],
    output_path: str = "",
) -> dict:
    """Run E4 ethics evaluation."""
    dataset = build_synthetic_dataset()
    summary = dataset_summary()

    print(f"Dataset: {summary['total']} cases ({summary['violation_cases']} violations, {summary['clean_cases']} clean)")
    print(f"Categories: {summary['by_category']}")
    print()

    stub_results = None
    llm_results = None

    if "stub" in modes:
        print("Running stub (rule-based) mode...")
        stub_results = run_all_stub(dataset)
        correct = sum(
            1 for r, c in zip(stub_results, dataset)
            if r.predicted_detected == c.expected_detected
        )
        print(f"  Stub accuracy: {correct}/{len(dataset)} ({correct/len(dataset)*100:.1f}%)")
        print()

    if "llm" in modes:
        print("Running LLM mode...")
        llm_results = await run_all_llm(dataset)
        correct = sum(
            1 for r, c in zip(llm_results, dataset)
            if r.predicted_detected == c.expected_detected
        )
        print(f"  LLM accuracy: {correct}/{len(dataset)} ({correct/len(dataset)*100:.1f}%)")
        print()

    # Generate report
    report = generate_full_report(
        stub_results=stub_results or [],
        llm_results=llm_results,
        dataset=dataset,
    )

    # Write outputs
    out_path = Path(output_path) if output_path else RESULTS_DIR / "e4_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)

    # Also save raw results as JSON
    raw = {
        "summary": summary,
        "stub_results": [
            {"case_id": r.case_id, "detected": r.predicted_detected,
             "severity": r.predicted_severity, "violations": r.violations}
            for r in (stub_results or [])
        ],
        "llm_results": [
            {"case_id": r.case_id, "detected": r.predicted_detected,
             "severity": r.predicted_severity, "violations": r.violations}
            for r in (llm_results or [])
        ] if llm_results else None,
    }
    out_path.with_suffix(".json").write_text(json.dumps(raw, indent=2, default=str))

    print(f"Report written to {out_path}")
    print(f"JSON written to {out_path.with_suffix('.json')}")
    print()
    print(report)

    return raw


def main():
    parser = argparse.ArgumentParser(description="E4 Ethics Detection Evaluation")
    parser.add_argument(
        "--modes", default="stub",
        help="Comma-separated modes: stub,llm (default: stub)",
    )
    parser.add_argument("--output", default="", help="Output file path")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    modes = [m.strip() for m in args.modes.split(",")]
    asyncio.run(run_evaluation(modes, output_path=args.output))


if __name__ == "__main__":
    main()
