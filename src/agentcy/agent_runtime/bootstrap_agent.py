#src/agentcy/agent_runtime/bootstrap_agent.py

from __future__ import annotations

import asyncio, logging, os, signal
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from aiohttp import web
from agentcy.pipeline_orchestrator.resource_manager import resource_manager_context, ResourceManager
from agentcy.observability.bootstrap import start_observability
from agentcy.agent_runtime.consumers import run_consumers
from agentcy.pydantic_models.config import AgentSettings
from agentcy.agent_runtime.forwarder import MicroserviceLogicFunc
from agentcy.agent_runtime.registry_client import (
    AgentRegistryClient,
    AgentStatus,
    configure_registry_client,
    heartbeat_loop,
    load_registry_config,
)
logger = logging.getLogger(__name__)

# Type alias for service business‑logic
LogicFn = MicroserviceLogicFunc


# ──────────────────────────────────────────────────────────────────────────────
#  1) tiny /health endpoint (shares the same ResourceManager)
# ──────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def _http_server(rm: ResourceManager, service_name: str, host: str = "0.0.0.0", port: int = 0):
    async def _health(_req):
        return web.json_response({
            "status":  "ok",
            "service": service_name,
        })

    app = web.Application()
    app.add_routes([web.get("/health", _health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    bound_host, bound_port = runner.addresses[0]
    logger.info("HTTP server started for %s – GET http://%s:%s/health", service_name, bound_host, bound_port)
    try:
        yield
    finally:
        logger.info("Shutting down HTTP server for %s", service_name)
        await runner.cleanup()


async def _cancel_and_wait(t: asyncio.Task[None]) -> None:
     t.cancel()
     with suppress(asyncio.CancelledError):
        await t

# ──────────────────────────────────────────────────────────────────────────────
#  2) internal main – receives the injected bits from `serve()`
# ──────────────────────────────────────────────────────────────────────────────
async def _main(*, service_name: str, logic_fn: LogicFn) -> None:
    """Orchestrates RM, listeners, consumers, /health & graceful shutdown."""
    
    logger.info("🛠  _main starting, service=%s", service_name)
    start_observability(app_title=service_name)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: (logger.warning("Got %s → shutting down", s.name), stop_event.set()))

    async with AsyncExitStack() as stack:
        # a) create one shared ResourceManager (RabbitMQ required, Couchbase optional)
        use_cb = os.getenv("AGENT_RUNTIME_CB", "0") == "1"
        use_ephemeral = os.getenv("AGENT_RUNTIME_EPHEMERAL_CB", "1") != "0"
        logger.info(
            "Creating ResourceManager (rmq=True, cb=%s, ephemeral=%s)…",
            use_cb,
            use_ephemeral,
        )
        rm = await stack.enter_async_context(
            resource_manager_context(rmq=True, cb=use_cb, ephemeral=use_ephemeral)
        )
        logger.info("ResourceManager ready: rm.rabbit_conn=%s", "set" if rm.rabbit_mgr else "none")

        registry_client: AgentRegistryClient | None = None
        registry_stop = asyncio.Event()
        registry_cfg = load_registry_config(service_name)
        if registry_cfg:
            registry_client = AgentRegistryClient(registry_cfg)
            configure_registry_client(registry_client)
            if await registry_client.register():
                logger.info(
                    "Agent registry registered %s (%s)",
                    registry_client.agent_id,
                    registry_client.username,
                )
            else:
                logger.warning("Agent registry registration failed; continuing without hard failure.")
            registry_task = asyncio.create_task(
                heartbeat_loop(
                    registry_client,
                    interval_seconds=registry_cfg.heartbeat_interval,
                    stop_event=registry_stop,
                ),
                name="agent-registry-heartbeat",
            )
            stack.push_async_callback(_cancel_and_wait, registry_task)
            stack.push_async_callback(registry_client.close)

        consumers_task = asyncio.create_task(
            run_consumers(rm, service_name, logic_fn, shutdown_event=stop_event),
            name="agent-runtime-consumers",
        )
        logger.info("Started run_consumers task %s", consumers_task.get_name())
        stack.push_async_callback(_cancel_and_wait, consumers_task)

        # d) tiny /health endpoint – shares RM for any future use
        await stack.enter_async_context(_http_server(rm, service_name))

        # e) wait until the process receives SIGINT / SIGTERM
        await stop_event.wait()
        logger.info("Shutdown initiated – waiting for tasks …")

        registry_stop.set()
        if registry_client is not None:
            try:
                await registry_client.heartbeat(status=AgentStatus.OFFLINE, ttl_seconds=0)
            except Exception:
                logger.debug("Failed to post offline heartbeat", exc_info=True)

    # After the ExitStack context closes, RM and other resources are cleaned up.
    with suppress(asyncio.CancelledError):
        await consumers_task

    logger.info("👋  graceful shutdown complete")


# ──────────────────────────────────────────────────────────────────────────────
#  3) public helper the micro‑service calls
# ──────────────────────────────────────────────────────────────────────────────

def serve(*, service_name: str | None = None, logic_fn: LogicFn | None = None) -> None:
    """Entry‑point that a micro‑service invokes **once** in its `__main__.py`.

    Example:

    ```python
    from agentcy.agent_runtime.bootstrap_agent import serve
    from .handler import run  # <- your business logic coroutine

    if __name__ == "__main__":
        serve(service_name="task_9", logic_fn=run)
    ```
    """
    if service_name is None:
        service_name = AgentSettings().service_name # type: ignore
    if not service_name:
        raise RuntimeError("SERVICE_NAME env‑var missing and no service_name provided to serve()")

    if logic_fn is None:
        raise RuntimeError("logic_fn (your async business‑logic) must be passed to serve()")
    logger.info("🚀 launching service %s via asyncio.run", service_name)
    asyncio.run(_main(service_name=service_name, logic_fn=logic_fn))
