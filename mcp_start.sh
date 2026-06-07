#!/usr/bin/env zsh
# Start local MCP servers by profile, plus the optional PO/SM UI.
#
# Usage:
#   ./Automation/mcp_start.sh                    # start UI only (legacy)
#   ./Automation/mcp_start.sh --profile dev      # start dev-mcp + jira-mcp + gitlab-mcp in parallel
#   ./Automation/mcp_start.sh --profile jira     # start jira-mcp only
#   ./Automation/mcp_start.sh --profile gitlab   # start gitlab-mcp only

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$AUTOMATION_DIR/.env.local"
RUNNER="$AUTOMATION_DIR/bin/mcp_runner.sh"
PROFILE=""

# ── Parse args ────────────────────────────────────────────────────────────────
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile|-p)
      shift
      PROFILE="$1"
      ;;
    *)
      ;;
  esac
  shift
done

log() {
  echo "[automation] $*"
}

# ── Load env ──────────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
  log "Loaded env from Automation/.env.local"
else
  log "No Automation/.env.local found. Jira/GitLab calls will fail until env vars are configured."
fi

log "Validating Automation config..."
python3 "$AUTOMATION_DIR/mcp_manager.py" --validate

if [ ! -f "$AUTOMATION_DIR/.memory/codebase-index.json" ]; then
  log "Generating repo cognition index..."
  python3 "$AUTOMATION_DIR/scripts/repo_graph.py" \
    --root "$AUTOMATION_DIR/.." \
    --output "$AUTOMATION_DIR/docs/repo-graph.html" || true
fi

# ── Profile mode: start servers in parallel ───────────────────────────────────
if [ -n "$PROFILE" ]; then
  case "$PROFILE" in
    dev)
      SERVERS=(dev-mcp jira-mcp gitlab-mcp)
      ;;
    jira)
      SERVERS=(jira-mcp)
      ;;
    gitlab)
      SERVERS=(gitlab-mcp)
      ;;
    test)
      SERVERS=(test-mcp)
      ;;
    review)
      SERVERS=(review-mcp)
      ;;
    *)
      echo "Unknown profile: $PROFILE. Valid: dev | jira | gitlab | test | review" >&2
      exit 1
      ;;
  esac

  PIDS=()
  log "Starting profile '$PROFILE': ${SERVERS[*]}"
  for server in "${SERVERS[@]}"; do
    log "  → $server"
    "$RUNNER" "$server" &
    PIDS+=($!)
  done

  # Trap Ctrl-C and kill all child processes cleanly
  trap 'log "Stopping all MCP servers..."; for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done; exit 0' INT TERM

  log "All servers running. Press Ctrl-C to stop."
  # Wait for all background jobs; exit when all finish
  for pid in "${PIDS[@]}"; do
    wait "$pid" || true
  done
  exit 0
fi

# ── Legacy mode: UI only ───────────────────────────────────────────────────────
log "Starting enabled agents..."
log "Starting PO/SM Operations UI at http://127.0.0.1:${AUTOMATION_UI_PORT:-8787}/"
python3 "$AUTOMATION_DIR/mcp_manager.py" --ui
