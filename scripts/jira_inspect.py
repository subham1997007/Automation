#!/usr/bin/env python3
"""Helper: inspect BDATART-686 subtasks/children and current sprint info."""

import os
import sys
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

JIRA_BASE = os.environ.get("JIRA_BASE_URL", "")
USERNAME  = os.environ.get("JIRA_USERNAME", os.environ.get("CONFLUENCE_USERNAME", ""))
TOKEN     = os.environ.get("JIRA_API_TOKEN", os.environ.get("CONFLUENCE_API_TOKEN", ""))

auth    = HTTPBasicAuth(USERNAME, TOKEN)
headers = {"Accept": "application/json", "Content-Type": "application/json"}


def get(path, params=None):
    r = requests.get(f"{JIRA_BASE}{path}", params=params, auth=auth, headers=headers, verify=False)
    r.raise_for_status()
    return r.json()


def main():
    # 1. Get the feature issue
    issue = get("/rest/api/3/issue/BDATART-686", {"fields": "summary,subtasks,issuetype,project,assignee,components,customfield_10020"})
    fields = issue["fields"]
    print("=== BDATART-686 ===")
    print(f"  Summary   : {fields['summary']}")
    print(f"  Type      : {fields['issuetype']['name']}")
    print(f"  Project   : {fields['project']['key']} — {fields['project']['name']}")
    project_key = fields['project']['key']

    # Sprint field
    sprint_field = fields.get("customfield_10020")
    if sprint_field:
        for s in sprint_field:
            print(f"  Sprint    : {s.get('name')} (id={s.get('id')}, state={s.get('state')})")

    # 2. Subtasks
    subtasks = fields.get("subtasks", [])
    print(f"\n  Subtasks ({len(subtasks)}):")
    for st in subtasks:
        print(f"    {st['key']} [{st['fields']['issuetype']['name']}] — {st['fields']['summary']} ({st['fields']['status']['name']})")

    # 3. Find child stories via JQL
    print("\n=== Child stories via JQL ===")
    jql = f'issueFunction in subtasksOf("key = BDATART-686") OR "Epic Link" = BDATART-686 OR parent = BDATART-686'
    results = get("/rest/api/3/search", {"jql": jql, "fields": "summary,status,issuetype", "maxResults": 50})
    issues = results.get("issues", [])
    print(f"  Found {len(issues)} linked stories:")
    for i in issues:
        print(f"    {i['key']} [{i['fields']['issuetype']['name']}] — {i['fields']['summary']} ({i['fields']['status']['name']})")

    # 4. Current active sprints for the board
    print("\n=== Active sprints (BDRSP project board) ===")
    try:
        boards = get("/rest/agile/1.0/board", {"projectKeyOrId": "BDRSP", "maxResults": 5})
        for board in boards.get("values", []):
            bid = board["id"]
            bname = board["name"]
            sprints = get(f"/rest/agile/1.0/board/{bid}/sprint", {"state": "active"})
            for sp in sprints.get("values", []):
                print(f"  Board: {bname} | Sprint: {sp['name']} | id={sp['id']} | start={sp.get('startDate','?')} end={sp.get('endDate','?')}")
    except Exception as e:
        print(f"  (could not fetch board sprints: {e})")

    # 5. Issue types for the project
    print(f"\n=== Issue types for project {project_key} ===")
    meta = get(f"/rest/api/3/issue/createmeta/{project_key}/issuetypes")
    for it in meta.get("issueTypes", []):
        print(f"  id={it['id']}  name={it['name']}")


if __name__ == "__main__":
    main()

