"""
app/services/jobs.py
------------------------
Tiny shared helper so every router enqueues background work the same way:
create the Job row (status=pending), then hand its Celery task ID back so
the row can be looked up by either job_id (for the client) or task_id (if
you ever need to inspect Celery directly).
"""

from __future__ import annotations

import json

from celery import Task
from sqlalchemy.orm import Session

from app.models import Job, JobType, User


def enqueue_job(
    db: Session,
    user: User,
    project_id: int,
    job_type: JobType,
    params: dict,
    task: Task,
) -> Job:
    job = Job(
        user_id=user.id,
        project_id=project_id,
        job_type=job_type,
        params_json=json.dumps(params),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    async_result = task.delay(job.id)
    job.celery_task_id = async_result.id
    db.commit()
    db.refresh(job)
    return job
