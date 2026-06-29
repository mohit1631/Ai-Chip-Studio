# Phase 1 tests

```bash
pip install -r requirements.txt
pytest
```

Covers:
- `test_auth.py` — register/login/me, duplicate email, wrong password
- `test_security.py` — production refuses to boot with the default JWT
  secret; `/auth/login` rate limiting
- `test_workshop.py` — publish/unpublish, browse (public-only), view
  (+1 view), import/fork (+1 fork, file copy verified), ownership checks
- `test_ai_lint.py` — regex-fallback lint rules, predicted/confirmed
  status field, and a regression test for the `MOCK_MODE` import bug

Notes:
- All DB-backed tests use an in-memory SQLite engine per test (see
  `conftest.py`) — never touches the real dev `.db` file.
- `AICHIP_CELERY_TASK_ALWAYS_EAGER=true` is set by `conftest.py` by
  default. If you add tests against routes that enqueue Celery tasks
  (code_fix, testbench, simulation), this is what makes them run
  synchronously instead of needing a real Redis + worker.
- This suite covers Sprint 5 (auth + workshop) and the AI lint fallback.
  code_fix.py, testbench.py, and simulation.py don't have tests yet —
  worth adding next, roughly in that priority order since code_fix and
  testbench both call out to the same AI client this suite already
  exercises the mock path for.
