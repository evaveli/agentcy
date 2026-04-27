"""Email / Communication Drafting Agent — drafts client follow-up emails from deal context."""

import json
import logging
from agentcy.demo_agents.base import call_llm, query_db, rows_to_context

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an Email Drafting Agent for a commercial real-estate / warehouse brokerage. \
You receive deal information, communication history, client details, and upstream \
context (deal summaries, call transcripts, necessity forms, proposals).

Your job:
1. Determine the appropriate email type from the task description:
   - **Intro email**: First contact — introduce the brokerage, reference how you learned \
     about the client's need, and propose an initial meeting or call.
   - **Quote follow-up**: Follow up on a previously shared proposal or pricing — \
     recap key terms, address any open questions, and push for next steps.
   - **Status update**: Inform the client about deal progress — current stage, \
     actions taken, upcoming milestones, and any items needing client input.

2. Draft a professional, client-facing email that:
   - Uses ONLY facts present in the provided deal data and communication history. \
     NEVER invent dates, prices, agreements, or commitments not in the source data.
   - Correctly references the client's name, company, deal details, volumes, \
     locations, timelines, and any previously discussed terms.
   - For follow-ups, accurately references what was previously discussed or agreed \
     (based on prior emails, notes, and call records).
   - Contains a clear call-to-action or next step for the recipient.
   - Matches the deal stage — do not use cold-intro tone for an existing client \
     in negotiation, and do not assume familiarity for a new prospect.
   - Uses a professional but warm tone appropriate for B2B logistics/warehousing.

3. Output format — return a JSON object with these fields:
   {
     "email_type": "intro" | "quote_follow_up" | "status_update",
     "to": "recipient email",
     "from": "broker email",
     "subject": "email subject line",
     "body": "full email body text",
     "deal_entities": {
       "client_name": "...",
       "company": "...",
       "deal_name": "...",
       "locations": ["..."],
       "volumes_or_specs": "...",
       "timeline": "...",
       "pricing": "...",
       "key_requirements": ["..."]
     },
     "references_prior": true | false,
     "prior_references_summary": "what prior interactions this email references (if any)"
   }

IMPORTANT RULES:
- Every entity in "deal_entities" must come directly from the source data provided.
- If information is not available in the source data, omit it from the email — do NOT fabricate.
- The "body" must follow this structure: greeting → context/reference → body with specifics → \
  call-to-action → professional sign-off.
- Sign emails from the assigned broker on the deal."""


async def run(rm, pipeline_run_id, to_task, triggered_by, message):
    """Microservice entry-point (4-arg protocol)."""
    data = getattr(message, "data", {}) or {}
    description = data.get("description", str(data))
    upstream = data.get("raw_output", "")

    # Pull deal context from Postgres: deals, emails, clients, notes, calls
    sections = []
    for table, cols, label in [
        ("deals", "id, deal_name, client_name, deal_stage, deal_value, currency, "
                  "start_date, expected_close, assigned_broker, notes", "deals"),
        ("emails", "id, deal_id, sender, recipient, subject, body_snippet, "
                   "sent_at", "communication_history"),
        ("clients", "id, company_name, industry, contact_name, contact_email, "
                    "contact_phone, company_size", "clients"),
        ("notes", "id, deal_id, author, content, created_at", "internal_notes"),
        ("calls", "id, caller_name, caller_type, phone, call_date, "
                  "duration_minutes, notes, status", "call_records"),
        ("client_requirements", "id, client_id, min_sqft, max_sqft, "
                                "preferred_location, max_monthly_budget, "
                                "lease_term_months, special_requirements, priority",
         "client_requirements"),
    ]:
        try:
            rows = await query_db(
                f"SELECT {cols} FROM {table} ORDER BY 1 DESC LIMIT 25"
            )
            sections.append(f"## {label.title()}\n{rows_to_context(rows, label)}")
        except Exception as e:
            logger.warning("DB query for %s failed: %s", table, e)
            sections.append(f"## {label.title()}\n(database unavailable)")

    full_context = "\n\n".join(sections)
    if upstream:
        full_context += f"\n\n## Upstream Output\n{upstream}"

    result = await call_llm(SYSTEM_PROMPT, description, full_context)

    # Try to parse structured output; fall back to raw text
    try:
        parsed = json.loads(result)
        return {"raw_output": result, "structured": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"raw_output": result}
