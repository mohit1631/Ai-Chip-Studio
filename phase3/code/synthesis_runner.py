"""
synthesis_runner.py
--------------------
Sprint 1 — Synthesis Engine Foundation

Wraps Yosys to convert an RTL project (single Verilog/SystemVerilog file,
or a ZIP containing a multi-file project) into a gate-level netlist, while
capturing full synthesis logs.

Usage (CLI):
    python synthesis_runner.py path/to/design.v --top my_module
    python synthesis_runner.py path/to/project.zip --top my_module

Usage (as a library, e.g. from a FastAPI/Flask job handler):
    from synthesis_runner import run_synthesis

    result = run_synthesis(
        input_path="uploads/project.zip",
        work_dir="jobs/job_123",
        top_module="cpu_core",
    )
    if result.success:
        print(result.netlist_path, result.log_path)
    else:
        print(result.error_message)
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
# Result type
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class SynthesisResult:
    success: bool
    netlist_path: Optional[Path] = None
    log_path: Optional[Path] = None
    top_module: Optional[str] = None
    rtl_files: Optional[List[Path]] = None
    error_message: Optional[str] = None

    def to_user_output(self) -> dict:
        """Shape matching the Sprint 1 'User Output' dashboard mock."""
        if self.success:
            return {
                "status": "Synthesis Successful",
                "generated_files": [
                    str(self.netlist_path.name),
                    str(self.log_path.name),
                ],
            }
        return {
            "status": "Synthesis Failed",
            "error": self.error_message,
            "log_file": str(self.log_path.name) if self.log_path else None,
        }


class SynthesisError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Project staging: single file or ZIP multi-file project
# --------------------------------------------------------------------------- #

def stage_project(input_path: Path, work_dir: Path) -> List[Path]:
    """
    Prepares the RTL project inside work_dir and returns the list of
    RTL source files to feed to Yosys.

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

    raise SynthesisError(
        f"Unsupported input type '{input_path.suffix}'. "
        f"Expected a .v/.sv file or a .zip project."
    )


def _stage_zip_project(zip_path: Path, work_dir: Path) -> List[Path]:
    extract_dir = work_dir / "rtl_src"
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            # Guard against path traversal in malicious/zip-bomb archives.
            for member in zf.infolist():
                member_path = extract_dir / member.filename
                if not str(member_path.resolve()).startswith(str(extract_dir.resolve())):
                    raise SynthesisError("Unsafe path in ZIP archive, aborting.")
            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise SynthesisError(f"Could not read ZIP archive: {exc}") from exc

    rtl_files = sorted(
        p for p in extract_dir.rglob("*")
        if p.suffix.lower() in RTL_EXTENSIONS and p.is_file()
    )
    if not rtl_files:
        raise SynthesisError("ZIP project contained no .v/.sv RTL files.")

    return rtl_files


# --------------------------------------------------------------------------- #
# Top-module detection (best-effort fallback if user doesn't specify one)
# --------------------------------------------------------------------------- #

_MODULE_DECL_RE = re.compile(r"\bmodule\s+([A-Za-z_]\w*)")
_INSTANTIATION_RE = re.compile(r"\b([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*\(")


def guess_top_module(rtl_files: List[Path]) -> Optional[str]:
    """
    Heuristic: a module that is declared but never instantiated by any
    other module in the project is likely the top of the hierarchy.
    This is a fallback only — explicit user selection should always
    take priority in the UI.
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
# Yosys invocation
# --------------------------------------------------------------------------- #

def build_yosys_script(rtl_files: List[Path], top_module: str, netlist_out: Path) -> str:
    """
    Generates a Yosys command script for generic (technology-independent)
    synthesis. Swap the `synth` line for `synth_<pdk>` once a real
    standard-cell library is wired in (Sprint 3/4 dependency).
    """
    read_lines = "\n".join(f"read_verilog -sv {f}" for f in rtl_files)
    return f"""
{read_lines}
hierarchy -check -top {top_module}
proc
opt
fsm
memory
opt
synth -top {top_module}
opt_clean
write_verilog -noattr {netlist_out}
stat
"""


def run_yosys(script_text: str, work_dir: Path, log_path: Path) -> subprocess.CompletedProcess:
    script_path = work_dir / "run.ys"
    script_path.write_text(script_text)

    proc = subprocess.run(
        ["yosys", "-q", "-s", str(script_path)],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=300,  # hard cap so a malformed/huge design can't hang a worker
    )

    log_path.write_text(proc.stdout + "\n" + proc.stderr)
    return proc


# --------------------------------------------------------------------------- #
# Top-level orchestration
# --------------------------------------------------------------------------- #

def run_synthesis(
    input_path: str | Path,
    work_dir: str | Path,
    top_module: Optional[str] = None,
) -> SynthesisResult:
    input_path = Path(input_path)
    work_dir = Path(work_dir)
    log_path = work_dir / "synthesis.log"
    netlist_path = work_dir / "netlist.v"

    try:
        rtl_files = stage_project(input_path, work_dir)
    except SynthesisError as exc:
        return SynthesisResult(success=False, error_message=str(exc))

    if top_module is None:
        top_module = guess_top_module(rtl_files)
        if top_module is None:
            return SynthesisResult(
                success=False,
                rtl_files=rtl_files,
                error_message=(
                    "Could not unambiguously determine the top module. "
                    "Please specify one explicitly."
                ),
            )

    script_text = build_yosys_script(rtl_files, top_module, netlist_path)

    try:
        proc = run_yosys(script_text, work_dir, log_path)
    except FileNotFoundError:
        return SynthesisResult(
            success=False,
            log_path=log_path if log_path.exists() else None,
            error_message="Yosys binary not found on this worker. Check installation.",
        )
    except subprocess.TimeoutExpired:
        return SynthesisResult(
            success=False,
            log_path=log_path if log_path.exists() else None,
            error_message="Synthesis timed out (300s limit exceeded).",
        )

    if proc.returncode != 0 or not netlist_path.exists():
        return SynthesisResult(
            success=False,
            log_path=log_path,
            rtl_files=rtl_files,
            top_module=top_module,
            error_message="Yosys reported errors. See synthesis.log for details.",
        )

    return SynthesisResult(
        success=True,
        netlist_path=netlist_path,
        log_path=log_path,
        top_module=top_module,
        rtl_files=rtl_files,
    )


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Run Yosys synthesis on an RTL project.")
    parser.add_argument("input_path", help="Path to a .v/.sv file or a .zip project")
    parser.add_argument("--top", dest="top_module", default=None, help="Top module name")
    parser.add_argument("--work-dir", default="synthesis_job", help="Output/work directory")
    args = parser.parse_args()

    result = run_synthesis(args.input_path, args.work_dir, args.top_module)
    output = result.to_user_output()

    if result.success:
        print(output["status"])
        print("Generated Files:")
        for f in output["generated_files"]:
            print(f"✓ {f}")
    else:
        print(output["status"])
        print(f"Error: {output['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
