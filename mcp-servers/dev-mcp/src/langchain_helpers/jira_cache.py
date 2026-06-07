"""TTL-based Jira story response cache.

Caches the full story_payload() result under .memory/jira-cache/
for a configurable TTL (default 15 minutes). This eliminates repeated
Jira API round-trips within the same dev session.

TTL covers a full DevFlow session: start → apply_story_update → after_code_changes → create_mr.
Override with AUTOMATION_JIRA_CACHE_TTL_SECONDS env var.

Usage:
    from langchain_helpers.jira_cache import JiraCache

    cache = JiraCache()

    # Try cache first
    cached = cache.get("BDRSP-1705")
    if cached:
        return cached

    # Fetch fresh and store
    fresh = story_payload("BDRSP-1705")
    cache.set("BDRSP-1705", fresh)
    return fresh
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 15 minute TTL — covers a full DevFlow session (start → apply → code → MR)
# Short enough to pick up manual Jira edits between sessions.
# Override with AUTOMATION_JIRA_CACHE_TTL_SECONDS env var.
DEFAULT_TTL_SECONDS = int(os.getenv("AUTOMATION_JIRA_CACHE_TTL_SECONDS", str(15 * 60)))


class JiraCache:
    """Simple file-backed TTL cache for Jira story API responses.

    Stored under Automation/.memory/jira-cache/<STORY-KEY>.json.
    Each entry includes the full story_payload() response plus metadata.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        if cache_dir is None:
            try:
                from langchain_helpers.repo_resolver import get_resolver
                cache_dir = get_resolver().memory_path("jira-cache")
            except Exception:
                cache_dir = Path(__file__).resolve().parents[4] / ".memory" / "jira-cache"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, jira_id: str) -> dict[str, Any] | None:
        """Return cached story payload if still within TTL, else None."""
        path = self._path(jira_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - data.get("_cached_at_epoch", 0)
            if age <= self.ttl_seconds:
                log.debug("jira_cache: HIT for %s (age=%.0fs)", jira_id, age)
                return data.get("payload")
            log.debug("jira_cache: EXPIRED for %s (age=%.0fs > ttl=%.0fs)", jira_id, age, self.ttl_seconds)
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        return None

    def set(self, jira_id: str, payload: dict[str, Any]) -> None:
        """Write *payload* to the cache with the current timestamp."""
        path = self._path(jira_id)
        entry = {
            "_jira_id": jira_id.upper(),
            "_cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "_cached_at_epoch": int(time.time()),
            "_ttl_seconds": self.ttl_seconds,
            "payload": payload,
        }
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
        log.debug("jira_cache: SET for %s", jira_id)

    def invalidate(self, jira_id: str) -> None:
        """Remove the cache entry for *jira_id* (forces fresh fetch next time)."""
        path = self._path(jira_id)
        if path.exists():
            path.unlink()
            log.info("jira_cache: invalidated %s", jira_id)

    def is_cached(self, jira_id: str) -> bool:
        """Return True if a valid (non-expired) cache entry exists."""
        return self.get(jira_id) is not None

    # ── Internal ───────────────────────────────────────────────────────────────

    def _path(self, jira_id: str) -> Path:
        safe = "".join(ch for ch in jira_id.upper() if ch.isalnum() or ch == "-")
        return self.cache_dir / f"{safe}.json"

