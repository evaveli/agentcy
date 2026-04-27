from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

import httpx

from agentcy.pydantic_models.agent_registry_model import AgentRegistryEntry, AgentStatus

logger = logging.getLogger(__name__)


def _split_csv(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_agent_id(service_name: str) -> str:
    host = socket.gethostname()
    pid = os.getpid()
    return f"{service_name}-{host}-{pid}"


@dataclass(frozen=True)
class AgentRegistryConfig:
    base_url: str
    username: str
    agent_id: str
    service_name: str
    description: Optional[str]
    capabilities: list[str]
    tags: list[str]
    heartbeat_interval: float
    ttl_seconds: int
    timeout_seconds: float
    failure_threshold: int


class AgentRegistryClient:
    def __init__(
        self,
        config: AgentRegistryConfig,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            follow_redirects=True,
        )
        self._owns_client = client is None
        self._status = AgentStatus.IDLE
        self._busy_count = 0
        self._busy_lock = asyncio.Lock()
        self._failure_count = 0
        self._failure_threshold = max(1, config.failure_threshold)
        self._registered = False

    @property
    def agent_id(self) -> str:
        return self._config.agent_id

    @property
    def username(self) -> str:
        return self._config.username

    @property
    def status(self) -> AgentStatus:
        return self._status

    def _entry(self) -> AgentRegistryEntry:
        return AgentRegistryEntry(
            agent_id=self._config.agent_id,
            service_name=self._config.service_name,
            owner=self._config.username,
            description=self._config.description,
            capabilities=self._config.capabilities,
            tags=self._config.tags,
            status=self._status,
            metadata={
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
            },
        )

    async def register(self) -> bool:
        payload = self._entry().model_dump(mode="json")
        params = {"ttl_seconds": self._config.ttl_seconds}
        try:
            resp = await self._client.post(
                f"/agent-registry/{self._config.username}",
                json=payload,
                params=params,
            )
            if resp.is_error:
                logger.warning(
                    "Agent registry registration failed: %s %s",
                    resp.status_code,
                    resp.text,
                )
                self._registered = False
                return False
            self._registered = True
            return True
        except Exception as exc:
            logger.warning("Agent registry registration error: %s", exc)
            self._registered = False
            return False

    async def heartbeat(
        self,
        *,
        status: Optional[AgentStatus] = None,
        metadata: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        if status is not None:
            self._status = status
        payload: Dict[str, Any] = {
            "status": self._status.value,
            "ttl_seconds": ttl_seconds if ttl_seconds is not None else self._config.ttl_seconds,
            "metadata": metadata
            or {"busy_count": self._busy_count, "failure_count": self._failure_count},
        }
        try:
            resp = await self._client.post(
                f"/agent-registry/{self._config.username}/{self._config.agent_id}/heartbeat",
                json=payload,
            )
            if resp.status_code == 404:
                self._registered = False
                if await self.register():
                    resp = await self._client.post(
                        f"/agent-registry/{self._config.username}/{self._config.agent_id}/heartbeat",
                        json=payload,
                    )
            if resp.is_error:
                logger.debug(
                    "Agent registry heartbeat failed: %s %s",
                    resp.status_code,
                    resp.text,
                )
                self._registered = False
                return False
            self._registered = True
            return True
        except Exception as exc:
            logger.debug("Agent registry heartbeat error: %s", exc)
            return False

    async def mark_task_started(
        self,
        *,
        task_id: str,
        pipeline_run_id: Optional[str] = None,
        service_name: Optional[str] = None,
    ) -> None:
        async with self._busy_lock:
            self._busy_count += 1
            count = self._busy_count
        status = (
            AgentStatus.UNHEALTHY
            if self._failure_count >= self._failure_threshold
            else AgentStatus.BUSY
        )
        await self.heartbeat(
            status=status,
            metadata={
                "busy_count": count,
                "failure_count": self._failure_count,
                "task_id": task_id,
                "pipeline_run_id": pipeline_run_id,
                "service_name": service_name,
            },
        )

    async def mark_task_finished(
        self,
        *,
        task_id: str,
        pipeline_run_id: Optional[str] = None,
        service_name: Optional[str] = None,
        success: Optional[bool] = None,
    ) -> None:
        async with self._busy_lock:
            self._busy_count = max(0, self._busy_count - 1)
            count = self._busy_count

        if success is True:
            self._failure_count = 0
        elif success is False:
            self._failure_count += 1

        if self._failure_count >= self._failure_threshold:
            status = AgentStatus.UNHEALTHY
        else:
            status = AgentStatus.IDLE if count == 0 else AgentStatus.BUSY
        await self.heartbeat(
            status=status,
            metadata={
                "busy_count": count,
                "failure_count": self._failure_count,
                "task_id": task_id,
                "pipeline_run_id": pipeline_run_id,
                "service_name": service_name,
                "last_task_success": success,
            },
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def deregister(self) -> bool:
        try:
            resp = await self._client.delete(
                f"/agent-registry/{self._config.username}/{self._config.agent_id}"
            )
            if resp.status_code in (200, 204, 404):
                return True
            logger.warning(
                "Agent registry deregister failed: %s %s",
                resp.status_code,
                resp.text,
            )
            return False
        except Exception as exc:
            logger.warning("Agent registry deregister error: %s", exc)
            return False


async def heartbeat_loop(
    client: AgentRegistryClient,
    *,
    interval_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    try:
        while not stop_event.is_set():
            await client.heartbeat()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        logger.info("Agent registry heartbeat loop cancelled.")
        raise


def load_registry_config(service_name: str) -> Optional[AgentRegistryConfig]:
    base_url = os.getenv("AGENT_REGISTRY_URL")
    if not base_url:
        return None

    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _env_float(name: str, default: float) -> float:
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    username = os.getenv("AGENT_REGISTRY_USERNAME", "system")
    agent_id = os.getenv("AGENT_ID") or _default_agent_id(service_name)
    description = os.getenv("AGENT_DESCRIPTION")
    capabilities = _split_csv(os.getenv("AGENT_CAPABILITIES"))
    tags = _split_csv(os.getenv("AGENT_TAGS"))
    heartbeat_interval = _env_float("AGENT_REGISTRY_HEARTBEAT_SECONDS", 15.0)
    ttl_default = max(int(heartbeat_interval * 3), 30)
    ttl_seconds = _env_int("AGENT_REGISTRY_TTL_SECONDS", ttl_default)
    timeout_seconds = _env_float("AGENT_REGISTRY_TIMEOUT_SECONDS", 5.0)
    failure_threshold = _env_int("AGENT_REGISTRY_FAILURE_THRESHOLD", 3)

    return AgentRegistryConfig(
        base_url=base_url.rstrip("/"),
        username=username,
        agent_id=agent_id,
        service_name=service_name,
        description=description,
        capabilities=capabilities,
        tags=tags,
        heartbeat_interval=heartbeat_interval,
        ttl_seconds=ttl_seconds,
        timeout_seconds=timeout_seconds,
        failure_threshold=failure_threshold,
    )


_client_ref: Optional[AgentRegistryClient] = None


def configure_registry_client(client: Optional[AgentRegistryClient]) -> None:
    global _client_ref
    _client_ref = client


def get_registry_client() -> Optional[AgentRegistryClient]:
    return _client_ref
