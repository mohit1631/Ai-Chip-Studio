"""
app/routers/code_fix.py
---------------------------
Sprint 1 (AI Code Fixing), now a background job (Weak Area #1) rather than
a blocking request -- an AI call plus a re-lint AI call is exactly the
kind of work that shouldn't tie up a request thread:

    POST /projects/{project_id}/code-fix/preview  -> enqueues, no disk write on completion
    POST /projects/{project_id}/code-fix/apply     -> enqueues, writes fixed file back to storage on completion

Both return a Job (202 Accepted) immediately. Poll
GET /jobs/{job_id}/result for the CodeFixResult once status == "success".
Usage metering happens inside the task itself (app/tasks.py::task_code_fix),
since that's where AI call completion is observed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import enforce_usage_limit, require_project_role
from app.models import JobType, Project, UsageKind, User
from app.schemas import CodeFixRequest, JobOut
from app.services.jobs import enqueue_job
from app.tasks import task_code_fix

router = APIRouter(prefix="/projects/{project_id}/code-fix", tags=["code-fix"])


@router.post("/preview", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def preview_fix(
    request: CodeFixRequest,
    project: Project = Depends(require_project_role()),
    user: User = Depends(enforce_usage_limit(UsageKind.ai_call)),
    db: Session = Depends(get_db),
):
    params = {"file_path": request.file_path, "issues": [i.model_dump() for i in request.issues]}
    return enqueue_job(db, user, project.id, JobType.code_fix_preview, params, task_code_fix)


@router.post("/apply", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def apply_fix(
    request: CodeFixRequest,
    project: Project = Depends(require_project_role()),
    user: User = Depends(enforce_usage_limit(UsageKind.ai_call)),
    db: Session = Depends(get_db),
):
    params = {"file_path": request.file_path, "issues": [i.model_dump() for i in request.issues]}
    return enqueue_job(db, user, project.id, JobType.code_fix_apply, params, task_code_fix)
