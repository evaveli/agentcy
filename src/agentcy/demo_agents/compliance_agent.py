"""Compliance Validation Agent — rule-based validation of warehouse-client pairings.

No LLM — pure deterministic logic. Validates domain constraints:
- Cold storage requirements
- Hazmat certification
- Security level for critical deals
- Square footage range
- Budget compliance

Scoping: extracts the specific client and suggested warehouse(s) from upstream
warehouse-match output. Falls back to checking all pairs if extraction fails.
"""

import json
import logging
import re

from agentcy.demo_agents.base import query_db

logger = logging.getLogger(__name__)

RULES = [
    {"id": "COLD_STORAGE_REQUIRED", "severity": "BLOCK",
     "description": "Client requires cold storage but warehouse lacks it"},
    {"id": "HAZMAT_REQUIRED", "severity": "BLOCK",
     "description": "Client requires hazmat storage but warehouse is not certified"},
    {"id": "HIGH_SECURITY_FOR_CRITICAL", "severity": "BLOCK",
     "description": "Critical-priority deal requires enhanced or high security"},
    {"id": "SQFT_WITHIN_RANGE", "severity": "WARN",
     "description": "Warehouse available sqft outside client's required range"},
    {"id": "BUDGET_COMPLIANCE", "severity": "WARN",
     "description": "Monthly warehouse cost exceeds client's budget"},
]

# Known warehouse names from seed data (for extraction from LLM output)
KNOWN_WAREHOUSES = [
    "LogisPark Milano Nord",
    "Bavaria Logistics Hub",
    "PharmaStore Lyon",
    "Centro Logistico Bologna",
    "Gothenburg Port Warehouse",
    "Stuttgart TechCenter",
    "Roma Sud Distribution",
    "Paris CDG Logistics",
]

KNOWN_CLIENTS = [
    "FreshCo Logistics",
    "TechParts GmbH",
    "GreenLeaf Pharma",
    "QuickShip Express",
    "Nordic Steel AB",
]


def _extract_context_from_upstream(upstream: str) -> tuple[list[str], list[str]]:
    """Try to extract client names and warehouse names from upstream output.

    Returns (client_names, warehouse_names) found in the text.
    """
    clients_found = []
    warehouses_found = []

    if not upstream:
        return clients_found, warehouses_found

    text = upstream.lower()
    for name in KNOWN_CLIENTS:
        if name.lower() in text:
            clients_found.append(name)
    for name in KNOWN_WAREHOUSES:
        if name.lower() in text:
            warehouses_found.append(name)

    return clients_found, warehouses_found


def _check_pair(req: dict, wh: dict) -> list[dict]:
    """Run all compliance rules on a single client-warehouse pair."""
    violations = []
    special = (req.get("special_requirements") or "").lower()
    priority = (req.get("priority") or "medium").lower()
    client = req.get("company_name", "unknown")
    budget = float(req.get("max_monthly_budget") or 0)
    min_sqft = req.get("min_sqft") or 0
    max_sqft = req.get("max_sqft") or 999999
    wh_name = wh.get("name", "unknown")
    pair = {"client": client, "warehouse": wh_name}

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

    return violations


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    username = getattr(message, "username", None) or "default"

    # Fetch upstream warehouse-match output directly from the ephemeral store.
    # The message envelope doesn't carry raw_output — it's stored separately.
    upstream = ""
    eph_store = getattr(rm, "ephemeral_store", None)
    if eph_store and pipeline_run_id:
        try:
            output_doc = eph_store.read_task_output(username, "warehouse-match", pipeline_run_id)
            if output_doc:
                upstream = output_doc.get("raw_output", "")
                logger.info("Compliance: fetched warehouse-match output from ephemeral store (%d chars)", len(upstream))
        except Exception as e:
            logger.warning("Compliance: failed to fetch warehouse-match output: %s", e)

    if not upstream:
        # Fallback: try message data paths
        upstream = data.get("raw_output", "")
        if not upstream:
            payload = data.get("payload", {})
            if isinstance(payload, dict):
                upstream = payload.get("raw_output", "")

    logger.info("Compliance agent upstream length=%d, scoping attempt...", len(upstream))

    # Extract context from upstream warehouse-match output
    clients_found, warehouses_found = _extract_context_from_upstream(upstream)
    scoped = bool(clients_found and warehouses_found)

    if scoped:
        logger.info(
            "Compliance scoped to clients=%s warehouses=%s (from upstream)",
            clients_found, warehouses_found,
        )

    try:
        requirements = await query_db(
            "SELECT cr.*, c.company_name, c.industry "
            "FROM client_requirements cr JOIN clients c ON cr.client_id = c.id"
        )
    except Exception as e:
        logger.error("Failed to load client requirements: %s", e)
        requirements = []

    try:
        warehouses = await query_db(
            "SELECT * FROM warehouses WHERE status = 'available'"
        )
    except Exception as e:
        logger.error("Failed to load warehouses: %s", e)
        warehouses = []

    # Filter to relevant scope if extraction succeeded
    if scoped:
        requirements = [
            r for r in requirements
            if r.get("company_name") in clients_found
        ]
        warehouses = [
            w for w in warehouses
            if w.get("name") in warehouses_found
        ]

    violations = []
    checked_pairs = 0

    for req in requirements:
        for wh in warehouses:
            checked_pairs += 1
            violations.extend(_check_pair(req, wh))

    blocks = [v for v in violations if v["severity"] == "BLOCK"]
    warns = [v for v in violations if v["severity"] == "WARN"]

    scope_info = (
        f"Scoped to {clients_found} x {warehouses_found}"
        if scoped
        else "Full scan (all clients x all warehouses)"
    )

    result = {
        "passed": len(blocks) == 0,
        "checked_pairs": checked_pairs,
        "total_violations": len(violations),
        "blocks": len(blocks),
        "warnings": len(warns),
        "violations": violations,
        "rules_applied": [r["id"] for r in RULES],
        "scope": scope_info,
        "scoped": scoped,
        "clients_checked": clients_found if scoped else [r.get("company_name") for r in requirements],
        "warehouses_checked": warehouses_found if scoped else [w.get("name") for w in warehouses],
        "summary": (
            f"Checked {checked_pairs} client-warehouse pair(s). "
            f"Found {len(blocks)} blocking violation(s) and {len(warns)} warning(s). "
            f"({scope_info})"
        ),
    }

    return {"raw_output": json.dumps(result, indent=2)}
