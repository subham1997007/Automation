#!/usr/bin/env zsh
# Guided GitLab personal access token setup.
#
# This script does not scrape or capture tokens from the browser. GitLab shows
# a new PAT only once, so the safe workflow is:
#   1. open the official token page
#   2. user creates the token in GitLab
#   3. user pastes it once into this script
#   4. script writes only GITLAB_TOKEN in Automation/.env.local
#
# Run:
#   ./Automation/scripts/create-gitlab-token.sh

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$AUTOMATION_DIR/.env.local"
ENV_EXAMPLE="$AUTOMATION_DIR/.env.local.example"
TOKEN_URL_DEFAULT="https://git.mbos.cloud/-/user_settings/personal_access_tokens?page=1&state=active&sort=expires_asc"
TOKEN_NAME_DEFAULT="git new token"

log() { echo "[gitlab-token] $*" >&2; }

ensure_env_file() {
  if [ ! -f "$ENV_FILE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    log "Created Automation/.env.local from example."
  fi
}

env_value() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//' || true
}

write_env_value() {
  local key="$1"
  local value="$2"
  local tmpfile
  tmpfile="$(mktemp)"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" > "$tmpfile"
  else
    cp "$ENV_FILE" "$tmpfile"
    printf "\n%s=%s\n" "$key" "$value" >> "$tmpfile"
  fi
  mv "$tmpfile" "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

open_token_page() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  else
    log "Open this URL in your browser: $url"
  fi
}

print_instructions() {
  local token_name="$1"
  local url="$2"
  cat >&2 <<EOF

Create the GitLab token in the browser:

URL:
  $url

Token name:
  $token_name

Required scopes for full Automation features:
  read_user
  read_repository
  read_api
  write_repository
  api

Optional scopes only if your project really needs registry/runner/AI access:
  read_registry
  write_registry
  read_virtual_registry
  write_virtual_registry
  self_rotate
  ai_features
  create_runner
  manage_runner
  k8s_proxy

Recommended:
  Use the minimum required scopes. For Jira/GitLab reports, MR reading,
  branch/MR creation, and project API calls, api + read_repository +
  write_repository is usually enough.

After GitLab shows the token, paste it here. It will be hidden and saved to:
  Automation/.env.local

EOF
}

validate_token_shape() {
  local token="$1"
  if [ "${#token}" -lt 20 ]; then
    log "Token looks too short. Nothing was saved."
    exit 1
  fi
}

main() {
  ensure_env_file

  local configured_url
  configured_url="$(env_value GITLAB_BASE_URL)"
  local token_url="$TOKEN_URL_DEFAULT"
  if [ -n "$configured_url" ] && [[ "$configured_url" != "https://gitlab.com" ]]; then
    token_url="${configured_url%/}/-/user_settings/personal_access_tokens?page=1&state=active&sort=expires_asc"
  fi

  local token_name="${1:-$TOKEN_NAME_DEFAULT}"

  print_instructions "$token_name" "$token_url"
  open_token_page "$token_url"

  local token
  printf "Paste generated GitLab token: " >&2
  read -rs token
  printf "\n" >&2

  validate_token_shape "$token"
  write_env_value "GITLAB_TOKEN" "$token"

  log "GITLAB_TOKEN saved to Automation/.env.local."
  log "Token was not printed to terminal."
  log "Next check: ./Automation/scripts/sync-gitlab-token.sh"
}

main "$@"
