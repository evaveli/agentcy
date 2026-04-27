from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from agentcy.llm_utilities.llm_connector import LLM_Connector, Provider
from agentcy.pipeline_orchestrator.resource_manager import ResourceManager
from agentcy.pydantic_models.multi_agent_pipeline import RiskLevel, TaskSpec

logger = logging.getLogger(__name__)

MAX_RETRIES = 5


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _coerce_risk_level(value: Any) -> Optional[RiskLevel]:
    if isinstance(value, RiskLevel):
        return value
    if value is None:
        return None
    lowered = str(value).lower().strip()
    if lowered == RiskLevel.LOW.value:
        return RiskLevel.LOW
    if lowered == RiskLevel.MEDIUM.value:
        return RiskLevel.MEDIUM
    if lowered == RiskLevel.HIGH.value:
        return RiskLevel.HIGH
    return None


def _provider_from_env() -> Optional[Provider]:
    raw = os.getenv("LLM_SUPERVISOR_PROVIDER", "").strip().lower()
    if raw in ("openai", "gpt"):
        return Provider.OPENAI
    if raw in ("llama", "ollama"):
        return Provider.LLAMA
    return None


def _is_stub_mode() -> bool:
    """Check if stub mode is enabled (allows operation without LLM)."""
    return os.getenv("LLM_STUB_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def _stub_generate_specs(
    payload: Dict[str, Any],
    *,
    username: str,
    pipeline_id: str,
    run_id: str,
) -> List[TaskSpec]:
    """
    Generate task specs from payload without LLM.

    Uses rule-based extraction of task information from the payload.
    Enriches with sensible defaults where information is missing.
    """
    specs: List[TaskSpec] = []
    candidates = payload.get("task_specs") or payload.get("tasks") or payload.get("task_spec")

    if isinstance(candidates, dict):
        candidates = [candidates]
    if not isinstance(candidates, list):
        candidates = []

    def _default_priority(risk: str) -> int:
        if risk == "high":
            return 5
        if risk == "low":
            return 1
        return 3

    def _default_stimulus(priority: int) -> float:
        return 0.2 + ((priority - 1) / 4.0) * 0.6

    def _default_reward(priority: int) -> float:
        return 0.1 + float(priority)

    seen_ids: set[str] = set()
    for idx, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            continue

        # Extract or generate task_id
        task_id = item.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            task_id = f"stub-task-{idx}"
        task_id = task_id.strip()
        if task_id in seen_ids:
            task_id = f"{task_id}-{idx}"
        seen_ids.add(task_id)

        # Extract description
        description = item.get("description") or item.get("task_description") or f"Task {idx}"
        if not isinstance(description, str):
            description = str(description)

        # Extract capabilities
        caps = item.get("required_capabilities") or item.get("capabilities")
        if isinstance(caps, list) and caps:
            required_capabilities = [str(c).strip() for c in caps if str(c).strip()]
        else:
            required_capabilities = ["general"]

        # Extract tags
        tags = item.get("tags")
        if isinstance(tags, list):
            tags = [str(t).strip() for t in tags if str(t).strip()]
        else:
            tags = []

        # Extract risk level
        risk_raw = str(item.get("risk_level") or payload.get("risk_level") or "medium").lower()
        if risk_raw not in ("low", "medium", "high"):
            risk_raw = "medium"
        risk_level = _coerce_risk_level(risk_raw) or RiskLevel.MEDIUM

        # Extract requires_human_approval
        requires_human = item.get("requires_human_approval")
        if not isinstance(requires_human, bool):
            requires_human = risk_raw == "high"

        # Extract or infer task_type
        task_type = item.get("task_type")
        if not isinstance(task_type, str) or not task_type.strip():
            task_type = required_capabilities[0] if required_capabilities else "general"

        # Extract or calculate priority, stimulus, reward
        priority = _coerce_int(item.get("priority"))
        if priority is None:
            priority = _default_priority(risk_raw)
        priority = int(_clamp(float(priority), 1.0, 5.0))

        stimulus = _coerce_float(item.get("stimulus"))
        if stimulus is None:
            stimulus = _default_stimulus(priority)
        stimulus = _clamp(stimulus, 0.0, 1.0)

        reward = _coerce_float(item.get("reward"))
        if reward is None:
            reward = _default_reward(priority)
        reward = _clamp(reward, 0.1, 6.0)

        # Build metadata
        metadata = dict(item.get("metadata") or {})
        metadata.setdefault("pipeline_id", pipeline_id)
        metadata.setdefault("pipeline_run_id", run_id)
        metadata["source"] = "supervisor_agent_stub"
        metadata["task_type"] = task_type
        metadata["priority"] = priority
        metadata["stimulus"] = stimulus
        metadata["reward"] = reward
        metadata["llm_used"] = False

        spec = TaskSpec(
            task_id=task_id,
            username=username,
            description=description.strip(),
            required_capabilities=required_capabilities,
            tags=tags,
            risk_level=risk_level,
            requires_human_approval=requires_human,
            metadata=metadata,
        )
        specs.append(spec)

    # If no specs extracted, create a default one
    if not specs:
        desc = payload.get("task_description") or payload.get("objective") or payload.get("description") or "Default task"
        priority = _default_priority("medium")
        specs = [
            TaskSpec(
                task_id="stub-task-1",
                username=username,
                description=str(desc),
                required_capabilities=["general"],
                tags=[],
                risk_level=RiskLevel.MEDIUM,
                requires_human_approval=False,
                metadata={
                    "pipeline_id": pipeline_id,
                    "pipeline_run_id": run_id,
                    "source": "supervisor_agent_stub",
                    "task_type": "general",
                    "priority": priority,
                    "stimulus": _default_stimulus(priority),
                    "reward": _default_reward(priority),
                    "llm_used": False,
                },
            )
        ]

    return specs


def _extract_payload(message: Any) -> Dict[str, Any]:
    if isinstance(message, dict):
        return dict(message.get("data") or message)
    return dict(getattr(message, "data", {}) or {})


def _extract_task_payloads(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = payload.get("task_specs") or payload.get("tasks") or payload.get("task_spec")
    if not candidates:
        return []
    if isinstance(candidates, dict):
        return [candidates]
    if isinstance(candidates, list):
        return [item for item in candidates if isinstance(item, dict)]
    return []


def _extract_json(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return stripped[start : end + 1]


def _parse_task_specs(text: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not text or text == "Error":
        return None
    payload = _extract_json(text)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("task_specs") or data.get("tasks") or data.get("task_spec")
        if isinstance(items, dict):
            return [items]
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return None


def _infer_capabilities_from_payload(payload: Dict[str, Any]) -> List[str]:
    """Extract capability hints from the raw payload."""
    caps: set = set()
    for spec in payload.get("task_specs") or payload.get("tasks") or []:
        if isinstance(spec, dict):
            for c in spec.get("required_capabilities") or []:
                if isinstance(c, str) and c.strip():
                    caps.add(c.strip().lower())
    for c in payload.get("capabilities") or []:
        if isinstance(c, str) and c.strip():
            caps.add(c.strip().lower())
    return list(caps)


def _infer_tags_from_payload(payload: Dict[str, Any]) -> List[str]:
    """Extract tag hints from the raw payload."""
    tags: set = set()
    for spec in payload.get("task_specs") or payload.get("tasks") or []:
        if isinstance(spec, dict):
            for t in spec.get("tags") or []:
                if isinstance(t, str) and t.strip():
                    tags.add(t.strip().lower())
    for t in payload.get("tags") or []:
        if isinstance(t, str) and t.strip():
            tags.add(t.strip().lower())
    return list(tags)


def _build_prompt(
    payload: Dict[str, Any],
    *,
    pipeline_id: str,
    run_id: str,
    previous_error: Optional[str] = None,
    previous_response: Optional[str] = None,
    kg_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    system = "You are a task intake agent. Return ONLY valid JSON. No markdown."
    schema = {
        "task_specs": [
            {
                "task_id": "string",
                "description": "string",
                "required_capabilities": ["string"],
                "tags": ["string"],
                "risk_level": "low|medium|high",
                "requires_human_approval": False,
                "task_type": "string",
                "priority": 1,
                "stimulus": 0.5,
                "reward": 1.0,
                "metadata": {"key": "value"},
            }
        ]
    }
    context: Dict[str, Any] = {
        "pipeline_id": pipeline_id,
        "pipeline_run_id": run_id,
        "payload": payload,
    }
    if previous_error:
        context["previous_error"] = previous_error
    if previous_response:
        context["previous_response"] = previous_response
    if kg_context:
        context["similar_plans"] = kg_context.get("similar_plans", [])
        context["capability_stats"] = kg_context.get("capability_stats", {})
        context["recommended_templates"] = kg_context.get("recommended_templates", [])

    user = (
        "Translate the payload into task_specs JSON."
        " Return ONLY JSON with key task_specs."
        " Each task_spec MUST include task_id, description, required_capabilities, tags,"
        " risk_level, requires_human_approval, task_type, priority (1-5),"
        " stimulus (0-1), reward (0.1-6), and metadata."
        " If task specs are provided in the payload, reuse their task_id values and"
        " enrich missing fields."
        f" Schema example: {json.dumps(schema, separators=(',', ':'))}\n"
        f"Context: {json.dumps(context, separators=(',', ':'))}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _normalize_specs(
    items: List[Dict[str, Any]],
    *,
    username: str,
    pipeline_id: str,
    run_id: str,
    provider: Provider,
    attempts: int,
) -> Tuple[Optional[List[TaskSpec]], List[str]]:
    errors: List[str] = []
    specs: List[TaskSpec] = []
    seen_ids: set[str] = set()

    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"task_specs[{idx}] is not an object")
            continue

        task_id = item.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            errors.append(f"task_specs[{idx}].task_id is required")
            continue
        task_id = task_id.strip()
        if task_id in seen_ids:
            errors.append(f"duplicate task_id '{task_id}'")
            continue
        seen_ids.add(task_id)

        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"task_specs[{idx}].description is required")
            continue

        required_capabilities = item.get("required_capabilities")
        if not isinstance(required_capabilities, list):
            errors.append(f"task_specs[{idx}].required_capabilities must be a list")
            continue
        required_capabilities = _as_list(required_capabilities)
        if not required_capabilities:
            errors.append(f"task_specs[{idx}].required_capabilities cannot be empty")
            continue

        tags = item.get("tags")
        if not isinstance(tags, list):
            errors.append(f"task_specs[{idx}].tags must be a list")
            continue
        tags = _as_list(tags)

        risk_level = _coerce_risk_level(item.get("risk_level"))
        if risk_level is None:
            errors.append(f"task_specs[{idx}].risk_level must be low|medium|high")
            continue

        requires_human = item.get("requires_human_approval")
        if not isinstance(requires_human, bool):
            errors.append(f"task_specs[{idx}].requires_human_approval must be boolean")
            continue

        task_type = item.get("task_type")
        if not isinstance(task_type, str) or not task_type.strip():
            errors.append(f"task_specs[{idx}].task_type is required")
            continue
        task_type = task_type.strip()

        priority = _coerce_int(item.get("priority"))
        if priority is None:
            errors.append(f"task_specs[{idx}].priority must be an integer")
            continue
        priority = int(_clamp(float(priority), 1.0, 5.0))

        stimulus = _coerce_float(item.get("stimulus"))
        if stimulus is None:
            errors.append(f"task_specs[{idx}].stimulus must be a number")
            continue
        stimulus = _clamp(stimulus, 0.0, 1.0)

        reward = _coerce_float(item.get("reward"))
        if reward is None:
            errors.append(f"task_specs[{idx}].reward must be a number")
            continue
        reward = _clamp(reward, 0.1, 6.0)

        metadata = dict(item.get("metadata") or {})
        metadata.setdefault("pipeline_id", pipeline_id)
        metadata.setdefault("pipeline_run_id", run_id)
        metadata.setdefault("source", "supervisor_agent")
        metadata["task_type"] = task_type
        metadata["priority"] = priority
        metadata["stimulus"] = stimulus
        metadata["reward"] = reward
        metadata["llm_used"] = True
        metadata["llm_provider"] = provider.value
        metadata["llm_attempts"] = attempts

        spec = TaskSpec(
            task_id=task_id,
            username=username,
            description=description.strip(),
            required_capabilities=required_capabilities,
            tags=tags,
            risk_level=risk_level,
            requires_human_approval=requires_human,
            metadata=metadata,
        )
        specs.append(spec)

    if errors:
        return None, errors
    if not specs:
        return None, ["task_specs list is empty"]
    return specs, []


async def _collect_hints(
    store: Any,
    *,
    username: str,
    pipeline_id: Optional[str],
) -> List[Dict[str, Any]]:
    if store is None:
        return []
    try:
        specs, _ = store.list_task_specs(username=username)
    except Exception:
        logger.debug("Supervisor failed listing task specs", exc_info=True)
        return []
    if not pipeline_id:
        return specs
    filtered = [
        spec
        for spec in specs
        if isinstance(spec.get("metadata"), dict)
        and spec.get("metadata", {}).get("pipeline_id") == pipeline_id
    ]
    return filtered or specs


async def run(
    rm: ResourceManager,
    _run_id: str,
    _to_task: str,
    _triggered_by: Any,
    message: Any,
) -> Dict[str, Any]:
    store = rm.graph_marker_store
    if store is None:
        raise RuntimeError("graph_marker_store is not configured")

    username = getattr(message, "username", None) or (message.get("username") if isinstance(message, dict) else None)
    pipeline_id = getattr(message, "pipeline_id", None) or (message.get("pipeline_id") if isinstance(message, dict) else None)
    run_id = getattr(message, "pipeline_run_id", None) or (message.get("pipeline_run_id") if isinstance(message, dict) else None)
    if not username or not pipeline_id or not run_id:
        raise ValueError("supervisor_agent requires username, pipeline_id, pipeline_run_id")

    payload = _extract_payload(message)
    if payload.get("content_filter_passed") is False or payload.get("blocked") is True:
        logger.warning("Supervisor blocked content for %s/%s", username, pipeline_id)
        return {"created": 0, "task_ids": [], "blocked": True}

    hints = _extract_task_payloads(payload)
    if not hints:
        hints = await _collect_hints(store, username=username, pipeline_id=pipeline_id)

    llm_payload = dict(payload)
    if hints and not _extract_task_payloads(llm_payload):
        llm_payload["task_specs"] = hints

    # Query KG for cross-plan context (non-blocking, graceful fallback)
    kg_context: Optional[Dict[str, Any]] = None
    try:
        from agentcy.semantic.plan_recommender import get_plan_context

        inferred_caps = _infer_capabilities_from_payload(llm_payload)
        inferred_tags = _infer_tags_from_payload(llm_payload)
        if inferred_caps:
            kg_context = await get_plan_context(
                capabilities=inferred_caps,
                tags=inferred_tags,
            )
    except Exception:
        logger.debug("Failed to fetch KG context for supervisor", exc_info=True)

    provider = _provider_from_env()
    specs: Optional[List[TaskSpec]] = None
    attempts = 0

    # Use stub mode if no provider configured and LLM_STUB_MODE is enabled
    if not provider:
        if _is_stub_mode():
            logger.info("Supervisor agent using stub mode (LLM_STUB_MODE=1)")
            specs = _stub_generate_specs(
                llm_payload,
                username=username,
                pipeline_id=pipeline_id,
                run_id=run_id,
            )
            attempts = 0
        else:
            raise RuntimeError(
                "Supervisor agent requires LLM_SUPERVISOR_PROVIDER to be set. "
                "Set LLM_STUB_MODE=1 to use rule-based task generation instead."
            )
    else:
        try:
            connector = LLM_Connector(provider=provider)
        except Exception as exc:
            if _is_stub_mode():
                logger.warning("LLM init failed, falling back to stub mode: %s", exc)
                specs = _stub_generate_specs(
                    llm_payload,
                    username=username,
                    pipeline_id=pipeline_id,
                    run_id=run_id,
                )
            else:
                raise RuntimeError(f"Supervisor agent LLM init failed: {exc}") from exc

        if specs is None:
            last_error: Optional[str] = None
            last_response: Optional[str] = None
            await connector.start()
            try:
                for attempt in range(1, MAX_RETRIES + 1):
                    attempts = attempt
                    prompt = _build_prompt(
                        llm_payload,
                        pipeline_id=pipeline_id,
                        run_id=run_id,
                        previous_error=last_error,
                        previous_response=last_response,
                        kg_context=kg_context,
                    )
                    request_id = f"{pipeline_id}:{run_id}:supervisor:{attempt}"
                    responses = await connector.handle_incoming_requests([(request_id, prompt)])
                    raw = responses.get(request_id)
                    last_response = raw
                    items = _parse_task_specs(raw)
                    if not items:
                        last_error = "invalid_json_response"
                        continue
                    specs, errors = _normalize_specs(
                        items,
                        username=username,
                        pipeline_id=pipeline_id,
                        run_id=run_id,
                        provider=provider,
                        attempts=attempt,
                    )
                    if errors:
                        last_error = "; ".join(errors[:5])
                        specs = None
                        continue
                    break
            finally:
                await connector.stop()

            if not specs:
                if _is_stub_mode():
                    logger.warning("LLM failed after %d attempts, falling back to stub mode", MAX_RETRIES)
                    specs = _stub_generate_specs(
                        llm_payload,
                        username=username,
                        pipeline_id=pipeline_id,
                        run_id=run_id,
                    )
                else:
                    raise RuntimeError(
                        f"Supervisor agent failed after {MAX_RETRIES} attempts: {last_error}"
                    )

    created: List[str] = []
    for spec in specs:
        store.upsert_task_spec(username=username, spec=spec)
        created.append(spec.task_id)

    logger.info("Supervisor agent created %d task specs for %s", len(created), username)
    return {"created": len(created), "task_ids": created, "blocked": False, "llm_attempts": attempts}
