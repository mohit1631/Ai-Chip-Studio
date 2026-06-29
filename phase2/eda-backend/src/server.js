// src/server.js
// Silicon Lint EDA Backend — runs real Verilator (simulation/lint) and
// real Yosys (synthesis) jobs against uploaded RTL, inside this container.
//
// This is a separate service from the Netlify site. Netlify can't run
// native binaries like Verilator/Yosys, so this backend lives on Railway
// (or Render, Fly.io, etc.) where we control the full container.

const express = require('express');
const cors = require('cors');
const multer = require('multer');
const AdmZip = require('adm-zip');
const { v4: uuid } = require('uuid');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execFile } = require('child_process');

const {
  registerCoverageRoute,
  registerFormalRoute,
  registerLintAssertRoute
} = require('./verification');

const app = express();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 25 * 1024 * 1024 } });

// Allow your Netlify frontend (and localhost for testing) to call this API.
// Set ALLOWED_ORIGIN in Railway env vars to your real Netlify URL once deployed.
const allowedOrigin = process.env.ALLOWED_ORIGIN || '*';
app.use(cors({ origin: allowedOrigin }));
app.use(express.json({ limit: '5mb' }));

const JOB_TIMEOUT_MS = 30_000; // hard cap so one bad file can't hang a worker forever

app.get('/', (req, res) => {
  res.json({
    ok: true,
    service: 'silicon-lint-eda-backend',
    tools: ['verilator', 'yosys', 'verilator_coverage', 'symbiyosys'],
    endpoints: ['/simulate', '/synthesize', '/coverage', '/formal', '/lint-assert']
  });
});

app.get('/health', (req, res) => res.json({ status: 'ok' }));

// ---------- helpers ----------

function makeWorkdir() {
  const dir = path.join(os.tmpdir(), 'silicon-lint-' + uuid());
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function cleanup(dir) {
  fs.rm(dir, { recursive: true, force: true }, () => {});
}

function runCmd(cmd, args, cwd) {
  return new Promise((resolve) => {
    execFile(cmd, args, { cwd, timeout: JOB_TIMEOUT_MS, maxBuffer: 10 * 1024 * 1024 }, (err, stdout, stderr) => {
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

// Writes either a single uploaded file, or all .v/.sv files extracted from
// a ZIP, into the job's working directory. Returns the list of written
// verilog/systemverilog filenames (relative to dir).
function materializeInput(dir, file) {
  const name = file.originalname || 'top.v';
  if (/\.zip$/i.test(name)) {
    const zip = new AdmZip(file.buffer);
    const written = [];
    zip.getEntries().forEach(entry => {
      if (entry.isDirectory) return;
      if (!/\.(v|sv|vh|svh)$/i.test(entry.entryName)) return;
      // flatten paths to avoid zip-slip / directory traversal issues
      const safeName = entry.entryName.replace(/[\\/]/g, '__');
      const outPath = path.join(dir, safeName);
      fs.writeFileSync(outPath, entry.getData());
      written.push(safeName);
    });
    return written;
  } else {
    fs.writeFileSync(path.join(dir, name), file.buffer);
    return [name];
  }
}

function pickTopModule(dir, files, requestedTop) {
  if (requestedTop) return requestedTop;
  // crude heuristic: scan all files for `module <name>` and guess the one
  // that looks most "top-like" (not instantiated by name in any other file)
  const modules = [];
  files.forEach(f => {
    const text = fs.readFileSync(path.join(dir, f), 'utf8');
    const re = /\bmodule\s+([A-Za-z_]\w*)/g;
    let m;
    while ((m = re.exec(text))) modules.push(m[1]);
  });
  if (modules.length === 0) return null;
  return modules[modules.length - 1]; // last-declared module as a simple default
}

// ---------- /simulate (Verilator) ----------

app.post('/simulate', upload.single('file'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No file uploaded (field name must be "file")' });

  const dir = makeWorkdir();
  try {
    const files = materializeInput(dir, req.file);
    if (files.length === 0) {
      return res.status(400).json({ error: 'No .v/.sv files found in upload' });
    }

    const top = pickTopModule(dir, files, req.body.top);

    // --lint-only: fast, real Verilator lint pass (elaborates the design,
    // catches width mismatches, multi-driven nets, unused signals, etc.)
    // without requiring a testbench. This is the most useful "real tool"
    // mode for an uploaded design with no surrounding testbench.
    const args = ['--lint-only', '-Wall', '--timing'];
    if (top) args.push('--top-module', top);
    args.push(...files);

    const result = await runCmd('verilator', args, dir);

    res.json({
      tool: 'verilator',
      mode: 'lint-only',
      command: `verilator ${args.join(' ')}`,
      topModule: top,
      filesAnalyzed: files,
      exitCode: result.code,
      timedOut: !!result.timedOut,
      stdout: result.stdout,
      stderr: result.stderr,
      passed: result.ok
    });
  } catch (e) {
    res.status(500).json({ error: e.message || 'Verilator job failed' });
  } finally {
    cleanup(dir);
  }
});

// ---------- /synthesize (Yosys) ----------

app.post('/synthesize', upload.single('file'), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: 'No file uploaded (field name must be "file")' });

  const dir = makeWorkdir();
  try {
    const files = materializeInput(dir, req.file);
    if (files.length === 0) {
      return res.status(400).json({ error: 'No .v/.sv files found in upload' });
    }

    const top = pickTopModule(dir, files, req.body.top);
    if (!top) return res.status(400).json({ error: 'Could not detect a top module; pass one in the "top" field' });

    const netlistPath = path.join(dir, 'synth_out.v');

    // Real Yosys synthesis script: read sources, generic synth pass,
    // report cell/gate stats, write out a flattened gate-level netlist.
    const script = [
      ...files.map(f => `read_verilog -sv ${f}`),
      `synth -top ${top}`,
      `stat`,
      `write_verilog ${path.basename(netlistPath)}`
    ].join('\n');

    const scriptPath = path.join(dir, 'run.ys');
    fs.writeFileSync(scriptPath, script);

    const result = await runCmd('yosys', ['-s', 'run.ys'], dir);

    let netlist = null;
    if (fs.existsSync(netlistPath)) {
      netlist = fs.readFileSync(netlistPath, 'utf8');
    }

    res.json({
      tool: 'yosys',
      mode: 'synth (generic cell library)',
      command: `yosys -s run.ys`,
      script,
      topModule: top,
      filesAnalyzed: files,
      exitCode: result.code,
      timedOut: !!result.timedOut,
      stdout: result.stdout,
      stderr: result.stderr,
      netlist,
      passed: result.ok
    });
  } catch (e) {
    res.status(500).json({ error: e.message || 'Yosys job failed' });
  } finally {
    cleanup(dir);
  }
});

// ---------- Phase 2: Verification Studio routes ----------
// /coverage, /formal, /lint-assert — see src/verification.js
const verificationDeps = { upload, makeWorkdir, cleanup, materializeInput, pickTopModule };
registerCoverageRoute(app, verificationDeps);
registerFormalRoute(app, verificationDeps);
registerLintAssertRoute(app, verificationDeps);

const port = process.env.PORT || 8080;
app.listen(port, () => console.log(`Silicon Lint EDA backend listening on :${port}`));
