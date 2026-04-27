"""Shared OpenAI client and Postgres access for business-domain agents."""

import json
import logging
import os
from typing import Any

from openai import AsyncOpenAI
import asyncpg

logger = logging.getLogger(__name__)

_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano-2025-08-07")
_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "16384"))


async def call_llm(system_prompt: str, task: str, context: str = "") -> str:
    """Call the OpenAI Chat Completions API and return the assistant's text."""
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    user_content = f"Task: {task}"
    if context:
        user_content += f"\n\nContext / upstream data:\n{context}"

    logger.info("Calling OpenAI (%s) – task length=%d context length=%d",
                _MODEL, len(task), len(context))

    resp = await client.chat.completions.create(
        model=_MODEL,
        max_completion_tokens=_MAX_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    text = resp.choices[0].message.content or ""

    # Track token usage for TOK metric
    usage = getattr(resp, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    total_tokens = getattr(usage, "total_tokens", 0) if usage else 0
    logger.info("OpenAI responded – %d chars, tokens: in=%d out=%d total=%d",
                len(text), input_tokens, output_tokens, total_tokens)

    # Store token counts in a thread-local accumulator for the results collector
    import threading
    if not hasattr(call_llm, "_token_accumulator"):
        call_llm._token_accumulator = threading.local()
    acc = call_llm._token_accumulator
    if not hasattr(acc, "total"):
        acc.total = 0
        acc.input = 0
        acc.output = 0
    acc.total += total_tokens
    acc.input += input_tokens
    acc.output += output_tokens

    return text


def get_token_usage() -> dict[str, int]:
    """Return accumulated token usage and reset counters."""
    import threading
    if not hasattr(call_llm, "_token_accumulator"):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    acc = call_llm._token_accumulator
    result = {
        "input_tokens": getattr(acc, "input", 0),
        "output_tokens": getattr(acc, "output", 0),
        "total_tokens": getattr(acc, "total", 0),
    }
    acc.total = 0
    acc.input = 0
    acc.output = 0
    return result


async def get_db_pool() -> asyncpg.Pool:
    """Return a connection pool to the shared Postgres database."""
    dsn = os.environ.get("DATABASE_URL", "postgresql://agentcy:agentcy@postgres:5432/agentcy")
    return await asyncpg.create_pool(dsn, min_size=1, max_size=3)


async def query_db(sql: str, *args: Any) -> list[dict]:
    """Run a read query and return rows as list of dicts."""
    pool = await get_db_pool()
    try:
        rows = await pool.fetch(sql, *args)
        return [dict(r) for r in rows]
    finally:
        await pool.close()


def rows_to_context(rows: list[dict], label: str = "data") -> str:
    """Format DB rows as a readable context string for the LLM."""
    if not rows:
        return f"No {label} found."

    def _serialize(obj: Any) -> Any:
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return str(obj)

    return json.dumps(rows, indent=2, default=_serialize)
