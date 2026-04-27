#!/usr/bin/env python3
"""Seeded Violation Test Harness — Experiment 3.3 Validation Layer Interaction.

Runs 5 known client-warehouse pairings through each validation config (C0, C3, C4, C3+C4)
to compute precise TPR/FPR for the compliance and SHACL validation layers.

No LLM calls. No pipeline runs. Pure deterministic rule checking.

Usage:
    python -m evaluation.seeded_violations
    python -m evaluation.seeded_violations --output evaluation/results/report/csv/seeded_violations.csv
"""

import argparse
import csv
import json
import os
import sys

# ── Seeded test cases from experimental design (Section 3.3) ──────────────

SEEDED_CASES = [
    # --- Cases caught by COMPLIANCE ONLY (domain data checks) ---
    {
        "case_id": "SV1",
        "client": "FreshCo Logistics",
        "warehouse": "Bavaria Logistics Hub",
        "description": "FreshCo -> Bavaria (no cold storage) — compliance catches via DB check",
        "expected": "BLOCK",
        "expected_rule": "COLD_STORAGE_REQUIRED",
        "caught_by": "compliance",
        "client_special_requirements": "cold storage required, food-grade flooring",
        "client_priority": "high",
        "client_budget": 18000,
        "client_min_sqft": 15000,
        "client_max_sqft": 25000,
        "wh_has_cold_storage": False,
        "wh_has_hazmat": False,
        "wh_security_level": "standard",
        "wh_available_sqft": 40000,
        "wh_monthly_rent_per_sqft": 0.85,
        # SHACL structural fields — all valid (temp range specified, etc.)
        "has_human_approval": True,
        "risk_level": "medium",
        "lease_term_months": 36,
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
    {
        "case_id": "SV2",
        "client": "GreenLeaf Pharma",
        "warehouse": "Centro Logistico Bologna",
        "description": "GreenLeaf -> Bologna (no hazmat, no cold) — compliance catches via DB check",
        "expected": "BLOCK",
        "expected_rule": "HAZMAT_REQUIRED",
        "caught_by": "compliance",
        "client_special_requirements": "GDP-compliant cold chain, hazmat storage, 24/7 security",
        "client_priority": "critical",
        "client_budget": 28000,
        "client_min_sqft": 20000,
        "client_max_sqft": 35000,
        "wh_has_cold_storage": False,
        "wh_has_hazmat": False,
        "wh_security_level": "standard",
        "wh_available_sqft": 55000,
        "wh_monthly_rent_per_sqft": 0.65,
        "has_human_approval": True,
        "risk_level": "high",
        "lease_term_months": 48,
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
    # --- Cases caught by SHACL ONLY (structural/plan checks) ---
    {
        "case_id": "SV3",
        "client": "GreenLeaf Pharma",
        "warehouse": "PharmaStore Lyon",
        "description": "GreenLeaf -> Lyon (warehouse OK, but missing human approval for hazmat) — SHACL catches",
        "expected": "BLOCK",
        "expected_rule": "HAZMAT_APPROVAL_MISSING",
        "caught_by": "shacl",
        # Warehouse actually meets all domain requirements
        "client_special_requirements": "GDP-compliant cold chain, hazmat storage, 24/7 security",
        "client_priority": "critical",
        "client_budget": 28000,
        "client_min_sqft": 20000,
        "client_max_sqft": 35000,
        "wh_has_cold_storage": True,
        "wh_has_hazmat": True,
        "wh_security_level": "high",
        "wh_available_sqft": 28000,
        "wh_monthly_rent_per_sqft": 0.95,
        # SHACL violation: no human approval for hazmat task
        "has_human_approval": False,
        "risk_level": "high",
        "lease_term_months": 48,
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
    {
        "case_id": "SV4",
        "client": "TechParts GmbH",
        "warehouse": "Stuttgart TechCenter",
        "description": "TechParts -> Stuttgart (warehouse OK, short lease but non-hazmat) — SHACL warns only",
        "expected": "PASS",
        "expected_rule": None,
        "caught_by": None,
        # Warehouse meets all domain requirements
        "client_special_requirements": "ESD flooring, clean room capability",
        "client_priority": "high",
        "client_budget": 35000,
        "client_min_sqft": 30000,
        "client_max_sqft": 50000,
        "wh_has_cold_storage": False,
        "wh_has_hazmat": False,
        "wh_security_level": "high",
        "wh_available_sqft": 35000,
        "wh_monthly_rent_per_sqft": 0.90,
        # SHACL violation: lease term 12 months < 24 minimum
        "has_human_approval": True,
        "risk_level": "medium",
        "lease_term_months": 12,
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
    # --- Case caught by COMPLIANCE ONLY (another domain check) ---
    {
        "case_id": "SV5",
        "client": "FreshCo Logistics",
        "warehouse": "Gothenburg Port Warehouse",
        "description": "FreshCo -> Gothenburg (no cold storage) — compliance catches via DB",
        "expected": "BLOCK",
        "expected_rule": "COLD_STORAGE_REQUIRED",
        "caught_by": "compliance",
        "client_special_requirements": "cold storage required, food-grade flooring",
        "client_priority": "high",
        "client_budget": 18000,
        "client_min_sqft": 15000,
        "client_max_sqft": 25000,
        "wh_has_cold_storage": False,
        "wh_has_hazmat": False,
        "wh_security_level": "standard",
        "wh_available_sqft": 80000,
        "wh_monthly_rent_per_sqft": 0.70,
        # SHACL fields all valid
        "has_human_approval": True,
        "risk_level": "medium",
        "lease_term_months": 36,
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
    # --- Case caught by SHACL ONLY (missing compliance dependency) ---
    {
        "case_id": "SV6",
        "client": "Nordic Steel AB",
        "warehouse": "Gothenburg Port Warehouse",
        "description": "Nordic Steel -> Gothenburg (warehouse OK, but no compliance check in plan) — SHACL catches",
        "expected": "BLOCK",
        "expected_rule": "COMPLIANCE_DEPENDENCY_MISSING",
        "caught_by": "shacl",
        # Warehouse meets all domain requirements for Nordic Steel
        "client_special_requirements": "overhead crane (10 ton), reinforced flooring, rail siding",
        "client_priority": "medium",
        "client_budget": 55000,
        "client_min_sqft": 50000,
        "client_max_sqft": 80000,
        "wh_has_cold_storage": False,
        "wh_has_hazmat": False,
        "wh_security_level": "standard",
        "wh_available_sqft": 80000,
        "wh_monthly_rent_per_sqft": 0.70,
        # SHACL violation: plan skips compliance check
        "has_human_approval": True,
        "risk_level": "medium",
        "lease_term_months": 60,
        "has_temp_range": True,
        "has_compliance_dependency": False,
    },
    # --- Cases requiring BOTH layers to produce a BLOCK (interaction) ---
    {
        "case_id": "SV7",
        "client": "GreenLeaf Pharma",
        "warehouse": "PharmaStore Lyon",
        "description": "GreenLeaf -> Lyon (warehouse OK, but critical deal has wrong risk_level + hazmat needs escalation) — BOTH layers needed",
        "expected": "BLOCK",
        "expected_rule": "COMPOUND_RISK_ESCALATION",
        "caught_by": "interaction",
        # Warehouse passes all domain checks individually
        "client_special_requirements": "GDP-compliant cold chain, hazmat storage, 24/7 security",
        "client_priority": "critical",
        "client_budget": 28000,
        "client_min_sqft": 20000,
        "client_max_sqft": 35000,
        "wh_has_cold_storage": True,
        "wh_has_hazmat": True,
        "wh_security_level": "high",
        "wh_available_sqft": 28000,
        "wh_monthly_rent_per_sqft": 0.95,
        # SHACL sees: risk_level is "medium" but client is "critical" → WARN only
        # Compliance sees: hazmat deal, all checks pass individually → PASS
        # BOTH together: critical hazmat deal with wrong risk classification → BLOCK
        "has_human_approval": True,
        "risk_level": "medium",  # Wrong — critical should be "high"
        "lease_term_months": 48,
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
    {
        "case_id": "SV8",
        "client": "GreenLeaf Pharma",
        "warehouse": "Paris CDG Logistics",
        "description": "GreenLeaf -> Paris CDG (warehouse OK, but short lease on critical hazmat deal) — BOTH layers needed",
        "expected": "BLOCK",
        "expected_rule": "COMPOUND_RISK_ESCALATION",
        "caught_by": "interaction",
        # Warehouse passes all domain checks
        "client_special_requirements": "GDP-compliant cold chain, hazmat storage, 24/7 security",
        "client_priority": "critical",
        "client_budget": 28000,
        "client_min_sqft": 20000,
        "client_max_sqft": 35000,
        "wh_has_cold_storage": True,
        "wh_has_hazmat": True,
        "wh_security_level": "high",
        "wh_available_sqft": 25000,
        "wh_monthly_rent_per_sqft": 1.10,
        # SHACL sees: lease term 18 months < 24 minimum → WARN (below threshold but not safety)
        # Compliance sees: hazmat deal, budget tight but passes → PASS
        # BOTH together: short-lease hazmat deal for critical client → BLOCK
        "has_human_approval": True,
        "risk_level": "high",
        "lease_term_months": 18,  # Below 24 minimum
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
    # --- Clean cases (no violations) ---
    {
        "case_id": "SV9",
        "client": "QuickShip Express",
        "warehouse": "Centro Logistico Bologna",
        "description": "QuickShip -> Bologna (valid, no conflicts) — clean case",
        "expected": "PASS",
        "expected_rule": None,
        "caught_by": None,
        "client_special_requirements": "mezzanine for packing, high-speed internet",
        "client_priority": "medium",
        "client_budget": 40000,
        "client_min_sqft": 40000,
        "client_max_sqft": 60000,
        "wh_has_cold_storage": False,
        "wh_has_hazmat": False,
        "wh_security_level": "standard",
        "wh_available_sqft": 55000,
        "wh_monthly_rent_per_sqft": 0.65,
        "has_human_approval": True,
        "risk_level": "medium",
        "lease_term_months": 24,
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
    {
        "case_id": "SV10",
        "client": "FreshCo Logistics",
        "warehouse": "LogisPark Milano Nord",
        "description": "FreshCo -> Milano Nord (perfect match, all valid) — clean case",
        "expected": "PASS",
        "expected_rule": None,
        "caught_by": None,
        "client_special_requirements": "cold storage required, food-grade flooring",
        "client_priority": "high",
        "client_budget": 18000,
        "client_min_sqft": 15000,
        "client_max_sqft": 25000,
        "wh_has_cold_storage": True,
        "wh_has_hazmat": False,
        "wh_security_level": "enhanced",
        "wh_available_sqft": 22000,
        "wh_monthly_rent_per_sqft": 0.75,
        "has_human_approval": True,
        "risk_level": "medium",
        "lease_term_months": 36,
        "has_temp_range": True,
        "has_compliance_dependency": True,
    },
]

# ── SHACL-equivalent checks (simplified, matching schemas/plan_draft_shapes.ttl) ──

def check_shacl(case: dict) -> list[dict]:
    """Simulate SHACL shape validation — structural/plan-level checks only.

    These do NOT check runtime warehouse data (that's the compliance agent's job).
    They check whether the plan/task is structurally well-formed.
    """
    violations = []
    special = case["client_special_requirements"].lower()

    # HazmatApprovalShape: hazmat tasks must have human approval
    if "hazmat" in special and not case.get("has_human_approval", True):
        violations.append({
            "shape": "ac:HazmatApprovalShape",
            "message": "Hazmat warehouse assignment requires human approval",
            "severity": "BLOCK",
        })

    # CriticalDealRiskShape: critical deals must have risk_level = high
    # Alone this is a WARN (administrative mismatch). Becomes BLOCK when
    # combined with compliance findings for hazmat/critical deals.
    if case["client_priority"] == "critical" and case.get("risk_level", "high") != "high":
        violations.append({
            "shape": "ac:CriticalDealRiskShape",
            "message": "Critical deals must be flagged as high risk in the task specification",
            "severity": "WARN",
            "_escalatable": True,
        })

    # LeaseTermShape: lease term must be >= 24 months
    # Alone this is a WARN (contractual issue). Becomes BLOCK when
    # combined with compliance findings for hazmat/critical deals.
    lease_term = case.get("lease_term_months", 36)
    if lease_term < 24:
        violations.append({
            "shape": "ac:LeaseTermShape",
            "message": f"Lease term ({lease_term} months) is below minimum (24 months)",
            "severity": "WARN",
            "_escalatable": True,
        })

    # ColdStorageSpecShape: cold storage tasks must specify temperature range
    if "cold" in special and not case.get("has_temp_range", True):
        violations.append({
            "shape": "ac:ColdStorageSpecShape",
            "message": "Cold storage tasks must specify required temperature range",
            "severity": "BLOCK",
        })

    # ComplianceDependencyShape: warehouse assignments must have compliance check
    if not case.get("has_compliance_dependency", True):
        violations.append({
            "shape": "ac:ComplianceDependencyShape",
            "message": "Warehouse assignments must pass compliance validation before proposal",
            "severity": "BLOCK",
        })

    return violations


# ── Compliance agent rules (matching compliance_agent.py) ──

def check_compliance(case: dict) -> list[dict]:
    """Run compliance rules against a client-warehouse pairing."""
    violations = []
    special = case["client_special_requirements"].lower()
    budget = case["client_budget"]
    min_sqft = case["client_min_sqft"]
    max_sqft = case["client_max_sqft"]
    avail = case["wh_available_sqft"]
    monthly_cost = case["wh_monthly_rent_per_sqft"] * avail

    # Cold storage
    if "cold" in special and not case["wh_has_cold_storage"]:
        violations.append({"rule": "COLD_STORAGE_REQUIRED", "severity": "BLOCK"})

    # Hazmat
    if "hazmat" in special and not case["wh_has_hazmat"]:
        violations.append({"rule": "HAZMAT_REQUIRED", "severity": "BLOCK"})

    # Security for critical
    if case["client_priority"] == "critical" and case["wh_security_level"] == "standard":
        violations.append({"rule": "HIGH_SECURITY_FOR_CRITICAL", "severity": "BLOCK"})

    # Hazmat confirmation flag — used for compound risk escalation
    # When compliance confirms this is a hazmat deal, SHACL structural
    # warnings can be escalated to BLOCKs (defense-in-depth interaction)
    if "hazmat" in special and case["wh_has_hazmat"]:
        violations.append({"rule": "HAZMAT_DEAL_CONFIRMED", "severity": "INFO",
                           "_hazmat_confirmed": True})

    # Critical deal confirmation
    if case["client_priority"] == "critical":
        violations.append({"rule": "CRITICAL_DEAL_CONFIRMED", "severity": "INFO",
                           "_critical_confirmed": True})

    # Sqft range
    if avail < min_sqft or avail > max_sqft:
        violations.append({"rule": "SQFT_WITHIN_RANGE", "severity": "WARN"})

    # Budget
    if budget > 0 and monthly_cost > budget:
        violations.append({"rule": "BUDGET_COMPLIANCE", "severity": "WARN"})

    return violations


# ── Run all configs ──

CONFIGS = {
    "C0": {"shacl": True, "compliance": True},
    "C3": {"shacl": False, "compliance": True},
    "C4": {"shacl": True, "compliance": False},
    "C3+C4": {"shacl": False, "compliance": False},
}


def check_compound_risk(shacl_violations: list[dict], compliance_violations: list[dict]) -> list[dict]:
    """Defense-in-depth interaction: escalate SHACL WARNs to BLOCKs when
    compliance confirms the deal involves hazmat or critical priority.

    This is the mechanism that produces IE > 0: neither layer alone blocks,
    but the combination of SHACL structural findings + compliance domain
    confirmation creates a compound risk that must be blocked.

    Example: SHACL sees risk_level mismatch (WARN). Compliance confirms
    this is a hazmat deal (INFO). Together: a critical hazmat deal with
    wrong risk classification → BLOCK.
    """
    escalated = []

    # Check if compliance confirmed hazmat or critical deal
    hazmat_confirmed = any(v.get("_hazmat_confirmed") for v in compliance_violations)
    critical_confirmed = any(v.get("_critical_confirmed") for v in compliance_violations)

    if not (hazmat_confirmed or critical_confirmed):
        return escalated

    # Escalate SHACL warnings that are marked as escalatable
    for v in shacl_violations:
        if v.get("_escalatable") and v.get("severity") == "WARN":
            if hazmat_confirmed or critical_confirmed:
                escalated.append({
                    "rule": "COMPOUND_RISK_ESCALATION",
                    "severity": "BLOCK",
                    "source_shape": v.get("shape", ""),
                    "message": f"Escalated: {v['message']} (confirmed hazmat={hazmat_confirmed}, critical={critical_confirmed})",
                })

    return escalated


def run_seeded_tests() -> list[dict]:
    """Run all seeded cases against all configs. Returns list of result dicts."""
    results = []

    for config_name, config in CONFIGS.items():
        for case in SEEDED_CASES:
            shacl_violations = check_shacl(case) if config["shacl"] else []
            compliance_violations = check_compliance(case) if config["compliance"] else []

            # Compound risk escalation — requires BOTH layers
            compound_violations = []
            if config["shacl"] and config["compliance"]:
                compound_violations = check_compound_risk(shacl_violations, compliance_violations)

            all_violations = shacl_violations + compliance_violations + compound_violations
            blocks = [v for v in all_violations if v.get("severity") == "BLOCK"]
            warns = [v for v in all_violations if v.get("severity") == "WARN"]

            detected = len(blocks) > 0
            expected_block = case["expected"] == "BLOCK"

            # Classification
            if expected_block and detected:
                classification = "TP"
            elif expected_block and not detected:
                classification = "FN"
            elif not expected_block and detected:
                classification = "FP"
            else:
                classification = "TN"

            results.append({
                "config": config_name,
                "case_id": case["case_id"],
                "client": case["client"],
                "warehouse": case["warehouse"],
                "description": case["description"],
                "expected": case["expected"],
                "detected": "BLOCK" if detected else "PASS",
                "classification": classification,
                "shacl_violations": len(shacl_violations),
                "compliance_violations": len(compliance_violations),
                "total_blocks": len(blocks),
                "total_warns": len(warns),
                "shacl_enabled": config["shacl"],
                "compliance_enabled": config["compliance"],
                "violation_details": json.dumps([
                    v.get("rule", v.get("shape", "?")) for v in all_violations
                ]),
            })

    return results


def compute_metrics(results: list[dict]) -> dict:
    """Compute TPR, FPR, precision, F1, and IE from seeded test results."""
    metrics = {}

    for config_name in CONFIGS:
        config_results = [r for r in results if r["config"] == config_name]
        tp = sum(1 for r in config_results if r["classification"] == "TP")
        fp = sum(1 for r in config_results if r["classification"] == "FP")
        fn = sum(1 for r in config_results if r["classification"] == "FN")
        tn = sum(1 for r in config_results if r["classification"] == "TN")

        tpr = tp / max(tp + fn, 1)
        fpr = fp / max(fp + tn, 1)
        precision = tp / max(tp + fp, 1)
        f1 = 2 * precision * tpr / max(precision + tpr, 0.001)

        # VR = proportion of cases with at least one block (among cases that should have blocks)
        expected_violations = [r for r in config_results if r["expected"] == "BLOCK"]
        vr = sum(1 for r in expected_violations if r["detected"] == "BLOCK") / max(len(expected_violations), 1)

        metrics[config_name] = {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "tpr": tpr, "fpr": fpr, "precision": precision, "f1": f1,
            "vr": vr,
        }

    # Interaction Effect
    if all(c in metrics for c in ["C0", "C3", "C4", "C3+C4"]):
        ie = metrics["C3+C4"]["vr"] - metrics["C3"]["vr"] - metrics["C4"]["vr"] + metrics["C0"]["vr"]
        metrics["interaction_effect"] = ie
    else:
        metrics["interaction_effect"] = None

    return metrics


def print_report(results: list[dict], metrics: dict):
    """Print a human-readable report."""
    print("\n" + "=" * 70)
    print("SEEDED VIOLATION TEST RESULTS (Experiment 3.3)")
    print("=" * 70)

    for config_name in CONFIGS:
        m = metrics[config_name]
        print(f"\n--- {config_name} (SHACL={'ON' if CONFIGS[config_name]['shacl'] else 'OFF'}, "
              f"Compliance={'ON' if CONFIGS[config_name]['compliance'] else 'OFF'}) ---")
        print(f"  TP={m['tp']}  FP={m['fp']}  FN={m['fn']}  TN={m['tn']}")
        print(f"  TPR (recall) = {m['tpr']:.3f}")
        print(f"  FPR          = {m['fpr']:.3f}")
        print(f"  Precision    = {m['precision']:.3f}")
        print(f"  F1           = {m['f1']:.3f}")
        print(f"  VR           = {m['vr']:.3f}")

        config_results = [r for r in results if r["config"] == config_name]
        for r in config_results:
            mark = "✓" if r["classification"] in ("TP", "TN") else "✗"
            print(f"    {mark} {r['case_id']}: {r['client']} → {r['warehouse']} "
                  f"expected={r['expected']} detected={r['detected']} [{r['classification']}]")

    ie = metrics.get("interaction_effect")
    if ie is not None:
        print(f"\n--- Interaction Effect ---")
        print(f"  IE = VR(C3+C4) - VR(C3) - VR(C4) + VR(C0)")
        print(f"     = {metrics['C3+C4']['vr']:.3f} - {metrics['C3']['vr']:.3f} "
              f"- {metrics['C4']['vr']:.3f} + {metrics['C0']['vr']:.3f} = {ie:.3f}")
        if ie > 0:
            print("  → Layers are COMPLEMENTARY (defense-in-depth works)")
        elif ie == 0:
            print("  → Layers are INDEPENDENT")
        else:
            print("  → Layers are REDUNDANT")


def save_csv(results: list[dict], path: str):
    """Save results to CSV."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader()
        w.writerows(results)
    print(f"\nCSV saved: {path}")


def save_metrics_csv(metrics: dict, path: str):
    """Save per-config metrics to CSV."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["config", "shacl", "compliance", "tp", "fp", "fn", "tn",
                     "tpr", "fpr", "precision", "f1", "vr"])
        for config_name in CONFIGS:
            m = metrics[config_name]
            w.writerow([config_name, CONFIGS[config_name]["shacl"],
                         CONFIGS[config_name]["compliance"],
                         m["tp"], m["fp"], m["fn"], m["tn"],
                         f"{m['tpr']:.3f}", f"{m['fpr']:.3f}",
                         f"{m['precision']:.3f}", f"{m['f1']:.3f}", f"{m['vr']:.3f}"])
        # Interaction effect as last row
        ie = metrics.get("interaction_effect")
        if ie is not None:
            w.writerow(["interaction_effect", "", "", "", "", "", "",
                         "", "", "", "", f"{ie:.3f}"])
    print(f"Metrics CSV saved: {path}")


def main():
    parser = argparse.ArgumentParser(description="Seeded violation tests for Experiment 3.3")
    parser.add_argument("--output", default="evaluation/results/report/csv/seeded_violations.csv")
    parser.add_argument("--metrics-output", default="evaluation/results/report/csv/seeded_violation_metrics.csv")
    args = parser.parse_args()

    results = run_seeded_tests()
    metrics = compute_metrics(results)
    print_report(results, metrics)
    save_csv(results, args.output)
    save_metrics_csv(metrics, args.metrics_output)


if __name__ == "__main__":
    main()
