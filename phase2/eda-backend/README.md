# Silicon Lint — Real EDA Backend (Verilator + Yosys + SymbiYosys)

This is a **separate service** from the main Netlify site. It runs real
Verilator (simulation/lint/coverage), real Yosys (synthesis/structural
check), and real SymbiYosys (formal verification) inside a Docker
container, because these are native binaries that Netlify's serverless
functions cannot run.

## What's real here vs. what's not

- **Verilator lint (`/simulate`)** — 100% real. Runs `verilator --lint-only`
  on your uploaded file(s) and returns its actual stdout/stderr.
- **Yosys synthesis (`/synthesize`)** — 100% real. Runs an actual `synth`
  pass with Yosys's generic cell library and returns the real gate count
  report and a real synthesized netlist.
- **Verilator coverage (`/coverage`)** — 100% real (Phase 2). Builds with
  `--coverage`, runs the simulation, and post-processes with
  `verilator_coverage` for real line/toggle coverage numbers. Needs a
  self-checking testbench among the uploaded files — without one, nothing
  executes and coverage reads near zero (which is itself a useful signal).
- **SymbiYosys formal verification (`/formal`)** — 100% real (Phase 2).
  Runs actual `sby` bounded model checking (BMC) against `assert` /
  `assume` / `cover` SVA properties already present in the uploaded
  design. If the design has zero properties, the response flags that
  explicitly rather than reporting a misleading "pass".
- **Yosys structural check (`/lint-assert`)** — 100% real (Phase 2). A
  fast `yosys check` pass plus a count of assert/assume/cover statements
  found, useful as a quick pre-flight gate before paying the cost of a
  full formal run.
- **OpenROAD / physical design (floorplanning, placement, CTS, routing,
  GDSII)** is **not included** in this backend. OpenROAD needs a multi-GB
  image and long install/build times, which doesn't fit a quick "click
  deploy" setup. If you want this later, see "Adding OpenROAD" below.
- **AI-authored content** (the SVA assertions a user generates on the
  Verification Studio tab, UVM skeletons, coverage-hole explanations) is
  produced by Claude/Gemini/Ollama, **not** by this backend — this backend
  only runs real open-source tools against whatever Verilog/SVA text the
  AI or the user produced. See the main README and `netlify/functions/review.js`.

## Step 1 — Push this folder to GitHub

This can be a new repo, or a subfolder of your existing `silicon-lint` repo
— either works. If it's a subfolder, you'll point Railway/Render at this
subfolder as the "root directory" (Railway) or via `render.yaml`
(Render) in Step 2.

## Step 2 — Deploy (pick one)

### Option A — Railway

1. Go to https://railway.app and sign up (free tier available, can use
   GitHub login)
2. **New Project** → **Deploy from GitHub repo**
3. Select the repo containing this `eda-backend` folder
4. If this folder is a subfolder of a bigger repo: in the service's
   **Settings** tab, set **Root Directory** to `eda-backend`
5. Railway will detect the `Dockerfile` automatically and build it — this
   takes several minutes the first time (it's installing Verilator,
   Yosys, and building Boolector + SymbiYosys from source for Phase 2)
6. Once deployed, Railway gives you a public URL like
   `https://silicon-lint-eda-backend-production.up.railway.app`

### Option B — Render (free tier)

A `render.yaml` Blueprint file is included at the **repo root** (not
inside `eda-backend/`) so Render can auto-detect this service.

1. Go to https://dashboard.render.com and sign up (free, no card required
   for the Free plan)
2. **New** → **Blueprint**, connect the GitHub repo containing
   `render.yaml`
3. Render reads `render.yaml`, proposes a `silicon-lint-eda-backend` web
   service pointed at this `eda-backend/Dockerfile` — click **Apply**
4. First build takes several minutes (same Boolector/SymbiYosys build as
   above). Once live, Render gives you a URL like
   `https://silicon-lint-eda-backend.onrender.com`
5. **Free-tier caveat**: Render's free web services spin down after ~15
   minutes idle and take 30-60s to wake up on the next request. Fine for
   personal/demo use; if you need always-on, upgrade that one service to
   a paid instance type in the Render dashboard.

## Step 3 — Set environment variable (recommended, either platform)

In your platform's dashboard → your service → **Environment/Variables**:
- `ALLOWED_ORIGIN` = your Netlify site URL (e.g.
  `https://your-site.netlify.app`) — this locks down CORS so random sites
  can't hit your EDA backend. Leave unset (defaults to `*`) while testing
  locally if you want.

## Step 4 — Point the frontend at this backend

In your Netlify site's environment variables (Site configuration →
Environment variables), this isn't needed server-side — the **frontend**
calls this backend directly from the browser. You'll set the backend URL
once, directly in `public/index.html` (search for `edaBackendUrl`), or
just paste it into the "Real EDA Tools" tab's URL field each session. See
the main README for exact steps.

## Step 5 — Test it

```
curl https://YOUR-BACKEND-URL/health
```
Should return `{"status":"ok"}`. Then try uploading a `.v` file through
the site's "Real EDA Tools" tab, or generate assertions on the
"Verification Studio" tab and run them through `/lint-assert` or `/formal`.

## Using Ollama instead of a paid AI API (free, local)

The "Verification Studio" tab (and the original RTL Checker's AI review)
both let you pick **Ollama** as the model. Unlike Claude/Gemini, Ollama
calls are made **directly from your browser to your own machine** — this
backend and the Netlify function are never involved, so there's no API
key, no per-request cost, and your code never leaves your computer.

1. Install Ollama: https://ollama.com/download
2. Pull a model that's reasonably good at code, e.g.:
   ```
   ollama pull llama3.2
   ```
   (or a larger/more code-focused model if your machine can run it, e.g.
   `qwen2.5-coder`)
3. Make sure Ollama is running (`ollama serve`, or it auto-starts on
   most installs) — it listens on `http://localhost:11434` by default.
4. **CORS note**: by default Ollama only accepts requests from
   `http://localhost` origins. If you're running the Silicon Lint
   frontend from `https://your-site.netlify.app` instead of localhost,
   Ollama's CORS policy will block the browser request. Either:
   - run the frontend locally too (e.g. `npx serve public`) while using
     Ollama, or
   - set the `OLLAMA_ORIGINS` environment variable before starting Ollama:
     ```
     OLLAMA_ORIGINS=https://your-site.netlify.app ollama serve
     ```
5. On the Verification Studio tab, pick "Ollama (local, free)" from any
   model dropdown and click Generate — it calls
   `http://localhost:11434/api/chat` directly from your browser.

If you ever need a different local port or a remote Ollama install,
change the `ollamaUrl` constant near the top of the verification JS in
`public/index.html`.

## Cost notes

- Railway's free tier includes a monthly usage credit; Render's Free plan
  has no fixed cost but spins down when idle (see above). A small
  personal tool like this typically stays within either free tier.
- Each Verilator/Yosys job is capped at 30 seconds server-side; formal
  verification jobs (`/formal`, `/coverage`) get a longer 60-second cap
  since BMC can legitimately take longer — to prevent runaway jobs from
  racking up compute time.
- Uploads are capped at 25MB.

## Adding OpenROAD later (optional, advanced)

OpenROAD physical design (floorplan → placement → CTS → routing → GDSII)
needs a much bigger image (PDK files, OpenROAD-flow-scripts, several GB).
The cleanest way to add it without bloating this fast lint/synth/formal
backend:
1. Create a second service (Railway or Render) from a separate Dockerfile
   based on `openroad/flow-scripts` or `the-openroad-project/openroad`
   images.
2. Expose a `/physical-design` endpoint similar to `/synthesize` above,
   accepting a netlist + a PDK choice (e.g. the free, open Sky130 PDK) and
   returning the flow's logs + a GDSII file.
3. Point the frontend's "Physical Design" sub-tab at that second URL.
This is a substantial follow-up project on its own — happy to help build
it as a next step once the lint/synth/formal backend is live and working.
