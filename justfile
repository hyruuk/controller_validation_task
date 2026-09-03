# controller_validation_task developer recipes
# Usage: `just <recipe>`. `just` itself must be installed (cargo / brew / apt).

# Default: show available recipes.
default:
    @just --list

# Set up the dev environment (Linux).
setup:
    bash setup_env.sh

# Run the task. Usage: `just run 01 001`
run subject session:
    bash run.sh {{subject}} {{session}}

# Run the pure-Python test suite (no display, no psychopy side effects).
test:
    uv run pytest tests/ -k "not integration" -v

# Run the display-bound integration tests (requires DISPLAY + psychopy).
test-integration:
    uv run pytest tests/ -k integration -v

# Lint.
lint:
    uv run ruff check controller_validation_task tests

# Auto-fix lint issues where possible.
lint-fix:
    uv run ruff check --fix controller_validation_task tests

# Re-resolve dependency lockfile.
lock:
    uv lock
