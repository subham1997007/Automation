"""
Read all ADR pages from a Confluence folder.
Usage: python3 read_adr_folder.py
"""

import urllib.request
import urllib.parse
import json
import base64
import re
import os
import sys

ENV_FILE = os.path.join(os.path.dirname(__file__), "..", ".env.local")
FOLDER_ID = "2717732568"


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def get_auth_header(env):
    username = env.get("CONFLUENCE_USERNAME") or env.get("JIRA_USERNAME", "")
    token = env.get("CONFLUENCE_API_TOKEN") or env.get("JIRA_API_TOKEN", "")
    creds = base64.b64encode(f"{username}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Accept": "application/json"}


def strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_children(folder_id, env):
    base_url = env.get("CONFLUENCE_BASE_URL") or env.get("JIRA_BASE_URL", "")
    url = f"{base_url}/wiki/rest/api/content/{folder_id}/child/page?limit=50&expand=title,version"
    req = urllib.request.Request(url, headers=get_auth_header(env))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def fetch_page(page_id, env):
    base_url = env.get("CONFLUENCE_BASE_URL") or env.get("JIRA_BASE_URL", "")
    url = f"{base_url}/wiki/rest/api/content/{page_id}?expand=body.storage,title,space,version"
    req = urllib.request.Request(url, headers=get_auth_header(env))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def print_page(data):
    base_url = os.environ.get("CONFLUENCE_BASE_URL") or os.environ.get("JIRA_BASE_URL", "")
    web_ui = data.get("_links", {}).get("webui", "")
    print(f"\n{'='*70}")
    print(f"  TITLE  : {data.get('title')}")
    print(f"  VERSION: {data.get('version', {}).get('number', '')}")
    print(f"  URL    : {base_url}/wiki{web_ui}")
    print(f"{'='*70}")
    body = data.get("body", {}).get("storage", {}).get("value", "")
    print(strip_html(body))
    print()


def main():
    env = load_env(ENV_FILE)

    print(f"Listing pages in ADR folder (ID: {FOLDER_ID})...")
    try:
        children_data = fetch_children(FOLDER_ID, env)
        pages = children_data.get("results", [])
        print(f"Found {len(pages)} ADR page(s):\n")
        for p in pages:
            print(f"  [{p['id']}] {p['title']}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason}")
        print(e.read().decode()[:300])
        sys.exit(1)
    except Exception as ex:
        print(f"Error listing folder: {ex}")
        sys.exit(1)

    print("\n" + "="*70)
    print("FETCHING FULL CONTENT OF EACH ADR...")
    print("="*70)

    for p in pages:
        try:
            data = fetch_page(p["id"], env)
            print_page(data)
        except urllib.error.HTTPError as e:
            print(f"\n[ERROR] Could not fetch page {p['id']} ({p['title']}): HTTP {e.code}")
        except Exception as ex:
            print(f"\n[ERROR] Could not fetch page {p['id']} ({p['title']}): {ex}")


if __name__ == "__main__":
    main()

