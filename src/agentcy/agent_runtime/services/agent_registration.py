from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence

from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.agent_registry_model import AgentRegistryEntry, AgentStatus

logger = logging.getLogger(__name__)


def _split_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_status(value: Any) -> AgentStatus:
    if isinstance(value, AgentStatus):
        return value
    if not value:
        return AgentStatus.ONLINE
    lowered = str(value).strip().lower()
    for status in AgentStatus:
        if status.value == lowered:
            return status
    return AgentStatus.ONLINE


def _resolve_service_entries(
    service_store: Any,
    *,
    username: str,
    service_names: Optional[Sequence[str]],
) -> List[Dict[str, str]]:
    if service_store is None:
        return []
    try:
        entries = service_store.list_all(username)
    except Exception:
        logger.exception("Agent registration failed listing services")
        return []

    if not service_names:
        return entries

    wanted = {str(name) for name in service_names}
    return [entry for entry in entries if str(entry.get("service_name")) in wanted]


def _resolve_service_doc(
    service_store: Any,
    *,
    username: str,
    service_id: str,
) -> Dict[str, Any]:
    if service_store is None:
        return {}
    try:
        doc = service_store.get(username, service_id)
        return doc or {}
    except Exception:
        logger.debug("Agent registration could not fetch service %s", service_id, exc_info=True)
        return {}


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    registry = rm.agent_registry_store
    service_store = rm.service_store
    if registry is None or service_store is None:
        raise RuntimeError("agent_registration requires agent_registry_store and service_store")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    if not username:
        raise ValueError("agent_registration requires username")

    payload: Dict[str, Any] = {}
    if isinstance(message, dict):
        payload = dict(message.get("data") or message)
    else:
        payload = dict(getattr(message, "data", {}) or {})

    service_names = payload.get("service_names") or payload.get("services")
    if isinstance(service_names, str):
        service_names = [service_names]

    default_caps = _as_list(payload.get("default_capabilities"))
    default_caps += _split_csv(os.getenv("AGENT_SEEDER_DEFAULT_CAPABILITIES"))
    default_tags = _as_list(payload.get("default_tags"))
    default_tags += _split_csv(os.getenv("AGENT_SEEDER_TAGS"))
    default_status = _normalize_status(payload.get("default_status") or os.getenv("AGENT_SEEDER_STATUS"))

    caps_override = payload.get("service_capabilities") or {}
    tags_override = payload.get("service_tags") or {}

    entries = _resolve_service_entries(
        service_store,
        username=username,
        service_names=service_names if isinstance(service_names, list) else None,
    )
    if not entries:
        logger.warning("Agent registration found no services for %s", username)
        return {"registered": 0, "agent_ids": []}

    agent_ids: List[str] = []
    for entry in entries:
        service_name = entry.get("service_name")
        service_id = entry.get("service_id")
        if not service_name or not service_id:
            continue
        service_doc = _resolve_service_doc(service_store, username=username, service_id=str(service_id))
        description = service_doc.get("description")
        agent_id = str(service_name)

        capabilities = set(default_caps)
        capabilities.add(str(service_name))
        capabilities.update(_as_list(caps_override.get(service_name)))

        tags = set(default_tags)
        tags.update(_as_list(tags_override.get(service_name)))

        registry_entry = AgentRegistryEntry(
            agent_id=agent_id,
            service_name=str(service_name),
            owner=username,
            description=description,
            capabilities=sorted(capabilities),
            tags=sorted(tags),
            status=default_status,
            metadata={
                "service_id": str(service_id),
                "seeded": True,
                "source": "agent_registration",
            },
        )
        registry.upsert(username=username, entry=registry_entry)
        agent_ids.append(agent_id)

    logger.info("Agent registration seeded %d agents for %s", len(agent_ids), username)
    return {"registered": len(agent_ids), "agent_ids": agent_ids}
