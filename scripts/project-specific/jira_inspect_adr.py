#!/usr/bin/env python3
"""Inspect BDRSP-1418 structure and find current active sprint, then create ADR story."""

import os
import json
import requests
from requests.auth import HTTPBasicAuth

# Load .env.local
env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
with open(env_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

JIRA_BASE = "https://mercedes-benz.atlassian.net"
USERNAME  = os.environ.get("CONFLUENCE_USERNAME", "")
TOKEN     = os.environ.get("CONFLUENCE_API_TOKEN", "")
auth      = HTTPBasicAuth(USERNAME, TOKEN)
jira_h    = {"Accept": "application/json", "Content-Type": "application/json"}


def get(path, params=None):
    r = requests.get(f"{JIRA_BASE}{path}", params=params, auth=auth, headers=jira_h, verify=False)
    r.raise_for_status()
    return r.json()


def main():
    # 1. Inspect BDRSP-1418
    print("=== BDRSP-1418 ===")
    issue = get("/rest/api/3/issue/BDRSP-1418", {"fields": "summary,status,issuetype,parent,customfield_10020,customfield_10014"})
    f = issue["fields"]
    print(f"  Summary : {f['summary']}")
    print(f"  Type    : {f['issuetype']['name']}")
    parent = f.get("parent")
    if parent:
        print(f"  Parent  : {parent['key']} — {parent['fields']['summary']}")
    sprint_f = f.get("customfield_10020") or []
    for s in sprint_f:
        print(f"  Sprint  : {s.get('name')}  id={s.get('id')}  state={s.get('state')}")
    epic_link = f.get("customfield_10014")
    print(f"  EpicLink: {epic_link}")

    # 2. Find active sprints for BDRSP board
    print("\n=== Active sprints (BDRSP board) ===")
    boards = get("/rest/agile/1.0/board", {"projectKeyOrId": "BDRSP", "maxResults": 10})
    for board in boards.get("values", []):
        sprints = get(f"/rest/agile/1.0/board/{board['id']}/sprint", {"state": "active"})
        for sp in sprints.get("values", []):
            print(f"  Board={board['name']}  Sprint={sp['name']}  id={sp['id']}  end={sp.get('endDate','?')}")

    # 3. Issue types available in BDRSP project
    print("\n=== Issue types for BDRSP ===")
    meta = get("/rest/api/3/issue/createmeta/BDRSP/issuetypes")
    for it in meta.get("issueTypes", []):
        print(f"  id={it['id']}  name={it['name']}")


if __name__ == "__main__":
    main()

