#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# controller_validation_task — Linux environment bootstrap
#
# Idempotent: safe to re-run. Does four things:
#   1. install the system libraries PsychoPy needs (apt, optional)
#   2. install `uv` if it isn't already on PATH
#   3. create .venv and sync dependencies from pyproject.toml / uv.lock
#   4. smoke-test that every module imports
#
# Usage:  bash setup_env.sh
# Skip the apt stage (e.g. on a machine without sudo):  SKIP_APT=1 bash setup_env.sh
# ---------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

SKIP_APT="${SKIP_APT:-0}"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. System libraries
# ---------------------------------------------------------------------------
# PsychoPy's window/rendering stack needs GL, GLUT and the X extensions that
# pyglet dlopen()s at runtime. pyserial/pyparallel need no system packages;
# liblsl is bundled inside the pylsl wheel.
APT_PACKAGES=(
  # OpenGL / GLUT — pyglet's window + shape rendering
  libgl1-mesa-glx libglu1-mesa freeglut3-dev
  # X11 extensions pyglet dlopen()s (missing ones fail at import, not install)
  libxrandr2 libxinerama1 libxcursor1 libxi6
  # Pillow / matplotlib image + font handling
  libjpeg-dev zlib1g-dev libfreetype6-dev
  # Serial port access helper (optional, for `sync.backend = serial`)
  setserial
)

if [[ "${SKIP_APT}" == "1" ]]; then
  log "SKIP_APT=1 — skipping system package install."
elif ! command -v apt-get >/dev/null 2>&1; then
  warn "apt-get not found; skipping system packages. Install the equivalents manually:"
  warn "  ${APT_PACKAGES[*]}"
elif ! sudo -n true 2>/dev/null && [[ -z "${CI:-}" ]]; then
  warn "No passwordless sudo; skipping system packages."
  warn "If PsychoPy fails to open a window, install these and re-run:"
  warn "  sudo apt-get install -y ${APT_PACKAGES[*]}"
else
  log "Installing system packages (${#APT_PACKAGES[@]} of them)…"
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends "${APT_PACKAGES[@]}"
fi

# ---------------------------------------------------------------------------
# 2. uv
# ---------------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  log "uv already installed ($(uv --version))."
else
  log "Installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # The installer drops uv in ~/.local/bin, which may not be on PATH yet.
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v uv >/dev/null 2>&1 || die "uv install failed; see https://docs.astral.sh/uv/"
fi

# ---------------------------------------------------------------------------
# 3. Virtualenv + dependencies
# ---------------------------------------------------------------------------
log "Creating .venv and syncing dependencies…"
uv venv --python 3.10

# On a slow or flaky connection, try the local uv cache first: if another
# project on this machine already installed PsychoPy & friends, this completes
# in seconds with no network at all. Fall back to a normal (online) sync.
if [[ "${OFFLINE:-0}" == "1" ]]; then
  log "OFFLINE=1 — resolving from the uv cache only."
  uv sync --extra dev --offline
elif uv sync --extra dev --offline 2>/dev/null; then
  log "Resolved everything from the local uv cache (no download needed)."
else
  log "Cache incomplete; downloading. PsychoPy + Qt is ~130 MB, be patient."
  UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-600}" uv sync --extra dev
fi

# ---------------------------------------------------------------------------
# 4. Import smoke test
# ---------------------------------------------------------------------------
# Import the display-free modules only. Importing `task`/`session` would pull
# in psychopy.visual, which needs a DISPLAY and would fail on a headless box.
log "Smoke-testing imports…"
uv run python - <<'PY'
import importlib
mods = [
    "controller_validation_task",
    "controller_validation_task.settings",
    "controller_validation_task.paths",
    "controller_validation_task.design",
    "controller_validation_task.layout",
    "controller_validation_task.events",
    "controller_validation_task.markers",
    "controller_validation_task.sync",
]
for m in mods:
    importlib.import_module(m)
    print(f"  ok  {m}")

from controller_validation_task import layout, paths
lay = layout.load_layout()
print(f"  ok  layout: {len(lay.buttons)} buttons, image {lay.image_size}")
assert lay.image_path.is_file(), f"controller image missing: {lay.image_path}"
print(f"  ok  assets: {paths.default_assets_dir()}")
PY

log "Done. Run the task with:  bash run.sh"
log "First launch opens the config wizard; it writes ./config.json."
