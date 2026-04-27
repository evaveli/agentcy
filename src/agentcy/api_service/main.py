# src/agentcy/api_service/main.py
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


from agentcy.pipeline_orchestrator.resource_manager import resource_manager_context
from agentcy.api_service.routers.images     import router as images_router
from agentcy.api_service.routers.discovery import router as discovery_router
from agentcy.api_service.routers.services  import router as services_router
from agentcy.api_service.routers.pipelines import router as pipelines_router
from agentcy.api_service.routers.healthchecks import router as health_router
from agentcy.api_service.routers.services_create_with_artifact import router as services_create_router
from agentcy.api_service.routers.agent_registry import router as agent_registry_router
from agentcy.api_service.routers.graph_store import router as graph_store_router
from agentcy.api_service.routers.cnp import router as cnp_router
from agentcy.api_service.routers.semantic import router as semantic_router
from agentcy.api_service.routers.templates import router as templates_router
from agentcy.api_service.routers.evaluation import router as evaluation_router
logger = logging.getLogger(__name__)

def _sanitize(obj, max_len=2000):
    # Replace bytes with a small summary; truncate long strings/collections.
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes suppressed>"
    if isinstance(obj, str):
        return obj if len(obj) <= max_len else obj[:max_len] + " … [truncated]"
    if isinstance(obj, dict):
        return {k: _sanitize(v, max_len) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v, max_len) for v in obj]
    return obj



# ─────────────────────────────────────────────────────────────────────────────
# lifespan: single ResourceManager shared by every request
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with resource_manager_context(rmq=True, cb=True, ephemeral=True) as rm:
        app.state.rm = rm
        logger.info("ResourceManager initialised.")

        # Validate LLM provider configuration (non-blocking)
        try:
            from agentcy.llm_utilities.provider_validator import (
                validate_llm_config,
                log_validation_result,
            )
            llm_result = validate_llm_config()
            log_validation_result(llm_result)
            if not llm_result.valid:
                logger.warning(
                    "LLM configuration has errors - some services may fail: %s",
                    llm_result.errors,
                )
        except Exception:
            logger.exception("LLM config validation failed (non-fatal)")

        # Initialize Fuseki semantic layer (feature-flagged, non-blocking)
        try:
            from agentcy.semantic.fuseki_init import initialize_fuseki
            fuseki_status = await initialize_fuseki()
            if fuseki_status.get("enabled"):
                logger.info(
                    "Fuseki initialized: shapes=%s ontology=%s",
                    fuseki_status.get("shapes"),
                    fuseki_status.get("ontology"),
                )
        except Exception:
            logger.exception("Fuseki initialization failed (non-fatal)")

        yield
        logger.info("Shutting down – ResourceManager cleaned up.")

def create_app() -> FastAPI:
    app = FastAPI(title="Agentcy API Service", lifespan=lifespan, debug=False)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(images_router)
    app.include_router(discovery_router)
    app.include_router(services_router)
    app.include_router(pipelines_router)
    app.include_router(health_router)
    app.include_router(services_create_router)
    app.include_router(agent_registry_router)
    app.include_router(graph_store_router)
    app.include_router(cnp_router)
    app.include_router(semantic_router)
    app.include_router(templates_router)
    app.include_router(evaluation_router)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        simplified = [
            {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": simplified})

    # ⬇️ ADD: sanitize Starlette HTTP exceptions (don’t let bytes leak)
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": _sanitize(exc.detail)})

    return app

# ─────────────────────────────────────────────────────────────────────────────
# entry-point: `uvicorn api_service.main:create_app --factory`
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":                          # pragma: no cover
    import uvicorn
    uvicorn.run("api_service.main:create_app", factory=True,
                host="0.0.0.0", port=8002, reload=False)
