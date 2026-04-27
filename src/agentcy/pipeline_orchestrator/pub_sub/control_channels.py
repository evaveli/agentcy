from __future__ import annotations

import hashlib
import re
from typing import Tuple

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _safe_prefix(value: str, *, maxlen: int = 32) -> str:
    cleaned = _NAME_SAFE.sub("-", value).strip("-.")
    return cleaned[:maxlen]


def control_channel_names(
    *,
    service_name: str,
    username: str,
    pipeline_id: str,
    pipeline_run_id: str,
) -> Tuple[str, str, str]:
    seed = f"{service_name}|{username}|{pipeline_id}|{pipeline_run_id}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    prefix = _safe_prefix(service_name)
    suffix = f"{prefix}-{digest}" if prefix else digest
    exchange = f"control_exchange_{suffix}"
    queue = f"control_queue_{suffix}"
    routing_key = f"control.{suffix}"
    return exchange, queue, routing_key
