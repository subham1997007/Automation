#!/usr/bin/env zsh
# sync-gitlab-token.sh
#
# Automatically reads the current GitLab token from the local machine
# (glab OAuth/keyring, git credential store, or macOS Keychain) and writes it
# into .env.local.
#
# Run manually:
#   ./Automation/scripts/sync-gitlab-token.sh
#
# Called automatically by run_mcp_server.sh before every MCP server start.
#
# Safe to run repeatedly — only the GITLAB_TOKEN line is updated.
# Never prints the token to stdout.

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$AUTOMATION_DIR/.env.local"
ENV_EXAMPLE="$AUTOMATION_DIR/.env.local.example"
TOKEN_VALID_STAMP="$AUTOMATION_DIR/.memory/gitlab-token.last-valid"
TOKEN_VALID_TTL_SECONDS="${AUTOMATION_GITLAB_TOKEN_VALIDATE_TTL_SECONDS:-600}"

log() { echo "[sync-token] $*" >&2; }

# ── Bootstrap: ensure .env.local exists ────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  log "Created .env.local from example."
fi

# ── Read GitLab host from .env.local ───────────────────────────────────────
GITLAB_HOST=""
if grep -q "^GITLAB_BASE_URL=" "$ENV_FILE" 2>/dev/null; then
  raw_url="$(grep "^GITLAB_BASE_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d ' ')"
  # Strip protocol to get bare hostname
  GITLAB_HOST="${raw_url#https://}"
  GITLAB_HOST="${GITLAB_HOST#http://}"
  GITLAB_HOST="${GITLAB_HOST%%/*}"
fi

if [ -z "$GITLAB_HOST" ]; then
  log "GITLAB_BASE_URL not set in .env.local — cannot resolve host. Skipping token sync."
  exit 0
fi

log "Resolving token for host: $GITLAB_HOST"

mkdir -p "$AUTOMATION_DIR/.memory"

# ── Source 1: glab OAuth/keyring/config ───────────────────────────────────
resolve_from_glab() {
  if ! command -v glab >/dev/null 2>&1; then
    return 1
  fi

  local output
  output="$(glab auth status --hostname "$GITLAB_HOST" --show-token 2>/dev/null || true)"
  if [ -z "$output" ]; then
    return 1
  fi

  local token
  token="$(printf "%s\n" "$output" \
    | grep -Eo '(glpat-|glpoat-|gloas-|gldt-)[A-Za-z0-9_=-]+' \
    | head -1 || true)"

  if [ -z "$token" ]; then
    token="$(printf "%s\n" "$output" \
      | awk 'tolower($0) ~ /token/ {print $NF}' \
      | grep -E '^[A-Za-z0-9._~+/=-]{20,}$' \
      | head -1 || true)"
  fi

  if [ -n "$token" ]; then
    echo "$token"
    return 0
  fi
  return 1
}

# ── Source 2: ~/.git-credentials (git credential store) ───────────────────
resolve_from_git_credentials() {
  local creds_file="$HOME/.git-credentials"
  if [ ! -f "$creds_file" ]; then
    return 1
  fi
  # Format: https://oauth2:<token>@host or https://<user>:<token>@host
  local token
  token="$(grep -E "https://[^@]+@${GITLAB_HOST}" "$creds_file" 2>/dev/null \
    | sed -E "s|https://[^:]+:([^@]+)@.*|\1|" \
    | head -1)"
  if [ -n "$token" ] && [ "$token" != "x-oauth-basic" ]; then
    echo "$token"
    return 0
  fi
  return 1
}

# ── Source 3: macOS Keychain (internet password) ──────────────────────────
resolve_from_keychain() {
  if ! command -v security >/dev/null 2>&1; then
    return 1
  fi
  local token
  token="$(security find-internet-password -s "$GITLAB_HOST" -w 2>/dev/null || true)"
  if [ -n "$token" ]; then
    echo "$token"
    return 0
  fi
  return 1
}

# ── Source 4: git credential fill (runtime helper) ────────────────────────
resolve_from_git_credential_fill() {
  local token
  token="$(printf 'protocol=https\nhost=%s\n' "$GITLAB_HOST" \
    | git credential fill 2>/dev/null \
    | grep "^password=" \
    | sed 's/^password=//' \
    | head -1 || true)"
  if [ -n "$token" ] && [ "$token" != "x-oauth-basic" ] && [ "$token" != "" ]; then
    echo "$token"
    return 0
  fi
  return 1
}

# ── Resolve token from available sources in priority order ─────────────────
NEW_TOKEN=""

if token="$(resolve_from_glab 2>/dev/null)" && [ -n "$token" ]; then
  NEW_TOKEN="$token"
  log "Token resolved from glab auth"
elif token="$(resolve_from_git_credentials 2>/dev/null)" && [ -n "$token" ]; then
  NEW_TOKEN="$token"
  log "Token resolved from ~/.git-credentials"
elif token="$(resolve_from_keychain 2>/dev/null)" && [ -n "$token" ]; then
  NEW_TOKEN="$token"
  log "Token resolved from macOS Keychain"
elif token="$(resolve_from_git_credential_fill 2>/dev/null)" && [ -n "$token" ]; then
  NEW_TOKEN="$token"
  log "Token resolved from git credential fill"
fi

if [ -z "$NEW_TOKEN" ]; then
  log "Could not resolve a GitLab token from any local source."
  log "Sources tried: glab auth, ~/.git-credentials, macOS Keychain, git credential fill."
  log "Token in .env.local will not be changed."
  exit 0
fi

# ── Validate token against GitLab API — auto-refresh if expired ─────────────
validate_token() {
  local tkn="$1"
  local http_code
  # Try Bearer auth (OAuth tokens from glab)
  http_code="$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer ${tkn}" \
    "https://${GITLAB_HOST}/api/v4/user" 2>/dev/null || echo "000")"
  if [ "$http_code" = "200" ]; then
    return 0
  fi
  # Try PRIVATE-TOKEN auth (PATs)
  http_code="$(curl -s -o /dev/null -w "%{http_code}" \
    -H "PRIVATE-TOKEN: ${tkn}" \
    "https://${GITLAB_HOST}/api/v4/user" 2>/dev/null || echo "000")"
  if [ "$http_code" = "200" ]; then
    return 0
  fi
  return 1
}

token_fingerprint() {
  local tkn="$1"
  printf "%s" "$tkn" | shasum -a 256 | awk '{print $1}'
}

is_recently_validated() {
  local tkn="$1"
  if [ ! -f "$TOKEN_VALID_STAMP" ]; then
    return 1
  fi
  local fp now_ts stamp_fp stamp_ts age
  fp="$(token_fingerprint "$tkn")"
  stamp_fp="$(awk -F'|' 'NR==1 {print $1}' "$TOKEN_VALID_STAMP" 2>/dev/null || true)"
  stamp_ts="$(awk -F'|' 'NR==1 {print $2}' "$TOKEN_VALID_STAMP" 2>/dev/null || echo 0)"
  if [ -z "$stamp_fp" ] || [ -z "$stamp_ts" ] || [ "$stamp_fp" != "$fp" ]; then
    return 1
  fi
  now_ts="$(date +%s)"
  age=$((now_ts - stamp_ts))
  [ "$age" -lt "$TOKEN_VALID_TTL_SECONDS" ]
}

mark_recently_validated() {
  local tkn="$1"
  local fp now_ts
  fp="$(token_fingerprint "$tkn")"
  now_ts="$(date +%s)"
  printf "%s|%s\n" "$fp" "$now_ts" > "$TOKEN_VALID_STAMP"
}

auto_refresh_token() {
  log "⚠️  Token is expired/invalid. Auto-refreshing via glab OAuth..."
  if ! command -v glab >/dev/null 2>&1; then
    log "❌ glab CLI not installed — cannot auto-refresh. Install: brew install glab"
    return 1
  fi
  # Force logout + re-login to get a fresh OAuth token
  glab auth logout --hostname "$GITLAB_HOST" 2>/dev/null || true
  if glab auth login --hostname "$GITLAB_HOST" --web --git-protocol ssh 2>/dev/null; then
    log "✅ glab re-authentication successful."
  elif glab auth login --hostname "$GITLAB_HOST" --device --git-protocol ssh 2>/dev/null; then
    log "✅ glab re-authentication successful (device flow)."
  else
    log "❌ Auto-refresh failed. Manual action required:"
    log "   Run: ./Automation/scripts/glab-gitlab-login.sh"
    return 1
  fi
  # Extract the fresh token
  local fresh_token
  fresh_token="$(glab config get token --host "$GITLAB_HOST" 2>/dev/null || true)"
  if [ -z "$fresh_token" ]; then
    log "❌ Could not extract fresh token from glab after re-auth."
    return 1
  fi
  NEW_TOKEN="$fresh_token"
  return 0
}

CURRENT_TOKEN=""
if grep -q "^GITLAB_TOKEN=" "$ENV_FILE" 2>/dev/null; then
  CURRENT_TOKEN="$(grep "^GITLAB_TOKEN=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d ' ')"
fi

# Fast path: keep current token when still valid; avoid repeated auth churn.
if [ -n "$CURRENT_TOKEN" ]; then
  if is_recently_validated "$CURRENT_TOKEN"; then
    log "Current token recently validated — skipping remote auth check."
    exit 0
  fi
  if validate_token "$CURRENT_TOKEN"; then
    mark_recently_validated "$CURRENT_TOKEN"
    log "Current token is valid — no re-auth needed."
    exit 0
  fi
  log "Current token appears expired/invalid — attempting refresh flow."
fi

# Validate resolved token; only re-authenticate when token is actually invalid/expired.
if ! validate_token "$NEW_TOKEN"; then
  if auto_refresh_token; then
    if ! validate_token "$NEW_TOKEN"; then
      log "❌ Token still invalid after auto-refresh. Manual intervention needed."
      log "   Visit: https://${GITLAB_HOST}/-/user_settings/personal_access_tokens"
      exit 1
    fi
  else
    exit 1
  fi
fi
mark_recently_validated "$NEW_TOKEN"
log "✅ Token validated against GitLab API."

# ── Compare with existing token ────────────────────────────────────────────

if [ "$NEW_TOKEN" = "$CURRENT_TOKEN" ]; then
  log "Token is already up to date — no write needed."
  exit 0
fi

# ── Write updated token into .env.local (in-place, safe) ──────────────────
TMPFILE="$(mktemp)"
if grep -q "^GITLAB_TOKEN=" "$ENV_FILE"; then
  sed "s|^GITLAB_TOKEN=.*|GITLAB_TOKEN=${NEW_TOKEN}|" "$ENV_FILE" > "$TMPFILE"
else
  cp "$ENV_FILE" "$TMPFILE"
  echo "GITLAB_TOKEN=${NEW_TOKEN}" >> "$TMPFILE"
fi
mv "$TMPFILE" "$ENV_FILE"

log "GITLAB_TOKEN updated in .env.local ✓"

