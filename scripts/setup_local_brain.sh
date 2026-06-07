#!/usr/bin/env zsh
# Install optional llama-cpp-python support for dev-mcp local Automation Brain.

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEV_MCP_DIR="$AUTOMATION_DIR/mcp-servers/dev-mcp"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

echo "[local-brain] Preparing dev-mcp virtual environment..."
if [ ! -x "$DEV_MCP_DIR/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$DEV_MCP_DIR/.venv"
fi

"$DEV_MCP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$DEV_MCP_DIR/.venv/bin/python" -m pip install --quiet -e "$DEV_MCP_DIR"

echo "[local-brain] Installing llama-cpp-python optional dependency..."
"$DEV_MCP_DIR/.venv/bin/python" -m pip install --quiet -e "$DEV_MCP_DIR[local-brain]"

cat <<EOF
[local-brain] Installed.

Next, put a small GGUF model on disk and set this in Automation/.env.local:

AUTOMATION_LOCAL_BRAIN=auto
AUTOMATION_LOCAL_BRAIN_MODEL=/absolute/path/to/your-model.gguf

Recommended small models:
- Qwen2.5-Coder 1.5B Instruct GGUF Q4_K_M
- Llama 3.2 1B/3B Instruct GGUF Q4_K_M

If no model path is configured, Automation still uses deterministic local rules.
EOF
