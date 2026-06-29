"""
app/routers/testbench.py
----------------------------
Sprint 2 (AI Testbench Generation), background job (Weak Area #1):
    POST /projects/{project_id}/testbench/generate -> enqueues, returns Job

Poll GET /jobs/{job_id}/result for the TestbenchResult once finished.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import enforce_usage_limit, require_project_role
from app.models import JobType, Project, UsageKind, User
from app.schemas import JobOut, TestbenchRequest
from app.services.jobs import enqueue_job
from app.tasks import task_generate_testbench

router = APIRouter(prefix="/projects/{project_id}/testbench", tags=["testbench"])


@router.post("/generate", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def generate_testbench(
    request: TestbenchRequest,
    project: Project = Depends(require_project_role()),
    user: User = Depends(enforce_usage_limit(UsageKind.ai_call)),
    db: Session = Depends(get_db),
):
    params = {
        "file_path": request.file_path,
        "top_module": request.top_module,
        "spec_text": request.spec_text,
    }
    return enqueue_job(db, user, project.id, JobType.testbench_generation, params, task_generate_testbench)
