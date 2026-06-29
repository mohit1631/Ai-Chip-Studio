"""
app/models.py
--------------
SQLAlchemy models backing Sprint 5 (User Accounts) and the project storage
that Sprints 1-4 all read/write against.

Tables:
    User            - auth + tier
    Project         - a workspace (single file or staged multi-file project)
    ProjectMember   - team workspace / sharing (Sprint 5: "Project Sharing /
                      Team Workspaces") with per-member role
    UsageRecord     - one row per metered action, used to enforce
                      09_pricing_model.md tier limits (Sprint 5)
"""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Tier(str, enum.Enum):
    free = "free"
    pro = "pro"
    team = "team"
    enterprise = "enterprise"


class ProjectRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class UsageKind(str, enum.Enum):
    ai_call = "ai_call"          # Sprint 1 / Sprint 2 AI invocation
    sim_minute = "sim_minute"    # Sprint 4 simulation time, in minutes
    job = "job"                  # any job submission, regardless of type --
                                  # used for the simple "N jobs/month" free-trial
                                  # cap, tracked alongside (not instead of) the
                                  # more granular ai_call/sim_minute metrics above


class JobType(str, enum.Enum):
    simulation = "simulation"
    code_fix_preview = "code_fix_preview"
    code_fix_apply = "code_fix_apply"
    testbench_generation = "testbench_generation"
    ai_lint = "ai_lint"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failure = "failure"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    tier: Mapped[Tier] = mapped_column(Enum(Tier), default=Tier.free)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    memberships: Mapped[list["ProjectMember"]] = relationship(back_populates="user")
    usage_records: Mapped[list["UsageRecord"]] = relationship(back_populates="user")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    # Where staged files live on disk for this project, e.g. jobs/proj_<id>/
    storage_path: Mapped[str] = mapped_column(String(512))
    top_module: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    # --- Workshop / Community sharing ---
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    pdk: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "SKY130"
    views: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    forked_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True
    )

    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base):
    """Sprint 5: Project Sharing / Team Workspaces -- join table with a role."""

    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role: Mapped[ProjectRole] = mapped_column(Enum(ProjectRole), default=ProjectRole.owner)

    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


class UsageRecord(Base):
    """
    One row per metered action. Sprint 5 usage-tier enforcement sums these
    over the current month rather than keeping a single mutable counter, so
    history/audit and monthly resets fall out for free.
    """

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[UsageKind] = mapped_column(Enum(UsageKind))
    amount: Mapped[float] = mapped_column(Float, default=1.0)  # e.g. minutes, or 1 per AI call
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="usage_records")


class Job(Base):
    """
    Background job tracking (Redis + Celery worker queue). Every long-
    running action -- simulation, AI lint, AI code-fix, testbench
    generation -- creates one row here when enqueued; the Celery task
    updates status/result/error as it runs. The route that enqueues the
    job returns this row's id immediately instead of blocking.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    job_type: Mapped[JobType] = mapped_column(Enum(JobType))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.pending)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
