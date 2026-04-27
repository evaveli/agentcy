"""E1 CLI runner — evaluate agent output quality against ground truth.

Usage:
    python -m evaluation.e1_harness.run_e1 --agents all --deals 1,2,3,4,5
    python -m evaluation.e1_harness.run_e1 --agents email,warehouse --cached
    python -m evaluation.e1_harness.run_e1 --live --output evaluation/results/e1_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.e1_harness.ground_truth import (
    CLIENTS,
    CLIENT_REQUIREMENTS,
    DEALS,
    WAREHOUSES,
    build_deal_summary_ground_truths,
    build_email_ground_truths,
    build_necessity_form_ground_truths,
    build_proposal_ground_truths,
    build_warehouse_ground_truths,
)
from evaluation.e1_harness.report_generator import generate_report
from evaluation.e1_harness.scorers.deal_summary_scorer import score_deal_summary
from evaluation.e1_harness.scorers.email_scorer import score_email
from evaluation.e1_harness.scorers.global_scorer import score_global
from evaluation.e1_harness.scorers.necessity_form_scorer import score_necessity_form
from evaluation.e1_harness.scorers.proposal_scorer import score_proposal
from evaluation.e1_harness.scorers.warehouse_scorer import score_warehouse

logger = logging.getLogger(__name__)

FIXTURES_DIR = PROJECT_ROOT / "evaluation" / "fixtures"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"

# ── Seed data as mock DB rows ────────────────────────────────────────

def _build_mock_db() -> dict[str, list[dict]]:
    """Convert seed data constants into mock DB query results."""
    return {
        "deals": [{"id": k, **v} for k, v in DEALS.items()],
        "clients": [{"id": k, **v} for k, v in CLIENTS.items()],
        "client_requirements": [{"id": k, "client_id": k, **v} for k, v in CLIENT_REQUIREMENTS.items()],
        "warehouses": [{"id": k, **v} for k, v in WAREHOUSES.items()],
        "emails": [],  # Populated from seed if needed
        "notes": [],
        "calls": [],
        "communication_history": [],
        "internal_notes": [],
        "call_records": [],
    }


MOCK_DB = _build_mock_db()


async def _mock_query_db(sql: str, *args: Any) -> list[dict]:
    """Mock replacement for agentcy.demo_agents.base.query_db."""
    sql_lower = sql.lower()
    for table_name, rows in MOCK_DB.items():
        if table_name in sql_lower:
            return rows
    return []


# ── Agent runners ────────────────────────────────────────────────────

async def _run_agent(
    agent_module: str,
    task_description: str,
    *,
    use_cache: bool = True,
    cache_key: str = "",
) -> dict:
    """Run a demo agent with mocked DB and optionally cached LLM."""
    cache_file = FIXTURES_DIR / f"{cache_key}.json" if cache_key else None

    # Check cache first
    if use_cache and cache_file and cache_file.exists():
        logger.info("Loading cached output: %s", cache_file)
        return json.loads(cache_file.read_text())

    # Import and patch
    import agentcy.demo_agents.base as base_module

    original_query_db = base_module.query_db
    base_module.query_db = _mock_query_db

    try:
        # Dynamic import of agent module
        parts = agent_module.rsplit(":", 1)
        mod_path = parts[0]
        func_name = parts[1] if len(parts) > 1 else "run"

        import importlib
        mod = importlib.import_module(mod_path)
        run_fn = getattr(mod, func_name)

        # Build message mock
        message = MagicMock()
        message.data = {"description": task_description, "raw_output": ""}

        result = await run_fn(
            None,  # rm (not used by demo agents)
            "eval-run-001",  # pipeline_run_id
            "eval-task",  # to_task
            "eval-trigger",  # triggered_by
            message,
        )

        # Cache result
        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(result, indent=2, default=str))
            logger.info("Cached output: %s", cache_file)

        return result

    finally:
        base_module.query_db = original_query_db


# ── Task descriptions per deal ───────────────────────────────────────

def _email_task(deal_id: int) -> str:
    deal = DEALS[deal_id]
    client = next(c for c in CLIENTS.values() if c["company_name"] == deal["client_name"])
    stage = deal["deal_stage"]
    if stage == "prospecting":
        return f"Draft an introduction email to {client['contact_name']} at {client['company_name']} about {deal['deal_name']}."
    elif stage in ("qualification", "proposal"):
        return f"Draft a status update email to {client['contact_name']} at {client['company_name']} about {deal['deal_name']}. Reference previous discussions."
    else:
        return f"Draft a quote follow-up email to {client['contact_name']} at {client['company_name']} about {deal['deal_name']}. Reference the proposal and pricing."


def _summary_task(deal_id: int) -> str:
    deal = DEALS[deal_id]
    return f"Generate a comprehensive deal summary for {deal['deal_name']} including key contacts, financials, timeline, risks, and next steps."


def _necessity_task(client_id: int) -> str:
    client = CLIENTS[client_id]
    return f"Fill in the Client Necessity Form for {client['company_name']} based on available client data and requirements."


def _proposal_task(deal_id: int) -> str:
    deal = DEALS[deal_id]
    return f"Generate a client-ready proposal for {deal['deal_name']} ({deal['client_name']}) including warehouse options, pricing, and terms."


def _warehouse_task(client_id: int) -> str:
    client = CLIENTS[client_id]
    reqs = CLIENT_REQUIREMENTS[client_id]
    return f"Suggest the top 3-5 warehouses for {client['company_name']} in {reqs['preferred_location']}. Requirements: {reqs['special_requirements']}."


# ── Main evaluation loop ─────────────────────────────────────────────

async def run_evaluation(
    agents: list[str],
    deal_ids: list[int],
    use_cache: bool = True,
    output_path: str = "",
    compare_human: bool = False,
) -> dict:
    """Run E1 evaluation across specified agents and deals."""
    per_agent_scores: dict[str, list[dict]] = {}

    # Email agent
    if "email" in agents or "all" in agents:
        gts = build_email_ground_truths()
        scores = []
        for gt in gts:
            if gt.deal_id not in deal_ids:
                continue
            try:
                output = await _run_agent(
                    "agentcy.demo_agents.email_agent",
                    _email_task(gt.deal_id),
                    use_cache=use_cache,
                    cache_key=f"email_deal{gt.deal_id}",
                )
                scores.append(score_email(output, gt))
            except Exception as e:
                logger.error("Email agent failed for deal %d: %s", gt.deal_id, e)
                scores.append({"error": str(e)})
        per_agent_scores["email"] = scores

    # Deal summary agent
    if "deal_summary" in agents or "all" in agents:
        gts = build_deal_summary_ground_truths()
        scores = []
        for gt in gts:
            if gt.deal_id not in deal_ids:
                continue
            try:
                output = await _run_agent(
                    "agentcy.demo_agents.data_agent",
                    _summary_task(gt.deal_id),
                    use_cache=use_cache,
                    cache_key=f"summary_deal{gt.deal_id}",
                )
                raw = output.get("raw_output", "") if isinstance(output, dict) else str(output)
                scores.append(score_deal_summary(raw, gt))
            except Exception as e:
                logger.error("Deal summary agent failed for deal %d: %s", gt.deal_id, e)
                scores.append({"error": str(e)})
        per_agent_scores["deal_summary"] = scores

    # Necessity form agent
    if "necessity_form" in agents or "all" in agents:
        gts = build_necessity_form_ground_truths()
        scores = []
        for gt in gts:
            if gt.client_id not in deal_ids:  # deal_ids doubles as client_ids (1-5)
                continue
            try:
                output = await _run_agent(
                    "agentcy.demo_agents.ml_agent",
                    _necessity_task(gt.client_id),
                    use_cache=use_cache,
                    cache_key=f"necessity_client{gt.client_id}",
                )
                raw = output.get("raw_output", "") if isinstance(output, dict) else str(output)
                scores.append(score_necessity_form(raw, gt))
            except Exception as e:
                logger.error("Necessity form agent failed for client %d: %s", gt.client_id, e)
                scores.append({"error": str(e)})
        per_agent_scores["necessity_form"] = scores

    # Proposal agent
    if "proposal" in agents or "all" in agents:
        gts = build_proposal_ground_truths()
        scores = []
        for gt in gts:
            if gt.deal_id not in deal_ids:
                continue
            try:
                output = await _run_agent(
                    "agentcy.demo_agents.reporting_agent",
                    _proposal_task(gt.deal_id),
                    use_cache=use_cache,
                    cache_key=f"proposal_deal{gt.deal_id}",
                )
                raw = output.get("raw_output", "") if isinstance(output, dict) else str(output)
                scores.append(score_proposal(raw, gt))
            except Exception as e:
                logger.error("Proposal agent failed for deal %d: %s", gt.deal_id, e)
                scores.append({"error": str(e)})
        per_agent_scores["proposal"] = scores

    # Warehouse agent
    if "warehouse" in agents or "all" in agents:
        gts = build_warehouse_ground_truths()
        scores = []
        for gt in gts:
            if gt.client_id not in deal_ids:
                continue
            try:
                output = await _run_agent(
                    "agentcy.demo_agents.infra_agent",
                    _warehouse_task(gt.client_id),
                    use_cache=use_cache,
                    cache_key=f"warehouse_client{gt.client_id}",
                )
                raw = output.get("raw_output", "") if isinstance(output, dict) else str(output)
                scores.append(score_warehouse(raw, gt))
            except Exception as e:
                logger.error("Warehouse agent failed for client %d: %s", gt.client_id, e)
                scores.append({"error": str(e)})
        per_agent_scores["warehouse"] = scores

    # Global scores (use first test case per agent for single-run scoring)
    flat_scores = {}
    for agent_name, scores_list in per_agent_scores.items():
        valid = [s for s in scores_list if "error" not in s]
        if valid:
            flat_scores[agent_name] = valid[0]  # First test case
    global_scores = score_global(flat_scores)

    # Generate report
    report_md = generate_report(per_agent_scores, global_scores)
    report_json = generate_report(per_agent_scores, global_scores, output_format="json")

    # Write outputs
    out_path = Path(output_path) if output_path else RESULTS_DIR / "e1_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_md)
    out_path.with_suffix(".json").write_text(report_json)

    print(f"Report written to {out_path}")
    print(f"JSON written to {out_path.with_suffix('.json')}")
    print("\n" + report_md)

    return {"per_agent": per_agent_scores, "global": global_scores}


def main():
    parser = argparse.ArgumentParser(description="E1 Evaluation — Agent Output Quality")
    parser.add_argument(
        "--agents", default="all",
        help="Comma-separated agent names: email,deal_summary,necessity_form,proposal,warehouse (or 'all')",
    )
    parser.add_argument(
        "--deals", default="1,2,3,4,5",
        help="Comma-separated deal/client IDs to evaluate",
    )
    parser.add_argument("--live", action="store_true", help="Use live LLM calls (requires OPENAI_API_KEY)")
    parser.add_argument("--cached", action="store_true", help="Use cached responses only (fail if missing)")
    parser.add_argument("--output", default="", help="Output file path")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    agents = [a.strip() for a in args.agents.split(",")]
    deal_ids = [int(d.strip()) for d in args.deals.split(",")]
    use_cache = not args.live

    asyncio.run(run_evaluation(agents, deal_ids, use_cache=use_cache, output_path=args.output))


if __name__ == "__main__":
    main()
