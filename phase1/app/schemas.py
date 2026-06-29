"""
app/app/app/schemas.py
---------------
Pydantic request/response models for every router. Kept in one file since
the skeleton is small; split per-domain once Phase 1 grows past this size.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models import JobStatus, JobType, ProjectRole, Tier


# --- Auth (Sprint 5) ---

class UserCreate(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    tier: Tier
    created_at: datetime.datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --- Projects (Sprint 3 + Sprint 5 sharing) ---

class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    top_module: str | None
    created_at: datetime.datetime
    is_public: bool
    pdk: str | None


class FileTreeNode(BaseModel):
    name: str
    path: str
    is_dir: bool
    children: list["FileTreeNode"] = []


class DependencyEdge(BaseModel):
    module: str
    instantiates: list[str]


class ProjectDetail(ProjectOut):
    file_tree: list[FileTreeNode]
    dependencies: list[DependencyEdge]


class ProjectMemberAdd(BaseModel):
    user_email: EmailStr
    role: ProjectRole = ProjectRole.viewer


# --- Workshop / Community sharing ---

class PublishRequest(BaseModel):
    # Free text, not validated against an enum -- any string is accepted.
    # Phase 4's pdk_presets.py has a small known-names registry
    # (sky130, gf180mcu, freepdk45, openrpdk28, freepdk15) used to resolve
    # the OpenROAD -site name when actually running physical design; using
    # one of those names here lets a viewer cross-reference what a
    # project was (or could be) run through. Note that everything below
    # freepdk45 in that registry is an academic/predictive model, not
    # tapeout-capable -- worth surfacing in the UI rather than implying
    # otherwise just because a project lists e.g. "FreePDK15".
    pdk: str | None = None  # e.g. "SKY130"


class WorkshopProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    top_module: str | None
    pdk: str | None
    views: int
    forks: int
    owner_email: str
    created_at: datetime.datetime


class ImportResult(BaseModel):
    new_project_id: int
    name: str


# --- Sprint 1: AI Code Fixing ---

class LintIssue(BaseModel):
    """
    Shape of one issue as produced by the existing Silicon Lint product
    (AI RTL Review / AI Bug Detection, already built per the README's
    'Current State'). This bundle does not implement lint itself -- see
    app/services/lint_stub.py for the pluggable interface.

    `status` mirrors phase4's AIInsight.predicted/confirmed discipline
    (see physical_design_runner.py): an issue the LLM claims to have found
    is `predicted` until something deterministic backs it up, since an AI
    review is a claim, not a measurement. The regex fallback's matches are
    `confirmed` immediately -- a regex either matched the source text or
    it didn't, there's no AI uncertainty to flag for that path.
    """
    rule: str
    message: str
    line: int
    severity: str = "warning"
    status: str = "predicted"  # "predicted" | "confirmed"


class CodeFixRequest(BaseModel):
    file_path: str  # relative path within the project's staged files
    issues: list[LintIssue]


class CodeFixResult(BaseModel):
    file_path: str
    diff: str  # unified diff, before -> after
    fixed_source: str
    relint_issue_count: int
    relint_issues: list[LintIssue]


# --- Sprint 2: AI Testbench Generation ---

class TestbenchRequest(BaseModel):
    file_path: str
    top_module: str | None = None
    spec_text: str | None = None  # optional natural-language spec


class TestbenchResult(BaseModel):
    top_module: str
    ports_detected: list[str]
    testbench_source: str
    testbench_file_name: str


# --- Sprint 4: Simulation (wraps code/simulation_runner.py) ---

class SimulationRequest(BaseModel):
    testbench_file_path: str
    top_module: str | None = None
    engine: str = "verilator"  # "verilator" | "icarus"


class SimulationResultOut(BaseModel):
    success: bool
    engine: str | None
    sim_time_seconds: float | None
    assertions_passed: int | None
    assertions_failed: int | None
    vcd_file: str | None
    log_file: str | None
    error_message: str | None


# --- Background jobs (Redis + Celery worker queue) ---

class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    job_type: JobType
    status: JobStatus
    created_at: datetime.datetime
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    error_message: str | None


class JobResultOut(JobOut):
    # result_json from the DB row, parsed back into a plain dict for the
    # client -- shape depends on job_type (e.g. a SimulationResultOut dict
    # for job_type == simulation, a CodeFixResult dict for code_fix_*).
    result: dict | list | None
