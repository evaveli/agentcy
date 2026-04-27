"""Speed Estimation Agent — optimizes warehouse deals for fastest time-to-operational.

Competes with cost_estimator via CNP bidding on deal_estimation capability.
Wins when stimulus/priority is high/critical (time matters more than cost).
"""

import logging

from agentcy.demo_agents.base import call_llm, query_db, rows_to_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Speed Estimation Agent for a commercial warehouse brokerage. You receive \
warehouse inventory, client requirements, and upstream context (warehouse suggestions, \
deal summaries).

Your job:
1. For each client-warehouse pairing from upstream suggestions, estimate:
   - Warehouse readiness (move-in ready vs needs fit-out)
   - Fit-out timeline based on special requirements:
     * Cold storage installation: 6-8 weeks
     * ESD flooring: 3-4 weeks
     * Clean room (ISO Class 7): 8-12 weeks
     * Crane upgrade (8-ton to 15-ton): 6-8 weeks
     * Food-grade flooring: 4-6 weeks
     * Hazmat certification: 4-6 weeks
     * Mezzanine installation: 4-6 weeks
   - Compliance/certification timeline (GDP certification: 4 weeks, \
     security upgrades: 2-3 weeks)
   - Total time-to-operational from lease signing
2. Compare against client's move-in date and flag any that cannot meet the deadline.
3. Rank options by shortest time-to-operational.
4. Recommend the fastest viable option with justification.
5. Output in structured markdown with a timeline comparison table.

IMPORTANT: Only use data from the provided context. Do not invent timelines \
not derivable from the warehouse specs and requirements data."""


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    description = data.get("description", str(data))
    upstream = data.get("raw_output", "")

    sections = []
    for table, cols, label in [
        ("warehouses",
         "id, name, city, region, available_sqft, status, year_built, "
         "has_cold_storage, has_hazmat, security_level, ceiling_height_ft, dock_doors",
         "warehouses"),
        ("client_requirements",
         "cr.id, cr.client_id, c.company_name, cr.min_sqft, cr.max_sqft, "
         "cr.move_in_date, cr.special_requirements, cr.priority",
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
