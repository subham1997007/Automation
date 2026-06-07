#!/usr/bin/env zsh
# Generate project-specific Automation artifacts after copying Automation/ into a repo.

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$AUTOMATION_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
DEV_PY="$AUTOMATION_DIR/mcp-servers/dev-mcp/.venv/bin/python"

log() {
  echo "[bootstrap] $*"
}

run_best_effort() {
  local label="$1"
  shift
  log "$label"
   "$@" || log "Skipped: $label"
}

if [ -x "$DEV_PY" ]; then
  PY="$DEV_PY"
else
  PY="$PYTHON_BIN"
fi

mkdir -p "$AUTOMATION_DIR/docs" "$AUTOMATION_DIR/.memory"

run_best_effort "Detecting project and generating AGENTS/Copilot instructions..." \
  "$PY" "$AUTOMATION_DIR/scripts/setup_project.py"

run_best_effort "Refreshing repo and Confluence memory (incremental)..." \
  "$PY" "$AUTOMATION_DIR/scripts/refresh_memory.py" \
    --python-bin "$PY"

if [ -f "$AUTOMATION_DIR/.env.local" ]; then
  set -a
  . "$AUTOMATION_DIR/.env.local"
  set +a
fi

run_best_effort "Ensuring local brain model is present in Automation/models..." \
  zsh "$AUTOMATION_DIR/scripts/ensure_local_brain_model.sh"

# Confluence sync is handled by refresh_memory.py using incremental policy.

run_best_effort "Generating DevFlow analytics/report page..." \
  "$PY" "$AUTOMATION_DIR/scripts/generate_analytics.py"

if [ -f "$AUTOMATION_DIR/docs/automation-routing-diagram.html" ]; then
  log "Routing diagram ready: Automation/docs/automation-routing-diagram.html"
fi

log "Repo graph ready: Automation/docs/repo-graph.html"
if [ -f "$AUTOMATION_DIR/.memory/confluence-knowledge-index.json" ]; then
  log "Confluence knowledge ready: Automation/.memory/confluence-knowledge-index.json"
fi
log "Analytics ready: Automation/docs/devflow-analytics.html"
