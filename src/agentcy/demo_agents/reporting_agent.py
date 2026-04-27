"""Proposal Template Agent — generates client-specific deal proposals using LLM reasoning."""

import logging
from agentcy.demo_agents.base import call_llm, query_db, rows_to_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Proposal Template Agent for a commercial real-estate / warehouse brokerage. \
You receive deal information, client requirements, warehouse options, and upstream \
context (call transcripts, deal summaries, client necessity forms).

Your job:
1. Generate a professional, client-ready deal proposal document containing:
   - Executive summary
   - Client requirements recap
   - Recommended warehouse option(s) with key specs
   - Financial terms (monthly rent, deposit, lease term, escalation)
   - Location & logistics advantages
   - Comparison table if multiple options
   - Proposed timeline & next steps
2. Tailor language and emphasis to the client's industry and priorities.
3. Include a "Why This Property" section linking property features to client needs.
4. Output as a polished markdown document ready for PDF conversion."""


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    description = data.get("description", str(data))
    upstream = data.get("raw_output", "")

    # Pull deals + warehouses from Postgres
    sections = []
    for table, cols, label in [
        ("deals", "id, deal_name, client_name, deal_stage, deal_value, currency, "
                  "start_date, expected_close, assigned_broker, notes", "deals"),
        ("warehouses", "id, name, address, city, region, total_sqft, "
                       "available_sqft, ceiling_height_ft, dock_doors, "
                       "monthly_rent_per_sqft, has_cold_storage, has_hazmat, "
                       "security_level, year_built, status", "warehouses"),
        ("client_requirements", "id, client_id, min_sqft, max_sqft, "
                                "preferred_location, max_monthly_budget, "
                                "lease_term_months, special_requirements, priority",
         "client_requirements"),
    ]:
        try:
            rows = await query_db(f"SELECT {cols} FROM {table} ORDER BY 1 DESC LIMIT 20")
            sections.append(f"## {label.title()}\n{rows_to_context(rows, label)}")
        except Exception as e:
            logger.warning("DB query for %s failed: %s", table, e)
            sections.append(f"## {label.title()}\n(database unavailable)")

    full_context = "\n\n".join(sections)
    if upstream:
        full_context += f"\n\n## Upstream Output\n{upstream}"

    result = await call_llm(SYSTEM_PROMPT, description, full_context)
    return {"raw_output": result}
