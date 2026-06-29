"""
Phase 5 — AI Copilot API
Simple Flask wrapper around ai_copilot.py
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
import os, sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

app = Flask(__name__)
CORS(app)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "phase": 5,
        "name": "AI Copilot",
        "provider": os.getenv("AI_PROVIDER", "mock")
    })

@app.route('/generate', methods=['POST'])
def generate():
    # FIXED: this previously referenced GenerationTask (doesn't exist
    # anywhere in ai_copilot.py), CopilotClient(context=...) (the real
    # param is project_context=...), client.generate(...) (no such
    # method -- there's generate_rtl/generate_fsm/generate_uvm_env/
    # generate_assertions/generate_constraints, each with a different
    # signature), and result.artifact/result.model_used (the real fields
    # are .content/.verification_status -- there's no "model used" field
    # at all). Every one of those would have raised at call time.
    try:
        from ai_copilot import CopilotClient, ProjectContext, GeneratedArtifact
        data = request.json or {}

        ctx = ProjectContext(
            project_id=data.get('project_id', 'default'),
            top_module=data.get('top_module', 'top'),
        )
        client = CopilotClient(project_context=ctx)

        task = (data.get('task') or 'rtl').lower()
        description = data.get('description', '')

        if task == 'rtl':
            artifact = client.generate_rtl(spec=description)
        elif task == 'fsm':
            artifact = client.generate_fsm(
                state_diagram=description,
                encoding=data.get('encoding', 'one-hot'),
            )
        elif task in ('uvm_env', 'assertions', 'constraints'):
            # These three all need an existing RTL artifact's .content as
            # input, not just a free-text description -- the caller must
            # supply it as 'rtl_content' (and 'top_module' is already on
            # the project context above).
            rtl_content = data.get('rtl_content')
            if not rtl_content:
                return jsonify({
                    "success": False,
                    "error": f"task='{task}' requires 'rtl_content' (the RTL to generate against) in the request body",
                }), 400
            rtl_artifact = GeneratedArtifact(kind="rtl", content=rtl_content)
            if task == 'uvm_env':
                artifact = client.generate_uvm_env(rtl_artifact)
            elif task == 'assertions':
                artifact = client.generate_assertions(rtl_artifact)
            else:
                artifact = client.generate_constraints(
                    rtl_artifact, target_freq_mhz=data.get('target_freq_mhz'),
                )
        else:
            return jsonify({
                "success": False,
                "error": f"Unknown task '{task}'. Use one of: rtl, fsm, uvm_env, assertions, constraints",
            }), 400

        return jsonify({
            "success": True,
            "code": artifact.content,
            "status": artifact.verification_status,
            "kind": artifact.kind,
            "artifact_id": artifact.artifact_id,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/debug', methods=['POST'])
def debug():
    try:
        from ai_copilot import CopilotClient, ProjectContext
        data = request.json or {}
        ctx = ProjectContext(project_id=data.get('project_id', 'default'), top_module='top')
        client = CopilotClient(project_context=ctx)
        # FIXED: the real method is debug_assist (not debug_assistant),
        # and it returns a GeneratedArtifact, not a plain string/dict.
        artifact = client.debug_assist(error_log=data.get('error_log', ''))
        return jsonify({
            "success": True,
            "analysis": artifact.content,
            "status": artifact.verification_status,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8005))
    print(f"Phase 5 AI Copilot API running on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
