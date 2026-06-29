# Silicon Lint — Deploy Guide

This folder is ready to deploy to Netlify. It includes:
- `public/index.html` — the full site (frontend)
- `netlify/functions/review.js` — a serverless function that calls the Anthropic API server-side, so your API key is never exposed in the browser
- `netlify.toml` — tells Netlify where to find the site and functions

## Step 1 — Get an Anthropic API key
1. Go to https://console.anthropic.com
2. Sign up / log in
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-...`) — you'll need it in Step 4

Note: this is a **separate** account from claude.ai. Using the API costs a small amount per request (a typical RTL file review costs a fraction of a cent to a few cents depending on file size). You'll need to add a payment method in the Anthropic console to use it beyond the free trial credits.

## Step 2 — Push this folder to GitHub
1. Create a new repository on https://github.com (can be private)
2. From this folder, run:
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```

## Step 3 — Deploy to Netlify
1. Go to https://app.netlify.com and sign up (free, can use GitHub login)
2. Click **Add new site** → **Import an existing project**
3. Choose **GitHub** and select the repository you just pushed
4. Netlify will auto-detect the settings from `netlify.toml` — just click **Deploy**
5. Wait ~1 minute. You'll get a live URL like `https://random-name-123.netlify.app`

## Step 4 — Add your API key (critical step)
1. In your Netlify site dashboard, go to **Site configuration** → **Environment variables**
2. Click **Add a variable**
3. Key: `ANTHROPIC_API_KEY`
4. Value: paste the key you copied in Step 1
5. Save, then go to **Deploys** tab → **Trigger deploy** → **Deploy site** (so the function picks up the new variable)

## Step 5 — Test it
1. Open your live Netlify URL
2. Go to the RTL Checker tab, upload a `.v` file
3. Click "Run AI Deep Review" — it should now work, calling your serverless function instead of any browser-exposed key

## Optional — Custom domain
In Netlify: **Domain management** → **Add a domain**. You can either buy a domain through Netlify or point an existing domain's DNS to your Netlify site (free either way, you just pay the domain registrar if buying new).

## Cost control tips
- Anthropic billing is pay-per-use; set a spending limit in the Anthropic console under **Settings → Limits**
- The function already rejects files over ~60,000 characters to avoid runaway costs on huge uploads
- Netlify's free tier includes 125,000 function calls/month, which is far more than a small/personal site will use

## New: Multiple AI Models + Comparison Mode
The RTL Checker tab now has a model picker (Claude, Gemini, and slots ready
for ChatGPT/Grok once you add their keys) and a "Comparison mode" toggle
that runs several models side-by-side on the same file.

To enable Gemini:
1. Get a key from https://aistudio.google.com/apikey
2. In Netlify → **Site configuration → Environment variables**, add
   `GEMINI_API_KEY` with that key
3. Redeploy (Deploys tab → Trigger deploy)

To enable ChatGPT or Grok later: add `OPENAI_API_KEY` / `XAI_API_KEY` in
Netlify, then add a `callChatgpt`/`callGrok` function in
`netlify/functions/review.js` (follow the `callGemini` function as a
template) and wire it into the `if (model === ...)` dispatch at the top of
the handler. The frontend's model cards for ChatGPT/Grok will activate
automatically — just remove `disabled` from their `.model-card` in
`public/index.html`.

## New: ZIP / Multi-file Project Upload
The RTL Checker's dropzone now also accepts `.zip` files. It extracts every
`.v`/`.sv`/`.vhd` file inside, concatenates them with file-boundary markers,
and runs both the static checker and AI review across the whole project.

## New: Real EDA Tools (Verilator + Yosys)
The new **Real EDA Tools** tab runs your RTL through actual Verilator and
Yosys binaries — not a simulation of their output. This requires a second,
separate backend deployment because Netlify can't run native EDA binaries.
See **`eda-backend/README.md`** for full deployment instructions (Railway
or Render — see below). Once deployed, paste your backend URL into the
banner at the top of the Real EDA Tools tab.

## New (Phase 2): Verification Studio
A new **Verification Studio** tab adds:
- **Assertions Generator** — AI-generated SystemVerilog Assertions (SVA)
  for the design loaded on the RTL Checker tab, with a one-click real
  `yosys check` pass against the combined design+assertions.
- **Coverage Report** — real `verilator --coverage` + `verilator_coverage`
  line/toggle coverage on an uploaded design+testbench, with a visual
  coverage bar and an optional AI pass that explains the coverage holes.
- **Formal Verification** — real `SymbiYosys` (sby) bounded model checking
  against assert/assume/cover properties in your design, with a clear
  PASS/FAIL/UNKNOWN status badge and counterexample trace when a property
  fails.
- **UVM Skeleton Generator** — AI-generated starter UVM testbench scaffold
  (driver, monitor, sequencer, agent, env, test) for the loaded design.

All real-tool execution happens on the same `eda-backend` service as the
Real EDA Tools tab (new endpoints: `/coverage`, `/formal`, `/lint-assert`)
— no extra deployment needed, just redeploy the backend with the updated
code. See **`eda-backend/README.md`** for what's real vs. AI-generated in
this phase.

## New: Deploy the backend on Render (free tier alternative to Railway)
A `render.yaml` Blueprint is included at the repo root. In short: push to
GitHub, then on https://dashboard.render.com choose **New → Blueprint**
and point it at this repo — Render builds `eda-backend/Dockerfile`
automatically. Full steps, including the free-tier idle/wake-up caveat,
are in **`eda-backend/README.md`**.

## New: Ollama support (free, local AI — no API key, no per-request cost)
Every AI model picker in the app (RTL Checker, and all of Verification
Studio) now includes an **Ollama** option. Unlike Claude/Gemini, this
calls `http://localhost:11434` directly from your browser — your code
never leaves your machine, and there's no API key or per-request billing.
Setup (install Ollama, pull a model, handle CORS if not running the
frontend from localhost) is documented in **`eda-backend/README.md`**
under "Using Ollama instead of a paid AI API".

## Roadmap — what's not built yet
This project follows a phased plan; only Phase 1 (RTL Checker, ZIP
uploads, real Verilator/Yosys, AI review/fix/testbench) and Phase 2
(Verification Studio above) are implemented. Not yet built, in rough
priority order:
- **Phase 3 — Synthesis Studio**: richer area/timing/power reporting
  beyond the existing `/synthesize` gate-count output (the timing/power
  numbers shown in early product sketches need a real STA tool like
  OpenSTA wired in — Yosys alone doesn't produce timing/power numbers).
- **Phase 4 — Physical Design (OpenROAD)**: floorplan → placement → CTS →
  routing → GDSII. Deliberately deferred — see "Adding OpenROAD later" in
  `eda-backend/README.md` for why (multi-GB image, long build times) and
  a concrete plan for a second, separate backend service.
- **Phase 5 — AI Copilot**: a single natural-language box ("Create AXI4
  Lite Slave") that generates RTL + testbench + assertions + constraints
  together. The pieces it would compose (AI fix/testbench generation,
  this phase's assertion generator) already exist independently; the
  unified copilot UI/orchestration does not yet.
- **Phase 6 — Full cloud architecture** (API gateway, job queue,
  Postgres/Redis/BullMQ worker pool): the current architecture (Netlify
  frontend + functions + one EDA backend container) is intentionally
  simpler and fine for personal/demo-scale use. The queue/worker
  architecture matters once jobs are long enough or concurrent enough
  to need real scheduling — formal verification jobs in this phase are
  the first ones likely to need it.
