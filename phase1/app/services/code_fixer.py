"""
app/services/code_fixer.py
-----------------------------
Sprint 1 (AI Code Fixing):
    - Issue-to-Patch Mapping   -> build_fix_prompt + call_ai
    - Diff Preview             -> generate_diff
    - One-click Apply          -> apply_fix (caller writes fixed_source to disk)
    - Re-lint after fix        -> lint_stub.run_lint on the fixed source

The AI is asked to return ONLY the full corrected file between sentinel
markers (more reliable to parse than asking for a raw unified diff back
from the model) -- this module then computes the diff itself with
difflib, so diff correctness doesn't depend on the model's diff-formatting
ability at all.
"""

from __future__ import annotations

import difflib

from app.schemas import CodeFixResult, LintIssue
from app.services.ai_client import call_ai
from app.services.ai_lint import run_ai_lint

_FIX_SENTINEL_START = "<<<FIXED_SOURCE>>>"
_FIX_SENTINEL_END = "<<<END_FIXED_SOURCE>>>"

_SYSTEM_PROMPT = (
    "You are an expert Verilog/SystemVerilog RTL engineer. You will be given "
    "a source file and a list of lint/bug-detection issues found in it. Fix "
    "every issue with the smallest correct change possible -- do not refactor "
    "unrelated code, rename signals, or change formatting outside what's "
    "needed to fix the listed issues. "
    f"Return the COMPLETE corrected file, and nothing else, between "
    f"{_FIX_SENTINEL_START} and {_FIX_SENTINEL_END} markers."
)


def build_fix_prompt(source: str, issues: list[LintIssue]) -> str:
    issue_lines = "\n".join(
        f"- [{issue.severity}] line {issue.line}, rule '{issue.rule}': {issue.message}"
        for issue in issues
    )
    return (
        f"Issues to fix:\n{issue_lines}\n\n"
        f"Source file:\n```\n{source}\n```"
    )


def _extract_fixed_source(ai_response: str, original_source: str) -> str:
    start = ai_response.find(_FIX_SENTINEL_START)
    end = ai_response.find(_FIX_SENTINEL_END)
    if start == -1 or end == -1:
        # Mock mode or a malformed response -- don't silently "fix" the file
        # with garbage. Caller sees fixed_source == original, diff is empty,
        # and relint_issue_count is unchanged, which is an honest result.
        return original_source
    return ai_response[start + len(_FIX_SENTINEL_START): end].strip("\n")


def generate_diff(original_source: str, fixed_source: str, file_path: str) -> str:
    diff_lines = difflib.unified_diff(
        original_source.splitlines(keepends=True),
        fixed_source.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    )
    return "".join(diff_lines)


def fix_and_relint(file_path: str, original_source: str, issues: list[LintIssue]) -> CodeFixResult:
    prompt = build_fix_prompt(original_source, issues)
    ai_response = call_ai(_SYSTEM_PROMPT, prompt)
    fixed_source = _extract_fixed_source(ai_response, original_source)

    diff = generate_diff(original_source, fixed_source, file_path)
    relint = run_ai_lint(fixed_source, file_path)

    return CodeFixResult(
        file_path=file_path,
        diff=diff,
        fixed_source=fixed_source,
        relint_issue_count=len(relint.issues),
        relint_issues=relint.issues,
    )
