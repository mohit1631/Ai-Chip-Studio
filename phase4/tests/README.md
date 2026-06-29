# Phase 4 tests

```bash
pip install -r requirements-test.txt
cd phase4 && pytest
```

Covers `pdk_presets.py` (the PDK-site registry added to fix the bug where
`run_floorplan()`'s OpenROAD `-site` name was hardcoded to FreePDK45's
value regardless of which PDK's LEF/Liberty files were actually passed
in): preset lookup, the `site_override` escape hatch, unknown-preset
errors, the `is_real_fab` flag (sky130/gf180mcu are real, everything
smaller is academic/predictive-only), and a direct check that
`PDInputs.site` actually reaches the generated OpenROAD TCL instead of
being silently ignored.

No DB, no subprocess, no network needed — `run_openroad` is mocked in
the one test that exercises `run_floorplan()` directly.

Not covered yet: `run_placement`/`run_cts`/`run_routing`/`run_signoff`,
and `app.py`'s `/run` and `/pdks` Flask routes (would need a Flask test
client + the same kind of `run_openroad` mocking, worth adding next).
