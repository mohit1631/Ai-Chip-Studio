// src/verification.js
// Phase 2 — Verification Studio backend routes.
//
// Three real, working endpoints:
//   POST /coverage   -> Verilator line/toggle coverage (needs a testbench)
//   POST /formal      -> SymbiYosys (sby) bounded model checking against
//                         SVA assertions already present in the RTL
//   POST /lint-assert -> Yosys "check" pass, used as a fast sanity gate
//                         before a full formal run (catches multi-driven
//                         nets, missing top, etc. with real Yosys, not AI)
//
// AI-authored content (SVA assertions, formal properties, coverage-hole
// commentary) is generated separately by the frontend calling the existing
// /.netlify/functions/review endpoint (or local Ollama) and is NOT a tool
// in this file — this file only runs real open-source EDA tools against
// whatever Verilog/SVA text the user (or the AI) produced.
//
// Both /coverage and /formal expect TWO uploaded files:
//   - "file"   : the design/testbench ZIP or single .v/.sv file
//   - "top"    : (form field) optional top module name override
//
// Tool requirements baked into the Docker image (see Dockerfile):
//   - verilator        (already required by server.js)
//   - yosys             (already required by server.js)
//   - sby (SymbiYosys)  + a solver: yosys-smtbmc needs boolector or z3
//
// All jobs run inside the same per-request tmp workdir pattern used by
// server.js, and are subject to the same JOB_TIMEOUT_MS hard cap (formal
// runs get a longer cap since BMC can legitimately take longer — see
// FORMAL_TIMEOUT_MS below).

const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const FORMAL_TIMEOUT_MS = 60_000; // formal/BMC needs more headroom than lint

function runCmd(cmd, args, cwd, timeoutMs) {
  return new Promise((resolve) => {
    execFile(cmd, args, { cwd, timeout: timeoutMs, maxBuffer: 10 * 1024 * 1024 }, (err, stdout, stderr) => {
      resolve({
        ok: !err,
        code: err ? (err.code ?? 1) : 0,
        timedOut: err && err.killed && err.signal === 'SIGTERM',
        stdout: stdout || '',
        stderr: stderr || ''
      });
    });
  });
}

// ---------- /coverage (Verilator line + toggle coverage) ----------
//
// Needs a self-checking testbench among the uploaded files (a module with
// an `initial` block that drives the DUT — exactly what "AI Testbench
// Generation" produces). Without a testbench, Verilator will compile
// the design but no statements will ever execute, so coverage will read
// near-zero everywhere — which is itself useful "coverage hole" signal,
// not an error.
function registerCoverageRoute(app, { upload, makeWorkdir, cleanup, materializeInput, pickTopModule }) {
  app.post('/coverage', upload.single('file'), async (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded (field name must be "file")' });

    const dir = makeWorkdir();
    try {
      const files = materializeInput(dir, req.file);
      if (files.length === 0) {
        return res.status(400).json({ error: 'No .v/.sv files found in upload' });
      }

      const top = pickTopModule(dir, files, req.body.top);
      if (!top) return res.status(400).json({ error: 'Could not detect a top module; pass one in the "top" field' });

      const binPath = path.join(dir, 'sim.out');
      const covDatPath = path.join(dir, 'coverage.dat');

      // Build with coverage instrumentation enabled, then run the sim.
      // --coverage turns on line + toggle coverage collection; -cc/--exe
      // would be needed for a C++ harness, but our testbenches are pure
      // Verilog with their own `initial`/`$finish`, so --binary is enough.
      const buildArgs = [
        '--binary', '--coverage', '--timing',
        '-Wno-fatal',
        '--top-module', top,
        '-o', 'sim.out',
        ...files
      ];
      const build = await runCmd('verilator', buildArgs, dir, FORMAL_TIMEOUT_MS);

      if (!fs.existsSync(binPath)) {
        return res.json({
          tool: 'verilator',
          mode: 'coverage',
          stage: 'build',
          command: `verilator ${buildArgs.join(' ')}`,
          topModule: top,
          filesAnalyzed: files,
          exitCode: build.code,
          stdout: build.stdout,
          stderr: build.stderr,
          passed: false,
          error: 'Build failed — coverage requires the design to compile and link into a runnable simulation (a testbench with an initial block is usually required).'
        });
      }

      const run = await runCmd(binPath, [], dir, FORMAL_TIMEOUT_MS);

      let summary = null;
      let annotated = null;
      if (fs.existsSync(covDatPath)) {
        // verilator_coverage turns the raw .dat into a human-readable
        // per-line annotation and an overall summary line.
        const annotateDir = path.join(dir, 'cov_annotated');
        fs.mkdirSync(annotateDir, { recursive: true });
        const covResult = await runCmd(
          'verilator_coverage',
          ['--annotate', annotateDir, '--write-info', 'coverage.info', covDatPath],
          dir,
          FORMAL_TIMEOUT_MS
        );
        summary = covResult.stdout + covResult.stderr;

        // Collect annotated source for the files we actually analyzed, so
        // the frontend can show "this line never executed" inline.
        annotated = {};
        files.forEach(f => {
          const annPath = path.join(annotateDir, f);
          if (fs.existsSync(annPath)) {
            annotated[f] = fs.readFileSync(annPath, 'utf8');
          }
        });
      }

      // Parse coverage.info (lcov format) for a quick numeric rollup:
      // lines starting with LH: (lines hit) and LF: (lines found).
      let linesHit = 0, linesFound = 0;
      const infoPath = path.join(dir, 'coverage.info');
      if (fs.existsSync(infoPath)) {
        const info = fs.readFileSync(infoPath, 'utf8');
        (info.match(/^LH:(\d+)/gm) || []).forEach(l => linesHit += parseInt(l.slice(3), 10));
        (info.match(/^LF:(\d+)/gm) || []).forEach(l => linesFound += parseInt(l.slice(3), 10));
      }

      res.json({
        tool: 'verilator',
        mode: 'coverage',
        command: `verilator ${buildArgs.join(' ')} && ./sim.out && verilator_coverage --annotate ...`,
        topModule: top,
        filesAnalyzed: files,
        buildExitCode: build.code,
        runExitCode: run.code,
        runTimedOut: !!run.timedOut,
        linesHit,
        linesFound,
        coveragePct: linesFound > 0 ? Math.round((linesHit / linesFound) * 1000) / 10 : null,
        simStdout: run.stdout,
        simStderr: run.stderr,
        coverageSummary: summary,
        annotated, // { filename: annotated-source-with-counts }
        passed: build.ok
      });
    } catch (e) {
      res.status(500).json({ error: e.message || 'Coverage job failed' });
    } finally {
      cleanup(dir);
    }
  });
}

// ---------- /formal (SymbiYosys bounded model checking) ----------
//
// Expects the uploaded design to already contain SVA assertions (either
// hand-written or AI-generated — see AI Features below). Builds a minimal
// .sby config on the fly and runs `sby` in bmc mode, which itself drives
// Yosys (to build a formal model) + a backend solver (Yosys's smtbmc
// using Boolector by default).
//
// depth (form field, default 20) controls how many cycles BMC unrolls —
// deeper catches more bugs but takes longer; capped at 50 to stay inside
// FORMAL_TIMEOUT_MS on free-tier hardware.
function registerFormalRoute(app, { upload, makeWorkdir, cleanup, materializeInput, pickTopModule }) {
  app.post('/formal', upload.single('file'), async (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded (field name must be "file")' });

    const dir = makeWorkdir();
    try {
      const files = materializeInput(dir, req.file);
      if (files.length === 0) {
        return res.status(400).json({ error: 'No .v/.sv files found in upload' });
      }

      const top = pickTopModule(dir, files, req.body.top);
      if (!top) return res.status(400).json({ error: 'Could not detect a top module; pass one in the "top" field' });

      let depth = parseInt(req.body.depth, 10);
      if (!Number.isFinite(depth) || depth <= 0) depth = 20;
      depth = Math.min(depth, 50);

      // Quick pre-check: does the design even contain any assert/assume
      // properties? SymbiYosys "succeeds" trivially (and uselessly) on a
      // design with zero properties, so we flag that clearly rather than
      // reporting a false "PASS".
      const combinedSrc = files.map(f => fs.readFileSync(path.join(dir, f), 'utf8')).join('\n');
      const hasProperties = /\b(assert|assume|cover)\s*\(/.test(combinedSrc) || /\bassert\s+property\b/.test(combinedSrc);

      const sbyConfig = [
        '[options]',
        `mode bmc`,
        `depth ${depth}`,
        '',
        '[engines]',
        'smtbmc boolector',
        '',
        '[script]',
        ...files.map(f => `read_verilog -formal -sv ${f}`),
        `prep -top ${top}`,
        '',
        '[files]',
        ...files
      ].join('\n');

      const sbyPath = path.join(dir, 'run.sby');
      fs.writeFileSync(sbyPath, sbyConfig);

      const result = await runCmd('sby', ['-f', 'run.sby'], dir, FORMAL_TIMEOUT_MS);

      // sby writes a per-task directory named after the .sby file (run/)
      // containing a status file and, on failure, a counterexample trace.
      const taskDir = path.join(dir, 'run');
      let status = null;
      let traceVcd = null;
      const statusPath = path.join(taskDir, 'status');
      if (fs.existsSync(statusPath)) {
        status = fs.readFileSync(statusPath, 'utf8').trim();
      }
      // Look for an engine_0/.../trace.vcd produced on a failing property.
      if (fs.existsSync(taskDir)) {
        const findTrace = (d) => {
          for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
            const p = path.join(d, entry.name);
            if (entry.isDirectory()) {
              const found = findTrace(p);
              if (found) return found;
            } else if (/trace.*\.vcd$/i.test(entry.name)) {
              return p;
            }
          }
          return null;
        };
        const tracePath = findTrace(taskDir);
        if (tracePath) traceVcd = fs.readFileSync(tracePath, 'utf8');
      }

      res.json({
        tool: 'symbiyosys',
        mode: `bmc (depth ${depth})`,
        command: `sby -f run.sby`,
        config: sbyConfig,
        topModule: top,
        filesAnalyzed: files,
        hasProperties,
        exitCode: result.code,
        timedOut: !!result.timedOut,
        status, // "PASS", "FAIL", or "UNKNOWN" per SymbiYosys convention
        stdout: result.stdout,
        stderr: result.stderr,
        counterexampleVcd: traceVcd, // present only if a property failed
        passed: result.ok && status === 'PASS',
        warning: hasProperties ? null : 'No assert/assume/cover properties detected in the design — formal verification has nothing to check. Generate assertions first (AI Features -> Assertion generation), then re-run.'
      });
    } catch (e) {
      res.status(500).json({ error: e.message || 'Formal verification job failed' });
    } finally {
      cleanup(dir);
    }
  });
}

// ---------- /lint-assert (fast Yosys structural check) ----------
//
// A cheap pre-flight gate: real Yosys `check` pass (multi-driven nets,
// width mismatches, undriven nets at the netlist level) PLUS a count of
// how many SVA-style properties exist in the source. Useful as a fast
// "is this even worth a full formal run" signal before paying the BMC
// cost above.
function registerLintAssertRoute(app, { upload, makeWorkdir, cleanup, materializeInput, pickTopModule }) {
  app.post('/lint-assert', upload.single('file'), async (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded (field name must be "file")' });

    const dir = makeWorkdir();
    try {
      const files = materializeInput(dir, req.file);
      if (files.length === 0) {
        return res.status(400).json({ error: 'No .v/.sv files found in upload' });
      }
      const top = pickTopModule(dir, files, req.body.top);
      if (!top) return res.status(400).json({ error: 'Could not detect a top module; pass one in the "top" field' });

      const script = [
        ...files.map(f => `read_verilog -sv ${f}`),
        `prep -top ${top}`,
        `check`
      ].join('\n');
      const scriptPath = path.join(dir, 'check.ys');
      fs.writeFileSync(scriptPath, script);

      const result = await runCmd('yosys', ['-s', 'check.ys'], dir, 20_000);

      const combinedSrc = files.map(f => fs.readFileSync(path.join(dir, f), 'utf8')).join('\n');
      const assertCount = (combinedSrc.match(/\bassert\s*(property)?\s*\(/g) || []).length;
      const assumeCount = (combinedSrc.match(/\bassume\s*(property)?\s*\(/g) || []).length;
      const coverCount = (combinedSrc.match(/\bcover\s*(property)?\s*\(/g) || []).length;

      res.json({
        tool: 'yosys',
        mode: 'check',
        command: 'yosys -s check.ys',
        topModule: top,
        filesAnalyzed: files,
        exitCode: result.code,
        stdout: result.stdout,
        stderr: result.stderr,
        propertyCounts: { assert: assertCount, assume: assumeCount, cover: coverCount },
        passed: result.ok
      });
    } catch (e) {
      res.status(500).json({ error: e.message || 'Yosys check job failed' });
    } finally {
      cleanup(dir);
    }
  });
}

module.exports = {
  registerCoverageRoute,
  registerFormalRoute,
  registerLintAssertRoute
};
