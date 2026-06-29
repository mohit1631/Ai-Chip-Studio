"""
phase4/pdk_presets.py
------------------------
Small registry mapping a PDK preset name to the OpenROAD `-site` name
`run_floorplan()` needs for `initialize_floorplan`. This existed nowhere
before -- the site name used to be hardcoded to FreePDK45's value
regardless of which PDK's LEF/Liberty files were actually passed in,
which silently broke every other PDK's floorplan stage.

IMPORTANT — "open-source PDK" does not mean "real chips come out of it"
once you go below 45nm. SKY130 and FreePDK45 are real, fab-grade (or
fab-grade-derived) design rules; everything smaller in the open-source
world is a *predictive/academic* model meant for EDA tooling research and
education, not actual tapeout. `is_real_fab=False` presets will run
through Yosys/OpenROAD and produce a GDSII-shaped file, but no foundry
will manufacture it from that file. Surface this status to users (e.g. in
the Workshop publish form) rather than letting "PDK: 15nm" imply
something it can't deliver.

Site names: sky130 ("unithd"), gf180mcu, and freepdk45 are documented,
real values -- OpenLane/OpenROAD-flow-scripts officially support sky130
and gf180mcu, and FreePDK45's site name appears directly in OpenROAD's
own initialize_floorplan documentation/examples. openrpdk28 and
freepdk15 are NOT part of OpenLane/ORFS's tested PDK set -- there's no
confirmed public OpenROAD site name for them, so those two entries are
clearly marked _UNVERIFIED and should be confirmed against the real PDK
install before being relied on.

LICENSING -- see each preset's `license`/`commercial_use_ok` fields:
  sky130      Apache-2.0, commercial use fine
  gf180mcu    Apache-2.0, commercial use fine
  freepdk45   Apache-2.0, commercial use fine (files only -- still not
              tapeout-capable, see is_real_fab above)
  openrpdk28  MIT, commercial use fine
  freepdk15   Code is BSD, but the Design Rule Kit itself is
              CC BY-NC-SA 4.0 (NonCommercial) -- commercial use needs a
              separate paid license from NCSU Technology Transfer
              (techtransfer@ncsu.edu). commercial_use_ok=False here is a
              hard stop: don't surface this preset in a paid tier without
              contacting them first.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PDKPreset:
    site: str
    node_nm: int
    is_real_fab: bool  # False = predictive/academic model, not tapeout-capable
    license: str = ""
    commercial_use_ok: bool = True  # False = NonCommercial clause or fee required
    notes: str = ""
    # Filenames relative to the PDK's root directory, for presets where
    # that layout is actually known/standardized. None means "we don't
    # have a fixed layout for this one" -- caller must pass explicit
    # paths instead of relying on resolve_files().
    tech_lef: str | None = None
    cell_lef: str | None = None
    liberty: str | None = None


PDK_PRESETS: dict[str, PDKPreset] = {
    "sky130": PDKPreset(
        site="unithd",
        node_nm=130,
        is_real_fab=True,
        license="Apache-2.0",
        commercial_use_ok=True,
        notes="SkyWater 130nm, Google x SkyWater collaboration. Apache-2.0, no fee, commercial use fine. "
              "Officially supported by OpenLane/OpenROAD-flow-scripts, real fab via SkyWater/efabless/ChipIgnite. "
              "Note: Google/SkyWater still call this an 'experimental preview / alpha release', not yet "
              "positioned for production-grade signoff, even though many designs have been manufactured with it.",
        # Layout varies by how sky130 was installed (volare, open_pdks,
        # OpenLane's own checkout, ...) -- not standardized enough to
        # hardcode here the way freepdk45's is below. Pass tech_lef/
        # cell_lef/liberty explicitly for sky130 jobs.
    ),
    "gf180mcu": PDKPreset(
        site="GF180MCU_FIXED_VT",
        node_nm=180,
        is_real_fab=True,
        license="Apache-2.0",
        commercial_use_ok=True,
        notes="GlobalFoundries 180nm MCU, Google x GlobalFoundries collaboration. Apache-2.0, no fee, "
              "commercial use fine. Officially supported by OpenLane/OpenROAD-flow-scripts, real fab. "
              "Same 'experimental preview / alpha' caveat as sky130.",
        # Same installation-dependent-layout caveat as sky130.
    ),
    "freepdk45": PDKPreset(
        site="FreePDK45_38x28_10R_NP_162NW_34O",
        node_nm=45,
        is_real_fab=False,
        license="Apache-2.0",
        commercial_use_ok=True,
        notes="NCSU/OSU predictive technology model + Nangate Open Cell Library, packaged by "
              "github.com/mflowgen/freepdk-45nm. Apache-2.0, no NDA/fee, commercial use fine -- but "
              "the kit's own README says it 'does not correspond to any real process and cannot be "
              "fabricated', so 'commercial use fine' means 'free to redistribute/use the files', not "
              "'tapeout-capable'. This site name is the documented OpenROAD default (it appears "
              "directly in OpenROAD's own initialize_floorplan examples).",
        # Filenames as laid out by the mflowgen/freepdk-45nm repo, cloned
        # to /pdk/freepdk45 by phase4/Dockerfile.
        tech_lef="rtk-tech.lef",
        cell_lef="stdcells.lef",
        liberty="stdcells.lib",  # typical corner; stdcells-bc.lib/-wc.lib also available for corner analysis
    ),
    "openrpdk28": PDKPreset(
        # UNVERIFIED: RIOS Lab's OpenRPDK28 isn't part of OpenLane/OpenROAD-flow-scripts'
        # documented/tested PDK set, so there's no confirmed public OpenROAD site name for
        # it the way there is for sky130/gf180mcu/freepdk45. This is a guessed placeholder
        # following the common "<PDK>_<row>" naming convention -- confirm against the
        # actual PDK's OpenROAD platform config (or ask its maintainers) before relying on
        # it for a real run; it will very likely need correcting.
        site="OpenRPDK28_site_UNVERIFIED",
        node_nm=28,
        is_real_fab=False,
        license="MIT",
        commercial_use_ok=True,
        notes="RIOS Lab academic PDK (github.com/RIOSLaboratory/OpenRPDK28). MIT license, no fee, "
              "commercial use fine. Design/simulate/verify only, not tapeout-capable -- 'under "
              "construction' per the repo's own README. Site name below is an unverified guess, "
              "not a confirmed value -- check before using.",
    ),
    "freepdk15": PDKPreset(
        # UNVERIFIED for the same reason as openrpdk28 above.
        site="FreePDK15_site_UNVERIFIED",
        node_nm=15,
        is_real_fab=False,
        license="BSD (code) + CC BY-NC-SA 4.0 (design rule kit)",
        commercial_use_ok=False,
        notes="NCSU FinFET predictive model. Code files are New BSD, but the actual Design Rule Kit "
              "(the part that matters for synthesis/PD) is CC BY-NC-SA 4.0 -- NonCommercial. Free "
              "for academic use; commercial use requires a separate paid license from NCSU "
              "Technology Transfer (techtransfer@ncsu.edu). Do NOT bundle/offer this one in a paid "
              "tier without contacting them first. EDA research/education only regardless -- not "
              "tapeout-capable. Site name below is an unverified guess, not a confirmed value.",
    ),
}


def resolve_site(pdk_name: str, site_override: str | None = None) -> str:
    """Used by main() and app.py: an explicit --site/site_override always
    wins (lets someone point at a custom/local PDK we don't know about),
    otherwise look up the preset's site name."""
    if site_override:
        return site_override
    preset = PDK_PRESETS.get(pdk_name.lower())
    if preset is None:
        known = ", ".join(sorted(PDK_PRESETS))
        raise KeyError(f"Unknown PDK preset '{pdk_name}'. Known presets: {known}")
    return preset.site


def resolve_files(pdk_name: str, pdk_root: str) -> dict[str, str]:
    """Build absolute tech_lef/cell_lef/liberty paths for a preset whose
    on-disk layout is known (currently just freepdk45 -- see
    phase4/Dockerfile, which clones mflowgen/freepdk-45nm to /pdk/freepdk45).

    Raises KeyError for an unknown preset, ValueError for a known preset
    that doesn't have a standardized layout (sky130, gf180mcu) -- those
    need tech_lef/cell_lef/liberty passed explicitly by the caller
    instead, since their actual file locations depend on how/where they
    were installed.
    """
    preset = PDK_PRESETS.get(pdk_name.lower())
    if preset is None:
        known = ", ".join(sorted(PDK_PRESETS))
        raise KeyError(f"Unknown PDK preset '{pdk_name}'. Known presets: {known}")
    if not (preset.tech_lef and preset.cell_lef and preset.liberty):
        raise ValueError(
            f"PDK preset '{pdk_name}' has no standardized file layout -- "
            "pass tech_lef/cell_lef/liberty explicitly instead of using resolve_files()."
        )
    root = pdk_root.rstrip("/")
    return {
        "tech_lef": f"{root}/{preset.tech_lef}",
        "cell_lef": f"{root}/{preset.cell_lef}",
        "liberty": f"{root}/{preset.liberty}",
    }
