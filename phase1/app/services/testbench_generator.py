"""
app/services/testbench_generator.py
--------------------------------------
Sprint 2 (AI Testbench Generation):
    - Port/Interface Inference from RTL  -> parse_ports (real, regex-based)
    - Directed Test Generation           -> delegated to the AI prompt
    - Basic Randomized Stimulus          -> delegated to the AI prompt
    - Self-checking Assertions (basic)   -> delegated to the AI prompt

Per the roadmap note: "Quality depends entirely on the AI correctly
inferring intended behavior -- see the cross-phase risks doc for why this
needs an independent correctness check." This module does not add that
independent check (that's the formal-equivalence / confidence-surfacing
work flagged in 07_cross_phase_risks_and_recommendations.md) -- it only
generates the testbench.
"""

from __future__ import annotations

import re

_MODULE_HEADER_RE = re.compile(r"module\s+(\w+)\s*(?:#\([^)]*\))?\s*\((.*?)\);", re.DOTALL)
_PORT_RE = re.compile(
    r"\b(input|output|inout)\b\s*(?:reg|wire|logic)?\s*(?:\[[^\]]*\])?\s*(\w+)"
)

TESTBENCH_SYSTEM_PROMPT = (
    "You are an expert Verilog/SystemVerilog verification engineer. Given a "
    "module's port list and an optional behavioral spec, generate a complete, "
    "runnable, self-checking testbench. Include: a clock/reset generator if "
    "the ports suggest a clocked design, at least one directed test sequence, "
    "basic randomized stimulus for remaining input combinations, and "
    "self-checking assertions (SystemVerilog `assert` or `$error` checks) "
    "that report pass/fail counts at $finish. Return ONLY the testbench code, "
    "no explanation."
)


def parse_ports(source: str, top_module: str | None = None) -> tuple[str, list[str]]:
    """
    Extracts the top module name (if not given) and its port list using a
    regex match against module headers -- same class of heuristic as
    code/synthesis_runner.py's guess_top_module, intentionally simple.
    A real implementation should use a proper Verilog/SV parser once one is
    wired in; this is enough to drive prompt construction for now.

    Handles files containing more than one module declaration (common --
    e.g. a small helper module defined above the main one) by scanning all
    matches rather than just the first, since the testbench target isn't
    always the first module textually in the file.
    """
    matches = list(_MODULE_HEADER_RE.finditer(source))
    if not matches:
        raise ValueError("Could not find a module declaration in the source.")

    if top_module:
        for match in matches:
            if match.group(1) == top_module:
                ports = [f"{direction} {name}" for direction, name in _PORT_RE.findall(match.group(2))]
                return top_module, ports
        found_names = ", ".join(m.group(1) for m in matches)
        raise ValueError(f"Requested top_module '{top_module}' not found. Modules in file: {found_names}.")

    if len(matches) > 1:
        found_names = ", ".join(m.group(1) for m in matches)
        raise ValueError(
            f"File contains multiple modules ({found_names}) -- specify top_module explicitly."
        )

    match = matches[0]
    ports = [f"{direction} {name}" for direction, name in _PORT_RE.findall(match.group(2))]
    return match.group(1), ports


def build_testbench_prompt(module_name: str, ports: list[str], source: str, spec_text: str | None) -> str:
    port_list = "\n".join(f"  - {p}" for p in ports) or "  (no ports detected)"
    spec_section = f"\nBehavioral spec (natural language):\n{spec_text}\n" if spec_text else ""
    return (
        f"Module under test: {module_name}\n"
        f"Ports:\n{port_list}\n"
        f"{spec_section}\n"
        f"Full module source for reference:\n```\n{source}\n```"
    )
