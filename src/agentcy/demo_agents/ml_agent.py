"""Client Necessity Form Agent — AI-powered form filling based on contextual understanding."""

import logging
from agentcy.demo_agents.base import call_llm, query_db, rows_to_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Client Necessity Form Agent for a commercial real-estate / warehouse brokerage. \
You receive client records, their requirements, and any upstream context (deal summaries, \
call transcripts).

Your job:
1. For each client, produce a completed Client Necessity Form covering:
   - Company profile (name, industry, size, contacts)
   - Space requirements (sq footage, ceiling height, dock doors, power needs)
   - Location preferences (city/region, proximity constraints, transport access)
   - Budget & timeline (monthly budget, lease term, move-in date)
   - Special requirements (cold storage, hazmat, 24/7 access, security)
2. Pre-fill as much as possible from the database and upstream context.
3. Flag fields that need client confirmation vs. fields that are inferred.
4. Output in well-structured markdown with a form-like layout."""


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    description = data.get("description", str(data))
    upstream = data.get("raw_output", "")

    # Pull client requirements from Postgres
    sections = []
    for table, cols, label in [
        ("clients", "id, company_name, industry, contact_name, contact_email, "
                    "contact_phone, company_size, created_at", "clients"),
        ("client_requirements", "id, client_id, min_sqft, max_sqft, "
                                "ceiling_height_ft, dock_doors, preferred_location, "
                                "max_monthly_budget, lease_term_months, "
                                "move_in_date, special_requirements, priority", "requirements"),
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
