"""
Phase 4 — Physical Design API
Simple Flask wrapper around physical_design_runner.py

NOTE: this used to call `PhysicalDesignRunner(...)`, a class that doesn't
exist anywhere in physical_design_runner.py (only standalone functions +
PDInputs/PDResult dataclasses do) -- every call to POST /run would have
raised ImportError. Fixed to call the real run_physical_design(PDInputs)
orchestrator, the same one the CLI's main() uses.
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import os, sys

sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
CORS(app)

# Root directory PDK kits are mounted/cloned under -- see Dockerfile,
# which clones FreePDK45+Nangate to $PDK_ROOT/freepdk45.
PDK_ROOT = os.environ.get("AICHIP_PDK_ROOT", "/pdk")

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "phase": 4, "name": "Physical Design"})

@app.route('/pdks', methods=['GET'])
def list_pdks():
    """Lets other phases (e.g. the Workshop publish form) populate a PDK
    dropdown dynamically instead of hardcoding this list -- and surfaces
    is_real_fab so the UI can flag academic/predictive-only presets
    rather than implying they're tapeout-capable."""
    from pdk_presets import PDK_PRESETS

    return jsonify({
        name: {
            "node_nm": preset.node_nm,
            "is_real_fab": preset.is_real_fab,
            "license": preset.license,
            "commercial_use_ok": preset.commercial_use_ok,
            "notes": preset.notes,
            "auto_resolves_files": bool(preset.tech_lef and preset.cell_lef and preset.liberty),
        }
        for name, preset in PDK_PRESETS.items()
    })

@app.route('/run', methods=['POST'])
def run_pd():
    try:
        from physical_design_runner import PDInputs, run_physical_design
        from pdk_presets import resolve_site, resolve_files, PDK_PRESETS

        data = request.json or {}
        pdk_name = data.get("pdk", "sky130")

        try:
            site = resolve_site(pdk_name, data.get("site"))
        except KeyError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        # If the caller flags this as a paid-tier/commercial job (e.g. set
        # by phase1's billing logic for Pro/Team/Enterprise users), refuse
        # presets whose license doesn't clear commercial use -- currently
        # just freepdk15 (CC BY-NC-SA 4.0 design rule kit, needs a paid
        # license from NCSU Technology Transfer). This is a safety net,
        # not a substitute for actually deciding which presets are even
        # offered to paid users in the first place.
        preset = PDK_PRESETS.get(pdk_name.lower())
        if data.get("is_commercial") and preset is not None and not preset.commercial_use_ok:
            return jsonify({
                "success": False,
                "error": f"PDK preset '{pdk_name}' ({preset.license}) is not cleared for "
                         "commercial use without a separate license from its maintainer -- "
                         f"see pdk_presets.py notes. {preset.notes}",
            }), 403

        # tech_lef/cell_lef/liberty can be omitted if the chosen PDK has a
        # known on-disk layout (currently just freepdk45 -- cloned at
        # /pdk/freepdk45 by the Dockerfile). Explicit values always win,
        # so a caller can still point at a custom/local install of a
        # known preset, or use a preset that has no fixed layout (sky130,
        # gf180mcu) by always supplying paths themselves.
        tech_lef = data.get("tech_lef")
        cell_lef = data.get("cell_lef")
        liberty = data.get("liberty")
        if not (tech_lef and cell_lef and liberty):
            try:
                auto = resolve_files(pdk_name, f"{PDK_ROOT}/{pdk_name.lower()}")
            except ValueError as exc:
                return jsonify({
                    "success": False,
                    "error": f"{exc} (tech_lef/cell_lef/liberty were not all provided in the request).",
                }), 400
            tech_lef = tech_lef or auto["tech_lef"]
            cell_lef = cell_lef or auto["cell_lef"]
            liberty = liberty or auto["liberty"]

        missing = [
            name for name, val in [
                ("netlist_path", data.get("netlist_path")),
                ("tech_lef", tech_lef), ("cell_lef", cell_lef),
                ("liberty", liberty), ("sdc", data.get("sdc")),
            ] if not val
        ]
        if missing:
            return jsonify({
                "success": False,
                "error": f"Missing required field(s): {', '.join(missing)}.",
            }), 400

        die_area = tuple(data.get("die_area", [0, 0, 1000, 1000]))

        inputs = PDInputs(
            netlist_path=data["netlist_path"],
            tech_lef=tech_lef,
            cell_lef=cell_lef,
            liberty=liberty,
            sdc=data["sdc"],
            work_dir=data.get("work_dir", "/tmp/pd_job"),
            die_area=die_area,
            utilization=data.get("utilization", 0.65),
            top_module=data.get("top_module"),
            site=site,
        )
        result = run_physical_design(inputs)
        return jsonify({"success": result.success, "result": result.to_dashboard_summary()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8004))
    print(f"Phase 4 Physical Design API running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
