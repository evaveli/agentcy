"""Ground truth for the ablation study — assignment accuracy and constraint violations.

Derived from Section 3.1 of the experimental design plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class AssignmentGroundTruth:
    """Expected correct agent assignment per client scenario."""
    client_id: int
    client_name: str
    correct_warehouse_agent: str       # service_name of correct regional agent
    correct_estimator: str             # service_name of correct estimator
    warehouse_rationale: str
    estimator_rationale: str
    priority: Literal["medium", "high", "critical"]


@dataclass(frozen=True)
class ViolationGroundTruth:
    """Known constraint violations that validation layers should catch."""
    client_name: str
    warehouse_name: str
    rule: str
    severity: Literal["BLOCK", "WARN"]
    detail: str


# ── Assignment accuracy ground truth ─────────────────────────────────

ASSIGNMENT_GROUND_TRUTH: list[AssignmentGroundTruth] = [
    AssignmentGroundTruth(
        client_id=1,
        client_name="FreshCo Logistics",
        correct_warehouse_agent="warehouse-south",
        correct_estimator="cost-estimator",
        warehouse_rationale="Milan preference → Southern Europe (Lombardy). LogisPark Milano Nord has cold storage.",
        estimator_rationale="Firm budget (18K/mo), medium timeline → cost optimization matters more.",
        priority="high",
    ),
    AssignmentGroundTruth(
        client_id=2,
        client_name="TechParts GmbH",
        correct_warehouse_agent="warehouse-central",
        correct_estimator="cost-estimator",
        warehouse_rationale="Munich/Stuttgart preference → Central Europe (Bavaria/Baden-Wurttemberg).",
        estimator_rationale="Flexible timeline (Sept 2026), budget-conscious → cost optimization.",
        priority="high",
    ),
    AssignmentGroundTruth(
        client_id=3,
        client_name="GreenLeaf Pharma",
        correct_warehouse_agent="warehouse-central",
        correct_estimator="speed-estimator",
        warehouse_rationale="Lyon/Paris preference → Central Europe (Auvergne-Rhone-Alpes / Ile-de-France). PharmaStore Lyon has cold+hazmat.",
        estimator_rationale="Critical priority, May 15 audit deadline → speed is essential.",
        priority="critical",
    ),
    AssignmentGroundTruth(
        client_id=4,
        client_name="QuickShip Express",
        correct_warehouse_agent="warehouse-south",
        correct_estimator="cost-estimator",
        warehouse_rationale="Bologna/Rome corridor → Southern Europe (Emilia-Romagna / Lazio).",
        estimator_rationale="Budget flexible but medium priority → cost optimization preferred.",
        priority="medium",
    ),
    AssignmentGroundTruth(
        client_id=5,
        client_name="Nordic Steel AB",
        correct_warehouse_agent="warehouse-north",
        correct_estimator="speed-estimator",
        warehouse_rationale="Gothenburg port → Northern Europe (Vastra Gotaland). Crane + rail access.",
        estimator_rationale="Crane installation timeline (6-8 weeks) is the critical path → speed optimization.",
        priority="high",
    ),
]


# ── Constraint violation ground truth ────────────────────────────────
# These are violations that SHOULD be caught by the compliance agent
# and/or SHACL validation when incorrect assignments are made.

VIOLATION_GROUND_TRUTH: list[ViolationGroundTruth] = [
    # FreshCo → any warehouse without cold storage
    ViolationGroundTruth(
        client_name="FreshCo Logistics",
        warehouse_name="Bavaria Logistics Hub",
        rule="COLD_STORAGE_REQUIRED",
        severity="BLOCK",
        detail="Bavaria Hub lacks cold storage; FreshCo requires it for frozen goods",
    ),
    ViolationGroundTruth(
        client_name="FreshCo Logistics",
        warehouse_name="Centro Logistico Bologna",
        rule="COLD_STORAGE_REQUIRED",
        severity="BLOCK",
        detail="Bologna lacks cold storage; FreshCo requires it",
    ),
    # GreenLeaf → warehouses without hazmat
    ViolationGroundTruth(
        client_name="GreenLeaf Pharma",
        warehouse_name="Centro Logistico Bologna",
        rule="HAZMAT_REQUIRED",
        severity="BLOCK",
        detail="Bologna not hazmat-certified; GreenLeaf requires it",
    ),
    ViolationGroundTruth(
        client_name="GreenLeaf Pharma",
        warehouse_name="LogisPark Milano Nord",
        rule="HAZMAT_REQUIRED",
        severity="BLOCK",
        detail="LogisPark has cold but no hazmat; GreenLeaf requires both",
    ),
    # GreenLeaf (critical priority) → standard security warehouses
    ViolationGroundTruth(
        client_name="GreenLeaf Pharma",
        warehouse_name="Centro Logistico Bologna",
        rule="HIGH_SECURITY_FOR_CRITICAL",
        severity="BLOCK",
        detail="Bologna has standard security; GreenLeaf deal is critical priority",
    ),
    ViolationGroundTruth(
        client_name="GreenLeaf Pharma",
        warehouse_name="Bavaria Logistics Hub",
        rule="HIGH_SECURITY_FOR_CRITICAL",
        severity="BLOCK",
        detail="Bavaria Hub has standard security; GreenLeaf deal is critical priority",
    ),
]


# ── Seeded violation scenarios for validation layer testing (Section 3.3) ──

@dataclass(frozen=True)
class SeededViolation:
    """Force-assigned pairings to test validation layers."""
    scenario_id: str
    client_name: str
    warehouse_name: str
    should_block: bool
    expected_rules: list[str] = field(default_factory=list)
    description: str = ""


SEEDED_VIOLATIONS: list[SeededViolation] = [
    SeededViolation(
        scenario_id="SV1",
        client_name="FreshCo Logistics",
        warehouse_name="Bavaria Logistics Hub",
        should_block=True,
        expected_rules=["COLD_STORAGE_REQUIRED"],
        description="Force FreshCo → Bavaria (no cold storage) — should BLOCK",
    ),
    SeededViolation(
        scenario_id="SV2",
        client_name="GreenLeaf Pharma",
        warehouse_name="Centro Logistico Bologna",
        should_block=True,
        expected_rules=["HAZMAT_REQUIRED", "HIGH_SECURITY_FOR_CRITICAL"],
        description="Force GreenLeaf → Bologna (no hazmat, standard security) — should BLOCK",
    ),
    SeededViolation(
        scenario_id="SV3",
        client_name="GreenLeaf Pharma",
        warehouse_name="LogisPark Milano Nord",
        should_block=True,
        expected_rules=["HAZMAT_REQUIRED"],
        description="Force GreenLeaf → LogisPark (cold but no hazmat) — partial violation",
    ),
    SeededViolation(
        scenario_id="SV4",
        client_name="QuickShip Express",
        warehouse_name="Paris CDG Logistics",
        should_block=False,
        expected_rules=[],
        description="Force QuickShip → Paris CDG (valid but suboptimal) — should PASS",
    ),
    SeededViolation(
        scenario_id="SV5",
        client_name="Nordic Steel AB",
        warehouse_name="PharmaStore Lyon",
        should_block=False,
        expected_rules=["SQFT_WITHIN_RANGE"],
        description="Force Nordic Steel → PharmaStore Lyon (wrong type, sqft too small) — WARN only, no BLOCK rules triggered",
    ),
    # Additional scenarios for stronger statistical power
    SeededViolation(
        scenario_id="SV6",
        client_name="FreshCo Logistics",
        warehouse_name="Roma Sud Distribution",
        should_block=True,
        expected_rules=["COLD_STORAGE_REQUIRED"],
        description="Force FreshCo → Roma Sud (no cold storage) — should BLOCK",
    ),
    SeededViolation(
        scenario_id="SV7",
        client_name="GreenLeaf Pharma",
        warehouse_name="Gothenburg Port Warehouse",
        should_block=True,
        expected_rules=["HAZMAT_REQUIRED", "HIGH_SECURITY_FOR_CRITICAL"],
        description="Force GreenLeaf → Gothenburg (no hazmat, enhanced but not high) — should BLOCK",
    ),
    SeededViolation(
        scenario_id="SV8",
        client_name="TechParts GmbH",
        warehouse_name="Stuttgart TechCenter",
        should_block=False,
        expected_rules=[],
        description="Force TechParts → Stuttgart (valid match) — should PASS",
    ),
]


# ── Metrics computation helpers ──────────────────────────────────────

def compute_assignment_accuracy(
    awards: list[dict],
) -> dict[str, float]:
    """Compute assignment accuracy from ContractAward records.

    Args:
        awards: list of dicts with 'client_name' and 'bidder_service_name' keys

    Returns:
        Dict with per-client accuracy and overall accuracy.
    """
    gt_map = {gt.client_name: gt for gt in ASSIGNMENT_GROUND_TRUTH}
    results = {}
    correct_total = 0
    total = 0

    for gt in ASSIGNMENT_GROUND_TRUTH:
        client_awards = [a for a in awards if a.get("client_name") == gt.client_name]
        if not client_awards:
            results[gt.client_name] = {"warehouse_correct": False, "estimator_correct": False}
            total += 2
            continue

        # Check warehouse assignment
        wh_awards = [a for a in client_awards if "warehouse" in a.get("task_capability", "")]
        wh_correct = any(
            a.get("bidder_service_name") == gt.correct_warehouse_agent
            for a in wh_awards
        )

        # Check estimator assignment
        est_awards = [a for a in client_awards if "estimation" in a.get("task_capability", "")]
        est_correct = any(
            a.get("bidder_service_name") == gt.correct_estimator
            for a in est_awards
        )

        results[gt.client_name] = {
            "warehouse_correct": wh_correct,
            "estimator_correct": est_correct,
        }
        if wh_correct:
            correct_total += 1
        if est_correct:
            correct_total += 1
        total += 2

    results["_overall"] = correct_total / total if total > 0 else 0.0
    return results


def compute_violation_rate(
    compliance_output: dict,
    seeded: bool = False,
) -> dict[str, float]:
    """Compute constraint violation rate from compliance agent output.

    Returns:
        Dict with block_count, warn_count, violation_rate, and per-rule counts.
    """
    violations = compliance_output.get("violations", [])
    blocks = [v for v in violations if v.get("severity") == "BLOCK"]
    warns = [v for v in violations if v.get("severity") == "WARN"]

    total_pairs = compliance_output.get("checked_pairs", 1)

    return {
        "block_count": len(blocks),
        "warn_count": len(warns),
        "total_violations": len(violations),
        "violation_rate": len(blocks) / total_pairs if total_pairs > 0 else 0.0,
        "rules_triggered": list({v.get("rule") for v in violations}),
    }


def compute_interaction_effect(
    vr_c0: float,
    vr_c3: float,
    vr_c4: float,
    vr_c34: float,
) -> float:
    """Compute the H3 interaction effect.

    IE = VR(C3+C4) - VR(C3) - VR(C4) + VR(C0)
    IE > 0: layers are complementary (defense-in-depth works)
    IE ~ 0: layers are independent
    IE < 0: layers are redundant
    """
    return vr_c34 - vr_c3 - vr_c4 + vr_c0
