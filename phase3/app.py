"""
app.py
------
AI Chip Studio — Phase 3 Complete
  Sprint 1 : Synthesis Engine      → /synthesize
  Sprint 2 : Area / Timing / Power → /analyze/<job_id>
  Sprint 3 : Technology Mapping    → /techmap
  Sprint 4 : Synthesis Dashboard   → /dashboard

Simulation (Phase 1 Sprint 4) is also wired in:
  Simulation UI                    → /simulate

Run inside WSL2 or Linux with Yosys installed:
    pip install flask
    python3 app.py
Then open http://localhost:5000
"""

import os
import re
import sys
import uuid
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify
from flask_cors import CORS

BASE_DIR  = Path(__file__).parent
CODE_DIR  = BASE_DIR / "code"
sys.path.insert(0, str(CODE_DIR))

from synthesis_runner  import run_synthesis,  SynthesisError
from analysis_runner   import run_analysis,   AnalysisError
from techmap_runner    import run_techmap,    TechmapError
from simulation_runner import run_simulation, SimulationError

UPLOAD_DIR  = BASE_DIR / "uploads"
JOBS_DIR    = BASE_DIR / "jobs"
ALLOWED_RTL = {".v", ".sv", ".zip"}
ALLOWED_TB  = {".v", ".sv"}
MAX_UPLOAD  = 50 * 1024 * 1024

UPLOAD_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "dev-only-secret-change-before-any-real-deployment"
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
CORS(app)  # the JSON /api/* routes below are meant to be called from a browser on a different origin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_job():
    jid  = uuid.uuid4().hex[:12]
    jdir = JOBS_DIR / jid
    jdir.mkdir(parents=True, exist_ok=True)
    return jid, jdir


def _save_upload(file, job_id, suffix=None):
    s    = suffix or Path(file.filename).suffix.lower()
    dest = UPLOAD_DIR / f"{job_id}{s}"
    file.save(dest)
    return dest


# ---------------------------------------------------------------------------
# Sprint 1 — Synthesis
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/synthesize", methods=["POST"])
def synthesize():
    uploaded_file = request.files.get("rtl_file")
    top_module    = request.form.get("top_module", "").strip() or None

    if not uploaded_file or not uploaded_file.filename:
        flash("Please choose a .v, .sv, or .zip file to upload.")
        return redirect(url_for("index"))

    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in ALLOWED_RTL:
        flash(f"Unsupported file type '{suffix}'. Upload a .v, .sv, or .zip file.")
        return redirect(url_for("index"))

    job_id, job_dir = _new_job()
    saved = _save_upload(uploaded_file, job_id)

    try:
        result = run_synthesis(input_path=saved, work_dir=job_dir, top_module=top_module)
    except SynthesisError as exc:
        flash(f"Synthesis error: {exc}")
        return redirect(url_for("index"))

    return render_template("result.html", job_id=job_id, result=result,
                           output=result.to_user_output())


# ---------------------------------------------------------------------------
# Sprint 2 — Area / Timing / Power Analysis
# ---------------------------------------------------------------------------

@app.route("/analyze/<job_id>", methods=["GET", "POST"])
def analyze(job_id):
    job_dir = (JOBS_DIR / job_id).resolve()
    if not str(job_dir).startswith(str(JOBS_DIR.resolve())):
        return "Invalid job id", 400

    syn_log = job_dir / "synthesis.log"
    netlist = job_dir / "netlist.v"

    if not syn_log.exists():
        flash("No synthesis.log found for this job. Run synthesis first.")
        return redirect(url_for("index"))

    liberty_path = None
    if request.method == "POST":
        lib_file = request.files.get("liberty_file")
        if lib_file and lib_file.filename:
            lib_dest = job_dir / "cell_library.lib"
            lib_file.save(lib_dest)
            liberty_path = lib_dest

    report = run_analysis(
        synthesis_log_path=syn_log,
        netlist_path=netlist,
        work_dir=job_dir,
        liberty_file=liberty_path,
    )

    return render_template("analysis.html", job_id=job_id, report=report,
                           output=report.to_user_output())


# ---------------------------------------------------------------------------
# Sprint 3 — Technology Mapping
# ---------------------------------------------------------------------------

@app.route("/techmap", methods=["GET", "POST"])
def techmap():
    if request.method == "GET":
        return render_template("techmap.html")

    uploaded_file = request.files.get("rtl_file")
    liberty_file  = request.files.get("liberty_file")
    top_module    = request.form.get("top_module", "").strip() or None

    if not uploaded_file or not uploaded_file.filename:
        flash("Please choose a .v, .sv, or .zip file to upload.")
        return redirect(url_for("techmap"))

    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in ALLOWED_RTL:
        flash(f"Unsupported file type '{suffix}'.")
        return redirect(url_for("techmap"))

    job_id, job_dir = _new_job()
    saved_rtl = _save_upload(uploaded_file, job_id)

    saved_lib = None
    if liberty_file and liberty_file.filename:
        saved_lib = job_dir / "cell_library.lib"
        liberty_file.save(saved_lib)

    try:
        result = run_techmap(
            input_path=saved_rtl,
            work_dir=job_dir,
            top_module=top_module,
            liberty_file=saved_lib,
        )
    except TechmapError as exc:
        flash(f"Technology mapping error: {exc}")
        return redirect(url_for("techmap"))

    return render_template("techmap_result.html", job_id=job_id, result=result,
                           output=result.to_user_output())


# ---------------------------------------------------------------------------
# Sprint 4 — Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    jobs = []
    for job_dir in sorted(JOBS_DIR.iterdir(), reverse=True):
        if not job_dir.is_dir():
            continue
        entry = {"job_id": job_dir.name, "type": None, "status": None, "cells": None}

        if (job_dir / "synthesis.log").exists():
            entry["type"]    = "Synthesis"
            entry["netlist"] = (job_dir / "netlist.v").exists()
            entry["status"]  = "✓ Success" if entry["netlist"] else "✗ Failed"
            ar = job_dir / "analysis_report.txt"
            if ar.exists():
                m = re.search(r"Cells\s+:\s+(\d+)", ar.read_text())
                entry["cells"] = int(m.group(1)) if m else None

        elif (job_dir / "techmap.log").exists():
            entry["type"]    = "Techmap"
            entry["netlist"] = (job_dir / "mapped_netlist.v").exists()
            entry["status"]  = "✓ Success" if entry["netlist"] else "✗ Failed"
            log  = (job_dir / "techmap.log").read_text(errors="ignore")
            ms   = re.findall(r"Number of cells:\s+(\d+)", log)
            entry["cells"] = int(ms[-1]) if ms else None

        elif (job_dir / "simulation.log").exists():
            entry["type"]   = "Simulation"
            entry["status"] = "✓ Success" if (job_dir / "view.vcd").exists() else "✗ Failed"

        else:
            continue

        jobs.append(entry)

    return render_template("dashboard.html", jobs=jobs)


# ---------------------------------------------------------------------------
# Simulation UI (Phase 1 Sprint 4)
# ---------------------------------------------------------------------------

@app.route("/simulate", methods=["GET", "POST"])
def simulate():
    if request.method == "GET":
        return render_template("simulate.html")

    rtl_file   = request.files.get("rtl_file")
    tb_file    = request.files.get("tb_file")
    top_module = request.form.get("top_module", "").strip() or None
    engine     = request.form.get("engine", "icarus")

    if not rtl_file or not rtl_file.filename:
        flash("Please upload an RTL file (.v, .sv, or .zip).")
        return redirect(url_for("simulate"))
    if not tb_file or not tb_file.filename:
        flash("Please upload a testbench file (.v or .sv).")
        return redirect(url_for("simulate"))

    tb_suffix = Path(tb_file.filename).suffix.lower()
    if tb_suffix not in ALLOWED_TB:
        flash(f"Testbench must be .v or .sv, got '{tb_suffix}'.")
        return redirect(url_for("simulate"))

    job_id, job_dir = _new_job()
    saved_rtl = _save_upload(rtl_file, job_id)
    tb_dest   = UPLOAD_DIR / f"{job_id}_tb{tb_suffix}"
    tb_file.save(tb_dest)

    try:
        result = run_simulation(
            input_path=saved_rtl,
            testbench_path=tb_dest,
            work_dir=job_dir,
            top_module=top_module,
            engine=engine,
        )
    except SimulationError as exc:
        flash(f"Simulation error: {exc}")
        return redirect(url_for("simulate"))

    return render_template("sim_result.html", job_id=job_id, result=result,
                           output=result.to_user_output())


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------

@app.route("/jobs/<job_id>/<filename>")
def download_artifact(job_id, filename):
    job_dir = (JOBS_DIR / job_id).resolve()
    if not str(job_dir).startswith(str(JOBS_DIR.resolve())):
        return "Invalid job id", 400
    return send_from_directory(job_dir, filename, as_attachment=True)


# ---------------------------------------------------------------------------
# JSON API — same runner functions as the HTML routes above, but returns
# jsonify(...) instead of render_template(...). Added so a separate
# frontend (phase2_new_frontend/console.html) calling this service from a
# different origin gets a JSON contract instead of an HTML page + flash
# message + redirect, which isn't something fetch() can usefully consume.
# Every /api/* route below is purely additive -- none of the HTML routes
# above were changed, so anything depending on the old form-submit flow
# keeps working exactly as it did.
# ---------------------------------------------------------------------------

@app.route("/api/synthesize", methods=["POST"])
def api_synthesize():
    uploaded_file = request.files.get("rtl_file")
    top_module = request.form.get("top_module", "").strip() or None

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"success": False, "error": "No rtl_file uploaded"}), 400

    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in ALLOWED_RTL:
        return jsonify({"success": False, "error": f"Unsupported file type '{suffix}'"}), 400

    job_id, job_dir = _new_job()
    saved = _save_upload(uploaded_file, job_id)

    try:
        result = run_synthesis(input_path=saved, work_dir=job_dir, top_module=top_module)
    except SynthesisError as exc:
        return jsonify({"success": False, "error": str(exc), "job_id": job_id}), 422

    output = result.to_user_output()
    output["job_id"] = job_id
    return jsonify({"success": result.success, **output})


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    rtl_file = request.files.get("rtl_file")
    tb_file = request.files.get("tb_file")
    top_module = request.form.get("top_module", "").strip() or None
    engine = request.form.get("engine", "icarus")

    if not rtl_file or not rtl_file.filename:
        return jsonify({"success": False, "error": "No rtl_file uploaded"}), 400
    if not tb_file or not tb_file.filename:
        return jsonify({"success": False, "error": "No tb_file uploaded"}), 400

    tb_suffix = Path(tb_file.filename).suffix.lower()
    if tb_suffix not in ALLOWED_TB:
        return jsonify({"success": False, "error": f"Testbench must be .v or .sv, got '{tb_suffix}'"}), 400

    job_id, job_dir = _new_job()
    saved_rtl = _save_upload(rtl_file, job_id)
    tb_dest = UPLOAD_DIR / f"{job_id}_tb{tb_suffix}"
    tb_file.save(tb_dest)

    try:
        result = run_simulation(
            input_path=saved_rtl, testbench_path=tb_dest, work_dir=job_dir,
            top_module=top_module, engine=engine,
        )
    except SimulationError as exc:
        return jsonify({"success": False, "error": str(exc), "job_id": job_id}), 422

    output = result.to_user_output()
    output["job_id"] = job_id
    return jsonify({"success": result.success, **output})


@app.route("/api/techmap", methods=["POST"])
def api_techmap():
    uploaded_file = request.files.get("rtl_file")
    liberty_file = request.files.get("liberty_file")
    top_module = request.form.get("top_module", "").strip() or None

    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"success": False, "error": "No rtl_file uploaded"}), 400

    suffix = Path(uploaded_file.filename).suffix.lower()
    if suffix not in ALLOWED_RTL:
        return jsonify({"success": False, "error": f"Unsupported file type '{suffix}'"}), 400

    job_id, job_dir = _new_job()
    saved_rtl = _save_upload(uploaded_file, job_id)

    saved_lib = None
    if liberty_file and liberty_file.filename:
        saved_lib = job_dir / "cell_library.lib"
        liberty_file.save(saved_lib)

    try:
        result = run_techmap(
            input_path=saved_rtl, work_dir=job_dir, top_module=top_module, liberty_file=saved_lib,
        )
    except TechmapError as exc:
        return jsonify({"success": False, "error": str(exc), "job_id": job_id}), 422

    output = result.to_user_output()
    output["job_id"] = job_id
    return jsonify({"success": result.success, **output})


@app.route("/api/analyze/<job_id>", methods=["POST"])
def api_analyze(job_id):
    job_dir = (JOBS_DIR / job_id).resolve()
    if not str(job_dir).startswith(str(JOBS_DIR.resolve())):
        return jsonify({"success": False, "error": "Invalid job id"}), 400

    syn_log = job_dir / "synthesis.log"
    netlist = job_dir / "netlist.v"
    if not syn_log.exists():
        return jsonify({"success": False, "error": "No synthesis.log for this job -- run synthesis first"}), 404

    liberty_path = None
    lib_file = request.files.get("liberty_file")
    if lib_file and lib_file.filename:
        lib_dest = job_dir / "cell_library.lib"
        lib_file.save(lib_dest)
        liberty_path = lib_dest

    report = run_analysis(
        synthesis_log_path=syn_log, netlist_path=netlist, work_dir=job_dir, liberty_file=liberty_path,
    )
    output = report.to_user_output()
    output["job_id"] = job_id
    return jsonify({"success": report.success, **output})


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "phase": 3, "name": "Synthesis Studio"})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
