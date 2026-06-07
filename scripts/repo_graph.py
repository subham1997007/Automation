#!/usr/bin/env python3
"""
repo_graph.py — Local Repository Knowledge Graph Generator
===========================================================
Drop the Automation/ folder into ANY repo, then run:

    python3 Automation/scripts/repo_graph.py

It will:
  1. Scan all source files in the repo
  2. Parse functions, classes, imports per file
  3. Resolve file-to-file dependency edges
  4. Detect architectural layers (service, controller, util, test…)
  5. Compute health metrics (coupling, orphaned files, LOC, etc.)
  6. Generate a fully self-contained interactive HTML knowledge graph

Output: Automation/docs/repo-graph.html  (open in any browser, no server needed)

Supported languages: Java, Python, JS/TS/JSX/TSX, Go, Kotlin, Scala,
                     GraphQL, YAML, JSON, Markdown, Shell
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # 3 levels up from Automation/scripts/
AUTOMATION_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = AUTOMATION_DIR / "docs" / "repo-graph.html"

IGNORE_DIRS: set[str] = {
    "node_modules", ".git", "target", "build", "dist", ".next", "coverage",
    "__pycache__", ".venv", "venv", "env", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".eggs", "__macosx", ".idea", ".vscode",
    "out", "bin", "obj", ".gradle", ".mvn", "vendor", "bower_components",
}

CODE_EXTENSIONS: set[str] = {
    ".java", ".py", ".pyw", ".pyi",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".go", ".rs", ".rb", ".php",
    ".kt", ".kts", ".scala", ".clj",
    ".cs", ".swift", ".dart", ".elm",
    ".sh", ".bash", ".zsh",
    ".groovy", ".gradle",
}

TEXT_EXTENSIONS: set[str] = {
    ".md", ".markdown", ".txt",
    ".json", ".yaml", ".yml", ".toml", ".xml",
    ".graphql", ".gql", ".graphqls",
    ".sql", ".prisma", ".proto", ".tf",
    ".html", ".htm", ".css", ".scss",
    ".properties", ".env", ".ini", ".cfg",
}

BINARY_EXTENSIONS: set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf", ".pdf",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".db",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".webm", ".class",
    ".jar", ".war", ".ear",
}

# Layer detection patterns (path-based)
LAYER_PATTERNS: list[tuple[str, str]] = [
    (r"/(test|tests|spec|specs|__tests__|it)/",          "test"),
    (r"Test\.(java|kt|scala|py|ts|js)$",                 "test"),
    (r"Spec\.(java|kt|scala|ts|js)$",                    "test"),
    (r"/(controller|controllers|resource|resources|api|rest|endpoint|endpoints)/", "controller"),
    (r"Controller\.(java|kt|scala)$",                    "controller"),
    (r"Resource\.(java|kt|scala)$",                      "controller"),
    (r"/(service|services|usecase|usecases|business)/",  "service"),
    (r"Service\.(java|kt|scala|py|ts|js)$",              "service"),
    (r"UseCase\.(java|kt|scala)$",                       "service"),
    (r"/(repository|repositories|dao|store|storage)/",   "data"),
    (r"Repository\.(java|kt|scala)$",                    "data"),
    (r"Dao\.(java|kt|scala)$",                           "data"),
    (r"/(model|models|domain|entity|entities)/",         "model"),
    (r"Entity\.(java|kt|scala)$",                        "model"),
    (r"/(util|utils|helper|helpers|common|shared)/",     "util"),
    (r"Util\.(java|kt|scala|py|ts|js)$",                 "util"),
    (r"Helper\.(java|kt|scala|py|ts|js)$",               "util"),
    (r"/(config|configuration|settings)/",               "config"),
    (r"Config\.(java|kt|scala|py|ts|js)$",               "config"),
    (r"Configuration\.(java|kt|scala)$",                 "config"),
    (r"\.(graphql|graphqls|gql)$",                       "schema"),
    (r"/(component|components)/",                        "component"),
    (r"/(page|pages)/",                                  "ui"),
    (r"/(middleware|interceptor|interceptors|filter|filters|aspect|aspects)/", "middleware"),
    (r"Interceptor\.(java|kt|scala)$",                   "middleware"),
    (r"Filter\.(java|kt|scala)$",                        "middleware"),
    (r"Aspect\.(java|kt|scala)$",                        "middleware"),
]

LAYER_COLORS: dict[str, str] = {
    "controller": "#4d9fff",
    "service":    "#a78bfa",
    "data":       "#ff9f43",
    "model":      "#22d3ee",
    "util":       "#00ff9d",
    "config":     "#ec4899",
    "test":       "#f59e0b",
    "schema":     "#c084fc",
    "component":  "#22d3ee",
    "ui":         "#4d9fff",
    "middleware": "#ff5f5f",
    "other":      "#94a3b8",
}

# ---------------------------------------------------------------------------
# Layer Detector
# ---------------------------------------------------------------------------

def detect_layer(path: str) -> str:
    path_normalized = path.replace("\\", "/")
    for pattern, layer in LAYER_PATTERNS:
        if re.search(pattern, path_normalized, re.IGNORECASE):
            return layer
    return "other"


# ---------------------------------------------------------------------------
# Language Parsers
# ---------------------------------------------------------------------------

def parse_java(content: str, path: str) -> dict[str, Any]:
    """Extract package, imports, classes, methods from Java source."""
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []

    # Package
    pkg_match = re.search(r"^\s*package\s+([\w.]+)\s*;", content, re.MULTILINE)
    package = pkg_match.group(1) if pkg_match else ""

    # Imports
    for m in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?;", content, re.MULTILINE):
        imports.append(m.group(1))

    # Classes / Interfaces / Enums / Records
    for m in re.finditer(
        r"(?:^|\s)(?:public|protected|private|abstract|final|sealed)?\s*"
        r"(?:class|interface|enum|record|@interface)\s+(\w+)",
        content, re.MULTILINE
    ):
        name = m.group(1)
        if name not in classes:
            classes.append(name)

    # Methods (simplified - skip constructors and annotations)
    for m in re.finditer(
        r"(?:public|protected|private|static|final|abstract|synchronized|default)\s+"
        r"(?:[\w<>\[\],\s?]+)\s+(\w+)\s*\(",
        content
    ):
        name = m.group(1)
        if name not in ("if", "for", "while", "switch", "catch", "return", "new") and name not in functions:
            functions.append(name)

    return {"package": package, "imports": imports, "classes": classes, "functions": functions}


def parse_python(content: str, path: str) -> dict[str, Any]:
    """Extract imports, classes, functions from Python source."""
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []

    # from x import y / from x.y import z
    for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import", content, re.MULTILINE):
        imports.append(m.group(1))

    # import x[.y]
    for m in re.finditer(r"^\s*import\s+([\w., ]+)", content, re.MULTILINE):
        for part in m.group(1).split(","):
            imports.append(part.strip().split(" ")[0])

    # class Foo
    for m in re.finditer(r"^class\s+(\w+)", content, re.MULTILINE):
        classes.append(m.group(1))

    # def foo
    for m in re.finditer(r"^\s+?def\s+(\w+)|^def\s+(\w+)", content, re.MULTILINE):
        name = m.group(1) or m.group(2)
        functions.append(name)

    return {"imports": imports, "classes": classes, "functions": functions}


def parse_js_ts(content: str, path: str) -> dict[str, Any]:
    """Extract imports, classes, functions from JS/TS source."""
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []

    # import ... from '...'
    for m in re.finditer(r"""import\s+.*?from\s+['"]([^'"]+)['"]""", content):
        imports.append(m.group(1))

    # require('...')
    for m in re.finditer(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", content):
        imports.append(m.group(1))

    # class Foo
    for m in re.finditer(r"(?:^|\s)class\s+(\w+)", content, re.MULTILINE):
        classes.append(m.group(1))

    # function foo / async function foo
    for m in re.finditer(r"(?:async\s+)?function\s+(\w+)\s*\(", content):
        functions.append(m.group(1))

    # const foo = () => / const foo = async () =>
    for m in re.finditer(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(", content):
        functions.append(m.group(1))

    return {"imports": imports, "classes": classes, "functions": functions}


def parse_kotlin(content: str, path: str) -> dict[str, Any]:
    """Extract imports, classes, functions from Kotlin source."""
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []

    for m in re.finditer(r"^\s*import\s+([\w.]+)", content, re.MULTILINE):
        imports.append(m.group(1))
    for m in re.finditer(r"(?:^|\s)(?:class|object|interface|data class|sealed class|enum class)\s+(\w+)", content, re.MULTILINE):
        classes.append(m.group(1))
    for m in re.finditer(r"(?:^|\s)(?:fun)\s+(\w+)\s*[(<]", content, re.MULTILINE):
        functions.append(m.group(1))

    return {"imports": imports, "classes": classes, "functions": functions}


def parse_go(content: str, path: str) -> dict[str, Any]:
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []

    for m in re.finditer(r'"([\w./]+)"', content):
        imports.append(m.group(1))
    for m in re.finditer(r"^type\s+(\w+)\s+struct", content, re.MULTILINE):
        classes.append(m.group(1))
    for m in re.finditer(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", content, re.MULTILINE):
        functions.append(m.group(1))

    return {"imports": imports, "classes": classes, "functions": functions}


def parse_generic(content: str, path: str) -> dict[str, Any]:
    """Minimal regex parser for unsupported languages."""
    return {"imports": [], "classes": [], "functions": []}


def parse_file(content: str, path: str) -> dict[str, Any]:
    ext = Path(path).suffix.lower()
    if ext == ".java":
        return parse_java(content, path)
    if ext in (".py", ".pyw", ".pyi"):
        return parse_python(content, path)
    if ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        return parse_js_ts(content, path)
    if ext in (".kt", ".kts"):
        return parse_kotlin(content, path)
    if ext == ".go":
        return parse_go(content, path)
    return parse_generic(content, path)


# ---------------------------------------------------------------------------
# File Scanner
# ---------------------------------------------------------------------------

def scan_repo(root: Path, extra_ignore: set[str] | None = None) -> list[dict[str, Any]]:
    ignore = IGNORE_DIRS | (extra_ignore or set())
    files: list[dict[str, Any]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored dirs in-place
        dirnames[:] = [d for d in dirnames if d not in ignore and not d.startswith(".")]

        for filename in filenames:
            full_path = Path(dirpath) / filename
            rel_path = str(full_path.relative_to(root)).replace("\\", "/")
            ext = full_path.suffix.lower()

            if ext in BINARY_EXTENSIONS:
                continue
            if filename.startswith("."):
                continue

            is_code = ext in CODE_EXTENSIONS
            is_text = ext in TEXT_EXTENSIONS or (not is_code and ext not in BINARY_EXTENSIONS)

            files.append({
                "path":      rel_path,
                "name":      filename,
                "ext":       ext,
                "folder":    str(Path(rel_path).parent).replace("\\", "/"),
                "is_code":   is_code,
                "full_path": str(full_path),
            })

    return files


# ---------------------------------------------------------------------------
# Dependency Resolver
# ---------------------------------------------------------------------------

def build_class_to_file_index(analyzed: list[dict[str, Any]]) -> dict[str, str]:
    """Map fully-qualified class names AND simple class names → file path."""
    index: dict[str, str] = {}
    for f in analyzed:
        for cls in f.get("classes", []):
            # Simple name
            index[cls] = f["path"]
            # Qualified name (package.ClassName for Java)
            pkg = f.get("package", "")
            if pkg:
                index[f"{pkg}.{cls}"] = f["path"]
    return index


def resolve_import_to_file(
    imp: str,
    current_file: str,
    class_index: dict[str, str],
    all_paths: list[str],
    ext: str,
) -> str | None:
    """Try to resolve an import string to a repo-relative file path."""

    # ── Java / Kotlin / Scala: package qualified name ──────────────────────
    if ext in (".java", ".kt", ".kts", ".scala", ".groovy"):
        # Direct lookup in class index
        if imp in class_index:
            return class_index[imp]
        # Wildcard: com.example.* → check all files with that package prefix
        if imp.endswith(".*"):
            prefix = imp[:-2]
            for path in all_paths:
                if f"/{prefix.replace('.', '/')}" in f"/{path}" or path.startswith(prefix.replace(".", "/")):
                    return path
        return None

    # ── Python: module path ─────────────────────────────────────────────────
    if ext in (".py", ".pyw"):
        module_path = imp.replace(".", "/")
        candidates = [
            module_path + ".py",
            module_path + "/__init__.py",
        ]
        for c in candidates:
            if c in all_paths:
                return c
        # partial match
        for p in all_paths:
            if p.endswith(module_path + ".py") or p.endswith(module_path + "/__init__.py"):
                return p
        return None

    # ── JS / TS: relative or bare module ───────────────────────────────────
    if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        if not imp.startswith("."):
            return None  # external package
        base = Path(current_file).parent / imp
        candidates = [
            str(base) + ext,
            str(base) + ".js",
            str(base) + ".ts",
            str(base) + ".jsx",
            str(base) + ".tsx",
            str(base / "index.js"),
            str(base / "index.ts"),
        ]
        norm = [c.replace("\\", "/").lstrip("./") for c in candidates]
        for c in norm:
            if c in all_paths:
                return c
        return None

    return None


# ---------------------------------------------------------------------------
# Main Analysis Pipeline
# ---------------------------------------------------------------------------

def analyze_repo(root: Path) -> dict[str, Any]:
    print(f"[repo_graph] Scanning {root} ...")
    raw_files = scan_repo(root)
    print(f"[repo_graph] Found {len(raw_files)} files")

    analyzed: list[dict[str, Any]] = []
    all_paths_set: set[str] = {f["path"] for f in raw_files}
    all_paths: list[str] = list(all_paths_set)

    for f in raw_files:
        entry: dict[str, Any] = {
            "path":      f["path"],
            "name":      f["name"],
            "ext":       f["ext"],
            "folder":    f["folder"],
            "layer":     detect_layer(f["path"]),
            "is_code":   f["is_code"],
            "lines":     0,
            "classes":   [],
            "functions": [],
            "imports":   [],
            "package":   "",
            "deps":      [],      # resolved file paths this file depends on
        }

        if f["is_code"] or f["ext"] in TEXT_EXTENSIONS:
            try:
                text = Path(f["full_path"]).read_text(encoding="utf-8", errors="replace")
                entry["lines"] = text.count("\n") + 1
                if f["is_code"]:
                    parsed = parse_file(text, f["path"])
                    entry["classes"]   = parsed.get("classes", [])
                    entry["functions"] = parsed.get("functions", [])
                    entry["imports"]   = parsed.get("imports", [])
                    entry["package"]   = parsed.get("package", "")
            except Exception:
                pass

        analyzed.append(entry)

    # Build class→file index for dependency resolution
    class_index = build_class_to_file_index(analyzed)

    # Resolve imports → file paths (edges)
    path_to_entry = {e["path"]: e for e in analyzed}
    connections: list[dict[str, str]] = []

    for entry in analyzed:
        if not entry["is_code"]:
            continue
        seen_deps: set[str] = set()
        for imp in entry["imports"]:
            resolved = resolve_import_to_file(imp, entry["path"], class_index, all_paths, entry["ext"])
            if resolved and resolved != entry["path"] and resolved not in seen_deps:
                seen_deps.add(resolved)
                entry["deps"].append(resolved)
                connections.append({"source": entry["path"], "target": resolved})

    # ── Metrics ──────────────────────────────────────────────────────────────
    total_loc = sum(e["lines"] for e in analyzed)
    lang_stats: dict[str, int] = defaultdict(int)
    layer_stats: dict[str, int] = defaultdict(int)
    for e in analyzed:
        lang_stats[e["ext"] or "other"] += e["lines"]
        layer_stats[e["layer"]] += 1

    # Incoming edge count (coupling)
    incoming: dict[str, int] = defaultdict(int)
    for c in connections:
        incoming[c["target"]] += 1

    # Orphaned files (no deps in, no deps out — code files only)
    connected_files: set[str] = set()
    for c in connections:
        connected_files.add(c["source"])
        connected_files.add(c["target"])

    # Circular deps
    edge_set: set[tuple[str, str]] = {(c["source"], c["target"]) for c in connections}
    circular: list[list[str]] = []
    seen_circular: set[str] = set()
    for c in connections:
        rev = (c["target"], c["source"])
        if rev in edge_set:
            key = "|".join(sorted([c["source"], c["target"]]))
            if key not in seen_circular:
                seen_circular.add(key)
                circular.append([c["source"], c["target"]])

    # Highly coupled (8+ incoming) — exclude Automation/ tooling
    high_coupling = [
        {"file": f, "count": cnt}
        for f, cnt in sorted(incoming.items(), key=lambda x: -x[1])
        if cnt >= 8 and not f.startswith("Automation/")
    ]

    # Large files (15+ functions) — exclude Automation/ tooling
    large_files = [
        {"file": e["path"], "functions": len(e["functions"]), "lines": e["lines"]}
        for e in analyzed
        if len(e["functions"]) >= 15 and not e["path"].startswith("Automation/")
    ]

    stats = {
        "total_files":    len(analyzed),
        "code_files":     sum(1 for e in analyzed if e["is_code"]),
        "total_loc":      total_loc,
        "total_edges":    len(connections),
        "circular_deps":  len(circular),
        "high_coupling":  len(high_coupling),
        "large_files":    len(large_files),
        "languages":      sorted(
            [{"ext": k, "lines": v, "pct": round(v / total_loc * 100) if total_loc else 0}
             for k, v in lang_stats.items()],
            key=lambda x: -x["lines"]
        )[:10],
        "layers": dict(layer_stats),
    }

    # Slim down node data for JSON (drop full_path, keep what UI needs)
    nodes = [
        {
            "id":        e["path"],
            "name":      e["name"],
            "folder":    e["folder"],
            "layer":     e["layer"],
            "ext":       e["ext"],
            "lines":     e["lines"],
            "fnCount":   len(e["functions"]),
            "classes":   e["classes"][:20],
            "functions": e["functions"][:30],
            "deps":      e["deps"],
            "incoming":  incoming.get(e["path"], 0),
            "is_code":   e["is_code"],
        }
        for e in analyzed
    ]

    issues = []
    if large_files:
        issues.append({"type": "warning", "title": f"{len(large_files)} Large Files",
                        "desc": "Files with 15+ functions", "items": large_files})
    if circular:
        issues.append({"type": "critical", "title": f"{len(circular)} Circular Dependencies",
                        "desc": "Files that import each other",
                        "items": [{"files": c} for c in circular]})
    if high_coupling:
        issues.append({"type": "warning", "title": f"{len(high_coupling)} Highly Coupled Files",
                        "desc": "Imported by 8+ other files", "items": high_coupling})

    return {
        "meta": {
            "repo":       root.name,
            "generated":  datetime.now().isoformat(timespec="seconds"),
            "root":       ".",
        },
        "stats":       stats,
        "nodes":       nodes,
        "connections": connections,
        "issues":      issues,
    }


# ---------------------------------------------------------------------------
# HTML Generator
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- repo-graph v3 — all-nodes-labeled, working fit/layers -->
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Repo Knowledge Graph — {{REPO_NAME}}</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e2e8f0;display:flex;height:100vh;overflow:hidden}
#sidebar{width:280px;min-width:200px;background:#1a1d27;border-right:1px solid #2d3148;display:flex;flex-direction:column;overflow:hidden;z-index:10}
#main{flex:1;position:relative;overflow:hidden}
#topbar{padding:12px 16px;border-bottom:1px solid #2d3148;background:#1a1d27}
#topbar h1{font-size:13px;font-weight:700;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#topbar .meta{font-size:11px;color:#64748b;margin-top:2px}
#search-box{padding:8px 12px;border-bottom:1px solid #2d3148}
#search-box input{width:100%;background:#0f1117;border:1px solid #2d3148;border-radius:6px;padding:6px 10px;font-size:12px;color:#e2e8f0;outline:none}
#search-box input:focus{border-color:#4d9fff}
#tabs{display:flex;border-bottom:1px solid #2d3148}
.tab{flex:1;padding:8px 4px;font-size:11px;font-weight:600;text-align:center;cursor:pointer;color:#64748b;border-bottom:2px solid transparent;transition:.15s}
.tab.active{color:#4d9fff;border-bottom-color:#4d9fff}
#tab-content{flex:1;overflow-y:auto;padding:8px 0}
.file-item{padding:6px 12px;cursor:pointer;font-size:11px;border-left:3px solid transparent;transition:.1s}
.file-item:hover{background:#252838;border-left-color:#4d9fff}
.file-item.selected{background:#1e2340;border-left-color:#4d9fff}
.file-item .fname{font-weight:600;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center}
.file-item .fmeta{color:#64748b;font-size:10px;margin-top:1px}
.layer-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:5px;flex-shrink:0}
#canvas{width:100%;height:100%;display:block}
#info-panel{position:absolute;top:12px;right:12px;width:290px;background:#1a1d27;border:1px solid #2d3148;border-radius:10px;padding:14px;font-size:12px;display:none;max-height:85vh;overflow-y:auto;z-index:20}
#info-panel h2{font-size:13px;font-weight:700;margin-bottom:8px;color:#4d9fff;word-break:break-all;padding-right:16px}
#info-panel .stat-row{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #1e2340;color:#94a3b8;gap:8px}
#info-panel .stat-row span:last-child{color:#e2e8f0;font-weight:600;text-align:right;word-break:break-all}
#info-panel .section-title{font-size:10px;font-weight:700;text-transform:uppercase;color:#64748b;margin:10px 0 4px;letter-spacing:.04em}
#info-panel .fn-list{max-height:130px;overflow-y:auto}
#info-panel .fn-item{padding:2px 0;color:#a78bfa;font-size:11px}
#info-panel .cls-item{padding:2px 0;color:#22d3ee;font-size:11px}
#info-panel .dep-item{padding:2px 0;color:#94a3b8;font-size:10px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#info-panel .dep-item:hover{color:#4d9fff}
#stats-bar{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);background:#1a1d27dd;backdrop-filter:blur(8px);border:1px solid #2d3148;border-radius:10px;padding:8px 20px;display:flex;gap:24px;font-size:11px;pointer-events:none}
.stat-pill{text-align:center}
.stat-pill .val{font-size:17px;font-weight:700;color:#4d9fff}
.stat-pill .lbl{color:#64748b;font-size:10px}
#controls{position:absolute;top:12px;left:12px;display:flex;gap:6px;flex-wrap:wrap}
.ctrl-btn{background:#1a1d27;border:1px solid #2d3148;border-radius:6px;padding:6px 11px;font-size:11px;color:#94a3b8;cursor:pointer;transition:.15s;white-space:nowrap}
.ctrl-btn:hover{background:#252838;color:#e2e8f0}
.ctrl-btn.active{background:#1e2340;color:#4d9fff;border-color:#4d9fff}
#legend{position:absolute;bottom:14px;right:14px;background:#1a1d27dd;backdrop-filter:blur(8px);border:1px solid #2d3148;border-radius:8px;padding:8px 12px;font-size:10px;max-height:300px;overflow-y:auto}
#legend h4{color:#64748b;margin-bottom:6px;font-size:10px;text-transform:uppercase;letter-spacing:.06em}
.legend-item{display:flex;align-items:center;gap:5px;margin:3px 0;color:#94a3b8}
.close-btn{position:absolute;top:8px;right:10px;cursor:pointer;color:#64748b;font-size:16px;line-height:1;font-weight:300}
.close-btn:hover{color:#e2e8f0}
.issues-section{padding:8px 12px}
.issue-card{background:#252838;border-radius:6px;padding:8px 10px;margin-bottom:6px;font-size:11px}
.issue-card .issue-title{font-weight:700;margin-bottom:4px}
.issue-card.critical .issue-title{color:#ff5f5f}
.issue-card.warning .issue-title{color:#f59e0b}
.issue-card .issue-item{color:#94a3b8;padding:1px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:10px}
.metric-section{padding:8px 12px}
.metric-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #1e2340;font-size:11px}
.metric-row .mlabel{color:#94a3b8;display:flex;align-items:center;gap:4px}
.metric-row .mval{color:#e2e8f0;font-weight:600}
</style>
</head>
<body>

<div id="sidebar">
  <div id="topbar">
    <h1>📊 {{REPO_NAME}}</h1>
    <div class="meta">Generated {{GENERATED}}</div>
  </div>
  <div id="search-box">
    <input id="search" type="text" placeholder="🔍 Search files…" oninput="filterFiles()"/>
  </div>
  <div id="tabs">
    <div class="tab active" onclick="switchTab('files')">Files</div>
    <div class="tab" onclick="switchTab('metrics')">Metrics</div>
    <div class="tab" onclick="switchTab('issues')">Issues</div>
  </div>
  <div id="tab-content"></div>
</div>

<div id="main">
  <div id="controls">
    <button class="ctrl-btn active" id="btn-graph"  onclick="setView('graph')">🔵 Graph</button>
    <button class="ctrl-btn"        id="btn-layers" onclick="setView('layers')">🗂 Layers</button>
    <button class="ctrl-btn"        id="btn-mind"   onclick="setView('mind')">🧠 Mind Graph</button>
    <button class="ctrl-btn"                        onclick="fitToView()">⊡ Fit</button>
    <button class="ctrl-btn"                        onclick="clearSelection()">✕ Clear</button>
    <button class="ctrl-btn"                        onclick="exportJSON()">↓ JSON</button>
  </div>
  <svg id="canvas"></svg>
  <div id="stats-bar">
    <div class="stat-pill"><div class="val" id="s-files">0</div><div class="lbl">Files</div></div>
    <div class="stat-pill"><div class="val" id="s-loc">0</div><div class="lbl">Lines</div></div>
    <div class="stat-pill"><div class="val" id="s-edges">0</div><div class="lbl">Deps</div></div>
    <div class="stat-pill"><div class="val" id="s-circ">0</div><div class="lbl">Circular</div></div>
  </div>
  <div id="legend">
    <h4>Layers</h4>
    <div id="legend-items"></div>
  </div>
  <div id="info-panel">
    <span class="close-btn" onclick="clearSelection()">×</span>
    <div id="info-content"></div>
  </div>
</div>

<script>
// ── Data ──────────────────────────────────────────────────────────────────
const GRAPH_DATA   = {{GRAPH_DATA}};
const LAYER_COLORS = {{LAYER_COLORS}};

const allNodes  = GRAPH_DATA.nodes;
const allLinks  = GRAPH_DATA.connections.map(c => ({...c}));
const stats     = GRAPH_DATA.stats;
const issues    = GRAPH_DATA.issues;

// ── Helpers ───────────────────────────────────────────────────────────────
function nodeColor(d){ return LAYER_COLORS[d.layer] || '#94a3b8'; }
function nodeRadius(d){ return Math.max(6, Math.min(24, 5 + Math.sqrt(d.fnCount || 1) * 2.2)); }
function nodePath(n){
  const raw = n.id || `${n.folder || ''}/${n.name || ''}`;
  return raw.startsWith('./') ? raw.slice(2) : raw;
}
function isVisibleRepoNode(n){ return n.is_code; }
function shortName(name){
  const base = name.replace(/\.(java|ts|tsx|js|jsx|mjs|py|pyw|kt|kts|go|scala|rb|cs|sh|bash|zsh|groovy|gradle|rs|dart|elm)$/i, '');
  return base.length > 20 ? base.slice(0, 18) + '…' : base;
}
function srcId(l){ return typeof l.source === 'object' ? l.source.id : l.source; }
function tgtId(l){ return typeof l.target === 'object' ? l.target.id : l.target; }

// ── Stats bar ─────────────────────────────────────────────────────────────
document.getElementById('s-files').textContent = stats.code_files.toLocaleString();
document.getElementById('s-loc').textContent   = (stats.total_loc / 1000).toFixed(1) + 'k';
document.getElementById('s-edges').textContent = stats.total_edges.toLocaleString();
document.getElementById('s-circ').textContent  = stats.circular_deps;

// ── Legend ────────────────────────────────────────────────────────────────
const legendEl = document.getElementById('legend-items');
Object.entries(LAYER_COLORS).forEach(([layer, color]) => {
  const d = document.createElement('div');
  d.className = 'legend-item';
  d.innerHTML = `<span class="layer-dot" style="background:${color}"></span>${layer}`;
  legendEl.appendChild(d);
});

// ── Node index ────────────────────────────────────────────────────────────
const nodeById  = {};
allNodes.forEach(n => { nodeById[n.id] = n; n.x = (Math.random() - 0.5) * 600; n.y = (Math.random() - 0.5) * 600; });
const codeNodes = allNodes.filter(isVisibleRepoNode);
const codeIds   = new Set(codeNodes.map(n => n.id));
const simLinks  = allLinks.filter(l => codeIds.has(l.source) && codeIds.has(l.target));

// ── State ─────────────────────────────────────────────────────────────────
let currentTab   = 'files';
let fileFilter   = '';
let selectedNode = null;
let selectedMindLayer = null;
let viewMode     = 'graph';

// ── Sidebar renders ───────────────────────────────────────────────────────
function renderFilesTab() {
  const cont = document.getElementById('tab-content');
  const q    = fileFilter.toLowerCase();
  let filtered = codeNodes.filter(n =>
    q === '' || n.name.toLowerCase().includes(q) || nodePath(n).toLowerCase().includes(q)
  );
  filtered.sort((a, b) => (b.incoming + b.deps.length) - (a.incoming + a.deps.length));

  cont.innerHTML = filtered.slice(0, 250).map(n => `
    <div class="file-item ${selectedNode && selectedNode.id === n.id ? 'selected' : ''}"
         onclick='selectNodeById(${JSON.stringify(n.id)})'>
      <div class="fname">
        <span class="layer-dot" style="background:${nodeColor(n)}"></span>
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${n.name}</span>
      </div>
      <div class="fmeta">${n.layer} · ${n.lines.toLocaleString()} lines · ${n.fnCount} fns · ↑${n.incoming}</div>
    </div>
  `).join('') || '<div style="padding:16px;color:#64748b;font-size:11px">No files match.</div>';
}

function renderMetricsTab() {
  const cont = document.getElementById('tab-content');
  const langHtml = stats.languages.slice(0, 8).map(l => `
    <div class="metric-row">
      <span class="mlabel">${l.ext || 'other'}</span>
      <span class="mval">${l.lines.toLocaleString()} (${l.pct}%)</span>
    </div>`).join('');
  const layerHtml = Object.entries(stats.layers).sort((a,b)=>b[1]-a[1]).map(([k, v]) => `
    <div class="metric-row">
      <span class="mlabel"><span class="layer-dot" style="background:${LAYER_COLORS[k]||'#94a3b8'}"></span>${k}</span>
      <span class="mval">${v} files</span>
    </div>`).join('');
  cont.innerHTML = `<div class="metric-section">
    <div class="metric-row"><span class="mlabel">Total files</span><span class="mval">${stats.total_files}</span></div>
    <div class="metric-row"><span class="mlabel">Code files</span><span class="mval">${stats.code_files}</span></div>
    <div class="metric-row"><span class="mlabel">Total LOC</span><span class="mval">${stats.total_loc.toLocaleString()}</span></div>
    <div class="metric-row"><span class="mlabel">Dep edges</span><span class="mval">${stats.total_edges}</span></div>
    <div class="metric-row"><span class="mlabel">Circular deps</span>
      <span class="mval" style="color:${stats.circular_deps>0?'#ff5f5f':'#00ff9d'}">${stats.circular_deps}</span></div>
    <div class="metric-row"><span class="mlabel">High coupling</span>
      <span class="mval" style="color:${stats.high_coupling>0?'#f59e0b':'#00ff9d'}">${stats.high_coupling}</span></div>
    <div style="margin:10px 0 5px;font-size:10px;font-weight:700;text-transform:uppercase;color:#64748b">By Language</div>
    ${langHtml}
    <div style="margin:10px 0 5px;font-size:10px;font-weight:700;text-transform:uppercase;color:#64748b">By Layer</div>
    ${layerHtml}
  </div>`;
}

function renderIssuesTab() {
  const cont = document.getElementById('tab-content');
  if (!issues.length) {
    cont.innerHTML = '<div style="padding:24px;text-align:center;color:#00ff9d;font-size:12px">✅ No issues found!</div>';
    return;
  }
  cont.innerHTML = `<div class="issues-section">${issues.map(issue => `
    <div class="issue-card ${issue.type}">
      <div class="issue-title">${issue.title}</div>
      <div style="color:#64748b;font-size:10px;margin-bottom:5px">${issue.desc}</div>
      ${(issue.items || []).slice(0, 6).map(item => `
        <div class="issue-item">
          ${item.file || item.fn || (Array.isArray(item.files) ? item.files.map(f=>f.split('/').pop()).join(' ↔ ') : JSON.stringify(item))}
        </div>`).join('')}
      ${issue.items && issue.items.length > 6 ? `<div style="color:#64748b;font-size:10px;margin-top:3px">+${issue.items.length-6} more</div>` : ''}
    </div>`).join('')}</div>`;
}

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach((t, i) =>
    t.classList.toggle('active', ['files','metrics','issues'][i] === tab)
  );
  if (tab === 'files') renderFilesTab();
  else if (tab === 'metrics') renderMetricsTab();
  else renderIssuesTab();
}

function filterFiles() {
  fileFilter = document.getElementById('search').value;
  if (currentTab === 'files') renderFilesTab();
}


// ── SVG setup ─────────────────────────────────────────────────────────────
const svg    = d3.select('#canvas');
const mainEl = document.getElementById('main');
const W      = () => mainEl.clientWidth;
const H      = () => mainEl.clientHeight;

// Layer-group order matters for correct z-ordering
const g           = svg.append('g');
const hullGroup   = g.append('g').attr('class', 'hulls');
const linkGroup   = g.append('g').attr('class', 'links');
const nodeGroup   = g.append('g').attr('class', 'nodes');
const labelGroup  = g.append('g').attr('class', 'labels').attr('pointer-events', 'none');
const layerLbl    = g.append('g').attr('class', 'layer-labels').attr('pointer-events', 'none').attr('opacity', 0);
const mindGroup   = g.append('g').attr('class', 'mind-map').style('display', 'none');

// Arrow marker
svg.append('defs').append('marker')
  .attr('id', 'arrow').attr('viewBox', '0 -4 8 8')
  .attr('refX', 16).attr('refY', 0)
  .attr('markerWidth', 5).attr('markerHeight', 5).attr('orient', 'auto')
  .append('path').attr('d', 'M0,-4L8,0L0,4').attr('fill', '#3d4468');

// Zoom — labels fade in as you zoom in
const zoom = d3.zoom().scaleExtent([0.03, 12]).on('zoom', e => {
  g.attr('transform', e.transform);
  const k = e.transform.k;
  // Labels visible from zoom-level 0.25 onward, fully visible at 0.55+
  const labelOpacity = Math.max(0, Math.min(1, (k - 0.22) / 0.33));
  labelGroup.attr('opacity', labelOpacity);
});
svg.call(zoom);
svg.on('click', () => clearSelection());

// ── Links ─────────────────────────────────────────────────────────────────
const linkSel = linkGroup.selectAll('line').data(simLinks).enter().append('line')
  .attr('stroke', '#2a2f4a')
  .attr('stroke-width', 1)
  .attr('stroke-opacity', 0.55)
  .attr('marker-end', 'url(#arrow)');

// ── Nodes ─────────────────────────────────────────────────────────────────
const nodeSel = nodeGroup.selectAll('circle').data(codeNodes).enter().append('circle')
  .attr('r', d => nodeRadius(d))
  .attr('fill', d => nodeColor(d))
  .attr('fill-opacity', 0.85)
  .attr('stroke', '#0f1117')
  .attr('stroke-width', 1.5)
  .style('cursor', 'pointer')
  .on('click', (e, d) => { e.stopPropagation(); selectNodeById(d.id); })
  .call(d3.drag()
    .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
    .on('end',   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
  );

// ── Labels for ALL nodes (zoom-controlled visibility) ─────────────────────
const labelSel = labelGroup.selectAll('text').data(codeNodes).enter().append('text')
  .text(d => shortName(d.name))
  .attr('font-size', 9)
  .attr('fill', '#cbd5e1')
  .attr('text-anchor', 'middle')
  .attr('dy', d => nodeRadius(d) + 11);

// ── Simulation ────────────────────────────────────────────────────────────
const sim = d3.forceSimulation(codeNodes)
  .force('link',      d3.forceLink(simLinks).id(d => d.id).distance(75).strength(0.35))
  .force('charge',    d3.forceManyBody().strength(d => -120 - d.fnCount * 2))
  .force('center',    d3.forceCenter(0, 0))
  .force('collision', d3.forceCollide(d => nodeRadius(d) + 6));

sim.on('tick', () => {
  // Compute link end points at node edge (not center) so arrow looks right
  linkSel
    .attr('x1', d => d.source.x)
    .attr('y1', d => d.source.y)
    .attr('x2', d => {
      const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
      const dist = Math.sqrt(dx*dx + dy*dy) || 1;
      return d.target.x - (dx / dist) * (nodeRadius(d.target) + 4);
    })
    .attr('y2', d => {
      const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
      const dist = Math.sqrt(dx*dx + dy*dy) || 1;
      return d.target.y - (dy / dist) * (nodeRadius(d.target) + 4);
    });

  nodeSel.attr('cx', d => d.x).attr('cy', d => d.y);
  labelSel.attr('x', d => d.x).attr('y', d => d.y);
  drawHulls();
});

// ── Convex hull backgrounds (layers mode) ─────────────────────────────────
function drawHulls() {
  if (viewMode !== 'layers') { hullGroup.selectAll('*').remove(); return; }
  const byLayer = {};
  codeNodes.forEach(d => { (byLayer[d.layer] = byLayer[d.layer] || []).push([d.x, d.y]); });
  hullGroup.selectAll('path').remove();
  Object.entries(byLayer).forEach(([layer, pts]) => {
    if (pts.length < 3) return;
    const hull = d3.polygonHull(pts);
    if (!hull) return;
    const cx = d3.mean(pts, p => p[0]), cy = d3.mean(pts, p => p[1]);
    const padded = hull.map(([x, y]) => {
      const dx = x - cx, dy = y - cy, len = Math.sqrt(dx*dx+dy*dy) || 1;
      return [x + dx/len * 30, y + dy/len * 30];
    });
    hullGroup.append('path')
      .attr('d', 'M' + padded.join('L') + 'Z')
      .attr('fill', LAYER_COLORS[layer] || '#94a3b8')
      .attr('fill-opacity', 0.06)
      .attr('stroke', LAYER_COLORS[layer] || '#94a3b8')
      .attr('stroke-opacity', 0.28)
      .attr('stroke-width', 1.5)
      .attr('stroke-dasharray', '5 3');
  });
}

// ── Mind graph: draggable project map + class flow ───────────────────────
let mindNodePositions = new Map();
let mindBranchPositions = new Map();
let mindChildPositions = new Map();
let mindClassPositions = new Map();
let mindDepPositions = new Map();
let mindRootPosition = { x: -760, y: 0 };
let mindLayoutMetrics = { rootW: 250, leafX: 650, leafEndX: 1780 };

const MIND_PROJECT_GROUPS = [
  {
    key: 'app',
    title: 'Application Source',
    color: '#4d9fff',
    match: n => nodePath(n).startsWith('{{JAVA_MAIN_PKG}}'),
    childKey: n => nodePath(n).replace('{{JAVA_MAIN_PKG}}', '').split('/')[0] || 'root'
  },
  {
    key: 'tests',
    title: 'Tests',
    color: '#f59e0b',
    match: n => nodePath(n).startsWith('src/test/'),
    childKey: n => n.layer === 'test' ? 'unit and slice tests' : n.layer
  },
  {
    key: 'resources',
    title: 'Resources and Config',
    color: '#ec4899',
    match: n => nodePath(n).startsWith('src/main/resources/') || n.layer === 'config',
    childKey: n => nodePath(n).startsWith('src/main/resources/graphql') ? 'graphql schema' : n.layer
  },
  {
    key: 'platform',
    title: 'Platform and Legacy',
    color: '#a78bfa',
    match: n => nodePath(n).startsWith('src/main/java/org/') || nodePath(n).startsWith('docker/'),
    childKey: n => nodePath(n).startsWith('src/main/java/org/') ? 'spring oauth support' : 'docker assets'
  },
  {
    key: 'repo',
    title: 'Repo Documents',
    color: '#22d3ee',
    match: n => nodePath(n).startsWith('docs/') || n.folder === '.',
    childKey: n => nodePath(n).startsWith('docs/adr') ? 'architecture records' : n.ext || 'root'
  }
];

function mindImportance(d) {
  return (d.incoming || 0) * 2 + (d.deps || []).length + (d.fnCount || 0) * 0.45 + (d.lines || 0) / 260;
}

function mindProjectRows() {
  return MIND_PROJECT_GROUPS.map(group => {
    const nodes = codeNodes.filter(group.match).sort((a, b) => mindImportance(b) - mindImportance(a));
    const children = Array.from(d3.group(nodes, group.childKey), ([key, values]) => ({
      key,
      title: String(key).replace(/[-_]/g, ' '),
      values: values.sort((a, b) => mindImportance(b) - mindImportance(a))
    })).sort((a, b) => b.values.length - a.values.length).slice(0, 5);
    const selectedInGroup = selectedNode && group.match(selectedNode);
    if (selectedInGroup) {
      const selectedKey = group.childKey(selectedNode);
      const selectedChild = children.find(c => c.key === selectedKey);
      if (selectedChild) {
        const visibleValues = selectedChild.values.slice(0, 4);
        if (!visibleValues.some(n => n.id === selectedNode.id)) {
          selectedChild.values = [
            selectedNode,
            ...selectedChild.values.filter(n => n.id !== selectedNode.id)
          ];
        }
      } else {
        children.push({ key: 'selected', title: 'selected file', values: [selectedNode] });
      }
    }
    return { ...group, nodes, children };
  }).filter(group => group.nodes.length);
}

function mindPath(x1, y1, x2, y2) {
  const bend = Math.max(100, Math.abs(x2 - x1) * 0.46);
  return `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`;
}

function cleanClassName(name) {
  return String(name || '').split('.').pop().replace(/\$.*$/, '');
}

function nodeDisplayClass(n) {
  return cleanClassName((n.classes && n.classes[0]) || n.name.replace(/\.[^.]+$/, ''));
}

function nodeByDataId(selection, attrName, id) {
  return selection.filter(function() { return this.getAttribute(attrName) === id; });
}

function mindRootAnchor() {
  return { x: mindRootPosition.x + mindLayoutMetrics.rootW / 2 - 8, y: mindRootPosition.y };
}

function updateMindMainBranch(layer) {
  const branch = mindBranchPositions.get(layer);
  if (!branch) return;
  const root = mindRootAnchor();
  nodeByDataId(mindGroup.selectAll('.mind-main-branch'), 'data-layer', layer)
    .attr('d', mindPath(root.x, root.y, branch.x, branch.y));
}

function updateMindChildLine(childId) {
  const child = mindChildPositions.get(childId);
  if (!child) return;
  const branch = mindBranchPositions.get(child.layer);
  if (!branch) return;
  nodeByDataId(mindGroup.selectAll('.mind-child-line'), 'data-child-id', childId)
    .attr('d', mindPath(branch.x + 120, branch.y, child.x, child.y));
}

function updateMindLeafLine(nodeId) {
  const nodePos = mindNodePositions.get(nodeId);
  const node = nodeById[nodeId];
  if (!nodePos || !node) return;
  const child = mindChildPositions.get(nodePos.childId) || { x: nodePos.parentX, y: nodePos.parentY };
  const lineEnd = Math.min(mindLayoutMetrics.leafEndX, nodePos.x + Math.max(260, shortName(node.name).length * 13));
  nodeByDataId(mindGroup.selectAll('.mind-leaf-line'), 'data-node-id', nodeId)
    .attr('d', mindPath(child.x + 110, child.y, nodePos.x, nodePos.y) + ` L${lineEnd},${nodePos.y}`);
}

function updateMindClassLine(classId) {
  const classPos = mindClassPositions.get(classId);
  if (!classPos) return;
  nodeByDataId(mindGroup.selectAll('.mind-class-line'), 'data-class-id', classId)
    .attr('d', mindPath(classPos.sourceX, classPos.sourceY, classPos.x, classPos.y));
}

function updateMindClassDepLines() {
  mindGroup.selectAll('.mind-class-dep-line').each(function() {
    const classPos = mindClassPositions.get(this.getAttribute('data-class-id'));
    const depPos = mindDepPositions.get(this.getAttribute('data-dep-id'));
    if (!classPos || !depPos) return;
    d3.select(this).attr('d', mindPath(classPos.x + 220, classPos.y, depPos.x, depPos.y));
  });
}

function renderMindMap() {
  mindNodePositions = new Map();
  mindBranchPositions = new Map();
  mindChildPositions = new Map();
  mindClassPositions = new Map();
  mindDepPositions = new Map();
  mindGroup.selectAll('*').remove();

  const rows = mindProjectRows();
  const rootX = -760, rootY = 0, rootW = 250, rootH = 70;
  const branchX = -360, childX = 70, leafX = 650, leafEndX = 1780;
  mindRootPosition = { x: rootX, y: rootY };
  mindLayoutMetrics = { rootW, leafX, leafEndX };
  const leafGap = 44;
  const rowGap = 170;
  const rowHeights = rows.map(row => {
    const childBlocks = row.children.map(child => Math.max(120, Math.min(child.values.length, 4) * leafGap + 38));
    return Math.max(210, childBlocks.reduce((sum, h) => sum + h, 0) + Math.max(0, childBlocks.length - 1) * 44);
  });
  const totalHeight = rowHeights.reduce((sum, h) => sum + h, 0) + rowGap * Math.max(0, rows.length - 1);
  let cursorY = -totalHeight / 2;

  const root = mindGroup.append('g')
    .attr('class', 'mind-root')
    .attr('transform', `translate(${rootX},${rootY})`)
    .style('cursor', 'grab')
    .call(d3.drag()
      .on('start', function() { d3.select(this).style('cursor', 'grabbing'); })
      .on('drag', function(e) {
        mindRootPosition.x += e.dx;
        mindRootPosition.y += e.dy;
        d3.select(this).attr('transform', `translate(${mindRootPosition.x},${mindRootPosition.y})`);
        mindBranchPositions.forEach((_, layer) => updateMindMainBranch(layer));
      })
      .on('end', function() { d3.select(this).style('cursor', 'grab'); })
    );
  root.append('rect')
    .attr('x', -rootW / 2).attr('y', -rootH / 2)
    .attr('width', rootW).attr('height', rootH)
    .attr('rx', 16)
    .attr('fill', '#f8fafc')
    .attr('stroke', '#dbe4f0')
    .attr('stroke-width', 2)
    .attr('filter', 'drop-shadow(0 10px 20px rgba(15,17,23,.22))');
  root.append('text')
    .attr('x', 0).attr('y', -5)
    .attr('text-anchor', 'middle')
    .attr('font-size', 24)
    .attr('font-weight', 800)
    .attr('fill', '#2f3543')
    .text('Repo Map');
  root.append('text')
    .attr('x', 0).attr('y', 22)
    .attr('text-anchor', 'middle')
    .attr('font-size', 12)
    .attr('fill', '#64748b')
    .text(`${codeNodes.length} files • ${stats.total_edges} dependencies`);

  rows.forEach((row, i) => {
    const layer = row.key;
    const color = row.color;
    const branchY = cursorY + rowHeights[i] / 2;
    cursorY += rowHeights[i] + rowGap;
    mindBranchPositions.set(layer, { x: branchX, y: branchY });

    mindGroup.append('path')
      .attr('class', 'mind-main-branch')
      .attr('data-layer', layer)
      .attr('d', mindPath(mindRootAnchor().x, mindRootAnchor().y, branchX, branchY))
      .attr('fill', 'none')
      .attr('stroke', color)
      .attr('stroke-width', 8)
      .attr('stroke-linecap', 'round')
      .attr('stroke-opacity', 0.88);

    const branch = mindGroup.append('g')
      .attr('class', 'mind-branch')
      .attr('data-layer', layer)
      .attr('transform', `translate(${branchX},${branchY})`)
      .style('cursor', 'grab')
      .on('click', e => {
        e.stopPropagation();
        selectedMindLayer = layer;
        updateMindLayerSelection(layer);
      })
      .call(d3.drag()
        .on('start', function() { d3.select(this).style('cursor', 'grabbing'); })
        .on('drag', function(e) {
          const pos = mindBranchPositions.get(layer) || { x: branchX, y: branchY };
          pos.x += e.dx;
          pos.y += e.dy;
          mindBranchPositions.set(layer, pos);
          branch.attr('transform', `translate(${pos.x},${pos.y})`);
          updateMindMainBranch(layer);
          mindChildPositions.forEach((child, childId) => {
            if (child.layer === layer) updateMindChildLine(childId);
          });
        })
        .on('end', function() { d3.select(this).style('cursor', 'grab'); })
      );
    branch.append('rect')
      .attr('x', -24).attr('y', -46)
      .attr('width', 330).attr('height', 64)
      .attr('rx', 14)
      .attr('fill', '#0f1117')
      .attr('fill-opacity', 0.7)
      .attr('stroke', color)
      .attr('stroke-opacity', 0.22);
    branch.append('circle')
      .attr('r', 8)
      .attr('fill', '#f8fafc')
      .attr('stroke', color)
      .attr('stroke-width', 4);
    branch.append('text')
      .attr('x', 24).attr('y', -16)
      .attr('font-size', 20)
      .attr('font-weight', 700)
      .attr('fill', '#cbd5e1')
      .text(row.title);
    branch.append('text')
      .attr('x', 24).attr('y', 8)
      .attr('font-size', 11)
      .attr('fill', '#94a3b8')
      .text(`${row.nodes.length} files`);

    const childBlocks = row.children.map(child => Math.max(120, Math.min(child.values.length, 4) * leafGap + 38));
    let childCursor = branchY - rowHeights[i] / 2 + 30;
    row.children.forEach((childRow, childIndex) => {
      const childY = childCursor + childBlocks[childIndex] / 2;
      childCursor += childBlocks[childIndex] + 44;
      const childId = `${layer}:${childIndex}:${childRow.key}`;
      mindChildPositions.set(childId, { x: childX, y: childY, layer });

      mindGroup.append('path')
        .attr('class', 'mind-child-line')
        .attr('data-layer', layer)
        .attr('data-child-id', childId)
        .attr('d', mindPath(branchX + 120, branchY, childX, childY))
        .attr('fill', 'none')
        .attr('stroke', color)
        .attr('stroke-width', 5.5)
        .attr('stroke-linecap', 'round')
        .attr('stroke-opacity', 0.7);

      const childGroup = mindGroup.append('g')
        .attr('class', 'mind-child')
        .attr('data-layer', layer)
        .attr('data-child-id', childId)
        .attr('transform', `translate(${childX},${childY})`)
        .style('cursor', 'grab')
        .call(d3.drag()
          .on('start', function() { d3.select(this).style('cursor', 'grabbing'); })
          .on('drag', function(e) {
            const pos = mindChildPositions.get(childId) || { x: childX, y: childY, layer };
            pos.x += e.dx;
            pos.y += e.dy;
            mindChildPositions.set(childId, pos);
            d3.select(this).attr('transform', `translate(${pos.x},${pos.y})`);
            updateMindChildLine(childId);
            mindNodePositions.forEach((nodePos, nodeId) => {
              if (nodePos.childId === childId) updateMindLeafLine(nodeId);
            });
          })
          .on('end', function() { d3.select(this).style('cursor', 'grab'); })
        );
      childGroup.append('circle')
        .attr('r', 5)
        .attr('fill', '#0f1117')
        .attr('stroke', color)
        .attr('stroke-width', 2);
      childGroup.append('rect')
        .attr('x', 16).attr('y', -30)
        .attr('width', Math.max(190, childRow.title.length * 9 + 80))
        .attr('height', 36)
        .attr('rx', 9)
        .attr('fill', '#151923')
        .attr('stroke', color)
        .attr('stroke-opacity', 0.22);
      childGroup.append('text')
        .attr('x', 28).attr('y', -10)
        .attr('font-size', 14)
        .attr('font-weight', 700)
        .attr('fill', '#d8dee9')
        .text(childRow.title);

      const leaves = childRow.values.slice(0, 4);
      leaves.forEach((n, j) => {
        const leafY = childY + (j - (leaves.length - 1) / 2) * leafGap + 14;
        const textX = leafX + (j % 2) * 190;
        const lineEnd = Math.min(leafEndX, textX + Math.max(340, shortName(n.name).length * 15));
        mindNodePositions.set(n.id, { x: textX, y: leafY, parentX: childX, parentY: childY, childId });

        mindGroup.append('path')
          .attr('class', 'mind-leaf-line')
          .attr('data-node-id', n.id)
          .attr('data-layer', layer)
          .attr('d', mindPath(childX + 110, childY, textX, leafY) + ` L${lineEnd},${leafY}`)
          .attr('fill', 'none')
          .attr('stroke', color)
          .attr('stroke-width', 4.5)
          .attr('stroke-linecap', 'round')
          .attr('stroke-opacity', 0.82);

        const item = mindGroup.append('g')
          .attr('class', 'mind-node')
          .attr('data-node-id', n.id)
          .attr('data-layer', layer)
          .attr('transform', `translate(${textX},${leafY})`)
          .style('cursor', 'grab')
          .on('click', e => { e.stopPropagation(); selectNodeById(n.id, true); })
          .call(d3.drag()
            .on('start', function() { d3.select(this).style('cursor', 'grabbing'); })
            .on('drag', function(e) {
              const pos = mindNodePositions.get(n.id) || { x: textX, y: leafY, childId };
              pos.x += e.dx;
              pos.y += e.dy;
              mindNodePositions.set(n.id, pos);
              d3.select(this).attr('transform', `translate(${pos.x},${pos.y})`);
              updateMindLeafLine(n.id);
              if (selectedNode && selectedNode.id === n.id) {
                mindClassPositions.forEach((classPos, classId) => {
                  classPos.sourceX = pos.x + 280;
                  classPos.sourceY = pos.y;
                  updateMindClassLine(classId);
                });
                updateMindClassDepLines();
              }
            })
            .on('end', function() { d3.select(this).style('cursor', 'grab'); })
          );
        item.append('circle')
          .attr('cx', -15).attr('cy', -3)
          .attr('r', 6)
          .attr('fill', '#0f1117')
          .attr('stroke', color)
          .attr('stroke-width', 2);
        item.append('text')
          .attr('x', 0).attr('y', -8)
          .attr('font-size', 15)
          .attr('font-weight', n.incoming > 8 ? 700 : 500)
          .attr('fill', '#d8dee9')
          .text(shortName(n.name));
        item.append('text')
          .attr('x', 0).attr('y', 12)
          .attr('font-size', 9)
          .attr('fill', '#64748b')
          .text(`${n.layer} • ${n.lines.toLocaleString()} lines`);
        item.append('title').text(nodePath(n));
      });
    });
  });

  mindGroup.append('g').attr('class', 'mind-class-flow');
  updateMindSelection(selectedNode && selectedNode.id);
  if (selectedMindLayer) updateMindLayerSelection(selectedMindLayer);
}

function renderClassFlowForNode(n) {
  const flow = mindGroup.select('.mind-class-flow');
  flow.selectAll('*').remove();
  mindClassPositions = new Map();
  mindDepPositions = new Map();
  if (!n) return;
  const pos = mindNodePositions.get(n.id);
  if (!pos) return;

  const color = nodeColor(n);
  const startX = pos.x + 280;
  const classX = startX + 230;
  const depX = classX + 500;
  const classNames = (n.classes && n.classes.length ? n.classes : [nodeDisplayClass(n)]).slice(0, 4).map(cleanClassName);
  const deps = (n.deps || [])
    .map(id => nodeById[id])
    .filter(Boolean)
    .filter(dep => isVisibleRepoNode(dep))
    .filter((dep, index, arr) => arr.findIndex(other => nodeDisplayClass(other) === nodeDisplayClass(dep)) === index)
    .slice(0, 8);
  const classRows = classNames.map((className, i) => ({
    id: `${n.id}:class:${i}`,
    className,
    y: pos.y + (i - (classNames.length - 1) / 2) * 76
  }));
  const depRows = deps.map((dep, j) => ({
    id: `${n.id}:dep:${j}`,
    dep,
    y: pos.y + (j - (deps.length - 1) / 2) * 54
  }));

  classRows.forEach(row => {
    mindClassPositions.set(row.id, { x: classX, y: row.y, sourceX: startX, sourceY: pos.y });
    flow.append('path')
      .attr('class', 'mind-class-line')
      .attr('data-class-id', row.id)
      .attr('d', mindPath(startX, pos.y, classX, row.y))
      .attr('fill', 'none')
      .attr('stroke', color)
      .attr('stroke-width', 4)
      .attr('stroke-linecap', 'round')
      .attr('stroke-opacity', 0.92);
    const classNode = flow.append('g')
      .attr('class', 'mind-class-node')
      .attr('data-class-id', row.id)
      .attr('transform', `translate(${classX},${row.y})`)
      .style('cursor', 'grab')
      .call(d3.drag()
        .on('start', function() { d3.select(this).style('cursor', 'grabbing'); })
        .on('drag', function(e) {
          const classPos = mindClassPositions.get(row.id) || { x: classX, y: row.y, sourceX: startX, sourceY: pos.y };
          classPos.x += e.dx;
          classPos.y += e.dy;
          mindClassPositions.set(row.id, classPos);
          d3.select(this).attr('transform', `translate(${classPos.x},${classPos.y})`);
          updateMindClassLine(row.id);
          updateMindClassDepLines();
        })
        .on('end', function() { d3.select(this).style('cursor', 'grab'); })
      );
    classNode.append('circle')
      .attr('cx', -18).attr('cy', -5).attr('r', 7)
      .attr('fill', '#0f1117').attr('stroke', color).attr('stroke-width', 2.5);
    classNode.append('rect')
      .attr('x', -3).attr('y', -28)
      .attr('width', Math.max(190, row.className.length * 8.5 + 38))
      .attr('height', 30).attr('rx', 9)
      .attr('fill', '#151923').attr('stroke', color).attr('stroke-opacity', 0.32);
    classNode.append('text')
      .attr('x', 14).attr('y', -9)
      .attr('font-size', 13).attr('font-weight', 700)
      .attr('fill', '#f8fafc')
      .text(row.className);
  });

  depRows.forEach(row => {
    const nearestClass = classRows.length
      ? classRows.reduce((best, current) => Math.abs(current.y - row.y) < Math.abs(best.y - row.y) ? current : best, classRows[0])
      : { id: '', y: pos.y };
    const dep = row.dep;
    const depClass = nodeDisplayClass(dep);
    mindDepPositions.set(row.id, { x: depX, y: row.y });
    flow.append('path')
      .attr('class', 'mind-class-dep-line')
      .attr('data-class-id', nearestClass.id || '')
      .attr('data-dep-id', row.id)
      .attr('d', mindPath(classX + 220, nearestClass.y, depX, row.y))
      .attr('fill', 'none')
      .attr('stroke', nodeColor(dep))
      .attr('stroke-width', 2.8)
      .attr('stroke-linecap', 'round')
      .attr('stroke-opacity', 0.7);
    const depNode = flow.append('g')
      .attr('class', 'mind-class-dep-node')
      .attr('data-node-id', dep.id)
      .attr('data-dep-id', row.id)
      .attr('transform', `translate(${depX},${row.y})`)
      .style('cursor', 'grab')
      .on('click', e => { e.stopPropagation(); selectNodeById(dep.id, true); })
      .call(d3.drag()
        .on('start', function() { d3.select(this).style('cursor', 'grabbing'); })
        .on('drag', function(e) {
          const depPos = mindDepPositions.get(row.id) || { x: depX, y: row.y };
          depPos.x += e.dx;
          depPos.y += e.dy;
          mindDepPositions.set(row.id, depPos);
          d3.select(this).attr('transform', `translate(${depPos.x},${depPos.y})`);
          updateMindClassDepLines();
        })
        .on('end', function() { d3.select(this).style('cursor', 'grab'); })
      );
    depNode.append('circle')
      .attr('cx', -15).attr('cy', -4).attr('r', 5)
      .attr('fill', '#0f1117').attr('stroke', nodeColor(dep)).attr('stroke-width', 2);
    depNode.append('rect')
      .attr('x', -1).attr('y', -24)
      .attr('width', Math.max(170, depClass.length * 8 + 36))
      .attr('height', 28).attr('rx', 8)
      .attr('fill', '#10151f').attr('stroke', nodeColor(dep)).attr('stroke-opacity', 0.24);
    depNode.append('text')
      .attr('x', 10).attr('y', -7)
      .attr('font-size', 12).attr('font-weight', 650)
      .attr('fill', '#cbd5e1')
      .text(depClass);
    depNode.append('text')
      .attr('x', 10).attr('y', 10)
      .attr('font-size', 9)
      .attr('fill', '#64748b')
      .text(dep.layer);
  });
}

function updateMindSelection(id) {
  selectedMindLayer = null;
  mindGroup.selectAll('.mind-node').attr('opacity', null);
  mindGroup.selectAll('.mind-main-branch').attr('stroke-opacity', 0.88).attr('stroke-width', 8);
  mindGroup.selectAll('.mind-branch,.mind-child').attr('opacity', 1);
  mindGroup.selectAll('.mind-leaf-line').attr('stroke-opacity', 0.82).attr('stroke-width', 4.5);
  mindGroup.selectAll('.mind-node circle').attr('fill', '#0f1117').attr('r', 6);
  mindGroup.select('.mind-class-flow').selectAll('*').remove();
  if (!id) return;
  mindGroup.selectAll('.mind-node').attr('opacity', function() {
    return this.getAttribute('data-node-id') === id ? 1 : 0.32;
  });
  mindGroup.selectAll('.mind-node circle')
    .attr('fill', function() { return this.parentNode.getAttribute('data-node-id') === id ? '#ffffff' : '#0f1117'; })
    .attr('r', function() { return this.parentNode.getAttribute('data-node-id') === id ? 8 : 6; });
  mindGroup.selectAll('.mind-leaf-line')
    .attr('stroke-opacity', function() { return this.getAttribute('data-node-id') === id ? 1 : 0.18; })
    .attr('stroke-width', function() { return this.getAttribute('data-node-id') === id ? 7 : 4.5; });
  renderClassFlowForNode(nodeById[id]);
}

function updateMindLayerSelection(layer) {
  selectedMindLayer = layer || null;
  mindGroup.selectAll('.mind-main-branch')
    .attr('stroke-opacity', function() { return !layer || this.getAttribute('data-layer') === layer ? 0.95 : 0.12; })
    .attr('stroke-width', function() { return this.getAttribute('data-layer') === layer ? 11 : 8; });
  mindGroup.selectAll('.mind-branch,.mind-child')
    .attr('opacity', function() { return !layer || this.getAttribute('data-layer') === layer ? 1 : 0.22; });
  mindGroup.selectAll('.mind-node')
    .attr('opacity', function() { return !layer || this.getAttribute('data-layer') === layer ? 1 : 0.16; });
  mindGroup.selectAll('.mind-leaf-line')
    .attr('stroke-opacity', function() { return !layer || this.getAttribute('data-layer') === layer ? 0.9 : 0.08; })
    .attr('stroke-width', function() { return this.getAttribute('data-layer') === layer ? 6 : 4.5; });
}

function fitMindMap() {
  let box;
  try { box = mindGroup.node().getBBox(); } catch (e) { box = { x: -840, y: -760, width: 2200, height: 1520 }; }
  const pad = 80;
  const scale = Math.max(0.2, Math.min((W() - pad) / box.width, (H() - pad) / box.height, 1.35));
  const tx = W() / 2 - scale * (box.x + box.width / 2);
  const ty = H() / 2 - scale * (box.y + box.height / 2);
  svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}

function cameraFocusMindNode(id) {
  const pos = mindNodePositions.get(id);
  if (!pos) return;
  const s = 1.2;
  svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity.translate(W() / 2 - s * pos.x, H() / 2 - s * pos.y).scale(s));
}

// ── Fit to view (uses actual node bounding box) ───────────────────────────
function fitToView() {
  const xs = codeNodes.map(d => d.x).filter(v => isFinite(v));
  const ys = codeNodes.map(d => d.y).filter(v => isFinite(v));
  if (!xs.length) return;
  const pad  = 60;
  const xMin = Math.min(...xs) - pad, xMax = Math.max(...xs) + pad;
  const yMin = Math.min(...ys) - pad, yMax = Math.max(...ys) + pad;
  const w = W(), h = H();
  const scaleX = w / (xMax - xMin);
  const scaleY = h / (yMax - yMin);
  const scale  = Math.max(0.04, Math.min(Math.min(scaleX, scaleY) * 0.9, 3));
  const tx = w / 2 - scale * (xMin + xMax) / 2;
  const ty = h / 2 - scale * (yMin + yMax) / 2;
  svg.transition().duration(600)
     .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
}

// ── Layer cluster centers ─────────────────────────────────────────────────
const LAYER_CENTERS = {
  controller: [0,    -500],  service:    [-480, -200],
  middleware: [-480,  200],  model:      [480,  -200],
  data:       [480,   200],  util:       [0,     500],
  config:     [-700,    0],  test:       [700,     0],
  schema:     [0,       0],  component:  [-240, -500],
  ui:         [240,  -500],  other:      [0,    200],
};

// ── View switching ────────────────────────────────────────────────────────
function setView(mode) {
  viewMode = mode;
  document.getElementById('btn-graph').classList.toggle('active',  mode === 'graph');
  document.getElementById('btn-layers').classList.toggle('active', mode === 'layers');
  document.getElementById('btn-mind').classList.toggle('active',   mode === 'mind');
  mindGroup.style('display', mode === 'mind' ? null : 'none');
  linkGroup.style('display', mode === 'mind' ? 'none' : null);
  nodeGroup.style('display', mode === 'mind' ? 'none' : null);
  labelGroup.style('display', mode === 'mind' ? 'none' : null);

  if (mode === 'layers') {
    // Apply clustering forces toward layer centers
    sim.force('x', d3.forceX(d => (LAYER_CENTERS[d.layer] || LAYER_CENTERS.other)[0]).strength(0.18));
    sim.force('y', d3.forceY(d => (LAYER_CENTERS[d.layer] || LAYER_CENTERS.other)[1]).strength(0.18));
    sim.force('center', null);
    // Add large layer-name labels at each center
    layerLbl.attr('opacity', 1).selectAll('*').remove();
    Object.entries(LAYER_CENTERS).forEach(([layer, [cx, cy]]) => {
      if (!codeNodes.some(n => n.layer === layer)) return;
      layerLbl.append('text')
        .attr('x', cx).attr('y', cy - 70)
        .attr('text-anchor', 'middle')
        .attr('font-size', 14).attr('font-weight', 700)
        .attr('fill', LAYER_COLORS[layer] || '#94a3b8')
        .attr('fill-opacity', 0.6)
        .text(layer.toUpperCase());
    });
    sim.alpha(0.85).restart();
  } else if (mode === 'mind') {
    hullGroup.selectAll('*').remove();
    layerLbl.attr('opacity', 0).selectAll('*').remove();
    sim.stop();
    renderMindMap();
    setTimeout(fitMindMap, 80);
    return;
  } else {
    // Restore center-based layout
    sim.force('x', null).force('y', null);
    sim.force('center', d3.forceCenter(0, 0));
    hullGroup.selectAll('*').remove();
    layerLbl.attr('opacity', 0).selectAll('*').remove();
    sim.alpha(0.5).restart();
  }
  // Re-fit after sim settles
  setTimeout(fitToView, 1600);
}

// ── Camera: pan + zoom to a node ─────────────────────────────────────────
function cameraFocusNode(n, scale) {
  if (!n || !isFinite(n.x) || !isFinite(n.y)) return;
  const w = W(), h = H();
  const s = scale || 2.2;
  const tx = w / 2 - s * n.x;
  const ty = h / 2 - s * n.y;
  svg.transition().duration(550)
     .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(s));
}

// ── Selection + blast radius ──────────────────────────────────────────────
function selectNodeById(id, fromSidebar) {
  selectedNode = nodeById[id];
  if (!selectedNode) return;

  // BFS outgoing blast radius
  const blast = new Set([id]);
  const queue = [id];
  while (queue.length) {
    const cur = queue.shift();
    simLinks.forEach(l => {
      const s = srcId(l), t = tgtId(l);
      if (s === cur && !blast.has(t)) { blast.add(t); queue.push(t); }
    });
  }
  // Also add direct incomers (1 hop)
  simLinks.forEach(l => { if (tgtId(l) === id) blast.add(srcId(l)); });

  // ── Dim everything not in blast set ──────────────────────────────────────
  nodeSel
    .attr('fill-opacity', d => blast.has(d.id) ? 0.95 : 0.05)
    .attr('stroke',       d => {
      if (d.id === id)          return '#ffffff';
      if (blast.has(d.id))      return nodeColor(d);
      return 'none';
    })
    .attr('stroke-width', d => d.id === id ? 3.5 : 1.5)
    .attr('r',            d => d.id === id ? nodeRadius(d) * 1.35 : nodeRadius(d));

  linkSel
    .attr('stroke',         l => blast.has(srcId(l)) && blast.has(tgtId(l)) ? nodeColor(nodeById[srcId(l)] || {}) : '#1e2235')
    .attr('stroke-opacity', l => blast.has(srcId(l)) && blast.has(tgtId(l)) ? 0.9  : 0.02)
    .attr('stroke-width',   l => blast.has(srcId(l)) && blast.has(tgtId(l)) ? 1.5  : 0.5);

  // Force labels fully visible for blast set
  labelGroup.attr('opacity', 1);
  labelSel
    .attr('fill',         d => d.id === id ? '#ffffff' : blast.has(d.id) ? '#cbd5e1' : '#334155')
    .attr('font-weight',  d => d.id === id ? 700 : 400)
    .attr('font-size',    d => d.id === id ? 11  : 9)
    .attr('fill-opacity', d => blast.has(d.id) ? 1 : 0.0);

  // ── Pan + zoom camera to the selected node ────────────────────────────────
  // If blast is large zoom out a bit so the whole cluster is visible
  const zoomScale = blast.size > 30 ? 1.2 : blast.size > 10 ? 1.8 : 2.5;
  if (viewMode === 'mind') {
    if (!mindNodePositions.has(id)) renderMindMap();
    updateMindSelection(id);
    cameraFocusMindNode(id);
  } else {
    cameraFocusNode(selectedNode, zoomScale);
  }

  // ── Info panel ────────────────────────────────────────────────────────────
  const n   = selectedNode;
  const dep = (n.deps || []).map(d =>
    `<div class="dep-item" onclick='selectNodeById(${JSON.stringify(d)})' title="${d}">→ ${d.split('/').pop()}</div>`
  ).join('');
  const fns = (n.functions || []).map(f => `<div class="fn-item">⚡ ${f}</div>`).join('');
  const cls = (n.classes   || []).map(c => `<div class="cls-item">◆ ${c}</div>`).join('');

  document.getElementById('info-content').innerHTML = `
    <h2>${n.name}</h2>
    <div class="stat-row"><span>Layer</span><span style="color:${nodeColor(n)}">${n.layer}</span></div>
    <div class="stat-row"><span>Lines</span><span>${n.lines.toLocaleString()}</span></div>
    <div class="stat-row"><span>Functions</span><span>${n.fnCount}</span></div>
    <div class="stat-row"><span>Incoming</span><span>${n.incoming}</span></div>
    <div class="stat-row"><span>Outgoing</span><span>${(n.deps||[]).length}</span></div>
    <div class="stat-row"><span>Blast radius</span><span style="color:#ff9f43">${blast.size} files</span></div>
    <div class="stat-row"><span>Path</span><span style="font-size:9px">${n.folder}/</span></div>
    ${cls ? `<div class="section-title">Classes (${(n.classes||[]).length})</div><div class="fn-list">${cls}</div>` : ''}
    ${fns ? `<div class="section-title">Functions (${n.fnCount})</div><div class="fn-list">${fns}</div>` : ''}
    ${dep ? `<div class="section-title">Outgoing deps (${(n.deps||[]).length})</div><div class="fn-list">${dep}</div>` : ''}
  `;
  document.getElementById('info-panel').style.display = 'block';
  if (currentTab === 'files') renderFilesTab();
}

function clearSelection() {
  selectedNode = null;
  selectedMindLayer = null;
  if (viewMode === 'mind') updateMindSelection(null);
  nodeSel
    .attr('fill-opacity', 0.85)
    .attr('stroke', '#0f1117')
    .attr('stroke-width', 1.5)
    .attr('r', d => nodeRadius(d));
  linkSel
    .attr('stroke', '#2a2f4a')
    .attr('stroke-opacity', 0.55)
    .attr('stroke-width', 1);
  labelSel
    .attr('fill', '#cbd5e1')
    .attr('font-weight', 400)
    .attr('font-size', 9)
    .attr('fill-opacity', null);
  document.getElementById('info-panel').style.display = 'none';
  if (currentTab === 'files') renderFilesTab();
}

// ── Export ────────────────────────────────────────────────────────────────
function exportJSON() {
  const blob = new Blob([JSON.stringify(GRAPH_DATA, null, 2)], {type: 'application/json'});
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = 'repo-graph.json'; a.click();
  URL.revokeObjectURL(url);
}

// ── Init ──────────────────────────────────────────────────────────────────
switchTab('files');
// Fit after sim has settled enough (~2s for large graphs)
sim.on('end', fitToView);
setTimeout(fitToView, 2500);
</script>
</body>
</html>"""


def detect_build_system(root: Path) -> dict[str, str]:
    """Auto-detect the build system and return the correct test command strings."""
    if (root / "pom.xml").exists():
        return {
            "build_system": "maven",
            "full_test_command": "mvn --no-transfer-progress test -DfailIfNoTests=false",
            "targeted_test_command_template":
                "mvn --no-transfer-progress test -Dtest=\"{TEST_CLASSES}\" -DfailIfNoTests=false",
        }
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        return {
            "build_system": "gradle",
            "full_test_command": "./gradlew test",
            "targeted_test_command_template": "./gradlew test --tests \"{TEST_CLASSES}\"",
        }
    if (root / "package.json").exists():
        # prefer jest; fall back to npm test
        pkg = {}
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        if "jest" in deps or "vitest" in deps:
            runner = "npx vitest run" if "vitest" in deps else "npx jest"
            return {
                "build_system": "npm-jest" if "jest" in deps else "npm-vitest",
                "full_test_command": runner,
                "targeted_test_command_template": f"{runner} --testPathPattern=\"{{TEST_CLASSES}}\"",
            }
        return {
            "build_system": "npm",
            "full_test_command": "npm test",
            "targeted_test_command_template": "npm test -- --grep \"{TEST_CLASSES}\"",
        }
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() \
            or (root / "requirements.txt").exists():
        return {
            "build_system": "python-pytest",
            "full_test_command": "pytest",
            "targeted_test_command_template": "pytest -k \"{TEST_CLASSES}\"",
        }
    if (root / "go.mod").exists():
        return {
            "build_system": "go",
            "full_test_command": "go test ./...",
            "targeted_test_command_template": "go test ./... -run \"{TEST_CLASSES}\"",
        }
    if (root / "Cargo.toml").exists():
        return {
            "build_system": "rust-cargo",
            "full_test_command": "cargo test",
            "targeted_test_command_template": "cargo test \"{TEST_CLASSES}\"",
        }
    if (root / "mix.exs").exists():
        return {
            "build_system": "elixir-mix",
            "full_test_command": "mix test",
            "targeted_test_command_template": "mix test --only \"{TEST_CLASSES}\"",
        }
    # Generic fallback
    return {
        "build_system": "unknown",
        "full_test_command": "# replace with your test command",
        "targeted_test_command_template": "# replace with your targeted test command for {TEST_CLASSES}",
    }


def write_memory_index(data: dict[str, Any]) -> None:
    """Merge graph analysis into .memory/codebase-index.json for AI agent consumption.

    Fully portable — works for any project/language:
    - Auto-detects build system  (Maven / Gradle / npm / pytest / Go / Rust …)
    - Removes stale patterns from previous projects
    - Preserves valid manual patterns whose files still exist in this repo
    - Auto-discovers test class mappings for the current project
    """
    memory_dir = AUTOMATION_DIR / ".memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    index_path = memory_dir / "codebase-index.json"

    # Load existing index
    existing: dict[str, Any] = {}
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    nodes  = data["nodes"]
    stats  = data["stats"]
    now    = datetime.now().isoformat(timespec="seconds")
    all_paths: set[str] = {n["id"] for n in nodes}  # all repo-relative paths

    # ── Auto-detect build system ──────────────────────────────────────────
    build_info = detect_build_system(REPO_ROOT)

    # ── Layer groups ──────────────────────────────────────────────────────
    LAYER_DESC = {
        "controller": "HTTP/GraphQL controllers and REST endpoints — entry points for all mutations/queries",
        "service":    "Business logic and application services — core domain operations",
        "data":       "Repositories, DAOs, storage services — all database/graph DB access",
        "model":      "Domain models, entities, DTOs — data structures",
        "util":       "Utilities, helpers, constants — shared low-level logic",
        "config":     "Application configuration and settings",
        "middleware": "Interceptors, filters, aspects — cross-cutting concerns",
        "schema":     "GraphQL/OpenAPI schema files — API contract definitions",
        "component":  "Reusable component layer",
        "other":      "Uncategorized source files",
    }
    by_layer: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        if n["is_code"] and n["layer"] != "test":
            by_layer[n["layer"]].append(n["id"])

    graph_layers = {
        layer: {
            "description": LAYER_DESC.get(layer, f"{layer} layer"),
            "file_count":  len(files),
            "files":       sorted(files)[:60],
        }
        for layer, files in sorted(by_layer.items())
    }

    # ── Hotspots ──────────────────────────────────────────────────────────
    hotspots = sorted(
        [n for n in nodes if n["is_code"] and n["incoming"] >= 3],
        key=lambda x: -(x["incoming"] + x["fnCount"])
    )[:25]
    graph_hotspots = [
        {
            "file":          h["id"],
            "layer":         h["layer"],
            "incoming_deps": h["incoming"],
            "outgoing_deps": len(h.get("deps", [])),
            "functions":     h["fnCount"],
            "risk":          "⚠ high-coupling" if h["incoming"] >= 8 else "frequently-imported",
            "note":          f"Changes here affect {h['incoming']} direct callers — write targeted tests",
        }
        for h in hotspots
    ]

    # ── Stale pattern cleanup ─────────────────────────────────────────────
    # Remove manual patterns whose files no longer exist in this repo
    existing_patterns = existing.get("patterns", {})
    clean_patterns: dict[str, Any] = {}
    stale_removed = 0
    for name, pat in existing_patterns.items():
        pat_files = pat.get("files", []) + pat.get("test_files", [])
        # Keep the pattern if ANY of its files exist in the current repo
        exists = any(
            any(p in f for f in all_paths)   # path substring match (handles dir prefixes)
            for p in pat_files if p
        )
        if exists:
            clean_patterns[name] = pat
        else:
            stale_removed += 1

    # ── Auto-discover test mappings ───────────────────────────────────────
    # Keep only auto-mappings whose files still live in this repo
    existing_tc   = existing.get("test_command_patterns", {})
    existing_maps = existing_tc.get("mappings", [])
    valid_manual   = [m for m in existing_maps
                      if not m.get("_auto") and m.get("source_pattern") in
                      {p.split("/")[-1] for p in all_paths}]
    known_patterns: set[str] = {m["source_pattern"] for m in valid_manual}

    source_by_name: dict[str, str] = {
        n["name"]: n["id"]
        for n in nodes if n["is_code"] and n["layer"] != "test"
    }
    new_mappings: list[dict[str, str]] = []
    for n in nodes:
        if not n["is_code"] or n["layer"] != "test":
            continue
        test_class = n["name"]
        for ext in (".java", ".py", ".ts", ".js", ".kt", ".scala", ".rb", ".go", ".cs"):
            test_class = test_class.replace(ext, "")
        source_stem = test_class
        if source_stem.endswith("Test"):
            source_stem = source_stem[:-4]
        elif source_stem.endswith("Spec"):
            source_stem = source_stem[:-4]
        elif source_stem.startswith("Test"):
            source_stem = source_stem[4:]
        for ext in (".java", ".py", ".ts", ".js", ".kt", ".scala", ".rb", ".go", ".cs"):
            candidate = source_stem + ext
            if candidate in source_by_name and candidate not in known_patterns:
                new_mappings.append({
                    "source_pattern": candidate,
                    "test_class":     test_class,
                    "source_path":    source_by_name[candidate],
                    "test_path":      n["id"],
                    "_auto":          True,
                })
                known_patterns.add(candidate)
                break

    # ── Build merged document ─────────────────────────────────────────────
    meta = dict(existing.get("_meta", {}))
    meta.update({
        "last_updated":       now[:10],
        "graph_last_updated": now,
        "build_system":       build_info["build_system"],
        "ttl_days":           meta.get("ttl_days", 7),
        "description": (
            "Pre-indexed file paths and structural graph data for this repo. "
            "Auto-generated by repo_graph.py — safe to commit, always current."
        ),
        "rule": (
            "Check patterns and graph_layers BEFORE running semantic_search. "
            "Paths are relative to repo root."
        ),
        "graph_rule": (
            "ALWAYS check graph_hotspots before editing any file — "
            "high-coupling files need targeted tests. "
            "Use graph_layers to locate files by architectural role. "
            "graph_summary gives quick health and language stats."
        ),
    })

    merged: dict[str, Any] = {
        "_meta":    meta,
        "patterns": clean_patterns,
        "test_command_patterns": {
            "description": "Map of source file patterns to test class names for targeted test runs",
            "rule":        "When a source file changes, use test_class in the targeted test command",
            "build_system":   build_info["build_system"],
            "mappings":       valid_manual + new_mappings,
            "full_test_command":              build_info["full_test_command"],
            "targeted_test_command_template": build_info["targeted_test_command_template"],
        },
        "graph_layers":   graph_layers,
        "graph_hotspots": graph_hotspots,
        "graph_summary": {
            "total_code_files":    stats["code_files"],
            "total_loc":           stats["total_loc"],
            "total_dependencies":  stats["total_edges"],
            "circular_deps":       stats["circular_deps"],
            "high_coupling_files": stats["high_coupling"],
            "large_files":         stats["large_files"],
            "top_languages":       stats["languages"][:5],
            "layer_distribution":  stats["layers"],
            "generated_at":        now,
            "health": (
                "🟢 healthy"         if stats["circular_deps"] == 0 and stats["high_coupling"] == 0
                else "🟡 ok"         if stats["circular_deps"] == 0
                else "🔴 needs attention"
            ),
        },
    }

    index_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    n_new = len(new_mappings)
    print(f"[repo_graph] 🧠 AI memory updated → {index_path}")
    print(f"[repo_graph]    Build system   : {build_info['build_system']}")
    print(f"[repo_graph]    Layers indexed : {len(graph_layers)}")
    print(f"[repo_graph]    Hotspots found : {len(graph_hotspots)}")
    print(f"[repo_graph]    Test mappings  : {len(valid_manual)} kept + {n_new} auto-discovered")
    if stale_removed:
        print(f"[repo_graph]    Stale patterns : {stale_removed} removed (not in this repo)")


def generate_html(data: dict[str, Any]) -> str:
    html = HTML_TEMPLATE
    html = html.replace("{{REPO_NAME}}", data["meta"]["repo"])
    html = html.replace("{{GENERATED}}", data["meta"]["generated"])
    html = html.replace("{{GRAPH_DATA}}", json.dumps(data, separators=(",", ":")))
    html = html.replace("{{LAYER_COLORS}}", json.dumps(LAYER_COLORS, separators=(",", ":")))

    # Detect the main Java application package prefix dynamically so the
    # mind-map "Application Source" group works in any project.
    java_main_pkg = _detect_java_main_pkg(data.get("nodes", []))
    html = html.replace("{{JAVA_MAIN_PKG}}", java_main_pkg)

    return html


def _detect_java_main_pkg(nodes: list[dict]) -> str:
    """Return the longest common src/main/java/ folder prefix that contains
    the most Java source files, e.g. 'src/main/java/com/example/myapp/'.
    Falls back to 'src/main/java/' if nothing is found."""
    prefix = "src/main/java/"
    candidates: dict[str, int] = {}
    for n in nodes:
        path = n.get("path", "")
        if path.startswith(prefix) and path.endswith(".java"):
            # collect up to 6 directory segments for a fine-grained prefix
            parts = path[len(prefix):].split("/")
            for depth in range(1, min(len(parts), 6)):
                key = prefix + "/".join(parts[:depth]) + "/"
                candidates[key] = candidates.get(key, 0) + 1
    if not candidates:
        return prefix
    # Pick the deepest prefix that still covers >= 50% of the best count
    best_count = max(candidates.values())
    threshold = max(1, best_count // 2)
    qualified = [(k, v) for k, v in candidates.items() if v >= threshold]
    # Sort by depth (longest path) descending so we get the most specific prefix
    qualified.sort(key=lambda x: x[0].count("/"), reverse=True)
    return qualified[0][0]


# ---------------------------------------------------------------------------
# Dependency-Aware Test Suggestion
# ---------------------------------------------------------------------------

def suggest_tests_for_changes(root: Path) -> None:
    """Find recently changed files and suggest which tests to run using the dependency graph.

    Uses:
      1. git diff to find changed files (uncommitted + last 3 commits)
      2. codebase-index.json for test mappings
      3. Full graph analysis for transitive dependents (files that IMPORT the changed file)
    """
    import subprocess

    memory_path = AUTOMATION_DIR / ".memory" / "codebase-index.json"

    # ── 1. Get changed files from git ──────────────────────────────────────
    changed: set[str] = set()
    try:
        # Uncommitted changes (staged + unstaged)
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, cwd=root
        )
        if result.returncode == 0:
            changed.update(l.strip() for l in result.stdout.splitlines() if l.strip())

        # Staged files
        result = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True, text=True, cwd=root
        )
        if result.returncode == 0:
            changed.update(l.strip() for l in result.stdout.splitlines() if l.strip())

        # Last 3 commits
        result = subprocess.run(
            ["git", "log", "--name-only", "--format=", "-3"],
            capture_output=True, text=True, cwd=root
        )
        if result.returncode == 0:
            changed.update(l.strip() for l in result.stdout.splitlines() if l.strip())
    except Exception:
        pass

    if not changed:
        print("[suggest-tests] No recent changes detected.")
        return

    # ── 2. Load memory index for quick test mapping ────────────────────────
    test_map: dict[str, str] = {}  # source_pattern → test_class
    build_info = detect_build_system(root)
    if memory_path.exists():
        try:
            mem = json.loads(memory_path.read_text(encoding="utf-8"))
            for m in mem.get("test_command_patterns", {}).get("mappings", []):
                test_map[m["source_pattern"]] = m["test_class"]
        except Exception:
            pass

    # ── 3. Match changed files to tests ────────────────────────────────────
    direct_tests: set[str] = set()
    impacted_files: set[str] = set()

    for path in changed:
        filename = path.split("/")[-1]
        impacted_files.add(path)

        # Direct mapping from index
        if filename in test_map:
            direct_tests.add(test_map[filename])

        # Also check if the changed file IS a test
        if "test" in filename.lower() or "spec" in filename.lower():
            # Strip extension for class name
            cls = filename
            for ext in (".java", ".py", ".ts", ".js", ".kt", ".scala", ".rb", ".go", ".cs"):
                cls = cls.replace(ext, "")
            direct_tests.add(cls)

    # ── 4. Run quick analysis for transitive impact ────────────────────────
    # Only do this if there are non-test source changes
    source_changes = [p for p in changed if "test" not in p.lower()]
    if source_changes:
        # Quick scan to find dependents (files that import the changed files)
        print(f"[suggest-tests] 🔍 Scanning dependents for {len(source_changes)} changed source files...")
        data = analyze_repo(root)
        # Build reverse dependency map: target → list of files that depend on it
        reverse_deps: dict[str, list[str]] = defaultdict(list)
        for conn in data["connections"]:
            reverse_deps[conn["target"]].append(conn["source"])

        # Find all files that depend on changed files (1 hop)
        for path in source_changes:
            for dep_file in reverse_deps.get(path, []):
                impacted_files.add(dep_file)
                # Check if any impacted file has a test mapping
                dep_name = dep_file.split("/")[-1]
                if dep_name in test_map:
                    direct_tests.add(test_map[dep_name])

    # ── 5. Output ──────────────────────────────────────────────────────────
    print(f"\n[suggest-tests] 📋 Changed files ({len(changed)}):")
    for f in sorted(changed)[:15]:
        print(f"  • {f}")
    if len(changed) > 15:
        print(f"  ... +{len(changed) - 15} more")

    print(f"\n[suggest-tests] 💥 Impact radius: {len(impacted_files)} files affected")

    if direct_tests:
        test_list = ",".join(sorted(direct_tests))
        template = build_info["targeted_test_command_template"]
        cmd = template.replace("{TEST_CLASSES}", test_list)
        print(f"\n[suggest-tests] 🧪 Suggested tests ({len(direct_tests)}):")
        for t in sorted(direct_tests):
            print(f"  ✓ {t}")
        print(f"\n[suggest-tests] 📌 Run command:")
        print(f"  {cmd}")
    else:
        print(f"\n[suggest-tests] ⚠️  No test mappings found for changed files.")
        print(f"  Run full scan to rebuild: python3 Automation/scripts/repo_graph.py")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an interactive knowledge graph for any local repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze the repo this script lives in (auto-detected):
  python3 Automation/scripts/repo_graph.py

  # FAST incremental mode (only memory update, no HTML — for git hooks):
  python3 Automation/scripts/repo_graph.py --quick

  # Show which tests to run for recently changed files:
  python3 Automation/scripts/repo_graph.py --suggest-tests

  # Analyze a specific repo path:
  python3 Automation/scripts/repo_graph.py --root /path/to/some/repo

  # Custom output path:
  python3 Automation/scripts/repo_graph.py --output /tmp/graph.html
        """,
    )
    parser.add_argument("--root",   type=str, default=str(REPO_ROOT), help="Repo root to analyze (default: auto)")
    parser.add_argument("--output", type=str, default=str(OUTPUT_PATH), help="Output HTML path")
    parser.add_argument("--json",   action="store_true", help="Also dump raw JSON alongside HTML")
    parser.add_argument("--quick",  action="store_true", help="Fast mode: update memory only, skip HTML generation")
    parser.add_argument("--suggest-tests", action="store_true", dest="suggest_tests",
                        help="Show which tests to run for recently changed files")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not root.is_dir():
        print(f"[ERROR] Root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    # ── suggest-tests mode (fast — no full scan needed) ───────────────────
    if args.suggest_tests:
        suggest_tests_for_changes(root)
        sys.exit(0)

    data = analyze_repo(root)

    # ── Write AI agent memory (always, before HTML) ────────────────────
    write_memory_index(data)

    # ── Quick mode: skip HTML generation ──────────────────────────────────
    if args.quick:
        s = data["stats"]
        print(f"[repo_graph] ⚡ Quick mode: memory updated ({s['code_files']} files, {s['total_edges']} deps)")
        sys.exit(0)

    html = generate_html(data)
    output.write_text(html, encoding="utf-8")
    print(f"[repo_graph] ✅ Graph written to: {output}")
    print(f"[repo_graph]    Open in browser: file://{output}")

    if args.json:
        json_out = output.with_suffix(".json")
        json_out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[repo_graph]    JSON data:       {json_out}")

    # Summary
    s = data["stats"]
    print(f"\n[repo_graph] 📊 Summary:")
    print(f"  Code files   : {s['code_files']}")
    print(f"  Total LOC    : {s['total_loc']:,}")
    print(f"  Dependencies : {s['total_edges']}")
    print(f"  Circular deps: {s['circular_deps']}")
    print(f"  Issues found : {len(data['issues'])}")

    top_langs = s["languages"][:3]
    if top_langs:
        lang_str = ", ".join(f"{l['ext']}({l['pct']}%)" for l in top_langs)
        print(f"  Top languages: {lang_str}")


if __name__ == "__main__":
    main()
