"""
phase4/tests/test_pdk_presets.py
-----------------------------------
Covers pdk_presets.py: the registry that fixes the bug where
run_floorplan()'s OpenROAD `-site` name was hardcoded to FreePDK45's
value regardless of which PDK's LEF/Liberty files were actually passed
in (see PDInputs.site's docstring in physical_design_runner.py).

Run with:
    cd phase4 && pytest tests/

No fixtures/conftest needed -- this module has no DB, no network, no
subprocess calls; it's pure dict lookups.
"""
from __future__ import annotations

import pytest


def test_resolve_site_known_preset_returns_its_site_name():
    from pdk_presets import resolve_site

    assert resolve_site("sky130") == "unithd"
    assert resolve_site("freepdk45") == "FreePDK45_38x28_10R_NP_162NW_34O"


def test_resolve_site_is_case_insensitive():
    from pdk_presets import resolve_site

    assert resolve_site("SKY130") == resolve_site("sky130")
    assert resolve_site("Sky130") == resolve_site("sky130")


def test_resolve_site_override_always_wins():
    from pdk_presets import resolve_site

    # Lets someone point at a custom/local PDK not in the registry at all.
    assert resolve_site("sky130", site_override="my_custom_site") == "my_custom_site"
    assert resolve_site("does-not-exist", site_override="my_custom_site") == "my_custom_site"


def test_resolve_site_unknown_preset_without_override_raises_with_known_names_listed():
    from pdk_presets import resolve_site, PDK_PRESETS

    with pytest.raises(KeyError) as exc_info:
        resolve_site("totally-made-up-pdk")

    message = str(exc_info.value)
    # Every known preset name should be listed so the caller can self-correct.
    for name in PDK_PRESETS:
        assert name in message


def test_all_presets_have_a_nonempty_site_name():
    from pdk_presets import PDK_PRESETS

    for name, preset in PDK_PRESETS.items():
        assert preset.site, f"{name} has an empty site name"


def test_real_fab_presets_are_flagged_correctly():
    """sky130 and gf180mcu are real, documented OpenLane/ORFS PDKs --
    everything smaller (freepdk45 and below) is academic/predictive-only.
    Getting this flag wrong would let the UI imply a tapeout-capable
    result for something that fundamentally can't be one."""
    from pdk_presets import PDK_PRESETS

    assert PDK_PRESETS["sky130"].is_real_fab is True
    assert PDK_PRESETS["gf180mcu"].is_real_fab is True
    assert PDK_PRESETS["freepdk45"].is_real_fab is False
    assert PDK_PRESETS["openrpdk28"].is_real_fab is False
    assert PDK_PRESETS["freepdk15"].is_real_fab is False


def test_node_nm_ordering_matches_preset_names():
    """Sanity check against typos: sky130 should be 130nm, not 13 or 1300."""
    from pdk_presets import PDK_PRESETS

    assert PDK_PRESETS["sky130"].node_nm == 130
    assert PDK_PRESETS["gf180mcu"].node_nm == 180
    assert PDK_PRESETS["freepdk45"].node_nm == 45
    assert PDK_PRESETS["openrpdk28"].node_nm == 28
    assert PDK_PRESETS["freepdk15"].node_nm == 15


def test_resolve_files_freepdk45_builds_paths_from_root():
    from pdk_presets import resolve_files

    result = resolve_files("freepdk45", "/pdk/freepdk45")
    assert result == {
        "tech_lef": "/pdk/freepdk45/rtk-tech.lef",
        "cell_lef": "/pdk/freepdk45/stdcells.lef",
        "liberty": "/pdk/freepdk45/stdcells.lib",
    }


def test_resolve_files_strips_trailing_slash_on_root():
    from pdk_presets import resolve_files

    result = resolve_files("freepdk45", "/pdk/freepdk45/")
    assert result["tech_lef"] == "/pdk/freepdk45/rtk-tech.lef"


def test_resolve_files_is_case_insensitive():
    from pdk_presets import resolve_files

    assert resolve_files("FreePDK45", "/pdk/freepdk45") == resolve_files("freepdk45", "/pdk/freepdk45")


def test_resolve_files_unknown_preset_raises_keyerror():
    from pdk_presets import resolve_files

    with pytest.raises(KeyError):
        resolve_files("not-a-real-pdk", "/pdk/whatever")


def test_resolve_files_preset_without_known_layout_raises_valueerror():
    """sky130 and gf180mcu don't have a standardized file layout the way
    freepdk45 does (installed via volare/open_pdks/etc, varies) -- callers
    must supply tech_lef/cell_lef/liberty explicitly for those instead."""
    from pdk_presets import resolve_files

    with pytest.raises(ValueError):
        resolve_files("sky130", "/pdk/sky130")
    with pytest.raises(ValueError):
        resolve_files("gf180mcu", "/pdk/gf180mcu")


def test_commercial_use_flags_match_actual_licenses():
    """sky130/gf180mcu (Apache-2.0), freepdk45 (Apache-2.0), and openrpdk28
    (MIT) all clear for commercial use. freepdk15's actual Design Rule Kit
    is CC BY-NC-SA 4.0 (NonCommercial) -- verified against eda.ncsu.edu/freepdk15,
    which explicitly requires a separate paid license from NCSU Technology
    Transfer for commercial use. Getting this wrong would let a paid-tier
    user use a PDK without the right to do so commercially."""
    from pdk_presets import PDK_PRESETS

    assert PDK_PRESETS["sky130"].commercial_use_ok is True
    assert PDK_PRESETS["gf180mcu"].commercial_use_ok is True
    assert PDK_PRESETS["freepdk45"].commercial_use_ok is True
    assert PDK_PRESETS["openrpdk28"].commercial_use_ok is True
    assert PDK_PRESETS["freepdk15"].commercial_use_ok is False


def test_all_presets_have_a_license_string():
    from pdk_presets import PDK_PRESETS

    for name, preset in PDK_PRESETS.items():
        assert preset.license, f"{name} has no license recorded"


def test_pdinputs_default_site_matches_previous_hardcoded_behavior():
    """Regression guard: PDInputs.site's default must stay equal to the
    value that used to be hardcoded directly into run_floorplan()'s TCL,
    so any existing caller that doesn't pass site= explicitly keeps
    getting the exact same floorplan behavior as before this fix."""
    from physical_design_runner import PDInputs

    inputs = PDInputs(
        netlist_path="x.v", tech_lef="t.lef", cell_lef="c.lef",
        liberty="l.lib", sdc="x.sdc", work_dir="/tmp/x",
    )
    assert inputs.site == "FreePDK45_38x28_10R_NP_162NW_34O"


def test_pdinputs_site_is_used_verbatim_in_floorplan_tcl(tmp_path, monkeypatch):
    """The actual bug: -site used to be hardcoded in the TCL string,
    ignoring whatever PDInputs.site was set to. Confirm the generated
    TCL now reflects a non-default site value."""
    import physical_design_runner as pdr

    captured_tcl = {}

    def fake_run_openroad(tcl, work_dir, stage_name):
        captured_tcl["tcl"] = tcl
        raise FileNotFoundError("no real OpenROAD binary in this test env")

    monkeypatch.setattr(pdr, "run_openroad", fake_run_openroad)

    # Files just need to exist for _require_file's checks; content doesn't matter
    # since run_openroad is mocked before it would actually read them meaningfully.
    for fname in ("netlist.v", "tech.lef", "cell.lef", "cells.lib"):
        (tmp_path / fname).write_text("placeholder")

    inputs = pdr.PDInputs(
        netlist_path=str(tmp_path / "netlist.v"),
        tech_lef=str(tmp_path / "tech.lef"),
        cell_lef=str(tmp_path / "cell.lef"),
        liberty=str(tmp_path / "cells.lib"),
        sdc=str(tmp_path / "x.sdc"),
        work_dir=str(tmp_path / "work"),
        site="unithd",  # sky130's real site name, deliberately not the default
    )
    pdr.run_floorplan(inputs)

    assert "tcl" in captured_tcl, "run_openroad was never called -- check _require_file didn't short-circuit first"
    assert "-site unithd" in captured_tcl["tcl"]
    assert "FreePDK45" not in captured_tcl["tcl"]
