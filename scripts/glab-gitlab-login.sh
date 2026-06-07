#!/usr/bin/env zsh
# Start a safe GitLab OAuth login through glab, then sync the token into
# Automation/.env.local for the MCP servers.
#
# Run:
#   ./Automation/scripts/glab-gitlab-login.sh

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$AUTOMATION_DIR/.env.local"
ENV_EXAMPLE="$AUTOMATION_DIR/.env.local.example"
SYNC_SCRIPT="$AUTOMATION_DIR/scripts/sync-gitlab-token.sh"

log() { echo "[glab-login] $*" >&2; }

print_client_id_help() {
  cat >&2 <<EOF

glab needs an OAuth Application ID for this self-managed GitLab host.

Create/configure an OAuth application in GitLab with:
  Redirect URI: http://localhost:7171/auth/redirect
  Confidential: unchecked / disabled
  Scopes: openid, profile, read_user, write_repository, api

Then save the Application ID locally:
  glab config set client_id <APPLICATION_ID> --host $gitlab_host

After that, rerun:
  ./Automation/scripts/glab-gitlab-login.sh

EOF
}

if [ ! -f "$ENV_FILE" ]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  log "Created Automation/.env.local from example."
fi

if ! command -v glab >/dev/null 2>&1; then
  log "glab CLI is not installed."
  log "Install it first, for example: brew install glab"
  exit 1
fi

raw_url="$(grep "^GITLAB_BASE_URL=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' ' || true)"
if [ -z "$raw_url" ]; then
  log "GITLAB_BASE_URL is not set in Automation/.env.local."
  exit 1
fi

gitlab_host="${raw_url#https://}"
gitlab_host="${gitlab_host#http://}"
gitlab_host="${gitlab_host%%/*}"

if [ -z "$gitlab_host" ]; then
  log "Could not resolve GitLab host from GITLAB_BASE_URL=$raw_url"
  exit 1
fi

log "Using GitLab host: $gitlab_host"

# ── Validate existing token against the API before trusting glab status ────
token_valid=false
if glab auth status --hostname "$gitlab_host" >/dev/null 2>&1; then
  existing_token="$(glab config get token --host "$gitlab_host" 2>/dev/null || true)"
  if [ -n "$existing_token" ]; then
    http_code="$(curl -s -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer ${existing_token}" \
      "https://${gitlab_host}/api/v4/user" 2>/dev/null || echo "000")"
    if [ "$http_code" = "200" ]; then
      token_valid=true
      log "glab token is valid (API verified)."
    else
      log "glab reports authenticated but token is expired (HTTP $http_code). Forcing re-login."
    fi
  fi
fi

if [ "$token_valid" = "false" ]; then
  # Force logout to clear stale token
  glab auth logout --hostname "$gitlab_host" 2>/dev/null || true
  log "Starting glab OAuth login. Complete the browser/device authorization when prompted."
  login_output="$(mktemp)"
  if ! glab auth login --hostname "$gitlab_host" --web --git-protocol ssh --use-keyring 2>"$login_output"; then
    cat "$login_output" >&2
    if grep -qi "client_id" "$login_output"; then
      print_client_id_help
      exit 1
    fi
    log "Web login did not complete. Trying device flow."
    if ! glab auth login --hostname "$gitlab_host" --device --git-protocol ssh --use-keyring 2>"$login_output"; then
      cat "$login_output" >&2
      if grep -qi "client_id" "$login_output"; then
        print_client_id_help
      fi
      exit 1
    fi
  fi
  rm -f "$login_output"
fi

"$SYNC_SCRIPT"
log "Done. MCP servers can now read GITLAB_TOKEN from Automation/.env.local."
