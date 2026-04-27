from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

from agentcy.pydantic_models.agent_registry_model import AgentStatus
from agentcy.pydantic_models.multi_agent_pipeline import TaskSpec
from agentcy.semantic.capability_taxonomy import expand_capabilities


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def normalize_agent(doc: Dict[str, Any]) -> Dict[str, Any]:
    policy = doc.get("policy")
    if not isinstance(policy, dict):
        metadata = doc.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("policy"), dict):
            policy = metadata.get("policy")
        else:
            policy = {}
    return {
        "agent_id": doc.get("agent_id") or doc.get("id") or doc.get("name"),
        "service_name": doc.get("service_name"),
        "capabilities": _as_list(doc.get("capabilities")),
        "tags": _as_list(doc.get("tags")),
        "status": doc.get("status"),
        "metadata": doc.get("metadata") or {},
        "policy": policy or {},
    }


def _status_weight(status: Any) -> float:
    if isinstance(status, AgentStatus):
        status = status.value
    status = (status or "").lower()
    if status == AgentStatus.IDLE.value:
        return 1.0
    if status == AgentStatus.ONLINE.value:
        return 0.75
    if status == AgentStatus.BUSY.value:
        return 0.4
    if status in (AgentStatus.UNHEALTHY.value, AgentStatus.OFFLINE.value):
        return 0.0
    return 0.5


def _policy_factor(agent: Dict[str, Any]) -> float:
    policy = agent.get("policy")
    if not isinstance(policy, dict):
        policy = {}
    freshness = None
    decay = None
    if isinstance(policy.get("freshness"), dict):
        freshness = policy.get("freshness", {}).get("score")
    if isinstance(policy.get("decay"), dict):
        decay = policy.get("decay", {}).get("factor")

    factor = 1.0
    if isinstance(freshness, (int, float)):
        factor *= max(0.0, min(1.0, float(freshness)))
    if isinstance(decay, (int, float)):
        factor *= max(0.0, min(1.0, float(decay)))

    try:
        weight = float(os.getenv("AGENT_REGISTRY_POLICY_WEIGHT", "0.25"))
    except ValueError:
        weight = 0.25
    weight = max(0.0, min(1.0, weight))
    return (1.0 - weight) + (weight * factor)


def score_agent_for_task(agent: Dict[str, Any], spec: TaskSpec) -> float:
    raw_capabilities = set(_as_list(agent.get("capabilities")))
    capabilities = expand_capabilities(raw_capabilities)
    tags = set(_as_list(agent.get("tags")))
    required = set(spec.required_capabilities or [])
    required_ratio = (len(required & capabilities) / len(required)) if required else 0.5

    spec_tags = set(spec.tags or [])
    tag_ratio = (len(spec_tags & tags) / len(spec_tags)) if spec_tags else 0.0

    policy = agent.get("policy") if isinstance(agent.get("policy"), dict) else {}
    status_value = policy.get("effective_status") or agent.get("status")
    score = (0.6 * required_ratio) + (0.2 * tag_ratio) + (0.2 * _status_weight(status_value))

    preferred = set(_as_list(spec.metadata.get("preferred_agents")))
    if preferred and agent.get("agent_id") in preferred:
        score += 0.1

    score *= _policy_factor(agent)
    return max(0.0, min(1.0, score))


def rank_agents_for_task(
    agents: Iterable[Dict[str, Any]],
    spec: TaskSpec,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    scored = []
    for raw in agents:
        agent = normalize_agent(raw)
        if not agent.get("agent_id"):
            continue
        scored.append({"agent": agent, "score": score_agent_for_task(agent, spec)})
    scored.sort(key=lambda item: item["score"], reverse=True)
    if limit:
        scored = scored[:limit]
    return scored
