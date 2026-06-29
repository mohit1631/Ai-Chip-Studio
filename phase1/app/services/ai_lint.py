"""
app/services/ai_lint.py
---------------------------
Weak Area #3: "AI Layer Still Missing". The roadmap lists AI RTL Review and
AI Bug Detection as Phase 1 surface area; the original skeleton only had a
deterministic regex stub standing in for them (see _regex_fallback below --
kept, but now demoted to fallback rather than being the whole
implementation).

This module calls the real model (via app.services.ai_client, same
Anthropic wrapper Sprint 1/2 already use) and asks it to return structured
JSON issues. If no API key is configured, or the model's response can't be
parsed as the expected JSON shape, it falls back to the old regex
heuristic rather than silently returning nothing -- a degraded lint is
better than a missing one, but the caller can tell the difference via
`AILintResult.source`.

Every LintIssue also carries `status` ("predicted" | "confirmed"), the
same evidence discipline as phase4's AIInsight: an AI-claimed issue stays
`predicted` until something deterministic backs it up, while the regex
fallback's matches are `confirmed` immediately since a regex match isn't
a probabilistic claim.
"""

from __future__ import annotations

import json
import re

from app.schemas import LintIssue
from app.services.ai_client import MOCK_MODE, call_ai

SYSTEM_PROMPT = (
    "You are an expert Verilog/SystemVerilog RTL reviewer performing automated "
    "bug detection (latches, blocking/non-blocking assignment misuse, width "
    "mismatches, unintended combinational loops, missing default cases, "
    "X-propagation risks, and similar synthesis/simulation-mismatch bugs). "
    "Review the given source and return ONLY a JSON array (no markdown "
    "fences, no prose) where each element is "
    '{"rule": string, "message": string, "line": integer, "severity": '
    '"error"|"warning"|"info"}. Return an empty array [] if you find nothing.'
)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)


class AILintResult:
    def __init__(self, issues: list[LintIssue], source: str):
        self.issues = issues
        self.source = source  # "ai" | "regex_fallback"


def _regex_fallback(source: str) -> list[LintIssue]:
    """
    The original deterministic heuristic, used only when a real AI call
    isn't available or its output can't be parsed. Same two checks as
    before: latch risk (if w/o else in always_comb) and blocking
    assignment inside always_ff (correctly excluding <=, ==, >=, !=).
    """
    issues: list[LintIssue] = []

    has_if = bool(re.search(r"\bif\s*\(", source))
    has_else = bool(re.search(r"\belse\b", source))
    if has_if and not has_else and "always_comb" in source:
        issues.append(
            LintIssue(
                rule="latch-inferred",
                message="Possible latch inferred (if without else in combinational block)",
                line=1,
                severity="warning",
                status="confirmed",  # deterministic regex match, not an AI claim
            )
        )

    blocking_re = re.compile(
        r"always_ff\b.*?\bbegin\b(?:(?!\bend\b).)*?(?<!<)(?<!!)(?<!>)(?<!=)=(?!=)", re.DOTALL
    )
    if blocking_re.search(source):
        issues.append(
            LintIssue(
                rule="blocking-assign-in-seq",
                message="Blocking assignment (=) used inside always_ff; prefer <=",
                line=1,
                severity="error",
                status="confirmed",  # deterministic regex match, not an AI claim
            )
        )

    return issues


def _parse_ai_issues(raw_response: str) -> list[LintIssue] | None:
    text = raw_response.strip()
    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1)

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return None
        return [LintIssue(**item) for item in parsed]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def run_ai_lint(source: str, file_path: str = "") -> AILintResult:
    """
    Real AI RTL Review / Bug Detection. Falls back to the regex heuristic
    if no API key is configured (MOCK_MODE) or the model's response
    doesn't parse as the expected JSON shape.
    """
    if MOCK_MODE:
        return AILintResult(_regex_fallback(source), source="regex_fallback")

    user_prompt = f"File: {file_path or '(unnamed)'}\n\nSource:\n```\n{source}\n```"
    raw_response = call_ai(SYSTEM_PROMPT, user_prompt, max_tokens=1500)

    issues = _parse_ai_issues(raw_response)
    if issues is None:
        return AILintResult(_regex_fallback(source), source="regex_fallback")
    return AILintResult(issues, source="ai")
