"""
Confluence Client — reads credentials from Automation/.env.local
Usage:
  python3 confluence_client.py <page_id>
  python3 confluence_client.py 2771823625
  python3 confluence_client.py search "opaque tokens"
"""

import urllib.request
import urllib.parse
import json
import base64
import re
import sys
import os

ENV_FILE = os.path.join(os.path.dirname(__file__), "..", ".env.local")


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
    token    = env.get("CONFLUENCE_API_TOKEN") or env.get("JIRA_API_TOKEN", "")
    creds    = base64.b64encode(f"{username}:{token}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Accept": "application/json"}


def strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_page(page_id, env):
    base_url = env.get("CONFLUENCE_BASE_URL") or env.get("JIRA_BASE_URL", "")
    url = f"{base_url}/wiki/rest/api/content/{page_id}?expand=body.storage,title,space,version"
    req = urllib.request.Request(url, headers=get_auth_header(env))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def search_pages(query, env, space=None):
    base_url = env.get("CONFLUENCE_BASE_URL") or env.get("JIRA_BASE_URL", "")
    space_key = space or env.get("CONFLUENCE_SPACE_KEY", "")
    cql = f'space="{space_key}" AND text~"{query}" ORDER BY lastmodified DESC'
    encoded = urllib.parse.quote(cql)
    url = f"{base_url}/wiki/rest/api/content/search?cql={encoded}&limit=10"
    req = urllib.request.Request(url, headers=get_auth_header(env))
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def print_page(data):
    print(f"\n{'='*60}")
    print(f"  TITLE  : {data.get('title')}")
    print(f"  STATUS : {data.get('status')}")
    print(f"  SPACE  : {data.get('space', {}).get('key', '')}")
    print(f"  VERSION: {data.get('version', {}).get('number', '')}")
    print(f"  URL    : {data.get('_links', {}).get('webui', '')}")
    print(f"{'='*60}\n")
    body = data.get("body", {}).get("storage", {}).get("value", "")
    print(strip_html(body))


if __name__ == "__main__":
    env = load_env(ENV_FILE)

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 confluence_client.py <page_id>")
        print("  python3 confluence_client.py search <query>")
        sys.exit(1)

    try:
        if sys.argv[1] == "search":
            query = " ".join(sys.argv[2:])
            print(f"Searching Confluence for: '{query}'...")
            results = search_pages(query, env)
            for r in results.get("results", []):
                print(f"  [{r['id']}] {r['title']} — {r.get('_links', {}).get('webui', '')}")
        else:
            page_id = sys.argv[1]
            print(f"Fetching Confluence page {page_id}...")
            data = fetch_page(page_id, env)
            print_page(data)
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason}")
        print(e.read().decode()[:300])
    except Exception as ex:
        print(f"Error: {ex}")
