#!/usr/bin/env python3
"""Fetch all ADR pages from Confluence folder and print their content."""

import os
import sys
import re
import requests
from requests.auth import HTTPBasicAuth

# Load .env.local
env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

BASE_URL = os.environ.get("CONFLUENCE_BASE_URL") or os.environ.get("JIRA_BASE_URL", "")
USERNAME = os.environ.get("CONFLUENCE_USERNAME", "")
TOKEN = os.environ.get("CONFLUENCE_API_TOKEN", "")
FOLDER_ID = "2717732568"

auth = HTTPBasicAuth(USERNAME, TOKEN)
headers = {"Accept": "application/json"}


def get_plain_text(html_body):
    """Strip HTML tags for a rough plain-text view."""
    text = re.sub(r"<[^>]+>", " ", html_body)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_children(parent_id):
    resp = requests.get(
        f"{BASE_URL}/wiki/rest/api/content/{parent_id}/child/page",
        params={"limit": 50},
        auth=auth,
        headers=headers,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def fetch_page(page_id):
    resp = requests.get(
        f"{BASE_URL}/wiki/rest/api/content/{page_id}",
        params={"expand": "body.storage,version,metadata.labels"},
        auth=auth,
        headers=headers,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    print(f"Fetching ADR folder children (ID={FOLDER_ID})...\n")
    children = fetch_children(FOLDER_ID)
    print(f"Found {len(children)} ADR pages:\n")

    for child in children:
        page_id = child["id"]
        title = child["title"]
        print(f"{'='*80}")
        print(f"TITLE : {title}")
        print(f"ID    : {page_id}")
        print(f"URL   : {BASE_URL}/wiki/spaces/BDATARTINT/pages/{page_id}")

        page = fetch_page(page_id)
        body_html = page.get("body", {}).get("storage", {}).get("value", "")
        version = page.get("version", {}).get("number", "?")
        print(f"VER   : {version}")
        print(f"{'─'*80}")
        plain = get_plain_text(body_html)
        # Print max 3000 chars per page to avoid overwhelming output
        print(plain[:4000])
        if len(plain) > 4000:
            print(f"\n... [truncated — {len(plain)} total chars]")
        print()


if __name__ == "__main__":
    main()

