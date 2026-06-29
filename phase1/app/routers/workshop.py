"""
app/routers/workshop.py
--------------------------
Community / Workshop feature: lets a user publish a project so anyone can
browse it, view its file tree, and "import" (fork) a copy into their own
workspace -- the same loop SiliconSpace calls a "Workshop".

Endpoints:
    POST /projects/{id}/publish     - owner only, marks a project public
    POST /projects/{id}/unpublish   - owner only, makes it private again
    GET  /workshop                  - browse all public projects
    GET  /workshop/{id}             - view one public project (+1 view)
    POST /workshop/{id}/import      - fork a public project into your own
                                       workspace (+1 fork on the original)

Publishing does not move files -- it just flips a flag on the existing
Project row. Importing/forking *does* copy files, via the storage layer's
list_keys()/get_bytes()/put_bytes(), so the new owner has their own
independent copy to edit without touching the original.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_project_role
from app.models import Project, ProjectMember, ProjectRole, User
from app.schemas import ImportResult, PublishRequest, ProjectOut, WorkshopProjectOut
from app.services.storage import get_storage

router = APIRouter(prefix="/workshop", tags=["workshop"])

# Publish/unpublish are project-scoped actions, so they live under the same
# /projects prefix that projects.router uses -- kept as a separate router
# here (rather than added to projects.py) since they're part of this
# feature's surface area, but mounted with a matching prefix for
# consistency with every other router in app/routers/.
project_router = APIRouter(prefix="/projects", tags=["workshop"])


@project_router.post("/{project_id}/publish", response_model=ProjectOut)
def publish_project(
    payload: PublishRequest,
    project: Project = Depends(require_project_role(ProjectRole.owner)),
    db: Session = Depends(get_db),
):
    project.is_public = True
    if payload.pdk:
        project.pdk = payload.pdk
    db.commit()
    db.refresh(project)
    return project


@project_router.post("/{project_id}/unpublish", response_model=ProjectOut)
def unpublish_project(
    project: Project = Depends(require_project_role(ProjectRole.owner)),
    db: Session = Depends(get_db),
):
    project.is_public = False
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[WorkshopProjectOut])
def browse_workshop(
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),  # any logged-in user can browse
):
    rows = db.execute(
        select(Project, User.email)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .join(User, User.id == ProjectMember.user_id)
        .where(Project.is_public.is_(True), ProjectMember.role == ProjectRole.owner)
        .order_by(Project.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    return [
        WorkshopProjectOut(
            id=proj.id,
            name=proj.name,
            top_module=proj.top_module,
            pdk=proj.pdk,
            views=proj.views,
            forks=proj.forks,
            owner_email=owner_email,
            created_at=proj.created_at,
        )
        for proj, owner_email in rows
    ]


@router.get("/{project_id}", response_model=WorkshopProjectOut)
def view_workshop_project(
    project_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if project is None or not project.is_public:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Design not found")

    owner_membership = db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id, ProjectMember.role == ProjectRole.owner
        )
    )
    owner_email = owner_membership.user.email if owner_membership else "unknown"

    project.views += 1
    db.commit()
    db.refresh(project)

    return WorkshopProjectOut(
        id=project.id,
        name=project.name,
        top_module=project.top_module,
        pdk=project.pdk,
        views=project.views,
        forks=project.forks,
        owner_email=owner_email,
        created_at=project.created_at,
    )


@router.post("/{project_id}/import", response_model=ImportResult, status_code=status.HTTP_201_CREATED)
def import_workshop_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Fork a public design: copies its files into a brand-new project
    owned by the current user. The original is untouched, just its
    fork counter goes up."""
    source = db.get(Project, project_id)
    if source is None or not source.is_public:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Design not found")

    storage = get_storage()
    proj_key = uuid.uuid4().hex[:12]
    new_prefix = f"projects/proj_{proj_key}/source"

    for key in storage.list_keys(source.storage_path):
        rel = key[len(source.storage_path):].lstrip("/")
        data = storage.get_bytes(key)
        storage.put_bytes(f"{new_prefix}/{rel}", data)

    new_project = Project(
        name=f"{source.name} (forked)",
        storage_path=new_prefix,
        top_module=source.top_module,
        pdk=source.pdk,
        forked_from_id=source.id,
    )
    db.add(new_project)
    db.flush()
    db.add(ProjectMember(project_id=new_project.id, user_id=user.id, role=ProjectRole.owner))

    source.forks += 1
    db.commit()
    db.refresh(new_project)

    return ImportResult(new_project_id=new_project.id, name=new_project.name)
