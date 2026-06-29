"""
app/tasks.py
--------------
Celery tasks -- the actual background-job workers (Weak Area #1). Each
task:
    1. opens its own DB session (a worker process never shares the
       request-scoped session FastAPI's get_db() yields)
    2. marks the Job row running
    3. materializes the project's files from storage (app/services/storage.py)
       into a throwaway local scratch dir -- EDA tools and regex parsers
       still need real files on disk, the storage backend is just the
       durable copy
    4. does the actual work by calling the same service-layer functions
       the original synchronous routes used (code_fixer, testbench_generator,
       simulation_runner, ai_lint) -- nothing about *what* runs changed,
       only *where* and *when* it runs
    5. persists results, uploads any changed/output files back to storage,
       records usage, marks the Job row success/failure

Run a worker:
    celery -A app.celery_app worker --loglevel=info
"""

from __future__ import annotations

import datetime
import json
import math
import tempfile
import zipfile
from pathlib import Path

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models import Job, JobStatus, Project, UsageKind, UsageRecord
from app.schemas import LintIssue
from app.services.ai_client import call_ai
from app.services.ai_lint import run_ai_lint
from app.services.code_fixer import fix_and_relint
from app.services.simulation_runner import run_simulation
from app.services.staging import RTL_EXTENSIONS
from app.services.storage import get_storage
from app.services.testbench_generator import (
    TESTBENCH_SYSTEM_PROMPT,
    build_testbench_prompt,
    parse_ports,
)


def _record_usage(db, user_id: int, kind: UsageKind, amount: float = 1.0, note: str | None = None) -> None:
    db.add(UsageRecord(user_id=user_id, kind=kind, amount=amount, note=note))
    db.commit()


def _mark_running(db, job: Job) -> None:
    job.status = JobStatus.running
    job.started_at = datetime.datetime.utcnow()
    db.commit()


def _mark_done(db, job: Job, result: dict | list) -> None:
    job.status = JobStatus.success
    job.result_json = json.dumps(result)
    job.finished_at = datetime.datetime.utcnow()
    db.commit()
    # Counts toward the universal jobs_per_month cap (see UsageKind.job's
    # docstring in app/models.py) regardless of which finer-grained metric
    # (ai_call/sim_minute) this job type also recorded above.
    _record_usage(db, job.user_id, UsageKind.job, note=f"{job.job_type.value} completed")


def _mark_failed(db, job: Job, error: str) -> None:
    job.status = JobStatus.failure
    job.error_message = error
    job.finished_at = datetime.datetime.utcnow()
    db.commit()
    # A failed job still consumed compute/a submission slot -- count it,
    # so retrying a failing job repeatedly can't be used to bypass the
    # free tier's jobs_per_month cap.
    _record_usage(db, job.user_id, UsageKind.job, note=f"{job.job_type.value} failed: {error[:200]}")


def _load_job_and_project(db, job_id: int) -> tuple[Job, Project]:
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} not found")
    project = db.get(Project, job.project_id)
    if project is None:
        raise ValueError(f"Project {job.project_id} (for job {job_id}) not found")
    return job, project


@celery_app.task(bind=True, name="ai_chip_studio.run_ai_lint_job")
def task_run_ai_lint(self, job_id: int) -> None:
    """job_type == JobType.ai_lint. Reviews every RTL file in the project."""
    db = SessionLocal()
    try:
        job, project = _load_job_and_project(db, job_id)
        _mark_running(db, job)

        storage = get_storage()
        with tempfile.TemporaryDirectory() as tmp_dir:
            scratch = Path(tmp_dir)
            storage.materialize_prefix(project.storage_path, scratch)

            results: dict[str, list[dict]] = {}
            ai_calls_made = 0
            for f in scratch.rglob("*"):
                if f.suffix.lower() not in RTL_EXTENSIONS or not f.is_file():
                    continue
                rel = str(f.relative_to(scratch))
                lint_result = run_ai_lint(f.read_text(errors="ignore"), rel)
                results[rel] = [issue.model_dump() for issue in lint_result.issues]
                if lint_result.source == "ai":
                    ai_calls_made += 1

        if ai_calls_made:
            _record_usage(db, job.user_id, UsageKind.ai_call, amount=ai_calls_made, note="ai_lint project review")

        _mark_done(db, job, results)
    except Exception as exc:  # noqa: BLE001 -- a failed job should record *why*, not crash the worker
        _mark_failed(db, job, str(exc))
    finally:
        db.close()


@celery_app.task(bind=True, name="ai_chip_studio.code_fix_job")
def task_code_fix(self, job_id: int) -> None:
    """job_type in (code_fix_preview, code_fix_apply). params_json: {"file_path", "issues": [...]}."""
    db = SessionLocal()
    try:
        job, project = _load_job_and_project(db, job_id)
        _mark_running(db, job)

        params = json.loads(job.params_json or "{}")
        file_path = params["file_path"]
        issues = [LintIssue(**i) for i in params.get("issues", [])]

        storage = get_storage()
        key = f"{project.storage_path}/{file_path}"
        original_source = storage.get_bytes(key).decode(errors="ignore")

        result = fix_and_relint(file_path, original_source, issues)

        if job.job_type.value == "code_fix_apply":
            storage.put_bytes(key, result.fixed_source.encode())

        _record_usage(db, job.user_id, UsageKind.ai_call, note=f"{job.job_type.value}: {file_path}")
        _mark_done(db, job, result.model_dump())
    except Exception as exc:  # noqa: BLE001
        _mark_failed(db, job, str(exc))
    finally:
        db.close()


@celery_app.task(bind=True, name="ai_chip_studio.generate_testbench_job")
def task_generate_testbench(self, job_id: int) -> None:
    """job_type == testbench_generation. params_json: {"file_path", "top_module", "spec_text"}."""
    db = SessionLocal()
    try:
        job, project = _load_job_and_project(db, job_id)
        _mark_running(db, job)

        params = json.loads(job.params_json or "{}")
        file_path = params["file_path"]
        top_module_req = params.get("top_module")
        spec_text = params.get("spec_text")

        storage = get_storage()
        key = f"{project.storage_path}/{file_path}"
        source = storage.get_bytes(key).decode(errors="ignore")

        module_name, ports = parse_ports(source, top_module_req)
        prompt = build_testbench_prompt(module_name, ports, source, spec_text)
        testbench_source = call_ai(TESTBENCH_SYSTEM_PROMPT, prompt)

        tb_file_name = f"tb_{module_name}.sv"
        storage.put_bytes(f"{project.storage_path}/{tb_file_name}", testbench_source.encode())

        _record_usage(db, job.user_id, UsageKind.ai_call, note=f"testbench gen: {file_path}")
        _mark_done(
            db,
            job,
            {
                "top_module": module_name,
                "ports_detected": ports,
                "testbench_source": testbench_source,
                "testbench_file_name": tb_file_name,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _mark_failed(db, job, str(exc))
    finally:
        db.close()


@celery_app.task(bind=True, name="ai_chip_studio.run_simulation_job")
def task_run_simulation(self, job_id: int) -> None:
    """job_type == simulation. params_json: {"testbench_file_path", "top_module", "engine"}."""
    db = SessionLocal()
    try:
        job, project = _load_job_and_project(db, job_id)
        _mark_running(db, job)

        params = json.loads(job.params_json or "{}")
        testbench_file_path = params["testbench_file_path"]
        top_module_req = params.get("top_module")
        engine = params.get("engine", "verilator")

        storage = get_storage()
        with tempfile.TemporaryDirectory() as tmp_dir:
            scratch = Path(tmp_dir)
            storage.materialize_prefix(project.storage_path, scratch)

            testbench_path = scratch / testbench_file_path
            if not testbench_path.is_file():
                raise FileNotFoundError(f"Testbench file not found: {testbench_file_path}")

            rtl_files = [p for p in scratch.rglob("*") if p.suffix.lower() in RTL_EXTENSIONS]
            rtl_zip = scratch / "_rtl_for_sim.zip"
            with zipfile.ZipFile(rtl_zip, "w") as zf:
                for f in rtl_files:
                    if f.resolve() != testbench_path.resolve():
                        zf.write(f, arcname=f.relative_to(scratch))

            work_dir = Path(settings.jobs_root) / f"sim_job_{job_id}"
            result = run_simulation(
                input_path=rtl_zip,
                testbench_path=testbench_path,
                work_dir=work_dir,
                top_module=top_module_req,
                engine=engine,
            )

            # Persist sim outputs (log/VCD) back to storage so they survive
            # this scratch dir being deleted.
            output_prefix = f"{project.storage_path.rsplit('/', 1)[0]}/job_outputs/{job_id}"
            vcd_key = log_key = None
            if result.vcd_path and Path(result.vcd_path).is_file():
                vcd_key = f"{output_prefix}/{Path(result.vcd_path).name}"
                storage.put_file(vcd_key, Path(result.vcd_path))
            if result.log_path and Path(result.log_path).is_file():
                log_key = f"{output_prefix}/{Path(result.log_path).name}"
                storage.put_file(log_key, Path(result.log_path))

        minutes_used = max(1, math.ceil((result.sim_time_seconds or 0) / 60)) if result.success else 1
        _record_usage(db, job.user_id, UsageKind.sim_minute, amount=minutes_used, note=f"sim run: {engine}")

        _mark_done(
            db,
            job,
            {
                "success": result.success,
                "engine": result.engine,
                "sim_time_seconds": result.sim_time_seconds,
                "assertions_passed": result.assertions_passed,
                "assertions_failed": result.assertions_failed,
                "vcd_file": vcd_key,
                "log_file": log_key,
                "error_message": result.error_message,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _mark_failed(db, job, str(exc))
    finally:
        db.close()
