"""
analysis_runner.py
------------------
Phase 3, Sprint 2 — Area / Timing / Power Analysis

Parses the Yosys `stat` output already captured in synthesis.log and runs
optional static timing analysis (STA) via OpenSTA when it is available on
the worker.  Falls back to a Yosys-only report if OpenSTA is absent.

Usage (as a library):
    from analysis_runner import run_analysis

    report = run_analysis(
        synthesis_log_path="jobs/abc123/synthesis.log",
        netlist_path="jobs/abc123/netlist.v",
        work_dir="jobs/abc123",
        liberty_file=None,          # None → timing skipped (no PDK wired yet)
    )
    if report.success:
        print(report.cell_count, report.wire_count, report.timing_met)
    else:
        print(report.error_message)
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class AnalysisReport:
    success: bool

    # --- Area (from Yosys stat) ---
    cell_count: Optional[int] = None
    wire_count: Optional[int] = None
    bit_count: Optional[int] = None        # total wire bits
    process_count: Optional[int] = None
    memory_count: Optional[int] = None

    # Per cell-type breakdown  {cell_name: count}
    cell_breakdown: Optional[dict] = None

    # --- Timing (OpenSTA, optional) ---
    timing_available: bool = False
    wns_ns: Optional[float] = None         # Worst Negative Slack
    tns_ns: Optional[float] = None         # Total Negative Slack
    timing_met: Optional[bool] = None

    # --- Power (OpenSTA, optional) ---
    power_available: bool = False
    leakage_power_mw: Optional[float] = None
    dynamic_power_mw: Optional[float] = None
    total_power_mw: Optional[float] = None

    # --- Meta ---
    report_path: Optional[Path] = None
    error_message: Optional[str] = None

    def to_user_output(self) -> dict:
        """Flat dict for the result dashboard."""
        if not self.success:
            return {"status": "Analysis Failed", "error": self.error_message}

        d: dict = {"status": "Analysis Complete"}

        # Area
        if self.cell_count is not None:
            d["cells"] = self.cell_count
        if self.wire_count is not None:
            d["wires"] = self.wire_count
        if self.bit_count is not None:
            d["wire_bits"] = self.bit_count
        if self.cell_breakdown:
            d["cell_breakdown"] = self.cell_breakdown

        # Timing
        d["timing_available"] = self.timing_available
        if self.timing_available:
            d["wns_ns"] = self.wns_ns
            d["tns_ns"] = self.tns_ns
            d["timing_met"] = self.timing_met

        # Power
        d["power_available"] = self.power_available
        if self.power_available:
            d["leakage_power_mw"] = self.leakage_power_mw
            d["dynamic_power_mw"] = self.dynamic_power_mw
            d["total_power_mw"] = self.total_power_mw

        return d


class AnalysisError(Exception):
    pass


# ---------------------------------------------------------------------------
# Yosys stat parsing
# ---------------------------------------------------------------------------

# Example Yosys stat output lines:
#   Number of wires:               47
#   Number of wire bits:          103
#   Number of cells:               28
#     $_AND_                        8
#     $_NOT_                        5
_STAT_RE = {
    "wire_count":    re.compile(r"Number of wires:\s+(\d+)"),
    "bit_count":     re.compile(r"Number of wire bits:\s+(\d+)"),
    "cell_count":    re.compile(r"Number of cells:\s+(\d+)"),
    "process_count": re.compile(r"Number of processes:\s+(\d+)"),
    "memory_count":  re.compile(r"Number of memories:\s+(\d+)"),
}
# Per-cell-type breakdown line:  "  $_AND_   8"
_CELL_TYPE_RE = re.compile(r"^\s{4}(\$\w+|\w+)\s+(\d+)\s*$")


def parse_yosys_stat(log_text: str) -> dict:
    """
    Extract the Yosys `stat` block from a synthesis log and return a
    flat dict of numeric fields plus a cell_breakdown sub-dict.
    Returns an empty dict if the stat block isn't found.
    """
    # Find the `stat` output section
    stat_start = log_text.rfind("=== ")        # Yosys stat starts with "=== <module> ==="
    if stat_start == -1:
        return {}

    stat_block = log_text[stat_start:]
    result: dict = {}

    for key, pattern in _STAT_RE.items():
        m = pattern.search(stat_block)
        if m:
            result[key] = int(m.group(1))

    breakdown: dict = {}
    for line in stat_block.splitlines():
        m = _CELL_TYPE_RE.match(line)
        if m:
            breakdown[m.group(1)] = int(m.group(2))
    if breakdown:
        result["cell_breakdown"] = breakdown

    return result


# ---------------------------------------------------------------------------
# OpenSTA timing / power (optional)
# ---------------------------------------------------------------------------

_STA_SCRIPT_TEMPLATE = """\
read_liberty {liberty}
read_verilog {netlist}
link_design {top}
report_wns
report_tns
report_power
"""

_WNS_RE  = re.compile(r"wns\s+([-\d.]+)", re.IGNORECASE)
_TNS_RE  = re.compile(r"tns\s+([-\d.]+)", re.IGNORECASE)
_PWR_RE  = re.compile(
    r"Total\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)",
    re.IGNORECASE,
)


def run_opensta(
    netlist_path: Path,
    liberty_file: Path,
    top_module: str,
    work_dir: Path,
    log_path: Path,
) -> dict:
    """
    Run OpenSTA (sta binary) and parse WNS/TNS/power from its output.
    Returns a dict with keys: wns_ns, tns_ns, timing_met,
    leakage_power_mw, dynamic_power_mw, total_power_mw.
    Raises AnalysisError on invocation failure.
    """
    script_text = _STA_SCRIPT_TEMPLATE.format(
        liberty=liberty_file,
        netlist=netlist_path,
        top=top_module,
    )
    script_path = work_dir / "sta.tcl"
    script_path.write_text(script_text)

    try:
        proc = subprocess.run(
            ["sta", "-exit", str(script_path)],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise AnalysisError("OpenSTA binary ('sta') not found — timing/power skipped.")
    except subprocess.TimeoutExpired:
        raise AnalysisError("OpenSTA timed out.")

    output = proc.stdout + "\n" + proc.stderr
    log_path.write_text(output)

    result: dict = {}

    m_wns = _WNS_RE.search(output)
    m_tns = _TNS_RE.search(output)
    if m_wns and m_tns:
        result["wns_ns"] = float(m_wns.group(1))
        result["tns_ns"] = float(m_tns.group(1))
        result["timing_met"] = result["wns_ns"] >= 0.0

    m_pwr = _PWR_RE.search(output)
    if m_pwr:
        # Columns: Internal  Switching  Leakage  Total  (all in mW typically)
        result["dynamic_power_mw"] = float(m_pwr.group(1)) + float(m_pwr.group(2))
        result["leakage_power_mw"] = float(m_pwr.group(3))
        result["total_power_mw"]   = float(m_pwr.group(4))

    return result


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def run_analysis(
    synthesis_log_path: str | Path,
    netlist_path: str | Path,
    work_dir: str | Path,
    top_module: Optional[str] = None,
    liberty_file: Optional[str | Path] = None,
) -> AnalysisReport:
    synthesis_log_path = Path(synthesis_log_path)
    netlist_path       = Path(netlist_path)
    work_dir           = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    report_path = work_dir / "analysis_report.txt"

    # --- Parse area from existing synthesis log ---
    if not synthesis_log_path.exists():
        return AnalysisReport(
            success=False,
            error_message=f"Synthesis log not found: {synthesis_log_path}",
        )

    log_text = synthesis_log_path.read_text(errors="ignore")
    stat     = parse_yosys_stat(log_text)

    if not stat:
        return AnalysisReport(
            success=False,
            error_message=(
                "Could not find Yosys stat output in synthesis.log. "
                "Ensure synthesis completed successfully before running analysis."
            ),
        )

    report = AnalysisReport(
        success=True,
        cell_count     = stat.get("cell_count"),
        wire_count     = stat.get("wire_count"),
        bit_count      = stat.get("bit_count"),
        process_count  = stat.get("process_count"),
        memory_count   = stat.get("memory_count"),
        cell_breakdown = stat.get("cell_breakdown"),
        report_path    = report_path,
    )

    # --- Optional timing / power via OpenSTA ---
    if liberty_file is not None:
        liberty_file = Path(liberty_file)
        sta_log = work_dir / "sta.log"
        try:
            sta_result = run_opensta(
                netlist_path  = netlist_path,
                liberty_file  = liberty_file,
                top_module    = top_module or "top",
                work_dir      = work_dir,
                log_path      = sta_log,
            )
            report.timing_available  = "wns_ns" in sta_result
            report.wns_ns            = sta_result.get("wns_ns")
            report.tns_ns            = sta_result.get("tns_ns")
            report.timing_met        = sta_result.get("timing_met")
            report.power_available   = "total_power_mw" in sta_result
            report.leakage_power_mw  = sta_result.get("leakage_power_mw")
            report.dynamic_power_mw  = sta_result.get("dynamic_power_mw")
            report.total_power_mw    = sta_result.get("total_power_mw")
        except AnalysisError:
            # OpenSTA absent — area report still valid, just skip timing/power
            pass

    # Write a human-readable summary
    _write_text_report(report, report_path)
    return report


def _write_text_report(report: AnalysisReport, path: Path) -> None:
    lines = [
        "AI Chip Studio — Phase 3 Analysis Report",
        "=" * 45,
        "",
        "[ Area (Yosys stat) ]",
        f"  Cells       : {report.cell_count}",
        f"  Wires       : {report.wire_count}",
        f"  Wire bits   : {report.bit_count}",
    ]
    if report.cell_breakdown:
        lines.append("")
        lines.append("  Cell breakdown:")
        for ctype, count in sorted(report.cell_breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"    {ctype:<20} {count}")

    if report.timing_available:
        lines += [
            "",
            "[ Timing (OpenSTA) ]",
            f"  WNS : {report.wns_ns:.3f} ns",
            f"  TNS : {report.tns_ns:.3f} ns",
            f"  Met : {'YES' if report.timing_met else 'NO — negative slack'}",
        ]

    if report.power_available:
        lines += [
            "",
            "[ Power (OpenSTA) ]",
            f"  Dynamic  : {report.dynamic_power_mw:.4f} mW",
            f"  Leakage  : {report.leakage_power_mw:.4f} mW",
            f"  Total    : {report.total_power_mw:.4f} mW",
        ]

    path.write_text("\n".join(lines) + "\n")
