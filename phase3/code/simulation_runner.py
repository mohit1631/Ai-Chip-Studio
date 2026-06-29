"""
simulation_runner.py
---------------------
Phase 1, Sprint 4 — Simulation Engine

Wraps Verilator and Icarus Verilog to run an RTL project (single Verilog/
SystemVerilog file, or a ZIP containing a multi-file project) against a
testbench, capturing pass/fail assertion counts, simulation logs, and a
VCD waveform for the in-browser viewer.

Shares the same project-staging contract as code/synthesis_runner.py
(Phase 3) — both accept a single file or a ZIP project and stage it the
same way — so the two modules can sit behind the same upload endpoint.

Usage (CLI):
    python simulation_runner.py path/to/design.v tb.v --top tb_top --engine verilator
    python simulation_runner.py path/to/project.zip tb.v --top tb_top --engine icarus

Usage (as a library, e.g. from a FastAPI/Flask job handler):
    from simulation_runner import run_simulation

    result = run_simulation(
        input_path="uploads/project.zip",
        testbench_path="uploads/tb_top.sv",
        work_dir="jobs/job_456",
        top_module="tb_top",
        engine="verilator",
    )
    if result.success:
        print(result.assertions_passed, result.assertions_failed, result.vcd_path)
    else:
        print(result.error_message)

Security notes (see 11_security_roadmap.md, Phase 1 Security):
    - ZIP-slip protection on extraction (same pattern as synthesis_runner.py)
    - Max ZIP size / max file count / max extracted size enforced BEFORE
      extraction, so a ZIP bomb is rejected without ever being unpacked
    - Hard subprocess timeout so a non-terminating testbench (e.g. an
      unconstrained `while(1)` simulation loop) can't hang a worker
    - This module assumes it is already running inside an isolated,
      resource-capped Docker worker (CPU/RAM caps applied at the container
      level, not here) per the Worker Isolation requirement in Phase 1
      Security — it does not attempt to sandbox itself
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import List, Optional

RTL_EXTENSIONS = {".v", ".sv", ".vh", ".svh"}

# --------------------------------------------------------------------------- #
# Security limits (Phase 1 Security — see 11_security_roadmap.md)
# --------------------------------------------------------------------------- #

MAX_ZIP_SIZE_BYTES = 50 * 1024 * 1024          # 50 MB compressed
MAX_ZIP_FILE_COUNT = 500                        # max member count
MAX_EXTRACTED_SIZE_BYTES = 500 * 1024 * 1024    # 500 MB uncompressed, sum of all members

SIM_TIMEOUT_SECONDS = 300  # hard cap so a non-terminating testbench can't hang a worker


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class SimulationResult:
    success: bool
    engine: Optional[str] = None
    log_path: Optional[Path] = None
    vcd_path: Optional[Path] = None
    top_module: Optional[str] = None
    rtl_files: Optional[List[Path]] = None
    sim_time_seconds: Optional[float] = None
    assertions_passed: Optional[int] = None
    assertions_failed: Optional[int] = None
    error_message: Optional[str] = None

    def to_user_output(self) -> dict:
        """Shape matching the Sprint 4 'User Dashboard' mock."""
        if self.success:
            return {
                "status": "Simulation Complete",
                "engine": self.engine,
                "sim_time": f"{self.sim_time_seconds:.1f}s" if self.sim_time_seconds is not None else None,
                "assertions": f"{self.assertions_passed} passed / {self.assertions_failed} failed",
                "waveform": str(self.vcd_path.name) if self.vcd_path else None,
            }
        return {
            "status": "Simulation Failed",
            "error": self.error_message,
            "log_file": str(self.log_path.name) if self.log_path else None,
        }


class SimulationError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Project staging: single file or ZIP multi-file project
#
# Mirrors code/synthesis_runner.py's stage_project()/_stage_zip_project(),
# with the size/count caps from Phase 1 Security added on top.
# --------------------------------------------------------------------------- #

def stage_project(input_path: Path, work_dir: Path) -> List[Path]:
    """
    Prepares the RTL project inside work_dir and returns the list of
    RTL source files to feed to the simulator.

    Supports:
      - a single .v/.sv file
      - a .zip archive containing one or more .v/.sv files (any directory depth)
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    if input_path.suffix.lower() == ".zip":
        return _stage_zip_project(input_path, work_dir)

    if input_path.suffix.lower() in RTL_EXTENSIONS:
        dest = work_dir / input_path.name
        shutil.copy(input_path, dest)
        return [dest]

    raise SimulationError(
        f"Unsupported input type '{input_path.suffix}'. "
        f"Expected a .v/.sv file or a .zip project."
    )


def _stage_zip_project(zip_path: Path, work_dir: Path) -> List[Path]:
    # --- Cap 1: reject oversized archives before ever opening them --- #
    zip_size = zip_path.stat().st_size
    if zip_size > MAX_ZIP_SIZE_BYTES:
        raise SimulationError(
            f"ZIP archive too large ({zip_size / 1024 / 1024:.1f} MB). "
            f"Max allowed is {MAX_ZIP_SIZE_BYTES / 1024 / 1024:.0f} MB."
        )

    extract_dir = work_dir / "rtl_src"
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            infolist = zf.infolist()

            # --- Cap 2: reject archives with too many members --- #
            if len(infolist) > MAX_ZIP_FILE_COUNT:
                raise SimulationError(
                    f"ZIP archive contains too many files ({len(infolist)}). "
                    f"Max allowed is {MAX_ZIP_FILE_COUNT}."
                )

            # --- Cap 3: reject ZIP bombs by checking *uncompressed* size
            #     BEFORE extraction. Compressed size alone (Cap 1) doesn't
            #     reveal decompressed size, so this check is mandatory. --- #
            total_uncompressed = sum(member.file_size for member in infolist)
            if total_uncompressed > MAX_EXTRACTED_SIZE_BYTES:
                raise SimulationError(
                    f"ZIP archive would extract to "
                    f"{total_uncompressed / 1024 / 1024:.1f} MB, exceeding the "
                    f"{MAX_EXTRACTED_SIZE_BYTES / 1024 / 1024:.0f} MB limit."
                )

            # --- ZIP-slip / path traversal guard (same pattern as
            #     synthesis_runner.py) --- #
            for member in infolist:
                member_path = extract_dir / member.filename
                if not str(member_path.resolve()).startswith(str(extract_dir.resolve())):
                    raise SimulationError("Unsafe path in ZIP archive, aborting.")

            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise SimulationError(f"Could not read ZIP archive: {exc}") from exc

    rtl_files = sorted(
        p for p in extract_dir.rglob("*")
        if p.suffix.lower() in RTL_EXTENSIONS and p.is_file()
    )
    if not rtl_files:
        raise SimulationError("ZIP project contained no .v/.sv RTL files.")

    return rtl_files


# --------------------------------------------------------------------------- #
# Top-module detection (best-effort fallback if user doesn't specify one)
#
# Same heuristic as synthesis_runner.py's guess_top_module(), reused here
# so Sprint 3's "ambiguous top -> ask the user" behavior is consistent
# across both the synthesis and simulation flows.
# --------------------------------------------------------------------------- #

_MODULE_DECL_RE = re.compile(r"\bmodule\s+([A-Za-z_]\w*)")
_INSTANTIATION_RE = re.compile(r"\b([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*\(")


def guess_top_module(rtl_files: List[Path]) -> Optional[str]:
    """
    Heuristic: a module that is declared but never instantiated by any
    other module in the project is likely the top of the hierarchy.
    For simulation, this is usually the testbench module itself — this
    is a fallback only; explicit user selection should always take
    priority in the UI.
    """
    declared, instantiated = set(), set()

    for f in rtl_files:
        text = f.read_text(errors="ignore")
        declared.update(_MODULE_DECL_RE.findall(text))
        instantiated.update(_INSTANTIATION_RE.findall(text))

    candidates = declared - instantiated
    if len(candidates) == 1:
        return next(iter(candidates))
    return None  # ambiguous — caller must ask the user


# --------------------------------------------------------------------------- #
# Assertion result parsing
# --------------------------------------------------------------------------- #

# Matches common SVA/$display-style pass/fail summary lines from both
# Verilator and Icarus output, e.g.:
#   "12 passed / 0 failed"
#   "PASS: 12  FAIL: 0"
_PASS_FAIL_RE = re.compile(
    r"(\d+)\s*(?:passed|PASS(?:ED)?)\D+(\d+)\s*(?:failed|FAIL(?:ED)?)",
    re.IGNORECASE,
)


def parse_assertion_results(log_text: str) -> tuple[int, int]:
    """
    Best-effort parse of a simulation log for assertion pass/fail counts.
    Returns (passed, failed). Defaults to (0, 0) if no recognizable
    summary line is found — callers should treat that as "unknown",
    not "zero assertions ran", and surface the raw log rather than a
    bare 0/0 on the dashboard.
    """
    match = _PASS_FAIL_RE.search(log_text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


# --------------------------------------------------------------------------- #
# Verilator invocation
# --------------------------------------------------------------------------- #

def run_verilator(
    rtl_files: List[Path],
    testbench_path: Path,
    top_module: str,
    work_dir: Path,
    log_path: Path,
    vcd_path: Path,
) -> subprocess.CompletedProcess:
    """
    Builds and runs a Verilator simulation. Verilator requires a build
    step (verilate -> compile -> execute), so this wraps `verilator
    --binary` which handles build+link in one invocation, with tracing
    enabled for VCD output.
    """
    all_sources = [str(f) for f in rtl_files] + [str(testbench_path)]

    build_cmd = [
        "verilator",
        "--binary",          # build an executable directly
        "--trace",           # enable VCD waveform dumping
        "--top-module", top_module,
        "-o", "sim.out",
        *all_sources,
    ]

    proc = subprocess.run(
        build_cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=SIM_TIMEOUT_SECONDS,
    )

    log_text = proc.stdout + "\n" + proc.stderr

    if proc.returncode == 0:
        # Verilator places the built binary under obj_dir/ by default
        binary_path = work_dir / "obj_dir" / "sim.out"
        if binary_path.exists():
            run_proc = subprocess.run(
                [str(binary_path)],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=SIM_TIMEOUT_SECONDS,
            )
            log_text += "\n" + run_proc.stdout + "\n" + run_proc.stderr
            proc = run_proc

            # Verilator writes the trace file relative to cwd; normalize
            # to the expected vcd_path if the testbench dumped one.
            default_vcd = work_dir / "sim.vcd"
            if default_vcd.exists() and default_vcd != vcd_path:
                shutil.move(str(default_vcd), str(vcd_path))

    log_path.write_text(log_text)
    return proc


# --------------------------------------------------------------------------- #
# Icarus Verilog invocation
# --------------------------------------------------------------------------- #

def run_icarus(
    rtl_files: List[Path],
    testbench_path: Path,
    top_module: str,
    work_dir: Path,
    log_path: Path,
    vcd_path: Path,
) -> subprocess.CompletedProcess:
    """
    Builds and runs an Icarus Verilog simulation via iverilog + vvp.
    Broader language support than Verilator, slower execution — used
    as the fallback engine per Sprint 4's architecture.
    """
    all_sources = [str(f) for f in rtl_files] + [str(testbench_path)]
    compiled_path = work_dir / "sim.vvp"

    compile_cmd = [
        "iverilog",
        "-g2012",            # SystemVerilog-2012 support
        "-s", top_module,
        "-o", str(compiled_path),
        *all_sources,
    ]

    proc = subprocess.run(
        compile_cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=SIM_TIMEOUT_SECONDS,
    )

    log_text = proc.stdout + "\n" + proc.stderr

    if proc.returncode == 0 and compiled_path.exists():
        run_proc = subprocess.run(
            ["vvp", str(compiled_path)],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=SIM_TIMEOUT_SECONDS,
        )
        log_text += "\n" + run_proc.stdout + "\n" + run_proc.stderr
        proc = run_proc

        # Icarus testbenches typically $dumpfile("sim.vcd") themselves;
        # normalize to the expected path if a default name was used.
        default_vcd = work_dir / "sim.vcd"
        if default_vcd.exists() and default_vcd != vcd_path:
            shutil.move(str(default_vcd), str(vcd_path))

    log_path.write_text(log_text)
    return proc


# --------------------------------------------------------------------------- #
# Top-level orchestration
# --------------------------------------------------------------------------- #

ENGINE_RUNNERS = {
    "verilator": run_verilator,
    "icarus": run_icarus,
}


def run_simulation(
    input_path: str | Path,
    testbench_path: str | Path,
    work_dir: str | Path,
    top_module: Optional[str] = None,
    engine: str = "verilator",
) -> SimulationResult:
    input_path = Path(input_path)
    testbench_path = Path(testbench_path)
    work_dir = Path(work_dir)
    log_path = work_dir / "simulation.log"
    vcd_path = work_dir / "view.vcd"

    if engine not in ENGINE_RUNNERS:
        return SimulationResult(
            success=False,
            error_message=f"Unknown engine '{engine}'. Expected one of: {list(ENGINE_RUNNERS)}.",
        )

    try:
        rtl_files = stage_project(input_path, work_dir)
    except SimulationError as exc:
        return SimulationResult(success=False, engine=engine, error_message=str(exc))

    work_dir.mkdir(parents=True, exist_ok=True)
    staged_tb = work_dir / testbench_path.name
    if testbench_path.resolve() != staged_tb.resolve():
        shutil.copy(testbench_path, staged_tb)

    if top_module is None:
        # Include the testbench itself as a candidate — for sim, the
        # top is almost always the testbench module, not an RTL module.
        top_module = guess_top_module(rtl_files + [staged_tb])
        if top_module is None:
            return SimulationResult(
                success=False,
                engine=engine,
                rtl_files=rtl_files,
                error_message=(
                    "Could not unambiguously determine the top (testbench) "
                    "module. Please specify one explicitly."
                ),
            )

    runner = ENGINE_RUNNERS[engine]

    try:
        import time
        start = time.monotonic()
        proc = runner(rtl_files, staged_tb, top_module, work_dir, log_path, vcd_path)
        elapsed = time.monotonic() - start
    except FileNotFoundError:
        return SimulationResult(
            success=False,
            engine=engine,
            log_path=log_path if log_path.exists() else None,
            error_message=f"'{engine}' binary not found on this worker. Check installation.",
        )
    except subprocess.TimeoutExpired:
        return SimulationResult(
            success=False,
            engine=engine,
            log_path=log_path if log_path.exists() else None,
            error_message=f"Simulation timed out ({SIM_TIMEOUT_SECONDS}s limit exceeded).",
        )

    log_text = log_path.read_text() if log_path.exists() else ""
    passed, failed = parse_assertion_results(log_text)

    if proc.returncode != 0:
        return SimulationResult(
            success=False,
            engine=engine,
            log_path=log_path,
            rtl_files=rtl_files,
            top_module=top_module,
            sim_time_seconds=elapsed,
            error_message=f"{engine} reported errors. See simulation.log for details.",
        )

    return SimulationResult(
        success=True,
        engine=engine,
        log_path=log_path,
        vcd_path=vcd_path if vcd_path.exists() else None,
        rtl_files=rtl_files,
        top_module=top_module,
        sim_time_seconds=elapsed,
        assertions_passed=passed,
        assertions_failed=failed,
    )


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a Verilator or Icarus Verilog simulation on an RTL project."
    )
    parser.add_argument("input_path", help="Path to a .v/.sv file or a .zip project (RTL)")
    parser.add_argument("testbench_path", help="Path to the testbench .v/.sv file")
    parser.add_argument("--top", dest="top_module", default=None, help="Top (testbench) module name")
    parser.add_argument(
        "--engine",
        choices=list(ENGINE_RUNNERS),
        default="verilator",
        help="Simulation engine to use (default: verilator)",
    )
    parser.add_argument("--work-dir", default="simulation_job", help="Output/work directory")
    args = parser.parse_args()

    result = run_simulation(
        args.input_path,
        args.testbench_path,
        args.work_dir,
        args.top_module,
        args.engine,
    )
    output = result.to_user_output()

    if result.success:
        print(output["status"])
        print(f"Engine     : {output['engine']}")
        print(f"Sim Time   : {output['sim_time']}")
        print(f"Assertions : {output['assertions']}")
        print(f"Waveform   : {output['waveform']}")
    else:
        print(output["status"])
        print(f"Error: {output['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
