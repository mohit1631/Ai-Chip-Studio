"""
tests/test_ai_lint.py
-----------------------
Covers app/services/ai_lint.py's regex-fallback path (deterministic, no
network/API key needed) and the predicted/confirmed status field added
to LintIssue.

Also a narrow regression test for a real bug found while doing this work:
app/services/ai_lint.py does `from app.services.ai_client import
MOCK_MODE, call_ai`, but ai_client.py never defined MOCK_MODE at all --
that's an ImportError at module-import time, which means it would have
taken down the whole app (main.py -> routers -> tasks.py -> ai_lint.py
import chain) the moment anything tried to import app.main. Fixed by
adding MOCK_MODE to ai_client.py; this test exists so a future edit that
removes/renames it again fails loudly in CI instead of silently at
startup.
"""
from __future__ import annotations


def test_ai_client_defines_mock_mode():
    """Regression test: ai_lint.py imports MOCK_MODE from ai_client.py.
    If this name disappears again, every import of app.main breaks."""
    from app.services import ai_client

    assert hasattr(ai_client, "MOCK_MODE")
    assert isinstance(ai_client.MOCK_MODE, bool)


def test_ai_lint_module_imports_cleanly():
    """The actual failure mode of the MOCK_MODE bug: importing this
    module at all raised ImportError. Re-importing it here (pytest caches
    imports, but a fresh interpreter wouldn't) is a cheap canary."""
    import app.services.ai_lint  # noqa: F401 -- import succeeding is the assertion


def test_regex_fallback_detects_inferred_latch():
    from app.services.ai_lint import _regex_fallback

    source = """
    always_comb begin
        if (sel)
            out = a;
    end
    """
    issues = _regex_fallback(source)
    rules = [i.rule for i in issues]
    assert "latch-inferred" in rules


def test_regex_fallback_no_latch_warning_when_else_present():
    from app.services.ai_lint import _regex_fallback

    source = """
    always_comb begin
        if (sel)
            out = a;
        else
            out = b;
    end
    """
    issues = _regex_fallback(source)
    rules = [i.rule for i in issues]
    assert "latch-inferred" not in rules


def test_regex_fallback_detects_blocking_assign_in_sequential_block():
    from app.services.ai_lint import _regex_fallback

    source = """
    always_ff @(posedge clk) begin
        q = d;
    end
    """
    issues = _regex_fallback(source)
    rules = [i.rule for i in issues]
    assert "blocking-assign-in-seq" in rules


def test_regex_fallback_does_not_flag_nonblocking_assign():
    from app.services.ai_lint import _regex_fallback

    source = """
    always_ff @(posedge clk) begin
        q <= d;
    end
    """
    issues = _regex_fallback(source)
    rules = [i.rule for i in issues]
    assert "blocking-assign-in-seq" not in rules


def test_regex_fallback_issues_are_marked_confirmed():
    """Deterministic regex matches should never be 'predicted' -- there's
    no AI uncertainty in a regex match, so it should read as confirmed
    immediately (see LintIssue.status docstring in app/schemas.py)."""
    from app.services.ai_lint import _regex_fallback

    source = """
    always_ff @(posedge clk) begin
        q = d;
    end
    """
    issues = _regex_fallback(source)
    assert issues, "expected at least one issue for this fixture"
    assert all(issue.status == "confirmed" for issue in issues)


def test_lint_issue_defaults_to_predicted_status():
    """An issue constructed without an explicit status (the shape the AI
    path produces via LintIssue(**item) from raw model JSON) should
    default to 'predicted', not silently read as verified."""
    from app.schemas import LintIssue

    issue = LintIssue(rule="x", message="y", line=1)
    assert issue.status == "predicted"


def test_run_ai_lint_uses_regex_fallback_in_mock_mode(monkeypatch):
    """When no real provider/key is configured, run_ai_lint must use the
    deterministic fallback rather than attempting (and presumably
    failing) a real network call.

    Note: ai_lint.py does `from app.services.ai_client import MOCK_MODE`,
    which binds its own independent copy of the name at import time --
    patching app.services.ai_client.MOCK_MODE afterwards would NOT affect
    what run_ai_lint() sees, since it reads the name from its own module's
    namespace. Patch that copy directly instead.
    """
    import app.services.ai_lint as ai_lint_module

    monkeypatch.setattr(ai_lint_module, "MOCK_MODE", True)

    source = """
    always_ff @(posedge clk) begin
        q = d;
    end
    """
    result = ai_lint_module.run_ai_lint(source, "dut.sv")
    assert result.source == "regex_fallback"
    assert any(i.rule == "blocking-assign-in-seq" for i in result.issues)
