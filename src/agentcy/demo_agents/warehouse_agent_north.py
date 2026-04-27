"""Warehouse Suggestion Agent — Northern Europe (Scandinavia, Nordics).

Competes with warehouse_agent_central and warehouse_agent_south via CNP bidding.
Region filter controlled by WAREHOUSE_REGION_FILTER env var.
"""

import logging
import os

from agentcy.demo_agents.base import call_llm, query_db, rows_to_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Warehouse Suggestion Agent specializing in Northern European logistics \
markets (Scandinavia, Nordics). You receive warehouse inventory for your region, \
client requirements, and upstream context.

Your job:
1. Match available warehouses against client requirements considering:
   - Space (sqft range, ceiling height, dock doors)
   - Location (city/region, proximity constraints)
   - Budget (monthly rent within budget)
   - Special features (cold storage, hazmat, security, crane, rail access)
   - Northern European logistics advantages (port access, rail networks, \
     Nordic trade corridors)
2. If no warehouses in your region match the client's needs, state this \
   clearly and explain why — do not suggest irrelevant warehouses.
3. Rank the top suggestions with a match score (0-100) and explain gaps.
4. For each suggestion, explain WHY it fits and note any constraints not met.
5. Output in well-structured markdown with tables."""

_REGION_FILTER = None


def _get_region_filter() -> str:
    global _REGION_FILTER
    if _REGION_FILTER is None:
        _REGION_FILTER = os.getenv("WAREHOUSE_REGION_FILTER", "Vastra Gotaland")
    return _REGION_FILTER


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    description = data.get("description", str(data))
    upstream = data.get("raw_output", "")

    regions = [r.strip() for r in _get_region_filter().split(",")]
    placeholders = ", ".join(f"${i+1}" for i in range(len(regions)))

    sections = []

    # Regional warehouses
    try:
        rows = await query_db(
            f"SELECT id, name, address, city, region, total_sqft, "
            f"available_sqft, ceiling_height_ft, dock_doors, "
            f"monthly_rent_per_sqft, has_cold_storage, has_hazmat, "
            f"security_level, year_built, status "
            f"FROM warehouses WHERE region IN ({placeholders})",
            *regions,
        )
        sections.append(f"## Regional Warehouses (Northern Europe)\n{rows_to_context(rows, 'warehouses')}")
    except Exception as e:
        logger.warning("DB query for warehouses failed: %s", e)
        sections.append("## Regional Warehouses\n(database unavailable)")

    # Client requirements
    for table, cols, label in [
        ("client_requirements", "id, client_id, min_sqft, max_sqft, "
                                "ceiling_height_ft, dock_doors, preferred_location, "
                                "max_monthly_budget, lease_term_months, "
                                "move_in_date, special_requirements, priority",
         "client_requirements"),
        ("clients", "id, company_name, industry, contact_name, company_size",
         "clients"),
    ]:
        try:
            rows = await query_db(f"SELECT {cols} FROM {table} ORDER BY 1 DESC LIMIT 30")
            sections.append(f"## {label.title()}\n{rows_to_context(rows, label)}")
        except Exception as e:
            logger.warning("DB query for %s failed: %s", table, e)
            sections.append(f"## {label.title()}\n(database unavailable)")

    full_context = "\n\n".join(sections)
    if upstream:
        full_context += f"\n\n## Upstream Output\n{upstream}"

    result = await call_llm(SYSTEM_PROMPT, description, full_context)
    return {"raw_output": result}
