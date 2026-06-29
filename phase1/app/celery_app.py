"""
app/celery_app.py
--------------------
Celery instance, Redis as both broker and result backend. This is what
turns simulation runs, AI lint, AI code-fix, and testbench generation from
blocking-the-request work into background jobs (Weak Area #1: "No
Background Jobs").

Run a worker (separate process from the API server):
    celery -A app.celery_app worker --loglevel=info

Dev/test shortcut -- run tasks inline, no Redis/worker needed at all:
    set AICHIP_CELERY_TASK_ALWAYS_EAGER=true
(Useful for `pytest`-style testing of the task functions themselves; don't
ship this on in production, it defeats the entire point of the queue.)
"""

from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery(
    "ai_chip_studio",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_always_eager=settings.celery_task_always_eager,
    # Don't let a wedged Yosys/Verilator/Icarus subprocess hang a worker
    # forever -- belt-and-suspenders alongside the subprocess-level
    # timeout already in simulation_runner.py / synthesis_runner.py.
    task_time_limit=settings.subprocess_timeout_seconds + 60,
)

celery_app.autodiscover_tasks(["app"])
