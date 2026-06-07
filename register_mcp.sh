#!/usr/bin/env zsh
# Configure this Automation folder as GitHub Copilot MCP servers by profile.

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$AUTOMATION_DIR/.." && pwd)"
RUNNER="$AUTOMATION_DIR/bin/mcp_runner.sh"
ENV_FILE="$AUTOMATION_DIR/.env.local"
ENV_EXAMPLE="$AUTOMATION_DIR/.env.local.example"
JETBRAINS_MCP="$HOME/.config/github-copilot/intellij/mcp.json"
PROJECT_MCP="$PROJECT_DIR/.github/copilot/mcp.json"
VSCODE_MCP="$PROJECT_DIR/.vscode/mcp.json"
WINDSURF_MCP="$HOME/.codeium/windsurf/mcp_config.json"
PROFILE="${1:-jira}"

log() {
  echo "[setup] $*"
}

usage() {
  cat <<EOF
Usage:
  ./Automation/register_mcp.sh <profile>
  ./Automation/register_mcp.sh auto "implement Jira story PROJ-123"

Profiles:
  auto        -> selects jira/gitlab/test/review/dev from the request text
  jira        -> jira-mcp only
  gitlab      -> gitlab-mcp only
  test        -> test-mcp only
  review      -> review-mcp only
  dev         -> dev-mcp only (Feature planning, batch story creation, and end-to-end story implementation)
  databricks-custom -> YOUR OWNED Databricks MCP server (44 tools in one file, fully editable)
  none        -> remove Automation MCP servers from Copilot config

Note: databricks is always included alongside any profile (persistent addon).

Options:
  --no-bootstrap  write MCP config only; do not create venvs/install dependencies now
EOF
}

BOOTSTRAP=1
REQUEST_TEXT=""
if [ "$#" -gt 0 ]; then
  shift || true
fi
for arg in "$@"; do
  case "$arg" in
    --no-bootstrap)
      BOOTSTRAP=0
      ;;
    *)
      REQUEST_TEXT="${REQUEST_TEXT} ${arg}"
      ;;
  esac
done
REQUEST_TEXT="${REQUEST_TEXT#"${REQUEST_TEXT%%[![:space:]]*}"}"

if [ "$PROFILE" = "auto" ]; then
  SELECTED_PROFILE="$(python3 "$AUTOMATION_DIR/scripts/auto_profile.py" "$REQUEST_TEXT")"
  log "Auto-selected profile '$SELECTED_PROFILE' for request: ${REQUEST_TEXT:-<empty>}"
  PROFILE="$SELECTED_PROFILE"
fi

case "$PROFILE" in
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
  dev)
    SERVERS=(dev-mcp jira-mcp gitlab-mcp)
    ;;
  databricks-custom)
    SERVERS=(databricks-custom)
    ;;
  none)
    SERVERS=()
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown profile: $PROFILE" >&2
    usage >&2
    exit 2
    ;;
esac

mkdir -p "$HOME/.config/github-copilot/intellij"
mkdir -p "$PROJECT_DIR/.github/copilot" "$PROJECT_DIR/.vscode"
mkdir -p "$HOME/.codeium/windsurf"

if [ ! -f "$ENV_FILE" ]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  log "Created Automation/.env.local from example. Fill values before using MCP tools."
else
  log "Automation/.env.local already exists."
fi

chmod +x "$RUNNER" "$AUTOMATION_DIR/mcp_start.sh"
chmod +x "$AUTOMATION_DIR/bootstrap.sh" "$AUTOMATION_DIR/scripts/auto_profile.py" 2>/dev/null || true

python3 - "$RUNNER" "$JETBRAINS_MCP" "$PROJECT_MCP" "$VSCODE_MCP" "$WINDSURF_MCP" "${SERVERS[@]}" <<'PY'
import json
import sys
from pathlib import Path

runner = sys.argv[1]
copilot_targets = sys.argv[2:5]
windsurf_target = sys.argv[5]
servers = sys.argv[6:]

def config_for(name):
    config = {
        "type": "stdio",
        "command": "zsh",
        "args": [runner, name],
    }
    if name == "dev-mcp":
        config["env"] = {"AUTOMATION_DEV_WORKTREE": "1"}
    return config

server_config = {name: config_for(name) for name in servers}

# Always include databricks-custom alongside any profile (persistent addon)
# unless the profile explicitly selected "none" (empty servers list)
if servers and "databricks-custom" not in server_config:
    server_config["databricks-custom"] = config_for("databricks-custom")

copilot_config = {"servers": server_config}

for target in copilot_targets:
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(copilot_config, indent=2) + "\n", encoding="utf-8")
    print(f"[setup] Wrote {path}")

windsurf_path = Path(windsurf_target)
windsurf_path.parent.mkdir(parents=True, exist_ok=True)
if windsurf_path.exists():
    try:
        windsurf_config = json.loads(windsurf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        windsurf_config = {}
else:
    windsurf_config = {}

existing = windsurf_config.get("mcpServers") or {}
for name in ("jira-mcp", "gitlab-mcp", "test-mcp", "review-mcp", "dev-mcp", "databricks-custom"):
    existing.pop(name, None)
existing.update(server_config)
windsurf_config["mcpServers"] = existing
windsurf_path.write_text(json.dumps(windsurf_config, indent=2) + "\n", encoding="utf-8")
print(f"[setup] Wrote {windsurf_path}")
PY

if [ "${#SERVERS[@]}" -eq 0 ]; then
  log "No MCP servers enabled for profile: $PROFILE"
else
  log "Enabled MCP servers for profile '$PROFILE': ${SERVERS[*]}"
  for server in "${SERVERS[@]}"; do
    if [ "$BOOTSTRAP" -eq 1 ]; then
      if [ "$server" = "databricks" ] || [ "$server" = "databricks-custom" ]; then
        log "Verifying databricks MCP server (pre-installed, skipping venv bootstrap)..."
        if [ -x "$AUTOMATION_DIR/mcp-servers/databricks/.venv/bin/python" ]; then
          log "  databricks .venv OK"
        else
          log "  WARNING: databricks .venv missing — run: bash Automation/mcp-servers/databricks/repo/install.sh"
        fi
      else
        log "Bootstrapping $server dependencies..."
        "$RUNNER" "$server" < /dev/null >/dev/null
      fi
    else
      log "Skipping bootstrap for $server."
    fi
  done
  if [ "$BOOTSTRAP" -eq 1 ]; then
    log "Generating project artifacts and local memory..."
    "$AUTOMATION_DIR/bootstrap.sh" || log "Artifact generation skipped."
  fi
fi

log "Done. Restart JetBrains/Copilot/Windsurf after changing profiles."
