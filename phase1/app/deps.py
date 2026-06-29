"""
app/deps.py
------------
Shared FastAPI dependencies: DB session (re-exported from database.py),
current-user resolution from the JWT bearer token, usage-tier enforcement
(Sprint 5, mirrors 09_pricing_model.md), and project-access checks backing
Sprint 5's sharing roles.
"""

from __future__ import annotations

import datetime

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.config import settings
from app.database import get_db
from app.models import Project, ProjectMember, ProjectRole, UsageKind, UsageRecord, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    email = decode_access_token(token)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def _usage_this_month(db: Session, user_id: int, kind: UsageKind) -> float:
    start_of_month = datetime.datetime.utcnow().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    total = db.scalar(
        select(func.coalesce(func.sum(UsageRecord.amount), 0.0)).where(
            UsageRecord.user_id == user_id,
            UsageRecord.kind == kind,
            UsageRecord.created_at >= start_of_month,
        )
    )
    return float(total or 0.0)


def enforce_usage_limit(kind: UsageKind):
    """
    Dependency factory: blocks the request with 429 if the user's monthly
    usage is already at/over their tier limit -- checking BOTH the simple
    universal "N jobs/month" cap (UsageKind.job, e.g. the free tier's
    5 jobs/month) AND the finer-grained `kind` passed in (ai_call or
    sim_minute). A free-tier user is blocked by whichever limit they hit
    first. Tier limits come from settings.usage_tiers (kept in sync with
    09_pricing_model.md).

    Existing call sites don't need to change -- enforce_usage_limit(
    UsageKind.ai_call) already implies the jobs_per_month check too, so
    every route that previously only checked ai_calls_per_month or
    sim_minutes_per_month now also respects jobs_per_month for free.

    Usage example on a route:
        @router.post("/code-fix/apply")
        def apply_fix(..., user: User = Depends(enforce_usage_limit(UsageKind.ai_call))):
    """
    LIMIT_KEYS = {
        UsageKind.ai_call: "ai_calls_per_month",
        UsageKind.sim_minute: "sim_minutes_per_month",
        UsageKind.job: "jobs_per_month",
    }

    def _check_one(db: Session, user: User, check_kind: UsageKind) -> None:
        tier_limits = settings.usage_tiers.get(user.tier.value, {})
        limit_key = LIMIT_KEYS[check_kind]
        limit = tier_limits.get(limit_key, 0)

        if limit != -1:  # -1 == unlimited
            used = _usage_this_month(db, user.id, check_kind)
            if used >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Monthly {limit_key.replace('_', ' ')} limit reached for the "
                        f"'{user.tier.value}' tier ({limit}). Upgrade tier or wait for reset."
                    ),
                )

    def _dependency(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        # Universal job-count gate first (cheapest mental model for users:
        # "you get N job submissions" regardless of type), then the
        # specific granular metric this route also cares about.
        _check_one(db, user, UsageKind.job)
        if kind != UsageKind.job:
            _check_one(db, user, kind)
        return user

    return _dependency


def record_usage(db: Session, user_id: int, kind: UsageKind, amount: float = 1.0, note: str | None = None) -> None:
    db.add(UsageRecord(user_id=user_id, kind=kind, amount=amount, note=note))
    db.commit()


_ROLE_RANK = {ProjectRole.viewer: 0, ProjectRole.editor: 1, ProjectRole.admin: 2, ProjectRole.owner: 3}


def require_project_role(min_role: ProjectRole = ProjectRole.viewer):
    """
    Dependency factory: loads a project the current user has at least
    `min_role` access to (owner > admin > editor > viewer). Returning a
    factory (rather than taking min_role as a plain parameter) is what lets
    FastAPI still resolve db/user via Depends() correctly for each route --
    calling a Depends-decorated function directly as a plain Python
    function would skip that resolution.

    Usage:
        @router.get("/{project_id}")
        def get_project(project: Project = Depends(require_project_role())):
            ...  # viewer is enough

        @router.post("/{project_id}/members")
        def add_member(project: Project = Depends(require_project_role(ProjectRole.admin))):
            ...  # needs admin or owner
    """

    def _dependency(
        project_id: int,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> Project:
        membership = db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user.id
            )
        )
        if membership is None or _ROLE_RANK[membership.role] < _ROLE_RANK[min_role]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        return project

    return _dependency
