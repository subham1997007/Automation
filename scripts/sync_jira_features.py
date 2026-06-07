#!/usr/bin/env python3
"""Jira Feature Sync — fetch features + stories for the analytics tree.

Pulls assigned stories and their parent Features/Epics from Jira
and caches them in Automation/.memory/feature-cache/ so the analytics
tree always shows current PI data — even before any DevFlow run.

Usage:
    python3 Automation/scripts/sync_jira_features.py
    python3 Automation/scripts/sync_jira_features.py --assignee "john.doe@company.com"

Auto-called by generate_analytics.py when cache is stale (>1 hour).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Paths + env
# ---------------------------------------------------------------------------

AUTO_DIR   = Path(__file__).resolve().parents[1]
CACHE_DIR  = AUTO_DIR / ".memory" / "feature-cache"
CACHE_FILE = CACHE_DIR / "pi-snapshot.json"
CACHE_TTL  = 3600   # 1 hour


def _load_env() -> None:
    env_path = AUTO_DIR / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

_load_env()


# ---------------------------------------------------------------------------
# Load Jira client
# ---------------------------------------------------------------------------

def _jira_client():
    path = AUTO_DIR / "mcp-servers" / "jira-mcp" / "src" / "jira_client.py"
    spec = importlib.util.spec_from_file_location("jira_client", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load jira_client from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_valid() -> bool:
    if not CACHE_FILE.exists():
        return False
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        fetched_at = data.get("fetched_at_epoch", 0)
        return (time.time() - fetched_at) < CACHE_TTL
    except Exception:
        return False


def _load_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data["fetched_at_epoch"] = int(time.time())
    data["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    CACHE_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Jira fetch
# ---------------------------------------------------------------------------

def _assignee_email() -> str:
    return os.getenv("JIRA_USERNAME") or os.getenv("JIRA_EMAIL") or ""


def _jira_url_for(key: str) -> str:
    base = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    return f"{base}/browse/{key}" if base else f"#browse-{key}"


def _build_jql(assignee: str | None) -> str:
    """Build JQL to fetch all active stories assigned to the user."""
    who = assignee or _assignee_email()
    parts = [
        'issuetype in (Story, Task, Sub-task)',
        f'assignee = "{who}"' if who else 'assignee is not EMPTY',
        'statusCategory != Done',
        'ORDER BY updated DESC',
    ]
    return " AND ".join(parts[:-1]) + f" {parts[-1]}"


def _build_jql_recent_done(assignee: str | None, days: int = 30) -> str:
    """Also fetch recently done stories so tree shows completed work."""
    who = assignee or _assignee_email()
    parts = [
        'issuetype in (Story, Task)',
        f'assignee = "{who}"' if who else 'assignee is not EMPTY',
        'statusCategory = Done',
        f'updated >= -{days}d',
        'ORDER BY updated DESC',
    ]
    return " AND ".join(parts[:-1]) + f" {parts[-1]}"


def fetch(assignee: str | None = None, force: bool = False) -> dict[str, Any]:
    """Fetch or return cached PI snapshot.

    Returns:
        {
          "features": {
            "BDATART-686": {
              "key": "BDATART-686",
              "name": "...",
              "status": "...",
              "stories": [
                {"key": "BDRSP-1413", "summary": "...", "status": "...",
                 "assignee": "...", "priority": "...", "updated": "..."}
              ]
            }
          },
          "unparented": [...],   ← stories with no feature/epic parent
          "fetched_at": "...",
          "total_stories": N
        }
    """
    if not force and _cache_valid():
        return _load_cache()

    jira = _jira_client()

    features: dict[str, Any] = {}
    unparented: list[dict[str, Any]] = []
    all_stories: list[dict[str, Any]] = []
    fetch_errors: list[str] = []

    # ── Fetch active stories ────────────────────────────────────────────────
    try:
        active = jira.search_issues(_build_jql(assignee), max_results=100)
        all_stories.extend(active)
    except Exception as exc:
        print(f"  ⚠️  Active stories fetch failed: {exc}", file=sys.stderr)
        fetch_errors.append(str(exc))

    # ── Fetch recently done stories ─────────────────────────────────────────
    try:
        done = jira.search_issues(_build_jql_recent_done(assignee), max_results=50)
        # Avoid duplicates
        done_keys = {s.get("key") for s in all_stories}
        all_stories.extend(s for s in done if s.get("key") not in done_keys)
    except Exception as exc:
        print(f"  ⚠️  Done stories fetch failed: {exc}", file=sys.stderr)
        fetch_errors.append(str(exc))

    if not all_stories and fetch_errors:
        cached = _load_cache()
        if cached.get("total_stories"):
            print("  ⚠️  Jira unavailable; keeping previous non-empty feature cache.", file=sys.stderr)
            return cached
        return {
            "features": {},
            "unparented": [],
            "total_stories": 0,
            "assignee": assignee or _assignee_email(),
            "fetch_errors": fetch_errors,
        }

    # Jira search is intentionally lightweight. Read each story fully so the
    # analytics tree can group by parent Feature/Epic and display subtasks.
    enriched_stories: list[dict[str, Any]] = []
    for story in all_stories:
        key = story.get("key")
        if not key:
            continue
        try:
            full_story = jira.read_issue(key)
            enriched_stories.append({**story, **full_story})
        except Exception as exc:
            print(f"  ⚠️  Full story fetch failed for {key}: {exc}", file=sys.stderr)
            enriched_stories.append(story)
    all_stories = enriched_stories

    # ── Group by Feature/Epic ───────────────────────────────────────────────
    for story in all_stories:
        key      = story.get("key") or ""
        summary  = story.get("summary") or "—"
        status   = story.get("status") or "To Do"
        assignee_name = story.get("assignee") or "—"
        if not assignee_name or assignee_name.lower() in ("unassigned","none","null"):
            assignee_name = "—"
        priority = story.get("priority") or "Medium"
        updated  = story.get("updated") or ""
        issue_type = story.get("issue_type") or "Story"
        subtasks = story.get("subtasks") or []
        story_url = _jira_url_for(key)

        story_item = {
            "key": key,
            "summary": summary,
            "status": status,
            "status_category": story.get("status_category") or "",
            "assignee": assignee_name,
            "priority": priority,
            "updated": updated,
            "issue_type": issue_type,
            "subtasks": subtasks,
            "url": story_url,
        }

        # Get parent feature/epic
        parent_raw = story.get("parent") or {}
        if isinstance(parent_raw, dict):
            parent_key = parent_raw.get("key") or ""
            parent_summary = parent_raw.get("summary") or parent_raw.get("name") or ""
            parent_type = (parent_raw.get("issue_type") or "").lower()
            parent_status = parent_raw.get("status") or ""
        else:
            parent_key = str(parent_raw or "")
            parent_summary = story.get("parent_summary") or ""
            parent_type = (story.get("parent_issue_type") or "").lower()
            parent_status = story.get("parent_status") or ""

        if parent_key and parent_type in ("feature", "epic", "initiative"):
            if parent_key not in features:
                # Try to get full feature details
                features[parent_key] = {
                    "key": parent_key,
                    "name": parent_summary,
                    "status": parent_status,
                    "url": _jira_url_for(parent_key),
                    "is_real_feature": True,
                    "stories": [],
                }
            features[parent_key]["stories"].append(story_item)
        elif parent_key:
            # Parent is a Story/Task, not a Feature/Epic.
            # Attach this item as a sub-task of its parent story rather than
            # creating a misleading pseudo-feature group.
            # We defer attachment; for now just add to unparented so it is
            # visible, and record the parent key so generate_analytics can
            # attach it to the parent story's subtask list.
            story_item_with_parent = {**story_item, "_parent_key": parent_key}
            unparented.append(story_item_with_parent)
        else:
            unparented.append(story_item)

    # ── Sort stories within each feature ────────────────────────────────────
    STATUS_ORDER = {"In Progress": 0, "In Analysis": 1, "Ready": 2, "To Do": 3, "Done": 9}
    for feat in features.values():
        feat["stories"].sort(
            key=lambda s: (STATUS_ORDER.get(s["status"], 5), s.get("updated") or ""),
            reverse=False
        )
        feat["total"] = len(feat["stories"])
        feat["done_count"]    = sum(1 for s in feat["stories"] if s.get("status_category","").lower() == "done")
        feat["active_count"]  = sum(1 for s in feat["stories"] if s.get("status_category","").lower() not in ("done",""))

    result = {
        "features": features,
        "unparented": unparented,
        "total_stories": len(all_stories),
        "assignee": assignee or _assignee_email(),
    }
    _save_cache(result)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sync Jira features to .memory/feature-cache/")
    parser.add_argument("--assignee", default=None, help="Jira username/email (default: JIRA_USERNAME from .env.local)")
    parser.add_argument("--force", action="store_true", help="Force refresh even if cache is fresh")
    args = parser.parse_args()

    print("🔄 Syncing Jira features...")
    data = fetch(assignee=args.assignee, force=args.force)
    feat_count = len(data.get("features", {}))
    story_count = data.get("total_stories", 0)
    print(f"✅ Synced {story_count} stories across {feat_count} features")
    print(f"   Assignee: {data.get('assignee')}")
    print(f"   Cache:    {CACHE_FILE}")
    for fk, fg in list(data.get("features", {}).items())[:5]:
        print(f"   {fk}: {fg['name'][:60]} — {fg['total']} stories")
