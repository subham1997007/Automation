#!/usr/bin/env zsh
# Generic stdio MCP runner for profile-based Copilot setup.

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: mcp_runner.sh <server-name>" >&2
  exit 2
fi

SERVER_NAME="$1"
AUTOMATION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$AUTOMATION_DIR/.env.local"
SERVER_DIR="$AUTOMATION_DIR/mcp-servers/$SERVER_NAME"
LOG_FILE="$AUTOMATION_DIR/mcp-server.log"
LOCAL_BRAIN_STAMP="$AUTOMATION_DIR/.memory/local-brain.last-ok"
PYTHON_BIN="${PYTHON_BIN:-}"
DEPENDENCY_MARKER="$SERVER_DIR/.venv/.automation-deps-installed"

if [ ! -d "$SERVER_DIR" ]; then
  echo "Unknown MCP server: $SERVER_NAME" >&2
  exit 1
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "No python interpreter found. Install Python 3 and retry." >&2
  exit 1
fi

{
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $SERVER_NAME invoked"
  echo "AUTOMATION_DIR=$AUTOMATION_DIR"
  echo "SERVER_DIR=$SERVER_DIR"
} >> "$LOG_FILE"

# Auto-sync GitLab token from local credential store / Keychain before loading env.
# This ensures the MCP server always starts with a fresh token without manual .env.local edits.
export MCP_STDIO=1  # Signal to sync script: non-interactive, skip browser OAuth
SYNC_SCRIPT="$AUTOMATION_DIR/scripts/sync-gitlab-token.sh"
if [ -f "$SYNC_SCRIPT" ]; then
  chmod +x "$SYNC_SCRIPT"
  "$SYNC_SCRIPT" >> "$LOG_FILE" 2>&1 || true
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] loaded .env.local" >> "$LOG_FILE"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] .env.local not found" >> "$LOG_FILE"
fi

# Local brain preflight for all profiles: if enabled, keep it warm and validate early.
LOCAL_BRAIN_MODE="${AUTOMATION_LOCAL_BRAIN:-false}"
if [ "$LOCAL_BRAIN_MODE" = "true" ] || [ "$LOCAL_BRAIN_MODE" = "1" ] || [ "$LOCAL_BRAIN_MODE" = "auto" ]; then
  mkdir -p "$AUTOMATION_DIR/.memory"
  BRAIN_MODEL="${AUTOMATION_LOCAL_BRAIN_MODEL:-}"
  if [ -n "$BRAIN_MODEL" ] && [ "${BRAIN_MODEL#/}" = "$BRAIN_MODEL" ]; then
    BRAIN_MODEL="$AUTOMATION_DIR/$BRAIN_MODEL"
  fi
  BRAIN_PY="$AUTOMATION_DIR/mcp-servers/dev-mcp/.venv/bin/python"
  BRAIN_GPU_LAYERS="${AUTOMATION_LOCAL_BRAIN_GPU_LAYERS:--1}"
  BRAIN_CTX="${AUTOMATION_LOCAL_BRAIN_CTX:-1024}"
  BRAIN_MAX_TOKENS="${AUTOMATION_LOCAL_BRAIN_MAX_TOKENS:-16}"
  BRAIN_TTL_SECONDS="${AUTOMATION_LOCAL_BRAIN_WARMUP_TTL_SECONDS:-900}"

  NOW_TS="$(date +%s)"
  LAST_OK_TS=0
  if [ -f "$LOCAL_BRAIN_STAMP" ]; then
    LAST_OK_TS="$(cat "$LOCAL_BRAIN_STAMP" 2>/dev/null || echo 0)"
  fi
  NEED_WARMUP=1
  if [ "$LAST_OK_TS" -gt 0 ] 2>/dev/null; then
    AGE=$((NOW_TS - LAST_OK_TS))
    if [ "$AGE" -lt "$BRAIN_TTL_SECONDS" ]; then
      NEED_WARMUP=0
    fi
  fi

  if [ "$NEED_WARMUP" -eq 1 ]; then
    if [ -z "$BRAIN_MODEL" ] || [ ! -f "$BRAIN_MODEL" ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] local brain enabled but model not found: ${BRAIN_MODEL:-<empty>}" >> "$LOG_FILE"
    elif [ ! -x "$BRAIN_PY" ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] local brain enabled but dev-mcp venv missing at $BRAIN_PY" >> "$LOG_FILE"
    else
      set +e
      if command -v timeout >/dev/null 2>&1; then
        timeout 25 "$BRAIN_PY" - <<PY >> "$LOG_FILE" 2>&1
import os
from llama_cpp import Llama

model_path = os.environ["AUTOMATION_LOCAL_BRAIN_MODEL"]
n_gpu_layers = int(os.environ.get("AUTOMATION_LOCAL_BRAIN_GPU_LAYERS", "-1"))
n_ctx = int(os.environ.get("AUTOMATION_LOCAL_BRAIN_CTX", "1024"))
max_tokens = max(1, int(os.environ.get("AUTOMATION_LOCAL_BRAIN_MAX_TOKENS", "16")))

llm = Llama(model_path=model_path, n_gpu_layers=n_gpu_layers, n_ctx=n_ctx, verbose=False)
llm("ping", max_tokens=max_tokens, temperature=0.0)
print("[local-brain] warmup ok")
PY
      else
        "$BRAIN_PY" - <<PY >> "$LOG_FILE" 2>&1
import os
from llama_cpp import Llama

model_path = os.environ["AUTOMATION_LOCAL_BRAIN_MODEL"]
n_gpu_layers = int(os.environ.get("AUTOMATION_LOCAL_BRAIN_GPU_LAYERS", "-1"))
n_ctx = int(os.environ.get("AUTOMATION_LOCAL_BRAIN_CTX", "1024"))
max_tokens = max(1, int(os.environ.get("AUTOMATION_LOCAL_BRAIN_MAX_TOKENS", "16")))

llm = Llama(model_path=model_path, n_gpu_layers=n_gpu_layers, n_ctx=n_ctx, verbose=False)
llm("ping", max_tokens=max_tokens, temperature=0.0)
print("[local-brain] warmup ok")
PY
      fi
      WARMUP_RC=$?
      set -e

      if [ "$WARMUP_RC" -eq 0 ]; then
        echo "$NOW_TS" > "$LOCAL_BRAIN_STAMP"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] local brain warmup complete" >> "$LOG_FILE"
      else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] local brain warmup failed" >> "$LOG_FILE"
      fi
    fi
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] local brain warmup skipped (fresh)" >> "$LOG_FILE"
  fi
fi

# Ensure every MCP server starts with a repo cognition index available.
# The tools load this index before processing requests, similar to a lightweight
# cognition graph preflight.
COGNITION_INDEX="$AUTOMATION_DIR/.memory/codebase-index.json"
REPO_GRAPH_SCRIPT="$AUTOMATION_DIR/scripts/repo_graph.py"
if [ ! -f "$COGNITION_INDEX" ] && [ -f "$REPO_GRAPH_SCRIPT" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] generating repo cognition index" >> "$LOG_FILE"
  "$PYTHON_BIN" "$REPO_GRAPH_SCRIPT" --root "$AUTOMATION_DIR/.." --output "$AUTOMATION_DIR/docs/repo-graph.html" >> "$LOG_FILE" 2>&1 || true
fi

# ── Clear stale .pyc so source edits take effect immediately ────────────
find "$SERVER_DIR/src" -name "*.pyc" -delete 2>/dev/null || true
find "$SERVER_DIR/src" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

cd "$SERVER_DIR"

# Auto-heal partially created/corrupted virtualenvs.
if [ -e "$SERVER_DIR/.venv/bin/python" ] && [ ! -x "$SERVER_DIR/.venv/bin/python" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] repairing broken .venv for $SERVER_NAME" >> "$LOG_FILE"
  rm -rf "$SERVER_DIR/.venv"
fi

if [ ! -x "$SERVER_DIR/.venv/bin/python" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] creating .venv for $SERVER_NAME" >> "$LOG_FILE"
  "$PYTHON_BIN" -m venv "$SERVER_DIR/.venv"
fi

NEEDS_INSTALL=0
if [ ! -f "$DEPENDENCY_MARKER" ]; then
  NEEDS_INSTALL=1
elif [ "$SERVER_DIR/pyproject.toml" -nt "$DEPENDENCY_MARKER" ]; then
  NEEDS_INSTALL=1
elif ! "$SERVER_DIR/.venv/bin/python" -c "import mcp" >/dev/null 2>&1; then
  NEEDS_INSTALL=1
elif ! "$SERVER_DIR/.venv/bin/python" -c "import langgraph" >/dev/null 2>&1; then
  NEEDS_INSTALL=1
fi

if [ "$NEEDS_INSTALL" -eq 1 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] installing dependencies for $SERVER_NAME" >> "$LOG_FILE"
  "$SERVER_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
  if [ "$SERVER_NAME" = "databricks-custom" ] && [ -d "$SERVER_DIR/databricks-tools-core" ]; then
    "$SERVER_DIR/.venv/bin/python" -m pip install --quiet -e "$SERVER_DIR/databricks-tools-core"
  fi
  if [ "${AUTOMATION_LOCAL_BRAIN:-false}" = "true" ] || [ "${AUTOMATION_LOCAL_BRAIN:-false}" = "1" ] || [ "${AUTOMATION_LOCAL_BRAIN:-false}" = "auto" ]; then
    # Keep llama-cpp dependency persistent across venv rebuilds.
    "$SERVER_DIR/.venv/bin/python" -m pip install --quiet -e ".[local-brain]"
  else
    "$SERVER_DIR/.venv/bin/python" -m pip install --quiet -e .
  fi
  date '+%Y-%m-%d %H:%M:%S' > "$DEPENDENCY_MARKER"
fi

exec "$SERVER_DIR/.venv/bin/python" src/server.py
