"""Cache-aware Confluence / external documentation loader.

Usage:
    from langchain_helpers.confluence_loader import ConfluenceCacheLoader

    loader = ConfluenceCacheLoader(cache_dir=".memory/confluence-cache/")
    text = loader.load_url("https://mercedes-benz.atlassian.net/wiki/...")
    # Returns cached text if TTL not expired, else fetches fresh and caches.

Environment variables (read from .env.local or OS env):
    JIRA_BASE_URL        - e.g. https://mercedes-benz.atlassian.net
    JIRA_USERNAME        - Atlassian account email
    JIRA_API_TOKEN       - Atlassian API token
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Default TTL: 24 hours in seconds
DEFAULT_TTL_SECONDS = 24 * 60 * 60


def _load_env() -> dict[str, str]:
    """Load environment variables from .env.local using portable RepoResolver."""
    try:
        from langchain_helpers.repo_resolver import get_resolver
        return get_resolver()._load_env()
    except Exception:
        pass
    # Fallback: scan from cwd
    env: dict[str, str] = {}
    for path in [Path(os.getcwd()) / ".env.local", Path(os.getcwd()) / "Automation" / ".env.local"]:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    env[key.strip()] = val.strip().strip('"').strip("'")
            break
    return env


def _url_to_cache_key(url: str) -> str:
    """Convert a URL to a safe cache filename."""
    # Short human-readable slug from the URL path
    slug = re.sub(r"[^a-zA-Z0-9]", "-", url.split("//")[-1])[:80]
    # Add a hash suffix to ensure uniqueness
    suffix = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"{slug}-{suffix}.json"


class ConfluenceCacheLoader:
    """Loads Confluence pages with a local JSON cache (TTL-based).

    On a cache hit (within TTL), returns cached content instantly.
    On a cache miss, fetches from Confluence REST API and writes to cache.
    Falls back to empty string on auth errors without crashing the workflow.
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        env = _load_env()
        self.base_url = (env.get("JIRA_BASE_URL") or os.getenv("JIRA_BASE_URL", "")).rstrip("/")
        self.username = env.get("JIRA_USERNAME") or os.getenv("JIRA_USERNAME", "")
        self.api_token = env.get("JIRA_API_TOKEN") or os.getenv("JIRA_API_TOKEN", "")
        self.ttl_seconds = ttl_seconds

        if cache_dir is None:
            try:
                from langchain_helpers.repo_resolver import get_resolver
                cache_dir = get_resolver().memory_path("confluence-cache")
            except Exception:
                cache_dir = Path(__file__).resolve().parents[4] / ".memory" / "confluence-cache"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.cache_dir / "index.json"

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_url(self, url: str) -> str:
        """Return page text for *url*, using the local cache when fresh.

        Returns empty string if the page cannot be fetched and is not cached.
        """
        cached = self._read_cache(url)
        if cached is not None:
            log.debug("confluence_loader: cache hit for %s", url)
            return cached

        log.info("confluence_loader: fetching %s", url)
        text = self._fetch_confluence(url)
        if text:
            self._write_cache(url, text)
        return text

    def load_page_id(self, page_id: str) -> str:
        """Fetch a Confluence page by numeric ID."""
        url = f"{self.base_url}/wiki/rest/api/content/{page_id}?expand=body.storage"
        return self.load_url(url)

    def is_cached(self, url: str) -> bool:
        """Return True if *url* is cached and the TTL has not expired."""
        return self._read_cache(url) is not None

    def invalidate(self, url: str) -> None:
        """Remove the cache entry for *url*."""
        cache_file = self.cache_dir / _url_to_cache_key(url)
        if cache_file.exists():
            cache_file.unlink()
            log.info("confluence_loader: invalidated cache for %s", url)

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _read_cache(self, url: str) -> str | None:
        """Return cached body string if fresh, else None."""
        cache_file = self.cache_dir / _url_to_cache_key(url)
        if not cache_file.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_at = data.get("_meta", {}).get("cached_at_epoch", 0)
            age = time.time() - cached_at
            if age <= self.ttl_seconds:
                return data.get("content", "")
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        return None

    def _write_cache(self, url: str, content: str) -> None:
        """Persist *content* to the local cache."""
        cache_file = self.cache_dir / _url_to_cache_key(url)
        payload = {
            "_meta": {
                "url": url,
                "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "cached_at_epoch": int(time.time()),
                "ttl_seconds": self.ttl_seconds,
            },
            "content": content,
        }
        cache_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self._update_index(url, str(cache_file), payload["_meta"]["cached_at"])
        log.debug("confluence_loader: cached %s → %s", url, cache_file.name)

    def _fetch_confluence(self, url: str) -> str:
        """Fetch a Confluence page via REST API and return plain text body."""
        try:
            import requests  # type: ignore[import]
        except ImportError:
            log.warning("confluence_loader: 'requests' not installed — cannot fetch %s", url)
            return ""

        if not self.username or not self.api_token:
            log.warning("confluence_loader: JIRA_USERNAME / JIRA_API_TOKEN not set — skipping fetch")
            return ""

        try:
            # Handle two URL shapes:
            # 1. /wiki/spaces/.../pages/<id>  → convert to REST API
            # 2. Already a REST API URL (rest/api/content/<id>)
            api_url = self._to_api_url(url)
            resp = requests.get(
                api_url,
                auth=(self.username, self.api_token),
                headers={"Accept": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            # Try body.storage first, then body.view, then title fallback
            body = (
                data.get("body", {}).get("storage", {}).get("value")
                or data.get("body", {}).get("view", {}).get("value")
                or ""
            )
            title = data.get("title", "")
            # Strip HTML tags for plain text
            plain = re.sub(r"<[^>]+>", " ", body)
            plain = re.sub(r"\s+", " ", plain).strip()
            return f"# {title}\n\n{plain}" if plain else title
        except Exception as exc:
            log.warning("confluence_loader: failed to fetch %s — %s", url, exc)
            return ""

    def _to_api_url(self, url: str) -> str:
        """Convert a Confluence browser URL to a REST API URL if needed."""
        if "rest/api/content" in url:
            return url
        # Extract page ID from /pages/<number> pattern
        match = re.search(r"/pages/(\d+)", url)
        if match:
            page_id = match.group(1)
            return f"{self.base_url}/wiki/rest/api/content/{page_id}?expand=body.storage,body.view"
        # Fallback: return as-is
        return url + "?expand=body.storage,body.view"

    def _update_index(self, url: str, cache_file: str, cached_at: str) -> None:
        """Add or update the URL entry in confluence-cache/index.json."""
        try:
            if self._index_path.exists():
                index = json.loads(self._index_path.read_text(encoding="utf-8"))
            else:
                index = {"_meta": {"description": "Confluence cache index", "ttl_hours": 24}, "cached_pages": []}

            pages: list[dict[str, str]] = index.get("cached_pages", [])
            # Update existing entry or append new one
            existing = next((p for p in pages if p.get("url") == url), None)
            entry = {"url": url, "cache_file": Path(cache_file).name, "cached_at": cached_at}
            if existing:
                existing.update(entry)
            else:
                pages.append(entry)
            index["cached_pages"] = pages
            self._index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            log.debug("confluence_loader: failed to update index — %s", exc)

