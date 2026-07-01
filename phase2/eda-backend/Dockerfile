# Silicon Lint EDA Backend
# Installs real, working open-source EDA tools:
#   - Verilator   (simulation / lint / coverage)
#   - Yosys       (synthesis / structural check)
#   - SymbiYosys + Boolector (formal verification / BMC) -- Phase 2
# OpenSTA/OpenROAD are intentionally NOT included here -- they are much
# heavier (multi-GB images, long build times) and are out of scope for a
# "click deploy" backend. See README for how to add an OpenROAD worker
# later if you need full physical design.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# ---- System deps + Verilator + Yosys from apt ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    build-essential \
    cmake \
    verilator \
    yosys \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ---- Node.js 20 LTS (ubuntu apt ships v12 which is too old for ?? operator) ----
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Confirm core tools are present at build time (fails the build loudly if not)
RUN verilator --version && yosys -V

# ---- Boolector (SMT solver used by SymbiYosys/yosys-smtbmc for BMC) ----
# Built from source: it's not in Ubuntu 22.04's apt repos, and it's the
# lightest-to-build solver SymbiYosys supports (faster/smaller than
# building Z3 or Yices from scratch in a free-tier build environment).
RUN git clone --depth 1 https://github.com/boolector/boolector.git /tmp/boolector \
    && cd /tmp/boolector \
    && ./contrib/setup-lingeling.sh \
    && ./contrib/setup-btor2tools.sh \
    && ./configure.sh \
    && cd build \
    && make -j"$(nproc)" \
    && make install \
    && rm -rf /tmp/boolector

# ---- SymbiYosys (sby) ----
# SymbiYosys is a Python driver script over Yosys + a solver, maintained
# in the YosysHQ/sby repo. It has no apt package, so install it straight
# from source via its own Makefile (just copies the script + libs).
RUN pip3 install --no-cache-dir click
RUN git clone --depth 1 https://github.com/YosysHQ/sby.git /tmp/sby \
    && cd /tmp/sby \
    && make install \
    && rm -rf /tmp/sby

# Confirm formal toolchain is present at build time too
RUN sby --version && boolector --version

WORKDIR /app

COPY package.json package.json
RUN npm install --omit=dev

COPY src ./src

ENV PORT=8080
EXPOSE 8080

CMD ["node", "src/server.js"]
