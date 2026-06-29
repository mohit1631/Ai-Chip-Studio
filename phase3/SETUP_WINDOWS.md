# Running AI Chip Studio (Phase 3 prototype) on Windows

This is a small working slice of the roadmap: a web page where you upload RTL
(`.v`/`.sv`/`.zip`) and it runs through Yosys, showing the generated netlist and
log for download. It's the smallest real version of Phase 3 Sprint 1.

Yosys is a Linux tool. On Windows, the reliable way to run it is **WSL2**
(Windows Subsystem for Linux) — a real Linux environment that runs alongside
Windows, not a separate computer or VM you manage yourself.

## Step 1 — Install WSL2

Open **PowerShell as Administrator** (right-click Start menu → "Windows
Terminal (Admin)" or search "PowerShell" → right-click → "Run as administrator")
and run:

```powershell
wsl --install
```

This installs Ubuntu by default. Restart your computer when prompted.

After restart, an Ubuntu terminal window should open automatically and ask you
to create a username and password for your Linux environment (this is separate
from your Windows login — pick anything, you'll use it for `sudo` commands).

If it doesn't open automatically, search "Ubuntu" in the Start menu and open it.

**Already have WSL?** Run `wsl --list --verbose` in PowerShell to check. If you
see a distro listed as version 2, skip to Step 2.

## Step 2 — Install Yosys and Python inside WSL/Ubuntu

In the Ubuntu terminal window:

```bash
sudo apt update
sudo apt install -y yosys python3 python3-pip python3-venv
```

Verify it installed:

```bash
yosys -V
```

You should see a version string printed (e.g. `Yosys 0.x ...`).

## Step 3 — Copy the app into WSL

Your Windows files are reachable from WSL under `/mnt/c/...`. If you downloaded
this project to, say, `C:\Users\YourName\Downloads\app`, copy it into your Linux
home folder (better performance, fewer path-translation issues than running
directly off `/mnt/c`):

```bash
cp -r /mnt/c/Users/YourName/Downloads/app ~/ai-chip-studio
cd ~/ai-chip-studio
```

Adjust the path to wherever you actually unzipped it.

## Step 4 — Set up a Python virtual environment and install Flask

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

You should now see `(venv)` at the start of your terminal prompt.

## Step 5 — Run it

```bash
python3 app.py
```

You should see output ending in something like:

```text
Running on http://127.0.0.1:5000
```

## Step 6 — Open it in your Windows browser

WSL2 automatically forwards `localhost` ports to Windows. Open your normal
Windows browser (Chrome, Edge, etc.) and go to:

```text
http://localhost:5000
```

You should see the AI Chip Studio synthesis page. Upload a `.v` file (a sample
is below) and click **Run Synthesis** — you should get a result page with a
downloadable `netlist.v` and `synthesis.log`.

## Sample RTL to try first

Save this as `counter.v` and upload it:

```verilog
module counter(input clk, input rst, output reg [3:0] q);
  always @(posedge clk) begin
    if (rst) q <= 0;
    else q <= q + 1;
  end
endmodule
```

## Stopping the server

Go back to the Ubuntu terminal and press `Ctrl+C`.

To run it again later, you only need to repeat Steps 4 (activate the venv) and
5 (run `python3 app.py`) — Step 2's Yosys install and Step 4's `pip install`
only need to happen once.

## Troubleshooting

| Problem | Likely cause / fix |
|---|---|
| `yosys: command not found` | Step 2 didn't complete — re-run `sudo apt install -y yosys` |
| `ModuleNotFoundError: No module named 'flask'` | The venv isn't activated — run `source venv/bin/activate` again, you should see `(venv)` in the prompt |
| Browser says "can't connect" to localhost:5000 | Check the Ubuntu terminal is still running `python3 app.py` and didn't crash — read the error in that terminal |
| "Could not unambiguously determine the top module" | Your project has either zero or multiple modules with no clear top — type the top module name into the "Top module" field on the upload page |
| Upload of a `.zip` says "no .v/.sv RTL files" | Check the ZIP actually contains `.v`/`.sv` files, not just other file types |

## What this prototype does *not* yet include

This covers Phase 3 Sprint 1 only. Not included here (see the roadmap's other
files for what's planned): AI bug detection/fixing (Phase 1), simulation UI
(Phase 1 Sprint 4 — `code/simulation_runner.py` is included but not wired into
this web UI yet), area/timing/power analysis (Phase 3 Sprints 2–4), physical
design (Phase 4), AI Copilot (Phase 5), accounts/auth, or any of the security
hardening in `11_security_roadmap.md` beyond the path-traversal and file-size
checks already in `synthesis_runner.py`. This is a local single-user dev
prototype, not something to expose to the internet as-is.
