"""Cost Estimation Agent — optimizes warehouse deals for lowest total cost of occupancy.

Competes with speed_estimator via CNP bidding on deal_estimation capability.
Wins when stimulus/priority is medium/low (budget matters more than urgency).
"""

import logging

from agentcy.demo_agents.base import call_llm, query_db, rows_to_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Cost Estimation Agent for a commercial warehouse brokerage. You receive \
warehouse inventory, client requirements, and upstream context (warehouse suggestions, \
deal summaries).

Your job:
1. For each client-warehouse pairing from upstream suggestions, calculate:
   - Monthly rent (available_sqft × monthly_rent_per_sqft, capped at client budget)
   - Total lease cost (monthly rent × lease_term_months)
   - Estimated fit-out cost based on special requirements:
     * Cold storage installation: ~€30/sqft for affected area
     * ESD flooring: ~€15/sqft
     * Clean room: ~€200/sqft for clean room section
     * Crane installation/upgrade: ~€50,000-150,000
     * Food-grade flooring: ~€20/sqft
     * Hazmat certification: ~€25,000
   - Total cost of occupancy (lease + fit-out)
2. Rank options by lowest total cost of occupancy.
3. Recommend the cheapest viable option with justification.
4. Flag any option where monthly cost exceeds client budget with specific overage amount.
5. Output in structured markdown with a comparison table including all cost components.

IMPORTANT: Only use data from the provided context. Do not invent prices or costs \
not derivable from the warehouse and requirements data."""


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    description = data.get("description", str(data))
    upstream = data.get("raw_output", "")

    sections = []
    for table, cols, label in [
        ("warehouses",
         "id, name, city, region, available_sqft, monthly_rent_per_sqft, "
         "has_cold_storage, has_hazmat, security_level, year_built, status",
         "warehouses"),
        ("client_requirements",
         "cr.id, cr.client_id, c.company_name, cr.min_sqft, cr.max_sqft, "
         "cr.max_monthly_budget, cr.lease_term_months, cr.special_requirements, cr.priority",
         "client_requirements"),
    ]:
        try:
            if "cr." in cols:
                rows = await query_db(
                    f"SELECT {cols} FROM client_requirements cr "
                    f"JOIN clients c ON cr.client_id = c.id ORDER BY cr.id"
                )
            else:
                rows = await query_db(
                    f"SELECT {cols} FROM {table} WHERE status = 'available' ORDER BY 1"
                )
            sections.append(f"## {label.title()}\n{rows_to_context(rows, label)}")
        except Exception as e:
            logger.warning("DB query for %s failed: %s", table, e)
            sections.append(f"## {label.title()}\n(database unavailable)")

    full_context = "\n\n".join(sections)
    if upstream:
        full_context += f"\n\n## Upstream Context (Warehouse Suggestions & Deal Summary)\n{upstream}"

    result = await call_llm(SYSTEM_PROMPT, description, full_context)
    return {"raw_output": result}
