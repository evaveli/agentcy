"""Deal Summary Agent — aggregates and synthesises from emails, notes, and HubSpot forms."""

import logging
from agentcy.demo_agents.base import call_llm, query_db, rows_to_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Deal Summary Agent for a commercial real-estate / warehouse brokerage. \
You receive deal records, associated emails, and internal notes from the database, \
plus any upstream context (e.g. call transcripts).

Your job:
1. Produce a consolidated deal summary for each active deal.
2. Highlight deal stage, key contacts, financial terms, and timeline.
3. Identify risks, missing information, and recommended next steps.
4. Cross-reference upstream call analysis if provided.
5. Output in well-structured markdown with a summary table."""


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    description = data.get("description", str(data))
    upstream = data.get("raw_output", "")

    # Pull deals, emails, notes from Postgres
    sections = []
    for table, cols, label in [
        ("deals", "id, deal_name, client_name, deal_stage, deal_value, currency, "
                  "start_date, expected_close, assigned_broker, notes", "deals"),
        ("emails", "id, deal_id, sender, recipient, subject, body_snippet, "
                   "sent_at", "emails"),
        ("notes", "id, deal_id, author, content, created_at", "notes"),
    ]:
        try:
            rows = await query_db(
                f"SELECT {cols} FROM {table} ORDER BY 1 DESC LIMIT 30"
            )
            sections.append(f"## {label.title()}\n{rows_to_context(rows, label)}")
        except Exception as e:
            logger.warning("DB query for %s failed: %s", table, e)
            sections.append(f"## {label.title()}\n(database unavailable)")

    full_context = "\n\n".join(sections)
    if upstream:
        full_context += f"\n\n## Upstream Output\n{upstream}"

    result = await call_llm(SYSTEM_PROMPT, description, full_context)
    return {"raw_output": result}
