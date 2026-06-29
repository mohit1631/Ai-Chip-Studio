"""
app/routers/projects.py
--------------------------
Sprint 3 (Multi-file Projects):
    - ZIP Upload                          -> POST /projects (stages locally,
                                              then persists to the storage
                                              backend -- app/services/storage.py)
    - Project File Tree View              -> GET /projects/{id} -> file_tree
    - Cross-file Dependency Resolution    -> GET /projects/{id} -> dependencies
    - Per-file + Project-wide Lint/Review -> POST /projects/{id}/lint/run
                                              (background job -- AI review of
                                              every file shouldn't block the
                                              request, see app/tasks.py)

Sprint 5 (User Accounts) sharing:
    - Project Sharing / Team Workspaces   -> POST /projects/{id}/members

File access in this router always goes through the configured storage
backend (local filesystem or S3-compatible), never a raw local Path --
see app/services/storage.py for why.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import enforce_usage_limit, get_current_user, require_project_role
from app.models import JobType, Project, ProjectMember, ProjectRole, UsageKind, User
from app.schemas import JobOut, ProjectDetail, ProjectMemberAdd, ProjectOut
from app.services import project_manager
from app.services.jobs import enqueue_job
from app.services.staging import StagingError, stage_project
from app.services.storage import get_storage
from app.tasks import task_run_ai_lint

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    name: str = Form(...),
    file: UploadFile = ...,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Accepts a single .v/.sv file OR a .zip multi-file project. Staging
    (ZIP-slip guard, size/file-count/extracted-size caps) still happens
    against a local scratch dir -- it has to, that's how zipfile works --
    but the result is persisted to the storage backend, not left on the
    API server's local disk.
    """
    proj_key = uuid.uuid4().hex[:12]
    storage = get_storage()

    with tempfile.TemporaryDirectory() as tmp_dir:
        scratch = Path(tmp_dir)
        upload_path = scratch / file.filename
        with upload_path.open("wb") as f:
            f.write(file.file.read())

        try:
            rtl_files = stage_project(upload_path, scratch)
        except StagingError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        top_module = project_manager.guess_top_module(rtl_files)

        # stage_project() always extracts ZIP projects into work_dir/rtl_src
        # and never creates that folder for a single-file upload -- check
        # for it directly rather than guessing from len(rtl_files), which
        # mis-handles a ZIP containing exactly one RTL file nested in a
        # subdirectory.
        staged_root = scratch / "rtl_src" if (scratch / "rtl_src").is_dir() else scratch

        storage_prefix = f"projects/proj_{proj_key}/source"
        for f in rtl_files:
            try:
                rel = f.relative_to(staged_root)
            except ValueError:
                rel = f.name
            storage.put_file(f"{storage_prefix}/{rel}", f)

    project = Project(name=name, storage_path=storage_prefix, top_module=top_module)
    db.add(project)
    db.flush()  # get project.id before adding the membership row
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.owner))
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    memberships = db.scalars(select(ProjectMember).where(ProjectMember.user_id == user.id)).all()
    return [m.project for m in memberships]


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project: Project = Depends(require_project_role())):
    storage = get_storage()
    with tempfile.TemporaryDirectory() as tmp_dir:
        scratch = Path(tmp_dir)
        storage.materialize_prefix(project.storage_path, scratch)
        rtl_files = [p for p in scratch.rglob("*") if p.is_file()]

        return ProjectDetail(
            id=project.id,
            name=project.name,
            top_module=project.top_module,
            created_at=project.created_at,
            file_tree=project_manager.build_file_tree(scratch),
            dependencies=project_manager.build_dependency_graph(rtl_files),
        )


@router.get("/{project_id}/files/{file_path:path}")
def read_file(file_path: str, project: Project = Depends(require_project_role())):
    storage = get_storage()
    key = f"{project.storage_path}/{file_path}"
    try:
        content = storage.get_bytes(key).decode(errors="ignore")
    except Exception as exc:  # storage backends raise their own not-found errors
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found") from exc
    return {"path": file_path, "content": content}


@router.post("/{project_id}/lint/run", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_project_lint(
    project: Project = Depends(require_project_role()),
    user: User = Depends(enforce_usage_limit(UsageKind.ai_call)),
    db: Session = Depends(get_db),
):
    """
    Per-file + project-wide AI RTL Review / Bug Detection. Runs as a
    background job (app/tasks.py::task_run_ai_lint) -- reviewing every file
    in a project can mean one AI call per file, which has no business
    blocking the HTTP request. Poll GET /jobs/{id}/result for the per-file
    issues once status == "success".
    """
    job = enqueue_job(db, user, project.id, JobType.ai_lint, params={}, task=task_run_ai_lint)
    return job


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(
    payload: ProjectMemberAdd,
    project: Project = Depends(require_project_role(ProjectRole.admin)),
    db: Session = Depends(get_db),
):
    """Sprint 5: Project Sharing / Team Workspaces. Requires admin+ on the project."""
    invited_user = db.scalar(select(User).where(User.email == payload.user_email))
    if invited_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == invited_user.id
        )
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already a member")

    db.add(ProjectMember(project_id=project.id, user_id=invited_user.id, role=payload.role))
    db.commit()
    return {"status": "added", "user_email": payload.user_email, "role": payload.role}
