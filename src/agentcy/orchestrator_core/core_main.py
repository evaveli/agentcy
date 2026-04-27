#src/agentcy/orchestrator_core/core_main.py

import asyncio
import logging
from contextlib import asynccontextmanager
import time

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from agentcy.pipeline_orchestrator.resource_manager import resource_manager_context, ResourceManager
from agentcy.orchestrator_core.consumers import (
    register_service_consumer,
    register_pipeline_consumer,
    revise_plan_consumer,
    cnp_lifecycle_consumer,
    task_dispatch_consumer,
    ethics_re_evaluation_consumer,
    cnp_manager_consumer,
)
from agentcy.orchestrator_core.consumers.launcher_consumer import start_task_consumer
from agentcy.orchestrator_core.webserver_supervisor import supervise
from agentcy.orchestrator_core.consumers.topology_consumer import pipeline_registered_consumer
from agentcy.observability.bootstrap import start_observability
from agentcy.api_service.dependecies import get_rm

logger = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. boot resources
    async with resource_manager_context(rmq=True, cb=True, ephemeral=True) as rm:
        app.state.rm = rm

        # 2. spawn supervised consumers
        tasks = [
            asyncio.create_task(
                supervise("service_consumer", register_service_consumer, rm)
            ),
            asyncio.create_task(
                supervise("pipeline_consumer", register_pipeline_consumer, rm)
            ),
            asyncio.create_task(supervise("topology_consumer", pipeline_registered_consumer, rm)),
            asyncio.create_task(supervise("start_task_consumer", start_task_consumer, rm)),
            asyncio.create_task(supervise("revise_plan_consumer", revise_plan_consumer, rm)),
            asyncio.create_task(supervise("cnp_lifecycle_consumer", cnp_lifecycle_consumer, rm)),
            asyncio.create_task(supervise("task_dispatch_consumer", task_dispatch_consumer, rm)),
            asyncio.create_task(supervise("ethics_re_eval_consumer", ethics_re_evaluation_consumer, rm)),
            asyncio.create_task(supervise("cnp_manager_consumer", cnp_manager_consumer, rm)),
        ]
        app.state.consumer_tasks = tasks
        logger.info("📡  consumers launched under supervision")

        try:
            # 3. hand control back to FastAPI
            yield
        finally:
            # 4. graceful shutdown
            logger.info("🛑 cancelling consumer tasks…")
            for t in tasks:
                t.cancel()

            # propagate any exceptions so your container still fails if something went wrong
            await asyncio.gather(*tasks, return_exceptions=False)
            logger.info("✅ all consumers stopped. resources cleaned up.")


app = FastAPI(title="Orchestrator-Core", lifespan=lifespan)
start_observability(app)


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/ready", include_in_schema=False)
async def ready(rm: ResourceManager = Depends(get_rm)):
    ok = True
    checks = {}

    try:
        t0 = time.perf_counter(); await rm.ping_rabbit(); 
        checks["rabbitmq"] = {"ok": True, "ms": round((time.perf_counter()-t0)*1000, 1)}
    except Exception as e:
        ok = False; checks["rabbitmq"] = {"ok": False, "err": str(e)[:200]}

    try:
        t0 = time.perf_counter(); await rm.ping_cb(); 
        checks["couchbase"] = {"ok": True, "ms": round((time.perf_counter()-t0)*1000, 1)}
    except Exception as e:
        ok = False; checks["couchbase"] = {"ok": False, "err": str(e)[:200]}

    try:
        t0 = time.perf_counter(); await rm.ping_cb_ephemeral(); 
        checks["couchbase_ephemeral"] = {"ok": True, "ms": round((time.perf_counter()-t0)*1000, 1)}
    except Exception as e:
        ok = False; checks["couchbase_ephemeral"] = {"ok": False, "err": str(e)[:200]}

    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orchestrator_core.main:app", host="0.0.0.0", port=8001)
