#src/agentcy/llm_utilities/llm_connector.py

import os
from ollama import chat
from aiohttp import ClientError
import logging
import asyncio
import json
from typing import Any, Dict, List, Optional
from agentcy.llm_utilities.async_context_manager import AsyncClientManager, AsyncOpenAIClientManager
from enum import Enum
import openai
from agentcy.llm_utilities.conversation_manager import openai_prompt
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _openai_api_key() -> Optional[str]:
    """Accept either legacy or modern OpenAI key env names."""
    for env_name in ("OPEN_AI_KEY", "OPENAI_API_KEY"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return None

class Provider(Enum):
    OPENAI = "openai"
    LLAMA = "llama"

#TODO Eventually take this out as an environment variable. Perform load testing and adjust accordingly. 
CONCURRENCY_LIMIT = 10
semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

class LLM_Connector:
    
    def __init__(self, provider: Provider):
        
        self.provider = provider
        self.mock_mode = os.getenv("LLM_MOCK_MODE", "").strip().lower() in ("1", "true", "yes", "on")
        openai_api_key = _openai_api_key()
        if provider == Provider.OPENAI:
            if not openai_api_key and not self.mock_mode:
                raise ValueError("Must provide an OpenAI API key when provider=OPENAI.")
            if self.mock_mode:
                self.openai_client = None
            else:
                self.openai_client = AsyncOpenAIClientManager(api_key=openai_api_key, concurrency_limit=CONCURRENCY_LIMIT)
            self.ollama_client = None
            self.gpt_model = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
            
        
        elif provider == Provider.LLAMA:
            if self.mock_mode:
                self.ollama_client = None
            else:
                self.ollama_client = AsyncClientManager()
            self.openai_client = None
            self.llama_model = os.getenv("LLAMA_MODEL", "llama3.2:latest")
        self._started = False

    async def start(self):
        """
        Open only the relevant client once (if necessary).
        """
        if not self._started:
            if self.mock_mode:
                self._started = True
                return
            if self.provider == Provider.LLAMA and self.ollama_client:
                await self.ollama_client.__aenter__()
            elif self.provider == Provider.OPENAI and self.openai_client:
                await self.openai_client.__aenter__()
            self._started = True

    async def stop(self):
        """
        Close only the relevant manager once.
        """
        if self._started:
            if self.mock_mode:
                self._started = False
                return
            if self.provider == Provider.LLAMA and self.ollama_client:
                await self.ollama_client.__aexit__(None, None, None)
            elif self.provider == Provider.OPENAI and self.openai_client:
                await self.openai_client.__aexit__(None, None, None)
            self._started = False

    # -------------- OpenAI Method --------------

    async def chat_with_tracking_openai(self, pipeline_run_id, messages, timeout: float = 30.0):
        if not self.openai_client:
            raise RuntimeError("OpenAI client not initialized. Use provider=OPENAI and call start().")
        try:
            async with semaphore:
                response = await self.openai_client.chat(
                    model=self.gpt_model,
                    messages=messages,
                    timeout=timeout
                )

                if response is None:
                    logger.error("OpenAI client returned None for run %s", pipeline_run_id)
                    return pipeline_run_id, None

                return pipeline_run_id, response.choices[0].message.content

        except openai.APIConnectionError as e:
            logger.error(f'Server cant be reached {e}')
            return pipeline_run_id, None
        except openai.RateLimitError as e:
            logger.error(f'RateLimitError: {e}. Retrying after n seconds."')
            return pipeline_run_id, None
        except openai.OpenAIError as e:
            logger.error(
                f"OpenAIError: {e}. The request failed and will not be retried."
            )
            return pipeline_run_id, None
            
        except Exception as e:
            logger.critical(f"An unexpected error occurred: {e}.", exc_info=True)
            return pipeline_run_id, None
            
    # ---------- Chat with Ollama (native async) ------------

    async def chat_with_tracking_llama(self, pipeline_run_id: str, messages: list, timeout: float = 30.0):
        if not self.ollama_client:
            raise RuntimeError("Ollama client not initialized. Use provider=LLAMA and call start().")

        try:
            async with semaphore:
                response = await asyncio.wait_for(
                    self.ollama_client.chat(model=self.llama_model, messages=messages),
                    timeout=timeout
                )
                return pipeline_run_id, response['message']['content']
        except asyncio.TimeoutError:
            logger.error(f"Timeout for pipeline_run_id {pipeline_run_id}")
        except ClientError as e:
            logger.error(f"HTTP client error for pipeline_run_id {pipeline_run_id}: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error for pipeline_run_id {pipeline_run_id}: {e}")

        return pipeline_run_id, None

    



     # ---------- Dispatcher ---------------

    async def handle_incoming_requests(self, requests):
        """
        Handle multiple incoming chat requests concurrently, returning
        { pipeline_run_id -> response_content or "Error" }.
        """
        if self.mock_mode:
            final_results: Dict[str, str] = {}
            for pipeline_run_id, messages in requests:
                final_results[pipeline_run_id] = self._mock_response(messages)
            return final_results
        tasks = []
        
        if self.provider == Provider.OPENAI:
            for pipeline_run_id, messages in requests:
                task = asyncio.create_task(
                    self.chat_with_tracking_openai(
                        pipeline_run_id=pipeline_run_id,
                        messages=messages
                    )
                )
                tasks.append(task)

        elif self.provider == Provider.LLAMA:
            for pipeline_run_id, messages in requests:
                task = asyncio.create_task(
                    self.chat_with_tracking_llama(
                        pipeline_run_id=pipeline_run_id,
                        messages=messages
                    )
                )
                tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=False)
        
        final_results = {}
        for pipeline_run_id, response in results:
            final_results[pipeline_run_id] = response if response is not None else "Error"
        return final_results

    def _mock_response(self, messages: List[Dict[str, Any]]) -> str:
        system = ""
        user = ""
        if messages:
            system = str(messages[0].get("content", "")).lower()
            if len(messages) > 1:
                user = str(messages[1].get("content", ""))

        context = self._extract_context(user)
        if "task intake agent" in system:
            payload = (context or {}).get("payload", {})
            return json.dumps(self._mock_task_specs(payload), separators=(",", ":"))
        if "plan validation assistant" in system:
            return json.dumps(self._mock_plan_validation(context), separators=(",", ":"))
        if "ethics reviewer" in system:
            return json.dumps(self._mock_ethics_review(context), separators=(",", ":"))
        if "planning strategist" in system:
            return json.dumps(self._mock_strategy(context), separators=(",", ":"))
        if "plan delta strategist" in system:
            return json.dumps(self._mock_plan_delta(context), separators=(",", ":"))
        return "{}"

    @staticmethod
    def _extract_context(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        marker = "Context:"
        idx = text.find(marker)
        if idx == -1:
            return None
        raw = text[idx + len(marker):].strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _mock_task_specs(payload: Dict[str, Any]) -> Dict[str, Any]:
        candidates = payload.get("task_specs") or payload.get("tasks") or payload.get("task_spec")
        specs: List[Dict[str, Any]] = []
        def _coerce_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def _coerce_float(value: Any, default: float) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

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

        if isinstance(candidates, dict):
            candidates = [candidates]
        if isinstance(candidates, list):
            for idx, item in enumerate(candidates, start=1):
                if not isinstance(item, dict):
                    continue
                spec = dict(item)
                spec.setdefault("task_id", f"mock-task-{idx}")
                spec.setdefault(
                    "description",
                    item.get("description") or item.get("task_description") or "mock task",
                )
                if not spec.get("required_capabilities"):
                    caps = item.get("capabilities")
                    if isinstance(caps, list) and caps:
                        spec["required_capabilities"] = caps
                    else:
                        spec["required_capabilities"] = ["plan"]
                tags = spec.get("tags")
                if not isinstance(tags, list):
                    tags = []
                spec["tags"] = tags
                risk = str(spec.get("risk_level") or payload.get("risk_level") or "medium").lower()
                if risk not in ("low", "medium", "high"):
                    risk = "medium"
                spec["risk_level"] = risk
                requires_human = spec.get("requires_human_approval")
                if not isinstance(requires_human, bool):
                    requires_human = risk == "high"
                spec["requires_human_approval"] = requires_human

                metadata = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
                task_type = spec.get("task_type") or metadata.get("task_type")
                if not task_type:
                    req_caps = spec.get("required_capabilities") or []
                    task_type = req_caps[0] if req_caps else "general"
                spec["task_type"] = task_type

                priority = _coerce_int(spec.get("priority") or metadata.get("priority"), _default_priority(risk))
                priority = max(1, min(5, priority))
                spec["priority"] = priority

                stimulus = _coerce_float(spec.get("stimulus") or metadata.get("stimulus"), _default_stimulus(priority))
                stimulus = max(0.0, min(1.0, stimulus))
                spec["stimulus"] = stimulus

                reward = _coerce_float(spec.get("reward") or metadata.get("reward"), _default_reward(priority))
                reward = max(0.1, min(6.0, reward))
                spec["reward"] = reward
                specs.append(spec)
        if not specs:
            desc = payload.get("task_description") or payload.get("objective") or payload.get("description") or "mock task"
            priority = _default_priority("medium")
            specs = [
                {
                    "task_id": "mock-task-1",
                    "description": desc,
                    "required_capabilities": ["plan"],
                    "tags": [],
                    "risk_level": "medium",
                    "requires_human_approval": False,
                    "task_type": "plan",
                    "priority": priority,
                    "stimulus": _default_stimulus(priority),
                    "reward": _default_reward(priority),
                }
            ]
        return {"task_specs": specs}

    @staticmethod
    def _mock_plan_validation(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        violations = []
        if context and isinstance(context.get("violations"), list):
            violations = context.get("violations", [])
        approved = not violations
        return {
            "approved": approved,
            "assessment": "mock validation",
            "risks": [v.get("code", "violation") for v in violations if isinstance(v, dict)] if violations else [],
            "suggested_fixes": ["address validation issues"] if violations else [],
            "confidence": 0.5,
        }

    @staticmethod
    def _mock_ethics_review(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {"approved": True, "issues": [], "notes": "mock approval"}

    @staticmethod
    def _mock_strategy(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        tasks = []
        edges = []
        if context:
            tasks = list(context.get("tasks") or [])
            edges = list(context.get("edges") or [])
        task_ids = [task.get("task_id") for task in tasks if isinstance(task, dict) and task.get("task_id")]
        order = LLM_Connector._topological_order(task_ids, edges)
        phases = [{"phase": idx + 1, "tasks": [task_id]} for idx, task_id in enumerate(order)]
        return {
            "summary": f"mock strategy with {len(order)} tasks",
            "phases": phases,
            "critical_path": order,
        }

    @staticmethod
    def _mock_plan_delta(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        context = context or {}
        graph = context.get("graph_spec") or {}
        tasks = list(graph.get("tasks") or [])
        run_state = context.get("run_state") or {}
        task_statuses = run_state.get("tasks") or {}

        pending_task_id = None
        for task in tasks:
            task_id = task.get("task_id")
            if not task_id:
                continue
            status = task_statuses.get(task_id, {}).get("status")
            if str(status).upper() == "PENDING":
                pending_task_id = task_id
                break
        if pending_task_id is None and tasks:
            pending_task_id = tasks[0].get("task_id")

        delta = {
            "rationale": "mock delta suggestion",
            "task_overrides": {},
            "add_tasks": [],
            "remove_tasks": [],
            "add_edges": [],
            "remove_edges": [],
        }
        if pending_task_id:
            delta["task_overrides"][pending_task_id] = {"tags": ["llm_suggested"]}
        return delta

    @staticmethod
    def _topological_order(task_ids: List[str], edges: List[Dict[str, Any]]) -> List[str]:
        adjacency: Dict[str, List[str]] = {task_id: [] for task_id in task_ids}
        indegree: Dict[str, int] = {task_id: 0 for task_id in task_ids}
        for edge in edges:
            src = edge.get("from")
            dst = edge.get("to")
            if src in adjacency and dst in adjacency:
                adjacency[src].append(dst)
                indegree[dst] += 1
        queue = [task_id for task_id, deg in indegree.items() if deg == 0]
        order: List[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor in adjacency.get(node, []):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)
        if len(order) != len(task_ids):
            return list(task_ids)
        return order




#TODO Replace this with pytest tests
async def main():
    # 1) Ollama example
    llama_connector = LLM_Connector(provider=Provider.LLAMA)
    await llama_connector.start()

    llama_requests = [
        ("llama_req_1", [{"role": "user", "content": "Hello Llama, who are you?"}]),
        ("llama_req_2", [{"role": "user", "content": "Tell me a joke."}]),
    ]
    llama_results = await llama_connector.handle_incoming_requests(llama_requests)
    print("LLAMA Results:", llama_results)

    await llama_connector.stop()

    # 2) OpenAI example (wrapped in context manager)
    openai_connector = LLM_Connector(
        provider=Provider.OPENAI
    )
    await openai_connector.start()

    openai_requests = [("id1",openai_prompt("You're  a cool agent", "Say hi in a cool way"))]
    print(type(openai_requests))
    openai_results = await openai_connector.handle_incoming_requests(openai_requests)
    print("OpenAI Results:", openai_results)

    await openai_connector.stop()

if __name__ == "__main__":
    asyncio.run(main())
