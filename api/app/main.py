"""FastAPI application wiring.

Domain logic is split across route modules.  This file owns cross-cutting
concerns: CORS, request metrics, rate limiting, error shaping, startup safety
checks, logging configuration, and router registration.
"""
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import inspect
from starlette.exceptions import HTTPException as StarletteHTTPException

from .app_limiter import limiter
from .auth import configure_auth, seed_dev_users
from .config import settings
from .db import SessionLocal, engine
from .logging_utils import configure_json_logging, configure_pii_logging
from .prom import REQUEST_COUNT, REQUEST_LATENCY
from .routes import admin, auth, documents, export, health, insights, jobs, review
from .storage import ensure_bucket

configure_pii_logging()
if settings.log_format.lower() == "json":
    configure_json_logging()

app = FastAPI(title="PDF Extract API")
app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Accept-Ranges", "Content-Length", "Content-Range", "Content-Disposition"],
    allow_credentials=True,
)


def add_cors_headers(response: JSONResponse, request: Request) -> JSONResponse:
    """Add CORS headers to responses, especially error responses."""
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origin_list:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return add_cors_headers(response, request)


app.add_middleware(SlowAPIMiddleware)

configure_auth(SessionLocal)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(jobs.router)
app.include_router(documents.router)
app.include_router(review.router)
app.include_router(insights.router)
app.include_router(export.router)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    retry_after = getattr(exc, "retry_after", None) or 60
    return JSONResponse(
        {"detail": "Rate limit exceeded"},
        status_code=429,
        headers={"Retry-After": str(int(retry_after))},
    )


@app.middleware("http")
async def record_metrics(request, call_next):
    """Record Prometheus request count and latency for every response."""
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = route.path if route else request.url.path
    REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
    return response


@app.on_event("startup")
def startup():
    """Run boot-time safety checks and local development initialization."""
    settings.validate_production_safety()
    ensure_bucket()
    if inspect(engine).has_table("users"):
        seed_dev_users()
