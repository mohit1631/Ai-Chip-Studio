"""
techmap_runner.py
-----------------
Phase 3, Sprint 3 — Technology Mapping

Extends the generic Yosys synthesis flow (Sprint 1) with standard-cell
technology mapping.  When a Liberty (.lib) file is provided, Yosys maps the
design to the target PDK's cell library using `synth` + `abc -liberty`
instead of the generic `synth` pass.

When no Liberty file is supplied the runner falls back transparently to the
Sprint 1 generic flow — so this module is a drop-in replacement for
synthesis_runner.py in the Flask app.

Usage (as a library):
    from techmap_runner import run_techmap

    result = run_techmap(
        input_path="uploads/design.v",
        work_dir="jobs/job_789",
        top_module="cpu_core",
        liberty_file="/pdk/sky130/sky130_fd_sc_hd__tt_025C_1v80.lib",
    )
    if result.success:
        print(result.netlist_path, result.mapped_cells)
"""

from __future__ import annotations

import dataclasses
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import List, Optional

RTL_EXTENSIONS = {".v", ".sv", ".vh", ".svh"}

TECHMAP_TIMEOUT_SECONDS = 600   # longer cap — ABC can be slow on big designs


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class TechmapResult:
    success: bool
    netlist_path: Optional[Path] = None
    log_path: Optional[Path] = None
    top_module: Optional[str] = None
    rtl_files: Optional[List[Path]] = None
    liberty_file: Optional[Path] = None
    mapped_cells: Optional[int] = None       # cell count post-mapping (from stat)
    error_message: Optional[str] = None

    def to_user_output(self) -> dict:
        if self.success:
            out = {
                "status": "Technology Mapping Successful",
                "top_module": self.top_module,
                "generated_files": [
                    str(self.netlist_path.name),
                    str(self.log_path.name),
                ],
            }
            if self.mapped_cells is not None:
                out["mapped_cells"] = self.mapped_cells
            if self.liberty_file:
                out["liberty"] = self.liberty_file.name
            else:
                out["note"] = "No Liberty file supplied — generic (tech-independent) netlist."
            return out
        return {
            "status": "Technology Mapping Failed",
            "error": self.error_message,
            "log_file": str(self.log_path.name) if self.log_path else None,
        }


class TechmapError(Exception):
    pass


# ---------------------------------------------------------------------------
# Project staging (mirrors synthesis_runner.py)
# ---------------------------------------------------------------------------

def stage_project(input_path: Path, work_dir: Path) -> List[Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    if input_path.suffix.lower() == ".zip":
        return _stage_zip_project(input_path, work_dir)
    if input_path.suffix.lower() in RTL_EXTENSIONS:
        dest = work_dir / input_path.name
        shutil.copy(input_path, dest)
        return [dest]
    raise TechmapError(
        f"Unsupported input type '{input_path.suffix}'. "
        "Expected a .v/.sv file or a .zip project."
    )


def _stage_zip_project(zip_path: Path, work_dir: Path) -> List[Path]:
    extract_dir = work_dir / "rtl_src"
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                member_path = extract_dir / member.filename
                if not str(member_path.resolve()).startswith(str(extract_dir.resolve())):
                    raise TechmapError("Unsafe path in ZIP archive, aborting.")
            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise TechmapError(f"Could not read ZIP archive: {exc}") from exc

    rtl_files = sorted(
        p for p in extract_dir.rglob("*")
        if p.suffix.lower() in RTL_EXTENSIONS and p.is_file()
    )
    if not rtl_files:
        raise TechmapError("ZIP project contained no .v/.sv RTL files.")
    return rtl_files


# ---------------------------------------------------------------------------
# Top-module detection (same heuristic as synthesis_runner.py)
# ---------------------------------------------------------------------------

_MODULE_DECL_RE    = re.compile(r"\bmodule\s+([A-Za-z_]\w*)")
_INSTANTIATION_RE  = re.compile(r"\b([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*\(")


def guess_top_module(rtl_files: List[Path]) -> Optional[str]:
    declared, instantiated = set(), set()
    for f in rtl_files:
        text = f.read_text(errors="ignore")
        declared.update(_MODULE_DECL_RE.findall(text))
        instantiated.update(_INSTANTIATION_RE.findall(text))
    candidates = declared - instantiated
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


# ---------------------------------------------------------------------------
# Yosys script generation
# ---------------------------------------------------------------------------

def build_yosys_script(
    rtl_files: List[Path],
    top_module: str,
    netlist_out: Path,
    liberty_file: Optional[Path],
) -> str:
    """
    Generates a Yosys command script.

    - With a Liberty file: uses `synth` + `dfflibmap` + `abc -liberty` for
      standard-cell mapping, then `write_verilog` in a cell-library-aware way.
    - Without:            falls back to the generic Sprint 1 flow so callers
      don't need to check which runner to call.
    """
    read_lines = "\n".join(f"read_verilog -sv {f}" for f in rtl_files)

    if liberty_file:
        # Technology-mapped flow
        return f"""
{read_lines}
hierarchy -check -top {top_module}
proc
opt
fsm
memory
opt
synth -top {top_module}
dfflibmap -liberty {liberty_file}
abc -liberty {liberty_file}
opt_clean
write_verilog -noattr {netlist_out}
stat -liberty {liberty_file}
"""
    else:
        # Generic fallback (same as Sprint 1)
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


# ---------------------------------------------------------------------------
# Yosys invocation
# ---------------------------------------------------------------------------

def run_yosys(script_text: str, work_dir: Path, log_path: Path) -> subprocess.CompletedProcess:
    script_path = work_dir / "techmap.ys"
    script_path.write_text(script_text)

    proc = subprocess.run(
        ["yosys", "-q", "-s", str(script_path)],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=TECHMAP_TIMEOUT_SECONDS,
    )
    log_path.write_text(proc.stdout + "\n" + proc.stderr)
    return proc


# ---------------------------------------------------------------------------
# Mapped cell count from log
# ---------------------------------------------------------------------------

_CELL_COUNT_RE = re.compile(r"Number of cells:\s+(\d+)")


def parse_mapped_cell_count(log_text: str) -> Optional[int]:
    # Use the last match — that's the post-mapping stat
    matches = _CELL_COUNT_RE.findall(log_text)
    return int(matches[-1]) if matches else None


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def run_techmap(
    input_path: str | Path,
    work_dir: str | Path,
    top_module: Optional[str] = None,
    liberty_file: Optional[str | Path] = None,
) -> TechmapResult:
    input_path  = Path(input_path)
    work_dir    = Path(work_dir)
    log_path    = work_dir / "techmap.log"
    netlist_path = work_dir / "mapped_netlist.v"
    liberty_path = Path(liberty_file) if liberty_file else None

    try:
        rtl_files = stage_project(input_path, work_dir)
    except TechmapError as exc:
        return TechmapResult(success=False, error_message=str(exc))

    if top_module is None:
        top_module = guess_top_module(rtl_files)
        if top_module is None:
            return TechmapResult(
                success=False,
                rtl_files=rtl_files,
                error_message=(
                    "Could not unambiguously determine the top module. "
                    "Please specify one explicitly."
                ),
            )

    script_text = build_yosys_script(rtl_files, top_module, netlist_path, liberty_path)

    try:
        proc = run_yosys(script_text, work_dir, log_path)
    except FileNotFoundError:
        return TechmapResult(
            success=False,
            log_path=log_path if log_path.exists() else None,
            error_message="Yosys binary not found on this worker. Check installation.",
        )
    except subprocess.TimeoutExpired:
        return TechmapResult(
            success=False,
            log_path=log_path if log_path.exists() else None,
            error_message=f"Technology mapping timed out ({TECHMAP_TIMEOUT_SECONDS}s limit exceeded).",
        )

    if proc.returncode != 0 or not netlist_path.exists():
        return TechmapResult(
            success=False,
            log_path=log_path,
            rtl_files=rtl_files,
            top_module=top_module,
            error_message="Yosys reported errors. See techmap.log for details.",
        )

    log_text    = log_path.read_text(errors="ignore")
    mapped_cells = parse_mapped_cell_count(log_text)

    return TechmapResult(
        success=True,
        netlist_path=netlist_path,
        log_path=log_path,
        top_module=top_module,
        rtl_files=rtl_files,
        liberty_file=liberty_path,
        mapped_cells=mapped_cells,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Run Yosys technology mapping on an RTL project."
    )
    parser.add_argument("input_path",   help="Path to a .v/.sv file or a .zip project")
    parser.add_argument("--top",        dest="top_module",   default=None)
    parser.add_argument("--liberty",    dest="liberty_file", default=None,
                        help="Liberty (.lib) file for standard-cell mapping")
    parser.add_argument("--work-dir",   default="techmap_job")
    args = parser.parse_args()

    result = run_techmap(args.input_path, args.work_dir, args.top_module, args.liberty_file)
    output = result.to_user_output()

    if result.success:
        print(output["status"])
        if "mapped_cells" in output:
            print(f"Mapped Cells : {output['mapped_cells']}")
        print("Generated Files:")
        for f in output["generated_files"]:
            print(f"  ✓ {f}")
        if "note" in output:
            print(f"Note: {output['note']}")
    else:
        print(output["status"])
        print(f"Error: {output['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
