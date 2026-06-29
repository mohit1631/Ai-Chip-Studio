"""
app/main.py
------------
FastAPI app entrypoint.

Run the API:
    uvicorn app.main:app --reload

Run a worker (separate process -- required for any of the AI/sim endpoints
to ever actually finish, see Weak Area #1 / app/celery_app.py):
    celery -A app.celery_app worker --loglevel=info

Then see /docs for interactive Swagger UI covering every endpoint below.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings
from app.database import Base, engine
from app.rate_limit import limiter
from app.routers import auth, code_fix, jobs, projects, simulation, testbench, workshop

if settings.auto_create_tables:
    # Dev/SQLite convenience only. Once Alembic (migrations/) is your
    # source of truth -- which it should be anywhere there's a real
    # Postgres instance -- turn this off via AICHIP_AUTO_CREATE_TABLES=false
    # and run `alembic upgrade head` instead.
    Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name)

# CORS: without this, a browser running the frontend on a different
# origin (Netlify/Render static site, custom domain, etc.) can't call
# this API at all -- the browser blocks the request before it even
# leaves, regardless of what the server would have answered. See
# settings.cors_allowed_origins's docstring for the "*" default rationale.
_cors_origins = (
    ["*"] if settings.cors_allowed_origins == "*"
    else [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,  # JWT bearer tokens only, no cookies -- keeps "*" origins safe
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting: a generous default (settings.rate_limit_default) applies
# to every route via SlowAPIMiddleware; specific routes (auth/login,
# auth/register) set a tighter limit directly via @limiter.limit(...).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(code_fix.router)        # Sprint 1 (background job)
app.include_router(testbench.router)        # Sprint 2 (background job)
# Sprint 3 (Multi-file Projects) lives inside projects.router above
app.include_router(simulation.router)        # Sprint 4 (background job)
app.include_router(jobs.router)               # poll any background job's status/result
app.include_router(workshop.project_router)    # Community / Workshop: publish, unpublish
app.include_router(workshop.router)            # Community / Workshop: browse, view, import
# Sprint 5 (User Accounts) is auth.router + the sharing endpoints in projects.router


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}
