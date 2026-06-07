#!/usr/bin/env python3
"""
confluence_to_markdown.py — Fetch Confluence pages and convert to structured markdown
=====================================================================================

This script is used by AI agents during story creation/refinement to gain
additional knowledge from Confluence documentation.

Usage:
  # By URL:
  python3 Automation/scripts/confluence_to_markdown.py "https://mercedes-benz.atlassian.net/wiki/spaces/BDATARTINT/pages/2909551264"

  # By page ID:
  python3 Automation/scripts/confluence_to_markdown.py 2909551264

  # Multiple pages:
  python3 Automation/scripts/confluence_to_markdown.py 2909551264 2771823625

  # Search + convert:
  python3 Automation/scripts/confluence_to_markdown.py --search "relatedArchitectureElement"

Output:
  - Prints markdown to stdout (for AI agent to read)
  - Caches to .memory/confluence-cache/<slug>.md (for future use within 24h TTL)
  - Returns structured JSON metadata alongside markdown

How agents use it:
  1. During story refinement → user provides Confluence link
  2. Agent calls this script with the URL
  3. Script returns clean markdown with all context
  4. Agent uses that context for better AC generation

Supports:
  - Confluence Cloud URLs (Atlassian)
  - Page IDs
  - Search by keyword
  - HTML → Markdown conversion (tables, code blocks, headings, lists)
  - 24h TTL caching
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AUTOMATION_DIR = Path(__file__).resolve().parent.parent
ENV_FILE       = AUTOMATION_DIR / ".env.local"
CACHE_DIR      = AUTOMATION_DIR / ".memory" / "confluence-cache"
CACHE_TTL_HOURS = 24

# ---------------------------------------------------------------------------
# Env loader
# ---------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def get_auth_header(env: dict[str, str]) -> dict[str, str]:
    username = env.get("CONFLUENCE_USERNAME") or env.get("JIRA_USERNAME", "")
    token    = env.get("CONFLUENCE_API_TOKEN") or env.get("JIRA_API_TOKEN", "")
    creds    = base64.b64encode(f"{username}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Accept": "application/json"}


def get_base_url(env: dict[str, str]) -> str:
    return env.get("CONFLUENCE_BASE_URL") or env.get("JIRA_BASE_URL", "")


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def extract_page_id_from_url(url: str) -> str | None:
    """Extract page ID from various Confluence URL formats."""
    # Format: .../pages/12345/Page+Title
    m = re.search(r"/pages/(\d+)", url)
    if m:
        return m.group(1)
    # Format: .../pageId=12345
    m = re.search(r"pageId=(\d+)", url)
    if m:
        return m.group(1)
    # Just a numeric ID passed directly
    if url.strip().isdigit():
        return url.strip()
    return None


# ---------------------------------------------------------------------------
# HTML → Markdown converter
# ---------------------------------------------------------------------------

def html_to_markdown(html: str) -> str:
    """Convert Confluence storage format HTML to readable markdown."""
    if not html:
        return ""

    text = html

    # ── Code blocks ────────────────────────────────────────────────────────
    # <ac:structured-macro ac:name="code"> ... <ac:plain-text-body><![CDATA[...]]></ac:plain-text-body>
    text = re.sub(
        r'<ac:structured-macro[^>]*ac:name="code"[^>]*>.*?<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>.*?</ac:structured-macro>',
        lambda m: f"\n```\n{m.group(1).strip()}\n```\n",
        text, flags=re.DOTALL
    )

    # ── Headings ───────────────────────────────────────────────────────────
    for i in range(6, 0, -1):
        text = re.sub(rf"<h{i}[^>]*>(.*?)</h{i}>", lambda m, lv=i: f"\n{'#' * lv} {m.group(1).strip()}\n", text, flags=re.DOTALL)

    # ── Tables → markdown tables ──────────────────────────────────────────
    def convert_table(m: re.Match) -> str:
        table_html = m.group(0)
        rows: list[list[str]] = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL):
            cells: list[str] = []
            for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.DOTALL):
                cells.append(re.sub(r"<[^>]+>", "", cell).strip())
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        # Build markdown table
        result = "\n| " + " | ".join(rows[0]) + " |\n"
        result += "| " + " | ".join(["---"] * len(rows[0])) + " |\n"
        for row in rows[1:]:
            # Pad to match header length
            padded = row + [""] * (len(rows[0]) - len(row))
            result += "| " + " | ".join(padded[:len(rows[0])]) + " |\n"
        return result + "\n"

    text = re.sub(r"<table[^>]*>.*?</table>", convert_table, text, flags=re.DOTALL)

    # ── Lists ──────────────────────────────────────────────────────────────
    text = re.sub(r"<li[^>]*>(.*?)</li>", lambda m: f"- {m.group(1).strip()}\n", text, flags=re.DOTALL)
    text = re.sub(r"<[uo]l[^>]*>", "\n", text)
    text = re.sub(r"</[uo]l>", "\n", text)

    # ── Bold, italic ───────────────────────────────────────────────────────
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)

    # ── Links ──────────────────────────────────────────────────────────────
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL)

    # ── Line breaks / paragraphs ──────────────────────────────────────────
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<p[^>]*>", "\n", text)
    text = re.sub(r"</p>", "\n", text)

    # ── Remove all remaining HTML tags ────────────────────────────────────
    text = re.sub(r"<[^>]+>", "", text)

    # ── Clean up HTML entities ────────────────────────────────────────────
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)

    # ── Clean up whitespace ───────────────────────────────────────────────
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Confluence API
# ---------------------------------------------------------------------------

def fetch_page(page_id: str, env: dict[str, str]) -> dict[str, Any]:
    base_url = get_base_url(env)
    url = f"{base_url}/wiki/rest/api/content/{page_id}?expand=body.storage,title,space,version,ancestors"
    req = urllib.request.Request(url, headers=get_auth_header(env))
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def search_pages(query: str, env: dict[str, str], limit: int = 5) -> list[dict[str, Any]]:
    base_url = get_base_url(env)
    space_key = env.get("CONFLUENCE_SPACE_KEY", "")
    cql = f'space="{space_key}" AND text~"{query}" ORDER BY lastmodified DESC'
    encoded = urllib.parse.quote(cql)
    url = f"{base_url}/wiki/rest/api/content/search?cql={encoded}&limit={limit}&expand=body.storage,title"
    req = urllib.request.Request(url, headers=get_auth_header(env))
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    return data.get("results", [])


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", slug)[:60].strip("-")


def get_cache_path(page_id: str, title: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(title) if title else page_id
    return CACHE_DIR / f"{slug}-{page_id}.md"


def is_cache_valid(cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
    return age_hours < CACHE_TTL_HOURS


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def page_to_markdown(page_data: dict[str, Any]) -> str:
    """Convert a Confluence page response to structured markdown."""
    title   = page_data.get("title", "Untitled")
    space   = page_data.get("space", {}).get("key", "")
    version = page_data.get("version", {}).get("number", "")
    page_id = page_data.get("id", "")
    url     = page_data.get("_links", {}).get("webui", "")

    # Ancestors (breadcrumb)
    ancestors = page_data.get("ancestors", [])
    breadcrumb = " > ".join(a.get("title", "") for a in ancestors) if ancestors else ""

    body_html = page_data.get("body", {}).get("storage", {}).get("value", "")
    body_md   = html_to_markdown(body_html)

    # Build structured markdown document
    lines: list[str] = [
        f"# {title}",
        "",
        "---",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Page ID | {page_id} |",
        f"| Space | {space} |",
        f"| Version | {version} |",
        f"| Breadcrumb | {breadcrumb} |",
        f"| URL | {url} |",
        f"| Fetched | {datetime.now().isoformat(timespec='seconds')} |",
        "---",
        "",
        body_md,
        "",
    ]

    return "\n".join(lines)


def fetch_and_convert(page_id: str, env: dict[str, str]) -> str:
    """Fetch a page, convert to markdown, cache it, return content."""
    data     = fetch_page(page_id, env)
    title    = data.get("title", "")
    markdown = page_to_markdown(data)

    # Cache
    cache_path = get_cache_path(page_id, title)
    cache_path.write_text(markdown, encoding="utf-8")

    return markdown


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Fetch Confluence pages and convert to markdown for AI agent context.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch by URL:
  python3 Automation/scripts/confluence_to_markdown.py "https://...atlassian.net/.../pages/12345/My+Page"

  # Fetch by page ID:
  python3 Automation/scripts/confluence_to_markdown.py 12345

  # Search and convert top results:
  python3 Automation/scripts/confluence_to_markdown.py --search "architecture linking"

  # Just check cache (no network):
  python3 Automation/scripts/confluence_to_markdown.py --cache-only 12345
        """,
    )
    parser.add_argument("pages", nargs="*", help="Page IDs or Confluence URLs")
    parser.add_argument("--search", type=str, help="Search Confluence and convert top results")
    parser.add_argument("--cache-only", action="store_true", help="Only return cached content, no network")
    parser.add_argument("--json-meta", action="store_true", help="Also output JSON metadata block")
    args = parser.parse_args()

    env = load_env()

    if not args.pages and not args.search:
        parser.print_help()
        sys.exit(1)

    results: list[dict[str, str]] = []

    # ── Search mode ────────────────────────────────────────────────────────
    if args.search:
        print(f"[confluence] 🔍 Searching: '{args.search}'...", file=sys.stderr)
        try:
            pages = search_pages(args.search, env)
            if not pages:
                print("[confluence] No results found.", file=sys.stderr)
                sys.exit(0)
            for p in pages[:3]:
                page_id = p["id"]
                title   = p.get("title", "")
                md      = page_to_markdown(p)
                cache_path = get_cache_path(page_id, title)
                cache_path.write_text(md, encoding="utf-8")
                results.append({"page_id": page_id, "title": title, "markdown": md})
                print(f"[confluence] ✓ {title} (id={page_id})", file=sys.stderr)
        except Exception as e:
            print(f"[confluence] ❌ Search failed: {e}", file=sys.stderr)
            sys.exit(1)

    # ── Direct page fetch mode ─────────────────────────────────────────────
    for page_arg in (args.pages or []):
        page_id = extract_page_id_from_url(page_arg)
        if not page_id:
            print(f"[confluence] ⚠️  Could not extract page ID from: {page_arg}", file=sys.stderr)
            continue

        # Check cache first
        # We don't know title yet, so check all cached files with this ID
        cached = list(CACHE_DIR.glob(f"*-{page_id}.md")) if CACHE_DIR.exists() else []
        if cached and is_cache_valid(cached[0]):
            md = cached[0].read_text(encoding="utf-8")
            print(f"[confluence] ✓ Loaded from cache: {cached[0].name}", file=sys.stderr)
            results.append({"page_id": page_id, "title": "(cached)", "markdown": md})
            continue

        if args.cache_only:
            print(f"[confluence] ⚠️  No cache for {page_id}", file=sys.stderr)
            continue

        # Fetch from API
        try:
            md = fetch_and_convert(page_id, env)
            title = md.split("\n")[0].lstrip("# ").strip()
            results.append({"page_id": page_id, "title": title, "markdown": md})
            print(f"[confluence] ✓ Fetched: {title} (id={page_id})", file=sys.stderr)
        except Exception as e:
            print(f"[confluence] ❌ Failed to fetch {page_id}: {e}", file=sys.stderr)

    # ── Output ─────────────────────────────────────────────────────────────
    if not results:
        print("[confluence] No content retrieved.", file=sys.stderr)
        sys.exit(1)

    if args.json_meta:
        meta = [{"page_id": r["page_id"], "title": r["title"]} for r in results]
        print(json.dumps(meta, indent=2))
        print("\n---\n")

    for r in results:
        print(r["markdown"])
        print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()

