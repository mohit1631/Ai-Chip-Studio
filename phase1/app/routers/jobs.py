"""
app/routers/jobs.py
-----------------------
Every background job (simulation, AI lint, code-fix, testbench gen) is
polled through here regardless of which router enqueued it.

    GET /jobs/{job_id}          status only (cheap, no result parsing)
    GET /jobs/{job_id}/result   status + parsed result once finished
    GET /projects/{project_id}/jobs   recent jobs for a project
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_project_role
from app.models import Job, Project, User
from app.schemas import JobOut, JobResultOut

router = APIRouter(tags=["jobs"])


def _get_owned_job(job_id: int, db: Session, user: User) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _get_owned_job(job_id, db, user)


@router.get("/jobs/{job_id}/result", response_model=JobResultOut)
def get_job_result(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = _get_owned_job(job_id, db, user)
    result = json.loads(job.result_json) if job.result_json else None
    return JobResultOut(
        id=job.id,
        project_id=job.project_id,
        job_type=job.job_type,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_message=job.error_message,
        result=result,
    )


@router.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_project_jobs(
    project: Project = Depends(require_project_role()),
    db: Session = Depends(get_db),
):
    jobs = db.scalars(
        select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(50)
    ).all()
    return jobs
