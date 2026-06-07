#!/usr/bin/env python3
"""Incremental memory refresh for the Automation DevFlow system.

Called by Automation/bootstrap.sh on every profile registration:
    python3 Automation/scripts/refresh_memory.py --python-bin <path>

What it does (all incremental — skips anything still fresh):
  1. Confluence cache  — re-fetches pages whose TTL (24 h) has expired
  2. Codebase index    — triggers repo_graph.py rebuild if index is stale (>7 days)
  3. Jira cache        — evicts entries that exceed their TTL
  4. Story memory      — writes a summary of all devflow story memories
  5. Knowledge index   — writes .memory/confluence-knowledge-index.json
                         (referenced by bootstrap.sh log line)

Exit codes:
  0  — success (full or partial)
  1  — fatal error (written to stderr)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
AUTOMATION_DIR = SCRIPT_DIR.parent
MEMORY_DIR = AUTOMATION_DIR / ".memory"
CONFLUENCE_CACHE_DIR = MEMORY_DIR / "confluence-cache"
JIRA_CACHE_DIR = MEMORY_DIR / "jira-cache"
DEVFLOW_STORIES_DIR = MEMORY_DIR / "devflow" / "stories"
CODEBASE_INDEX_PATH = MEMORY_DIR / "codebase-index.json"
KNOWLEDGE_INDEX_PATH = MEMORY_DIR / "confluence-knowledge-index.json"
ENV_PATH = AUTOMATION_DIR / ".env.local"
REPO_GRAPH_SCRIPT = SCRIPT_DIR / "repo_graph.py"

CONFLUENCE_CACHE_TTL_HOURS = 24
CODEBASE_INDEX_TTL_DAYS = 7
JIRA_CACHE_TTL_HOURS = 1   # Jira data is volatile — keep fresh


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    print(f"[refresh_memory] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[refresh_memory] ⚠️  {msg}", flush=True)


# ── Env helpers ───────────────────────────────────────────────────────────────

def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    try:
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    except OSError:
        pass
    return values


def _now_epoch() -> float:
    return time.time()


def _iso_to_epoch(iso: str) -> float:
    """Parse ISO-8601 string (with or without Z) → epoch float. Returns 0 on error."""
    try:
        iso = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0


def _epoch_to_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 1. Confluence cache refresh ───────────────────────────────────────────────

def _confluence_auth_header(env: dict[str, str]) -> str | None:
    import base64
    user = env.get("CONFLUENCE_USERNAME") or env.get("JIRA_USERNAME") or ""
    token = env.get("CONFLUENCE_API_TOKEN") or env.get("JIRA_API_TOKEN") or ""
    if not user or not token:
        return None
    creds = base64.b64encode(f"{user}:{token}".encode()).decode()
    return f"Basic {creds}"


def _fetch_confluence_page(url: str, auth: str) -> str | None:
    """Fetch a Confluence page via REST API and return cleaned plain-text content."""
    try:
        # Extract page ID from URL: .../pages/<id>...
        import re
        match = re.search(r"/pages/(\d+)", url)
        if not match:
            return None
        page_id = match.group(1)

        base_url_match = re.match(r"(https://[^/]+)", url)
        if not base_url_match:
            return None
        base = base_url_match.group(1)

        api_url = f"{base}/wiki/rest/api/content/{page_id}?expand=body.storage,title"
        req = urllib.request.Request(api_url, headers={"Authorization": auth, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        title = data.get("title", "")
        body_html = (data.get("body") or {}).get("storage", {}).get("value", "")
        # Strip HTML tags to plain text
        clean = re.sub(r"<[^>]+>", " ", body_html)
        clean = re.sub(r"\s+", " ", clean).strip()
        return f"{title}\n\n{clean}"
    except Exception as exc:
        warn(f"Could not fetch Confluence page {url}: {exc}")
        return None


def refresh_confluence_cache(env: dict[str, str]) -> dict[str, Any]:
    """Re-fetch Confluence pages whose TTL has expired. Returns a stats dict."""
    stats: dict[str, Any] = {"checked": 0, "refreshed": 0, "skipped": 0, "errors": 0}
    index_path = CONFLUENCE_CACHE_DIR / "index.json"
    if not index_path.exists():
        log("Confluence cache index not found — skipping Confluence refresh.")
        return stats

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warn(f"Could not read confluence-cache/index.json: {exc}")
        return stats

    pages: list[dict[str, Any]] = index.get("cached_pages") or []
    auth = _confluence_auth_header(env)
    now = _now_epoch()
    ttl_seconds = CONFLUENCE_CACHE_TTL_HOURS * 3600
    changed = False

    for page in pages:
        stats["checked"] += 1
        url = str(page.get("url") or "")
        cache_file = page.get("cache_file") or ""
        cached_at = str(page.get("cached_at") or "")
        cache_path = CONFLUENCE_CACHE_DIR / cache_file if cache_file else None

        if not url or not cache_path:
            stats["skipped"] += 1
            continue

        # Check TTL
        cached_epoch = _iso_to_epoch(cached_at) if cached_at else 0.0
        age_seconds = now - cached_epoch
        if age_seconds < ttl_seconds and cache_path.exists():
            stats["skipped"] += 1
            continue

        if not auth:
            stats["skipped"] += 1
            continue

        # Stale — re-fetch
        log(f"Refreshing Confluence page: {page.get('title') or url}")
        content = _fetch_confluence_page(url, auth)
        if content is None:
            stats["errors"] += 1
            continue

        # Write updated cache file
        now_iso = _epoch_to_iso(now)
        expires_iso = _epoch_to_iso(now + ttl_seconds)
        payload: dict[str, Any] = {}
        if cache_path and cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        payload["_meta"] = {
            "url": url,
            "title": page.get("title") or "",
            "cached_at": now_iso,
            "ttl_hours": CONFLUENCE_CACHE_TTL_HOURS,
            "expires_at": expires_iso,
        }
        payload["content"] = content
        if cache_path:
            cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        page["cached_at"] = now_iso
        changed = True
        stats["refreshed"] += 1

    if changed:
        index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    log(f"Confluence cache: {stats['checked']} checked, {stats['refreshed']} refreshed, "
        f"{stats['skipped']} skipped (fresh), {stats['errors']} errors.")
    return stats


# ── 2. Codebase index freshness ───────────────────────────────────────────────

def refresh_codebase_index(python_bin: str) -> dict[str, Any]:
    """Rebuild codebase-index.json via repo_graph.py if it's stale or missing."""
    stats: dict[str, Any] = {"action": "skipped", "reason": ""}

    if not CODEBASE_INDEX_PATH.exists():
        stats["action"] = "rebuild"
        stats["reason"] = "codebase-index.json missing"
    else:
        try:
            meta = json.loads(CODEBASE_INDEX_PATH.read_text(encoding="utf-8")).get("_meta", {})
            last_updated = meta.get("graph_last_updated") or meta.get("last_updated") or ""
            last_epoch = _iso_to_epoch(last_updated) if last_updated else 0.0
            age_days = (time.time() - last_epoch) / 86400
            if age_days > CODEBASE_INDEX_TTL_DAYS:
                stats["action"] = "rebuild"
                stats["reason"] = f"codebase-index.json is {age_days:.1f} days old (TTL={CODEBASE_INDEX_TTL_DAYS}d)"
            else:
                stats["reason"] = f"fresh ({age_days:.1f}d old, TTL={CODEBASE_INDEX_TTL_DAYS}d)"
        except Exception as exc:
            stats["action"] = "rebuild"
            stats["reason"] = f"could not read index: {exc}"

    if stats["action"] != "rebuild":
        log(f"Codebase index: already fresh — skipping rebuild. ({stats['reason']})")
        return stats

    if not REPO_GRAPH_SCRIPT.exists():
        warn(f"repo_graph.py not found at {REPO_GRAPH_SCRIPT} — skipping codebase index rebuild.")
        stats["action"] = "skipped"
        stats["reason"] = "repo_graph.py not found"
        return stats

    log(f"Rebuilding codebase index: {stats['reason']}")
    try:
        result = subprocess.run(
            [python_bin, str(REPO_GRAPH_SCRIPT)],
            cwd=str(AUTOMATION_DIR.parent),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            stats["action"] = "rebuilt"
            log("Codebase index rebuilt successfully.")
        else:
            warn(f"repo_graph.py exited {result.returncode}: {result.stderr[:300]}")
            stats["action"] = "failed"
    except subprocess.TimeoutExpired:
        warn("repo_graph.py timed out — codebase index not rebuilt.")
        stats["action"] = "timeout"
    except Exception as exc:
        warn(f"repo_graph.py failed: {exc}")
        stats["action"] = "error"

    return stats


# ── 3. Jira cache eviction ─────────────────────────────────────────────────────

def evict_jira_cache() -> dict[str, Any]:
    """Remove Jira cache entries older than TTL."""
    stats: dict[str, Any] = {"checked": 0, "evicted": 0, "kept": 0}
    if not JIRA_CACHE_DIR.exists():
        return stats

    ttl_seconds = JIRA_CACHE_TTL_HOURS * 3600
    now = _now_epoch()

    for cache_file in JIRA_CACHE_DIR.glob("*.json"):
        stats["checked"] += 1
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_at = data.get("cached_at") or data.get("updated_at") or ""
            cached_epoch = _iso_to_epoch(cached_at) if cached_at else 0.0
            if cached_epoch == 0.0:
                # Try updated_at_epoch (numeric)
                cached_epoch = float(data.get("updated_at_epoch") or 0)
            age = now - cached_epoch
            if age > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                stats["evicted"] += 1
            else:
                stats["kept"] += 1
        except Exception:
            stats["kept"] += 1  # Don't delete files we can't read

    if stats["evicted"]:
        log(f"Jira cache: evicted {stats['evicted']} stale entries, kept {stats['kept']}.")
    else:
        log(f"Jira cache: all {stats['kept']} entries are fresh.")
    return stats


# ── 4. Story memory summary ───────────────────────────────────────────────────

def refresh_story_summary() -> dict[str, Any]:
    """Scan devflow story memories and write a compact summary."""
    stats: dict[str, Any] = {"stories": 0, "stages": {}}
    if not DEVFLOW_STORIES_DIR.exists():
        return stats

    stories: list[dict[str, Any]] = []
    for story_file in sorted(DEVFLOW_STORIES_DIR.glob("*.json")):
        try:
            data = json.loads(story_file.read_text(encoding="utf-8"))
            stage = str(data.get("stage") or "unknown")
            stats["stages"][stage] = stats["stages"].get(stage, 0) + 1
            stories.append({
                "jira_id": data.get("jira_id") or story_file.stem,
                "stage": stage,
                "next_gate": data.get("next_gate") or "",
                "scan_method": data.get("scan_method") or "",
                "updated_at_epoch": data.get("updated_at_epoch") or 0,
            })
            stats["stories"] += 1
        except Exception:
            pass

    # Sort by most recently updated
    stories.sort(key=lambda s: s["updated_at_epoch"], reverse=True)

    summary_path = MEMORY_DIR / "devflow" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "_meta": {
                    "description": "Auto-generated summary of all DevFlow story memories.",
                    "generated_at": _epoch_to_iso(_now_epoch()),
                    "story_count": stats["stories"],
                    "stages": stats["stages"],
                },
                "stories": stories[:50],  # cap at 50 for readability
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    log(f"Story memory: {stats['stories']} stories summarised → .memory/devflow/summary.json")
    return stats


# ── 5. Confluence knowledge index ─────────────────────────────────────────────

def build_knowledge_index() -> dict[str, Any]:
    """Write .memory/confluence-knowledge-index.json — compact cross-reference.

    This is the file bootstrap.sh logs when present:
        'Confluence knowledge ready: Automation/.memory/confluence-knowledge-index.json'
    """
    stats: dict[str, Any] = {"pages": 0}
    index_path = CONFLUENCE_CACHE_DIR / "index.json"
    if not index_path.exists():
        return stats

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return stats

    entries: list[dict[str, Any]] = []
    for page in (index.get("cached_pages") or []):
        cache_file = page.get("cache_file") or ""
        cache_path = CONFLUENCE_CACHE_DIR / cache_file if cache_file else None
        excerpt = ""
        keywords: list[str] = []
        if cache_path and cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                # Prefer the summary block if it exists (ADR files have one)
                summary = payload.get("summary") or {}
                if summary:
                    excerpt = " | ".join(f"{k}: {v}" for k, v in list(summary.items())[:5])
                    keywords = list(summary.keys())
                else:
                    raw_content = str(payload.get("content") or "")
                    excerpt = " ".join(raw_content.split())[:300]
                    keywords = []
            except Exception:
                pass
        entries.append({
            "url": page.get("url") or "",
            "title": page.get("title") or "",
            "cached_at": page.get("cached_at") or "",
            "excerpt": excerpt,
            "keywords": keywords,
            "cache_file": cache_file,
        })
        stats["pages"] += 1

    KNOWLEDGE_INDEX_PATH.write_text(
        json.dumps(
            {
                "_meta": {
                    "description": (
                        "Confluence page knowledge index for DevFlow. "
                        "Check this before fetching any Confluence URL."
                    ),
                    "generated_at": _epoch_to_iso(_now_epoch()),
                    "page_count": stats["pages"],
                    "ttl_hours": CONFLUENCE_CACHE_TTL_HOURS,
                    "rule": (
                        "Use entries[].excerpt and entries[].keywords for fast lookup. "
                        "Read cache_file from confluence-cache/ for full content."
                    ),
                },
                "entries": entries,
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    log(f"Knowledge index: {stats['pages']} Confluence pages → .memory/confluence-knowledge-index.json")
    return stats


# ── 6. Turbovec index rebuild (when memory changed) ───────────────────────────

def rebuild_turbovec_if_stale(python_bin: str, memory_changed: bool) -> dict[str, Any]:
    """Rebuild turbovec code-index when Confluence or codebase memory changed."""
    stats: dict[str, Any] = {"action": "skipped", "reason": ""}

    build_script = SCRIPT_DIR / "build_turbovec_index.py"
    if not build_script.exists():
        stats["reason"] = "build_turbovec_index.py not found"
        return stats

    index_path = MEMORY_DIR / "code-index.tvim"
    meta_path  = MEMORY_DIR / "code-index-meta.json"

    # Always rebuild if memory changed; otherwise rebuild only if index is missing
    if not memory_changed and index_path.exists() and meta_path.exists():
        try:
            age_hours = (time.time() - index_path.stat().st_mtime) / 3600
            if age_hours < 24:
                stats["reason"] = f"index is fresh ({age_hours:.1f}h old)"
                log(f"Turbovec index: already fresh — skipping rebuild. ({stats['reason']})")
                return stats
        except Exception:
            pass

    reason = "memory changed" if memory_changed else "index missing or stale"
    log(f"Rebuilding turbovec index ({reason})…")
    try:
        result = subprocess.run(
            [python_bin, str(build_script)],
            cwd=str(AUTOMATION_DIR.parent),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            stats["action"] = "rebuilt"
            log("Turbovec index rebuilt successfully.")
        else:
            warn(f"build_turbovec_index.py exited {result.returncode}: {result.stderr[:300]}")
            stats["action"] = "failed"
    except subprocess.TimeoutExpired:
        warn("build_turbovec_index.py timed out.")
        stats["action"] = "timeout"
    except Exception as exc:
        warn(f"build_turbovec_index.py failed: {exc}")
        stats["action"] = "error"

    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Incremental memory refresh for the Automation DevFlow system."
    )
    parser.add_argument("--python-bin", default=sys.executable,
                        help="Python interpreter to use for sub-scripts (default: current interpreter)")
    parser.add_argument("--skip-confluence", action="store_true",
                        help="Skip Confluence cache refresh (useful when credentials unavailable)")
    parser.add_argument("--skip-codebase", action="store_true",
                        help="Skip codebase index rebuild check")
    parser.add_argument("--force-codebase", action="store_true",
                        help="Force codebase index rebuild even if fresh")
    parser.add_argument("--skip-turbovec", action="store_true",
                        help="Skip turbovec index rebuild (faster startup, index may be stale)")
    args = parser.parse_args(argv)

    python_bin = args.python_bin or sys.executable
    env = load_env()

    # Ensure memory dirs exist
    for d in (MEMORY_DIR, CONFLUENCE_CACHE_DIR, JIRA_CACHE_DIR, DEVFLOW_STORIES_DIR):
        d.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {}

    # ── Step 1: Confluence cache ─────────────────────────────────────────────
    if args.skip_confluence:
        log("Confluence cache refresh skipped (--skip-confluence).")
        results["confluence"] = {"skipped": True}
    else:
        results["confluence"] = refresh_confluence_cache(env)

    # ── Step 2: Codebase index ───────────────────────────────────────────────
    if args.skip_codebase:
        log("Codebase index check skipped (--skip-codebase).")
        results["codebase_index"] = {"skipped": True}
    else:
        if args.force_codebase and CODEBASE_INDEX_PATH.exists():
            # Temporarily invalidate TTL by touching _meta
            try:
                idx = json.loads(CODEBASE_INDEX_PATH.read_text(encoding="utf-8"))
                idx.setdefault("_meta", {})["graph_last_updated"] = "2000-01-01T00:00:00"
                CODEBASE_INDEX_PATH.write_text(json.dumps(idx, indent=2) + "\n", encoding="utf-8")
            except Exception:
                pass
        results["codebase_index"] = refresh_codebase_index(python_bin)

    # ── Step 3: Jira cache eviction ──────────────────────────────────────────
    results["jira_cache"] = evict_jira_cache()

    # ── Step 4: Story memory summary ─────────────────────────────────────────
    results["story_summary"] = refresh_story_summary()

    # ── Step 5: Knowledge index ───────────────────────────────────────────────
    results["knowledge_index"] = build_knowledge_index()

    # ── Step 6: Turbovec index rebuild ────────────────────────────────────────
    # Rebuild if Confluence was refreshed OR codebase index was rebuilt
    confluence_changed = results.get("confluence", {}).get("refreshed", 0) > 0
    codebase_changed   = results.get("codebase_index", {}).get("action") in ("rebuilt",)
    memory_changed     = confluence_changed or codebase_changed

    if args.skip_turbovec:
        log("Turbovec index rebuild skipped (--skip-turbovec).")
        results["turbovec"] = {"skipped": True}
    else:
        results["turbovec"] = rebuild_turbovec_if_stale(python_bin, memory_changed)

    # ── Final report ─────────────────────────────────────────────────────────
    report_path = MEMORY_DIR / "refresh-memory-last-run.json"
    report_path.write_text(
        json.dumps(
            {
                "run_at": _epoch_to_iso(_now_epoch()),
                "python_bin": python_bin,
                "results": results,
            },
            indent=2,
            default=str,
        ) + "\n",
        encoding="utf-8",
    )
    log("✅ Memory refresh complete. Report → .memory/refresh-memory-last-run.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

