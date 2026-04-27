# agentcy/api_service/routers/health.py
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
import asyncio, time
from agentcy.api_service.dependecies import get_rm, ResourceManager

router = APIRouter()

@router.get("/health", include_in_schema=False)
async def health() -> dict:
    # Liveness: no external calls
    return {"status": "ok"}

@router.get("/ready", include_in_schema=False)
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
