#!/usr/bin/env zsh
# Ensure local brain model exists in Automation/models (download once).

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$AUTOMATION_DIR/.env.local"
DEFAULT_MODEL_REL="models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
DEFAULT_MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"

log() { echo "[local-brain-setup] $*"; }

if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi

LOCAL_BRAIN_MODE="${AUTOMATION_LOCAL_BRAIN:-true}"
case "$LOCAL_BRAIN_MODE" in
  false|0|off|no)
    log "AUTOMATION_LOCAL_BRAIN is disabled; skipping model setup."
    exit 0
    ;;
esac

AUTO_DOWNLOAD="${AUTOMATION_LOCAL_BRAIN_AUTO_DOWNLOAD:-true}"
case "$AUTO_DOWNLOAD" in
  false|0|off|no)
    log "AUTOMATION_LOCAL_BRAIN_AUTO_DOWNLOAD is disabled; skipping download."
    exit 0
    ;;
esac

MODEL_PATH="${AUTOMATION_LOCAL_BRAIN_MODEL:-$DEFAULT_MODEL_REL}"
if [ "${MODEL_PATH#/}" = "$MODEL_PATH" ]; then
  MODEL_PATH="$AUTOMATION_DIR/$MODEL_PATH"
fi

MODEL_URL="${AUTOMATION_LOCAL_BRAIN_MODEL_URL:-$DEFAULT_MODEL_URL}"
MODEL_DIR="$(dirname "$MODEL_PATH")"

if [ -f "$MODEL_PATH" ]; then
  log "Model already present: $MODEL_PATH"
  exit 0
fi

mkdir -p "$MODEL_DIR"
log "Downloading local brain model to: $MODEL_PATH"
curl -L --fail --progress-bar "$MODEL_URL" -o "$MODEL_PATH"

if [ -f "$MODEL_PATH" ]; then
  log "Model download complete."
else
  log "Model download failed."
  exit 1
fi

