"""Warehouse Suggestion Agent scorer — top-k match, constraint satisfaction, distance delta."""

from __future__ import annotations

import re
from typing import Any

from evaluation.e1_harness.ground_truth import WAREHOUSES, WarehouseGroundTruth


# Approximate region groupings for distance delta
_REGION_GROUPS = {
    "Lombardy": "northern_italy",
    "Emilia-Romagna": "northern_italy",
    "Lazio": "central_italy",
    "Bavaria": "southern_germany",
    "Baden-Wurttemberg": "southern_germany",
    "Auvergne-Rhone-Alpes": "southeast_france",
    "Ile-de-France": "northern_france",
    "Vastra Gotaland": "western_sweden",
}


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _extract_ranked_warehouses(output: str) -> list[str]:
    """Extract warehouse names from ranked output, in order.

    Handles formats like:
    - 1. LogisPark Milano Nord
    - **#1: LogisPark Milano Nord**
    - | 1 | LogisPark Milano Nord | ...
    """
    # Collect all warehouse names found in output, preserving order of first appearance
    output_lower = output.lower()
    found = []
    for wid, wh in WAREHOUSES.items():
        name = wh["name"]
        # Search for the warehouse name in the output
        pos = output_lower.find(_normalize(name))
        if pos >= 0:
            found.append((pos, name))

    # Sort by position of first appearance (assumes ranking order matches text order)
    found.sort(key=lambda x: x[0])
    return [name for _, name in found]


def _warehouse_by_name(name: str) -> dict | None:
    """Look up warehouse data by name."""
    norm = _normalize(name)
    for wid, wh in WAREHOUSES.items():
        if _normalize(wh["name"]) == norm:
            return wh
    return None


def top1_match(output: str, gt: WarehouseGroundTruth) -> float:
    """1.0 if first recommended warehouse matches expert's top pick, else 0.0."""
    ranked = _extract_ranked_warehouses(output)
    if not ranked:
        return 0.0
    return 1.0 if _normalize(ranked[0]) == _normalize(gt.correct_top1) else 0.0


def top3_match(output: str, gt: WarehouseGroundTruth) -> float:
    """Fraction of expert's top choices appearing in AI's top 3."""
    ranked = _extract_ranked_warehouses(output)[:3]
    if not ranked or not gt.correct_top3:
        return 0.0

    ranked_norm = [_normalize(r) for r in ranked]
    matched = sum(1 for gt_name in gt.correct_top3 if _normalize(gt_name) in ranked_norm)
    return matched / len(gt.correct_top3)


def hard_constraint_satisfaction(output: str, gt: WarehouseGroundTruth) -> float:
    """Fraction of recommended warehouses that satisfy ALL hard constraints."""
    ranked = _extract_ranked_warehouses(output)
    if not ranked:
        return 0.0

    satisfying = 0
    for name in ranked:
        wh = _warehouse_by_name(name)
        if wh is None:
            continue

        passes_all = True
        for constraint, required in gt.hard_constraints.items():
            if constraint == "has_cold_storage" and wh.get("has_cold_storage") != required:
                passes_all = False
            elif constraint == "has_hazmat" and wh.get("has_hazmat") != required:
                passes_all = False
            elif constraint == "min_available_sqft" and wh.get("available_sqft", 0) < required:
                passes_all = False
            elif constraint == "min_dock_doors" and wh.get("dock_doors", 0) < required:
                passes_all = False
            elif constraint == "min_ceiling_height" and wh.get("ceiling_height_ft", 0) < required:
                passes_all = False

        if passes_all:
            satisfying += 1

    return satisfying / len(ranked)


def distance_delta(output: str, gt: WarehouseGroundTruth) -> str:
    """Compare AI's top pick region vs client's preferred region.

    Returns: 'same_city', 'same_region', or 'different_region'.
    """
    ranked = _extract_ranked_warehouses(output)
    if not ranked:
        return "no_recommendation"

    wh = _warehouse_by_name(ranked[0])
    if wh is None:
        return "unknown_warehouse"

    # City-level match
    if _normalize(wh["city"]) == _normalize(gt.client_location):
        return "same_city"

    # Region-group match
    wh_group = _REGION_GROUPS.get(wh.get("region", ""), "unknown")
    # Find client region group from location
    client_group = None
    for wid, w in WAREHOUSES.items():
        if _normalize(w["city"]) == _normalize(gt.client_location):
            client_group = _REGION_GROUPS.get(w.get("region", ""), "unknown")
            break

    if client_group and wh_group == client_group:
        return "same_region"

    return "different_region"


def score_warehouse(output: str, gt: WarehouseGroundTruth) -> dict[str, Any]:
    """Run all warehouse metrics."""
    raw = output if isinstance(output, str) else output.get("raw_output", "")
    return {
        "top1_match": top1_match(raw, gt),
        "top3_match": round(top3_match(raw, gt), 2),
        "hard_constraint_satisfaction_pct": round(
            hard_constraint_satisfaction(raw, gt) * 100, 1
        ),
        "distance_delta": distance_delta(raw, gt),
    }
