from fastapi import FastAPI

from app.api.router import api_router
from app.api.webhooks.cf_kv import router as cf_kv_router
from app.api.webhooks.sentry import router as sentry_router
from app.api.webhooks.stripe import router as stripe_router
from app.core.middleware import AuthMiddleware
from app.core.prometheus import PrometheusMiddleware, metrics_endpoint
from app.core.rate_limit import RateLimitMiddleware
from app.core.startup import lifespan
from app.core.websockets import router as ws_router


def create_app() -> FastAPI:
    app = FastAPI(title="GoBig API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(PrometheusMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        AuthMiddleware,
        public_paths={
            "/health",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/v1/storage/presign",
            "/api/upload",
            "/api/v1/nlp/ingest",
            "/auth/dev-token",
            "/auth/dev-token-unlimited",
            "/webhooks/cf-kv-sync",
            "/webhooks/stripe",
            "/webhooks/sentry",
        },
    )
    app.include_router(api_router)
    app.include_router(cf_kv_router)
    app.include_router(sentry_router)
    app.include_router(stripe_router)
    app.include_router(ws_router)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/metrics")
    async def metrics():
        return metrics_endpoint()

    return app


app = create_app()

