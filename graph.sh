#!/usr/bin/env zsh
# graph.sh — One-command repo knowledge graph generator
#
# Usage:
#   bash Automation/graph.sh              # analyze this repo
#   bash Automation/graph.sh /other/repo  # analyze any other repo
#   bash Automation/graph.sh --open       # analyze + auto-open in browser

set -euo pipefail

AUTOMATION_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$AUTOMATION_DIR/scripts/repo_graph.py"
ROOT="${1:-$(cd "$AUTOMATION_DIR/.." && pwd)}"
OUTPUT="$AUTOMATION_DIR/docs/repo-graph.html"
OPEN_AFTER=0

for arg in "$@"; do
  case "$arg" in
    --open) OPEN_AFTER=1 ;;
  esac
done

echo "[graph] 🔍 Analyzing: $ROOT"
echo "[graph] 📄 Output:    $OUTPUT"
echo ""

python3 "$SCRIPT" --root "$ROOT" --output "$OUTPUT"

# ── Also regenerate DevFlow Analytics dashboard ───────────────────────────
ANALYTICS_SCRIPT="$AUTOMATION_DIR/scripts/generate_analytics.py"
if [ -f "$ANALYTICS_SCRIPT" ]; then
  python3 "$ANALYTICS_SCRIPT" 2>/dev/null && echo "[graph] 📊 Analytics: $AUTOMATION_DIR/docs/devflow-analytics.html" || true
fi

echo ""
echo "[graph] ✅ Done! Serve via:"
echo "        cd $AUTOMATION_DIR/docs && python3 -m http.server 8080"
echo "        → http://localhost:8080/repo-graph.html"
echo "        → http://localhost:8080/devflow-analytics.html"
echo ""

if [ "$OPEN_AFTER" -eq 1 ]; then
  if command -v open &>/dev/null; then
    open "$OUTPUT"
  elif command -v xdg-open &>/dev/null; then
    xdg-open "$OUTPUT"
  fi
fi

