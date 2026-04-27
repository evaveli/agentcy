"""Call Transcription Agent — transcribes and extracts key info from customer/supplier calls."""

import logging
from agentcy.demo_agents.base import call_llm, query_db, rows_to_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a Call Transcription & Analysis Agent for a commercial real-estate / warehouse \
brokerage. You receive raw call records (caller, notes, duration, timestamps) from the \
database and any upstream context.

Your job:
1. Produce a clean, structured transcript summary for each call.
2. Extract key entities: client names, property references, locations, budget figures, \
   timelines, and action items.
3. Flag any urgent items or follow-ups.
4. Output in well-structured markdown."""


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    description = data.get("description", str(data))
    upstream = data.get("raw_output", "")

    # Pull recent calls from Postgres
    try:
        rows = await query_db(
            "SELECT id, caller_name, caller_type, phone, call_date, duration_minutes, "
            "notes, status FROM calls ORDER BY call_date DESC LIMIT 20"
        )
        db_context = rows_to_context(rows, "calls")
    except Exception as e:
        logger.warning("DB query failed, proceeding without call data: %s", e)
        db_context = "(database unavailable)"

    full_context = f"## Call Records from Database\n{db_context}"
    if upstream:
        full_context += f"\n\n## Upstream Output\n{upstream}"

    result = await call_llm(SYSTEM_PROMPT, description, full_context)
    return {"raw_output": result}
