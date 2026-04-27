"""API router for thesis evaluation experiments (E1, E3, E4).

GET  /api/evaluation/e4/stub          — run E4 stub mode, return confusion matrix
POST /api/evaluation/e4/llm           — run E4 LLM mode (requires API key)
GET  /api/evaluation/e4/dataset       — return the synthetic dataset summary
GET  /api/evaluation/e1/ground-truth  — return ground truth summaries
POST /api/evaluation/e1/score-email   — score an email agent output
POST /api/evaluation/e1/score-all     — score all agents (requires LLM)
GET  /api/evaluation/e3/configs       — list ablation configurations
POST /api/evaluation/e3/run           — run ablation study
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from agentcy.api_service.dependecies import CommandPublisher, get_publisher, get_rm
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager

# Ensure evaluation package is importable
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


# ── E4: Ethics Detection ─────────────────────────────────────────────

@router.get("/e4/dataset")
async def get_e4_dataset():
    """Return the synthetic dataset summary and all test cases."""
    from evaluation.e4_ethics.synthetic_dataset import build_synthetic_dataset, dataset_summary

    dataset = build_synthetic_dataset()
    summary = dataset_summary()
    cases = [
        {
            "case_id": c.case_id,
            "category": c.category,
            "expected_detected": c.expected_detected,
            "expected_severity": c.expected_severity,
            "description": c.description,
            "task_description": c.tasks[0]["description"] if c.tasks else "",
            "risk_level": c.risk_level,
        }
        for c in dataset
    ]
    return {"summary": summary, "cases": cases}


@router.get("/e4/stub")
async def run_e4_stub():
    """Run all 50 test cases through stub (rule-based) ethics checker."""
    from evaluation.e4_ethics.synthetic_dataset import build_synthetic_dataset
    from evaluation.e4_ethics.ethics_test_runner import run_all_stub
    from evaluation.e4_ethics.confusion_matrix import compute_confusion_matrix

    dataset = build_synthetic_dataset()
    results = run_all_stub(dataset)

    per_cat, overall = compute_confusion_matrix(results, dataset)

    correct = sum(
        1 for r, c in zip(results, dataset)
        if r.predicted_detected == c.expected_detected
    )

    return {
        "mode": "stub",
        "total_cases": len(dataset),
        "accuracy": round(correct / len(dataset) * 100, 1),
        "overall": {
            "tp": overall.tp, "fp": overall.fp, "fn": overall.fn, "tn": overall.tn,
            "precision": round(overall.precision, 3),
            "recall": round(overall.recall, 3),
            "f1": round(overall.f1, 3),
        },
        "per_category": {
            cat: {
                "tp": m.tp, "fp": m.fp, "fn": m.fn, "tn": m.tn,
                "precision": round(m.precision, 3),
                "recall": round(m.recall, 3),
                "f1": round(m.f1, 3),
            }
            for cat, m in per_cat.items()
        },
        "detailed_results": [
            {
                "case_id": r.case_id,
                "expected": c.expected_detected,
                "predicted": r.predicted_detected,
                "correct": r.predicted_detected == c.expected_detected,
                "category": c.category,
                "severity": r.predicted_severity,
                "violations": r.violations,
            }
            for r, c in zip(results, dataset)
        ],
    }


# ── Compliance Agent (standalone, no external deps) ──────────────────

@router.get("/compliance/run")
async def run_compliance_standalone():
    """Run the compliance agent against seed data — no DB or LLM needed.

    This is self-contained: uses hardcoded seed data matching init-postgres.sql.
    """
    from evaluation.e1_harness.ground_truth import (
        CLIENTS, CLIENT_REQUIREMENTS, WAREHOUSES,
    )

    # Build mock data matching what query_db would return
    requirements = []
    for cid, req in CLIENT_REQUIREMENTS.items():
        client = CLIENTS[cid]
        requirements.append({
            **req,
            "company_name": client["company_name"],
            "industry": client["industry"],
        })

    warehouses = [
        {**wh, "id": wid, "status": "available"}
        for wid, wh in WAREHOUSES.items()
    ]

    # Run compliance rules inline (mirrors compliance_agent.py logic)
    violations = []
    checked_pairs = 0

    for req in requirements:
        special = (req.get("special_requirements") or "").lower()
        priority = (req.get("priority") or "medium").lower()
        client = req.get("company_name", "unknown")
        budget = float(req.get("max_monthly_budget") or 0)
        min_sqft = req.get("min_sqft") or 0
        max_sqft = req.get("max_sqft") or 999999

        for wh in warehouses:
            wh_name = wh.get("name", "unknown")
            pair = {"client": client, "warehouse": wh_name}
            checked_pairs += 1

            if "cold storage" in special and not wh.get("has_cold_storage"):
                violations.append({
                    **pair, "rule": "COLD_STORAGE_REQUIRED", "severity": "BLOCK",
                    "detail": f"{wh_name} lacks cold storage; {client} requires it",
                })
            if "hazmat" in special and not wh.get("has_hazmat"):
                violations.append({
                    **pair, "rule": "HAZMAT_REQUIRED", "severity": "BLOCK",
                    "detail": f"{wh_name} not hazmat-certified; {client} requires it",
                })
            if priority == "critical" and wh.get("security_level") == "standard":
                violations.append({
                    **pair, "rule": "HIGH_SECURITY_FOR_CRITICAL", "severity": "BLOCK",
                    "detail": f"{wh_name} has standard security; {client} deal is critical priority",
                })

            avail = wh.get("available_sqft") or 0
            if avail < min_sqft or avail > max_sqft:
                violations.append({
                    **pair, "rule": "SQFT_WITHIN_RANGE", "severity": "WARN",
                    "detail": f"{wh_name} has {avail} sqft; {client} needs {min_sqft}-{max_sqft}",
                })

            monthly_cost = float(wh.get("monthly_rent_per_sqft") or 0) * avail
            if budget > 0 and monthly_cost > budget:
                violations.append({
                    **pair, "rule": "BUDGET_COMPLIANCE", "severity": "WARN",
                    "detail": f"{wh_name} costs {monthly_cost:,.0f}/mo; {client} budget is {budget:,.0f}/mo",
                })

    blocks = [v for v in violations if v["severity"] == "BLOCK"]
    warns = [v for v in violations if v["severity"] == "WARN"]

    # Cross-reference with ground truth
    from evaluation.e3_ablation.ground_truth import VIOLATION_GROUND_TRUTH, SEEDED_VIOLATIONS
    gt_violations = {(v.client_name, v.warehouse_name, v.rule) for v in VIOLATION_GROUND_TRUTH}
    detected_gt = {
        (v["client"], v["warehouse"], v["rule"])
        for v in violations
        if (v["client"], v["warehouse"], v["rule"]) in gt_violations
    }

    return {
        "checked_pairs": checked_pairs,
        "total_violations": len(violations),
        "blocks": len(blocks),
        "warnings": len(warns),
        "ground_truth_violations": len(gt_violations),
        "ground_truth_detected": len(detected_gt),
        "ground_truth_detection_rate": round(len(detected_gt) / len(gt_violations) * 100, 1) if gt_violations else 0,
        "violations": violations,
        "seeded_scenario_results": [
            {
                "scenario_id": s.scenario_id,
                "client": s.client_name,
                "warehouse": s.warehouse_name,
                "should_block": s.should_block,
                "actually_blocked": any(
                    v["client"] == s.client_name
                    and v["warehouse"] == s.warehouse_name
                    and v["severity"] == "BLOCK"
                    for v in violations
                ),
                "violations_found": [
                    v for v in violations
                    if v["client"] == s.client_name and v["warehouse"] == s.warehouse_name
                ],
                "correct": (
                    s.should_block == any(
                        v["client"] == s.client_name
                        and v["warehouse"] == s.warehouse_name
                        and v["severity"] == "BLOCK"
                        for v in violations
                    )
                ),
            }
            for s in SEEDED_VIOLATIONS
        ],
    }


# ── E1: Agent Quality ────────────────────────────────────────────────

@router.get("/e1/ground-truth")
async def get_e1_ground_truth():
    """Return ground truth summaries for all 5 agents."""
    from evaluation.e1_harness.ground_truth import (
        DEALS, CLIENTS, CLIENT_REQUIREMENTS, WAREHOUSES,
        build_email_ground_truths,
        build_deal_summary_ground_truths,
        build_necessity_form_ground_truths,
        build_proposal_ground_truths,
        build_warehouse_ground_truths,
    )

    email_gts = build_email_ground_truths()
    summary_gts = build_deal_summary_ground_truths()
    form_gts = build_necessity_form_ground_truths()
    proposal_gts = build_proposal_ground_truths()
    warehouse_gts = build_warehouse_ground_truths()

    return {
        "deals": DEALS,
        "clients": CLIENTS,
        "warehouses": {k: v["name"] for k, v in WAREHOUSES.items()},
        "agents": {
            "email": {
                "count": len(email_gts),
                "cases": [
                    {
                        "deal_id": gt.deal_id,
                        "email_type": gt.email_type,
                        "deal_stage": gt.deal_stage,
                        "expected_to": gt.expected_to,
                        "expected_from": gt.expected_from,
                        "entity_count": sum(
                            len(v) if isinstance(v, list) else 1
                            for v in gt.expected_entities.values()
                        ),
                    }
                    for gt in email_gts
                ],
            },
            "deal_summary": {
                "count": len(summary_gts),
                "cases": [
                    {
                        "deal_id": gt.deal_id,
                        "required_facts": len(gt.required_facts),
                        "required_sections": gt.required_sections,
                    }
                    for gt in summary_gts
                ],
            },
            "necessity_form": {
                "count": len(form_gts),
                "cases": [
                    {
                        "client_id": gt.client_id,
                        "total_fields": len(gt.expected_fields),
                        "critical_fields": gt.critical_fields,
                    }
                    for gt in form_gts
                ],
            },
            "proposal": {
                "count": len(proposal_gts),
                "cases": [
                    {
                        "deal_id": gt.deal_id,
                        "required_sections": gt.required_sections,
                        "critical_facts": len(gt.critical_facts),
                    }
                    for gt in proposal_gts
                ],
            },
            "warehouse": {
                "count": len(warehouse_gts),
                "cases": [
                    {
                        "client_id": gt.client_id,
                        "correct_top1": gt.correct_top1,
                        "correct_top3": gt.correct_top3,
                        "hard_constraints": gt.hard_constraints,
                    }
                    for gt in warehouse_gts
                ],
            },
        },
    }


class ScoreEmailRequest(BaseModel):
    deal_id: int = Field(..., ge=1, le=5)
    output: dict[str, Any]


@router.post("/e1/score-email")
async def score_email_output(req: ScoreEmailRequest):
    """Score an email agent output against ground truth."""
    from evaluation.e1_harness.ground_truth import build_email_ground_truths
    from evaluation.e1_harness.scorers.email_scorer import score_email

    gts = {gt.deal_id: gt for gt in build_email_ground_truths()}
    gt = gts.get(req.deal_id)
    if not gt:
        raise HTTPException(status_code=404, detail=f"No ground truth for deal {req.deal_id}")

    scores = score_email(req.output, gt)
    return {"deal_id": req.deal_id, "scores": scores}


# ── E3: Ablation Study ───────────────────────────────────────────────

@router.get("/e3/configs")
async def get_e3_configs():
    """List all ablation configuration profiles."""
    from evaluation.e3_ablation.config_profiles import ABLATION_CONFIGS, CONFIG_DESCRIPTIONS

    return {
        name: {
            "description": CONFIG_DESCRIPTIONS.get(name, ""),
            "env_vars": config,
        }
        for name, config in ABLATION_CONFIGS.items()
    }


@router.get("/e3/ground-truth")
async def get_e3_ground_truth():
    """Return assignment accuracy ground truth and seeded violation scenarios."""
    from evaluation.e3_ablation.ground_truth import (
        ASSIGNMENT_GROUND_TRUTH, SEEDED_VIOLATIONS, VIOLATION_GROUND_TRUTH,
    )

    return {
        "assignments": [
            {
                "client_id": gt.client_id,
                "client_name": gt.client_name,
                "correct_warehouse_agent": gt.correct_warehouse_agent,
                "correct_estimator": gt.correct_estimator,
                "warehouse_rationale": gt.warehouse_rationale,
                "estimator_rationale": gt.estimator_rationale,
                "priority": gt.priority,
            }
            for gt in ASSIGNMENT_GROUND_TRUTH
        ],
        "known_violations": [
            {
                "client_name": v.client_name,
                "warehouse_name": v.warehouse_name,
                "rule": v.rule,
                "severity": v.severity,
                "detail": v.detail,
            }
            for v in VIOLATION_GROUND_TRUTH
        ],
        "seeded_scenarios": [
            {
                "scenario_id": s.scenario_id,
                "client_name": s.client_name,
                "warehouse_name": s.warehouse_name,
                "should_block": s.should_block,
                "expected_rules": s.expected_rules,
                "description": s.description,
            }
            for s in SEEDED_VIOLATIONS
        ],
        "hypotheses": {
            "H1": "Pheromone removal degrades assignment accuracy over successive runs",
            "H2": "CNP removal produces largest single-component accuracy drop",
            "H3": "SHACL + compliance removal produces non-additive violation rate increase",
            "H4": "Strategist removal has minimal accuracy impact but latency impact",
            "H5": "Full framework outperforms minimal baseline on all quality metrics",
        },
    }


class PipelineLaunchRequest(BaseModel):
    client: str = Field(..., description="Client key: freshco, techparts, greenleaf, quickship, nordicsteel")
    username: str = Field(default="default")


class PipelineLaunchAllRequest(BaseModel):
    username: str = Field(default="default")
    clients: list[str] = Field(
        default=["freshco", "techparts", "greenleaf", "quickship", "nordicsteel"]
    )


@router.get("/pipeline/clients")
async def get_pipeline_clients():
    """Return available client scenarios for pipeline launch."""
    from evaluation.pipeline_templates import CLIENT_DESCRIPTIONS, build_pipeline_payload

    return {
        "clients": [
            {
                "key": key,
                "pipeline_name": f"ablation-{key}",
                "description": build_pipeline_payload(key)["description"],
                "tasks": [
                    {"id": t["id"], "name": t["name"], "service": t["available_services"]}
                    for t in build_pipeline_payload(key)["dag"]["tasks"]
                ],
            }
            for key in CLIENT_DESCRIPTIONS
        ]
    }


@router.post("/pipeline/launch")
async def launch_pipeline(
    req: PipelineLaunchRequest,
    rm: ResourceManager = Depends(get_rm),
    pub: CommandPublisher = Depends(get_publisher),
):
    """Register and launch a single client pipeline (C0 config)."""
    import uuid as uuid_mod

    from evaluation.pipeline_templates import ALL_CLIENT_KEYS, build_pipeline_payload
    from agentcy.pydantic_models.pipeline_validation_models.user_define_pipeline_model import PipelineCreate
    from agentcy.pydantic_models.commands import RegisterPipelineCommand, StartPipelineCommand
    from agentcy.orchestrator_core.utils import build_pipeline_config

    if req.client not in ALL_CLIENT_KEYS:
        raise HTTPException(400, f"Unknown client: {req.client}. Valid: {ALL_CLIENT_KEYS}")

    payload = build_pipeline_payload(req.client)
    pipeline_id = str(uuid_mod.uuid4())

    # Register pipeline (same logic as POST /pipelines/{username})
    store = rm.pipeline_store
    if store is None:
        raise HTTPException(500, "Pipeline store is not configured")

    pipeline_create = PipelineCreate.model_validate(payload)
    store.insert_stub(req.username, pipeline_id, pipeline_create)

    import os
    if os.getenv("PIPELINE_CREATE_SYNC", "1") != "0":
        full_cfg = build_pipeline_config(dto=pipeline_create, pipeline_id=pipeline_id)
        store.update(req.username, pipeline_id, full_cfg)

    payload_ref = f"pipeline::{req.username}::{pipeline_id}"
    reg_cmd = RegisterPipelineCommand(
        username=req.username, pipeline_id=pipeline_id, payload_ref=payload_ref,
    )
    await pub.publish("commands.register_pipeline", reg_cmd)

    # Start pipeline (same logic as POST /pipelines/{username}/{id}/start)
    start_cmd = StartPipelineCommand(
        username=req.username,
        pipeline_id=pipeline_id,
        pipeline_run_config_id=pipeline_id,
    )
    await pub.publish("commands.start_pipeline", start_cmd)

    return {
        "client": req.client,
        "pipeline_id": pipeline_id,
        "status": "launched",
        "pipeline_name": payload["name"],
    }


@router.post("/pipeline/launch-all")
async def launch_all_pipelines(
    req: PipelineLaunchAllRequest,
    rm: ResourceManager = Depends(get_rm),
    pub: CommandPublisher = Depends(get_publisher),
):
    """Register and launch pipelines for all specified clients."""
    results = []
    for client_key in req.clients:
        try:
            result = await launch_pipeline(
                PipelineLaunchRequest(client=client_key, username=req.username),
                rm=rm,
                pub=pub,
            )
            results.append(result)
        except HTTPException as e:
            results.append({"client": client_key, "status": "failed", "error": e.detail})
        except Exception as e:
            results.append({"client": client_key, "status": "failed", "error": str(e)})
    return {"results": results}


@router.post("/pheromone/decay")
async def decay_pheromone_markers(
    decay_factor: float = 0.9,
    rm: ResourceManager = Depends(get_rm),
):
    """Apply decay to all pheromone markers (multiply intensity by decay_factor)."""
    store = rm.graph_marker_store
    if store is None:
        raise HTTPException(500, "Graph marker store not configured")

    try:
        markers, total = store.list_affordance_markers(username="default")
        updated = 0
        for m in (markers or []):
            intensity = m.get("intensity", 0) if isinstance(m, dict) else getattr(m, "intensity", 0)
            new_intensity = float(intensity) * decay_factor
            if new_intensity < 0.01:
                new_intensity = 0.0
            from agentcy.pydantic_models.multi_agent_pipeline import AffordanceMarker
            marker = AffordanceMarker(
                marker_id=m.get("marker_id", ""),
                task_id=m.get("task_id", ""),
                agent_id=m.get("agent_id", ""),
                capability=m.get("capability", ""),
                intensity=new_intensity,
                rationale=f"decay:{decay_factor}",
                pipeline_id=m.get("pipeline_id"),
                pipeline_run_id=m.get("pipeline_run_id"),
                ttl_seconds=86400,
            )
            store.add_affordance_marker(username="default", marker=marker)
            updated += 1
        return {"decayed": updated, "decay_factor": decay_factor}
    except Exception as e:
        raise HTTPException(500, f"Decay failed: {e}")


@router.post("/pipeline/cleanup")
async def cleanup_stuck_pipelines(
    rm: ResourceManager = Depends(get_rm),
):
    """Delete all ablation pipelines with stuck (non-COMPLETED) runs, or no runs at all."""
    username = "default"
    store = rm.pipeline_store
    eph_store = rm.ephemeral_store
    if store is None:
        raise HTTPException(500, "Pipeline store not configured")

    all_pipes = store.list(username)
    deleted = []
    kept = []

    for pipe in all_pipes:
        name = pipe.get("name", pipe.get("pipeline_name", ""))
        pid = pipe.get("pipeline_id", "")
        if not name.startswith("ablation-"):
            continue

        run_ids = store.list_runs(username, pid) or []
        should_delete = False

        if not run_ids:
            should_delete = True
        else:
            for rid in run_ids:
                try:
                    run_doc = eph_store.read_run(username, pid, rid) if eph_store else None
                    if run_doc:
                        status = (run_doc.get("status") or "").upper()
                        if status in ("RUNNING", "PENDING", ""):
                            tasks = run_doc.get("tasks", {})
                            pending = sum(
                                1 for t in tasks.values()
                                if isinstance(t, dict) and (t.get("status") or "").upper() == "PENDING"
                            )
                            if pending >= 3:
                                should_delete = True
                except Exception:
                    pass

        if should_delete:
            try:
                store.delete(username, pid)
                deleted.append({"pipeline_id": pid, "name": name})
            except Exception as e:
                kept.append({"pipeline_id": pid, "name": name, "error": str(e)})
        else:
            kept.append({"pipeline_id": pid, "name": name})

    return {
        "deleted": len(deleted),
        "kept": len(kept),
        "deleted_pipelines": deleted,
        "kept_pipelines": kept,
    }


class AblationRequest(BaseModel):
    configs: list[str] = Field(default=["C0_full", "C2_no_cnp", "C7_minimal"])
    deal_ids: list[int] = Field(default=[1, 2, 3, 4, 5])
    inject_violations: bool = True
    inject_failures: bool = True
    failure_rate: float = Field(default=0.2, ge=0.0, le=1.0)


@router.post("/e3/run")
async def run_e3_ablation(req: AblationRequest):
    """Run ablation study (NOTE: requires live LLM for non-minimal configs)."""
    from evaluation.e3_ablation.config_profiles import ABLATION_CONFIGS
    from evaluation.e3_ablation.ablation_runner import run_ablation
    from evaluation.e3_ablation.result_collector import aggregate_results

    for name in req.configs:
        if name not in ABLATION_CONFIGS:
            raise HTTPException(400, f"Unknown config: {name}")

    all_results = {}
    for config_name in req.configs:
        results = await run_ablation(
            config_name=config_name,
            deal_ids=req.deal_ids,
            inject_violations=req.inject_violations,
            inject_failures=req.inject_failures,
            failure_rate=req.failure_rate,
        )
        all_results[config_name] = results

    summary = aggregate_results(all_results)

    return {
        "configs_tested": req.configs,
        "deals_per_config": len(req.deal_ids),
        "summary": summary,
        "detailed": {
            config_name: [
                {
                    "deal_id": r.deal_id,
                    "latency_ms": round(r.end_to_end_latency_ms, 1),
                    "violations": r.ethics_violations_reaching_output,
                    "corrections": r.manual_corrections,
                    "recovery_rate": round(r.recovery_rate, 2),
                    "quality": r.quality_scores,
                    "error": r.error,
                }
                for r in results_list
            ]
            for config_name, results_list in all_results.items()
        },
    }
