"""
app/routers/simulation.py
------------------------------
Sprint 4 (Simulation Engine), background job (Weak Area #1) -- a Verilator/
Icarus run can take anywhere from sub-second to minutes; it has no business
blocking an HTTP request thread either way:

    POST /projects/{project_id}/simulation/run -> enqueues, returns Job

The actual Verilator/Icarus integration is still
app/services/simulation_runner.py (unchanged from the roadmap bundle) --
app/tasks.py::task_run_simulation is what now calls it from inside a
Celery worker instead of from the route handler.

Poll GET /jobs/{job_id}/result for the SimulationResultOut shape once
finished; vcd_file/log_file in that result are storage keys (Weak Area #2),
not local paths -- fetch their bytes via the storage backend or extend
GET /projects/{id}/files/{path} to serve them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import enforce_usage_limit, require_project_role
from app.models import JobType, Project, UsageKind, User
from app.schemas import JobOut, SimulationRequest
from app.services.jobs import enqueue_job
from app.tasks import task_run_simulation

router = APIRouter(prefix="/projects/{project_id}/simulation", tags=["simulation"])


@router.post("/run", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
def run_project_simulation(
    request: SimulationRequest,
    project: Project = Depends(require_project_role()),
    user: User = Depends(enforce_usage_limit(UsageKind.sim_minute)),
    db: Session = Depends(get_db),
):
    if request.engine not in ("verilator", "icarus"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="engine must be 'verilator' or 'icarus'")

    params = {
        "testbench_file_path": request.testbench_file_path,
        "top_module": request.top_module,
        "engine": request.engine,
    }
    return enqueue_job(db, user, project.id, JobType.simulation, params, task_run_simulation)
