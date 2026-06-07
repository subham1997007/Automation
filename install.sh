#!/usr/bin/env bash
# =============================================================================
#  Automation Quick Setup — works in ANY project (frontend or backend)
#
#  Usage:  bash Automation/install.sh
#
#  What it does (all automatic, no manual steps):
#    1. Creates Python virtual environment inside dev-mcp
#    2. Installs all dependencies (LangChain, FAISS, sentence-transformers …)
#    3. Auto-detects your project type (Spring Boot / React / Vue / Angular /
#       Django / Go / Rust / Next.js …)
#    4. Generates AGENTS.md and .github/copilot-instructions.md
#    5. Builds the FAISS code search index
#    6. Checks your .env.local credentials
#    7. Prints what to do next
# =============================================================================
set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")" && pwd)"
DEV_MCP_DIR="$AUTOMATION_DIR/mcp-servers/dev-mcp"
VENV="$DEV_MCP_DIR/.venv"
SETUP_SCRIPT="$AUTOMATION_DIR/scripts/setup_project.py"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✅  $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠️   $*${NC}"; }
err()  { echo -e "${RED}  ❌  $*${NC}"; }
hdr()  { echo -e "\n${GREEN}━━━  $*  ━━━${NC}"; }

echo ""
echo "🚀  Automation Setup — Auto-configuring for this project"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Python check ────────────────────────────────────────────────────────────
hdr "Step 1/4 — Python environment"
PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
  err "Python 3 not found. Install Python 3.10+ and retry."
  exit 1
fi
PY_VERSION=$("$PYTHON" --version 2>&1 | awk '{print $2}')
ok "Python $PY_VERSION found at $PYTHON"

# ── 2. Create venv + install deps ─────────────────────────────────────────────
hdr "Step 2/4 — Installing dependencies (first run ~2 min)"
if [ ! -d "$VENV" ]; then
  echo "  Creating virtual environment..."
  "$PYTHON" -m venv "$VENV"
  ok "Virtual environment created"
else
  ok "Virtual environment already exists"
fi

PIP="$VENV/bin/pip"
PYEXEC="$VENV/bin/python"

# Upgrade pip silently
"$PIP" install --quiet --upgrade pip

# Install all required packages
"$PIP" install --quiet \
  "mcp[cli]>=1.0.0" \
  "langgraph>=0.2.0" \
  "langchain>=0.3.0" \
  "langchain-community>=0.3.0" \
  "langchain-core>=0.3.0" \
  "langchain-openai>=0.2.0" \
  "langchain-text-splitters>=0.3.0" \
  "faiss-cpu>=1.8.0" \
  "sentence-transformers>=3.0.0" \
  "atlassian-python-api>=3.41.0" \
  "requests>=2.31.0" \
  "tiktoken>=0.7.0" \
  "turbovec>=0.7.0" \
  "scikit-learn>=1.3.0" \
  "numpy>=1.20.0"

ok "All dependencies installed"

# ── 3. Run the universal project setup ────────────────────────────────────────
hdr "Step 3/4 — Auto-detecting project and generating config"
"$PYEXEC" "$SETUP_SCRIPT" 2>/dev/null

# ── 4. Check .env.local ───────────────────────────────────────────────────────
hdr "Step 4/4 — Credentials check"
ENV_FILE="$AUTOMATION_DIR/.env.local"
ENV_EXAMPLE="$AUTOMATION_DIR/.env.local.example"

if [ ! -f "$ENV_FILE" ]; then
  warn ".env.local not found — creating from template"
  if [ -f "$ENV_EXAMPLE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    ok "Created $ENV_FILE from template"
  else
    # Create minimal template
    cat > "$ENV_FILE" <<'EOF'
# ── Jira (required) ────────────────────────────────────
JIRA_BASE_URL=https://your-company.atlassian.net
JIRA_USERNAME=your.email@company.com
JIRA_API_TOKEN=your-jira-api-token

# ── GitLab (required for MR creation) ─────────────────
GITLAB_URL=https://gitlab.your-company.com
GITLAB_TOKEN=your-gitlab-token
GITLAB_PROJECT_ID=your-project-id

# ── Confluence (optional — for ADR/doc caching) ────────
# CONFLUENCE_BASE_URL=https://your-company.atlassian.net
# CONFLUENCE_SPACE_KEY=YOURSPACE

# ── OpenAI (optional — enables LLM-powered AC generation) ──
# OPENAI_API_KEY=sk-...

# ── Jira custom fields (optional) ─────────────────────
JIRA_ACCEPTANCE_CRITERIA_FIELD=customfield_10451
JIRA_REGULATORY_JUSTIFICATION_FIELD=customfield_11623
JIRA_REASON_COMMENTS_FIELD=customfield_12736
EOF
    ok "Created $ENV_FILE — fill in your credentials"
  fi
  warn "ACTION NEEDED: Edit $ENV_FILE with your actual credentials"
else
  ok ".env.local exists"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}  ✅  Setup complete! Your Automation is ready.${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  What was configured:"
echo "    ✅  AGENTS.md (project-specific)"
echo "    ✅  .github/copilot-instructions.md"
echo "    ✅  FAISS code search index"
echo "    ✅  turbovec semantic search index (Automation/.memory/code-index.tvim)"
echo "    ✅  Automation/.memory/ (caches + indexes)"

# ── Auto-generate repo knowledge graph + AI memory ─────────────────────────
echo ""
echo "[setup] 🧠 Building repo knowledge graph + AI memory..."
bash "$AUTOMATION_DIR/bootstrap.sh" 2>/dev/null || bash "$AUTOMATION_DIR/graph.sh" 2>/dev/null || python3 "$AUTOMATION_DIR/scripts/repo_graph.py" 2>/dev/null
echo "    ✅  Knowledge graph + .memory/codebase-index.json"

# ── Build turbovec semantic code search index ──────────────────────────────
echo ""
echo "[setup] ⚡ Building turbovec semantic code search index..."
"$PYEXEC" "$AUTOMATION_DIR/scripts/build_turbovec_index.py" 2>/dev/null \
  && echo "    ✅  turbovec index → Automation/.memory/code-index.tvim" \
  || echo "    ⚠️   turbovec index skipped (re-run: python3 Automation/scripts/build_turbovec_index.py)"

# ── Install git pre-push hook for auto-refresh ─────────────────────────────
GIT_DIR="$(cd "$AUTOMATION_DIR/.." && git rev-parse --git-dir 2>/dev/null || true)"
if [ -n "$GIT_DIR" ] && [ -d "$GIT_DIR/hooks" ]; then
  HOOK="$GIT_DIR/hooks/post-commit"
  if [ ! -f "$HOOK" ] || ! grep -q "repo_graph" "$HOOK" 2>/dev/null; then
    cat >> "$HOOK" <<'HOOKEOF'
#!/usr/bin/env bash
# Auto-refresh AI memory on commit (incremental, fast)
AUTOMATION_DIR="$(cd "$(dirname "$0")/../../Automation" 2>/dev/null && pwd)"
[ -f "$AUTOMATION_DIR/scripts/repo_graph.py" ] && python3 "$AUTOMATION_DIR/scripts/repo_graph.py" --quick >/dev/null 2>&1 &
HOOKEOF
    chmod +x "$HOOK"
    echo "    ✅  Git post-commit hook (auto-refreshes AI memory)"
  fi
fi

echo ""
echo "  Next steps:"
echo "    1️⃣   Edit Automation/.env.local with your Jira/GitLab credentials"
echo "    2️⃣   Reload your IDE / Copilot so it picks up the new instructions"
echo "    3️⃣   Start a story:  dev_implement_story(jira_id=\"PROJ-123\")"
echo ""
