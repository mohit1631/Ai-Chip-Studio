FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir flask flask-cors python-dotenv

# NOTE: OpenROAD itself is NOT installed in this image. It needs several
# GB of RAM per real run -- far beyond Render's free tier (512MB) and
# even most of its smaller paid tiers. This image will boot and serve
# /health and /pdks (so you can verify the deploy itself works), but a
# real /run request calling physical_design_runner.py's `openroad`
# subprocess will fail with "command not found" -- expected on this
# deployment path. Install OpenROAD here once you have a real paid-tier
# VM sized for it (see https://github.com/The-OpenROAD-Project/OpenROAD
# for build/install instructions); until then, treat this service's free
# deployment as an API-shape/health check, not a working PD pipeline.

# FreePDK45 + Nangate Open Cell Library, packaged as an ASIC design kit
# (Apache-2.0 / open-source, no NDA/license fee -- see pdk_presets.py for
# the is_real_fab=False caveat: this PDK is academic/predictive-only, it
# does not correspond to a real fab process). Cloned at build time rather
# than committed to this repo since it's ~tens of MB and not ours to vendor.
# Skip with `docker build --build-arg SKIP_FREEPDK45=1` if you don't need it
# (e.g. only running sky130/gf180mcu jobs, where the PDK lives elsewhere).
ARG SKIP_FREEPDK45=0
RUN if [ "$SKIP_FREEPDK45" != "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends git \
        && rm -rf /var/lib/apt/lists/* \
        && git clone --depth 1 https://github.com/mflowgen/freepdk-45nm.git /pdk/freepdk45; \
    fi

COPY . .
EXPOSE 8004
CMD ["python", "app.py"]
