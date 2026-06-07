#!/usr/bin/env python3
"""Create a new Jira story for ADR-002.3 under BDATART-686, add to current sprint, and attach ADR Confluence link."""

import os
import sys
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-servers" / "jira-mcp" / "src"))
from jira_client import MANDATORY_JIRA_STYLE_PROFILE, build_adf_from_markdownish, validate_mandatory_story_style

# Load .env.local
env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
with open(env_file) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

JIRA_BASE     = "https://mercedes-benz.atlassian.net"
USERNAME      = os.environ.get("CONFLUENCE_USERNAME", "")
TOKEN         = os.environ.get("CONFLUENCE_API_TOKEN", "")
auth          = HTTPBasicAuth(USERNAME, TOKEN)
jira_h        = {"Accept": "application/json", "Content-Type": "application/json"}

# Known values
PARENT_KEY    = "BDATART-686"
SPRINT_ID     = 2184733    # PMT 26.2 - S03 - BDRSP (active)
PROJECT_KEY   = "BDRSP"
ADR_URL       = "https://mercedes-benz.atlassian.net/wiki/spaces/BDATARTINT/pages/2909545675"
ADR_TITLE     = "ADR-002.3: Extending link management for MCP Jira/Xray (QM) → Rhapsody Architecture Elements (AM)"

# ────────────────────────────────────────────────────────
# Story content
# ────────────────────────────────────────────────────────
STORY_SUMMARY = "[Linking] ADR-002.3: Document architecture decision for QM (Jira/Xray) → AM (Rhapsody) VALIDATES_ARCHITECTURE_ELEMENT link type"

ACCEPTANCE_CRITERIA = (
    "ADR-002.3 is published in the Confluence ADR folder (BDATARTINT space)\n"
    "ADR covers: context, decision, 13-item considerations table, architectural changes (3 files), GraphQL API contract, data flow, consequences, and references\n"
    "ADR is linked to this story and to feature BDATART-686\n"
    "Implementation (MR !257) is verified on DEV: createNewLink creates VALIDATES_ARCHITECTURE_ELEMENT link, findLinksBySourceUri returns it"
)

DESCRIPTION_MARKDOWN_BDRSP_1623 = f"""## User Story
:::info
## Goal
Create and publish the Architecture Decision Record (ADR-002.3) that documents the design decisions, considerations, and implementation details for enabling the `VALIDATES_ARCHITECTURE_ELEMENT` link type between MCP Jira/Xray (Quality Management domain) and Rhapsody Architecture Elements (Architecture Management domain), as required by feature `BDATART-686`.
:::

## Feature Reference
| Field | Value |
|---|---|
| Feature | BDATART-686 |
| ADR | ADR-002.3 |
| Link Type | `VALIDATES_ARCHITECTURE_ELEMENT` |
| Reference Style | {MANDATORY_JIRA_STYLE_PROFILE} |

:::success
## Implementation Scope
1. Document why `VALIDATES_ARCHITECTURE_ELEMENT` is required for QM Jira/Xray to AM Rhapsody linking.
2. Capture the middleware changes in `schema.graphqls`, `UsecaseUtil.java`, and `EntityToDtoTransformerService.java`.
3. Link the story to the Confluence ADR page and the parent Feature.
:::

:::warning
## Constraints
- Story creation must preserve the mandatory `{MANDATORY_JIRA_STYLE_PROFILE}` format.
- ADR publication and Jira linkage must be validated before the story is considered done.
- Sprint assignment and custom Acceptance Criteria fields may be unavailable in some Jira configurations.
:::

:::note
## Acceptance Criteria
- ADR-002.3 is published in the Confluence ADR folder (BDATARTINT space) as a child of the ADR Overview page.
- ADR covers context, decision, considerations table, architectural changes, GraphQL API contract, data flow, consequences, and references.
- ADR is linked to this story and to feature BDATART-686.
- Implementation (MR !257) is verified on DEV: `createNewLink` creates `VALIDATES_ARCHITECTURE_ELEMENT`; `findLinksBySourceUri` returns it.
:::

## ADR Reference
{ADR_URL}
"""

style_validation = validate_mandatory_story_style(DESCRIPTION_MARKDOWN_BDRSP_1623)
if not style_validation["ok"]:
    raise SystemExit(f"Story description does not match {MANDATORY_JIRA_STYLE_PROFILE} style: {style_validation}")

DESCRIPTION_ADFV3 = build_adf_from_markdownish(DESCRIPTION_MARKDOWN_BDRSP_1623)


def create_story():
    payload = {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "summary": STORY_SUMMARY,
            "issuetype": {"name": "Story"},
            "parent": {"key": PARENT_KEY},
            "description": DESCRIPTION_ADFV3,
            "customfield_10020": {"id": str(SPRINT_ID)},   # Sprint field
            "customfield_10451": ACCEPTANCE_CRITERIA,       # Acceptance Criteria (custom field)
        }
    }

    print("Creating Jira story...")
    r = requests.post(
        f"{JIRA_BASE}/rest/api/3/issue",
        json=payload,
        auth=auth,
        headers=jira_h,
        verify=False,
    )

    if r.status_code in (200, 201):
        data = r.json()
        key = data["key"]
        print(f"  ✅ Story created: {key}")
        print(f"     URL: {JIRA_BASE}/browse/{key}")
        return key
    else:
        print(f"  ❌ Failed to create story: HTTP {r.status_code}")
        print(r.text[:1500])
        # Try without sprint and AC fields
        print("\n  Retrying without sprint/AC custom fields...")
        payload["fields"].pop("customfield_10020", None)
        payload["fields"].pop("customfield_10451", None)
        r2 = requests.post(
            f"{JIRA_BASE}/rest/api/3/issue",
            json=payload,
            auth=auth,
            headers=jira_h,
            verify=False,
        )
        if r2.status_code in (200, 201):
            data = r2.json()
            key = data["key"]
            print(f"  ✅ Story created (without sprint): {key}")
            print(f"     URL: {JIRA_BASE}/browse/{key}")
            return key
        else:
            print(f"  ❌ Retry also failed: HTTP {r2.status_code}")
            print(r2.text[:1500])
            return None


def add_sprint(issue_key):
    """Add issue to sprint via agile API."""
    print(f"\nAdding {issue_key} to sprint {SPRINT_ID}...")
    r = requests.post(
        f"{JIRA_BASE}/rest/agile/1.0/sprint/{SPRINT_ID}/issue",
        json={"issues": [issue_key]},
        auth=auth,
        headers=jira_h,
        verify=False,
    )
    if r.status_code in (200, 204):
        print(f"  ✅ Added to sprint PMT 26.2 - S03 - BDRSP")
    else:
        print(f"  ⚠️  Sprint assignment response: HTTP {r.status_code} — {r.text[:300]}")


def attach_adr_link(issue_key):
    """Attach Confluence ADR page as a remote link on the story."""
    print(f"\nAttaching ADR Confluence link to {issue_key}...")
    payload = {
        "globalId": f"confluence:{ADR_URL}",
        "object": {
            "url": ADR_URL,
            "title": ADR_TITLE,
            "icon": {
                "url16x16": "https://mercedes-benz.atlassian.net/favicon.ico",
                "title": "Confluence Page"
            }
        },
        "application": {
            "type": "com.atlassian.confluence",
            "name": "Confluence"
        },
        "relationship": "ADR"
    }
    r = requests.post(
        f"{JIRA_BASE}/rest/api/3/issue/{issue_key}/remotelink",
        json=payload,
        auth=auth,
        headers=jira_h,
        verify=False,
    )
    if r.status_code in (200, 201):
        print(f"  ✅ ADR Confluence link attached")
        print(f"     {ADR_URL}")
    else:
        print(f"  ⚠️  Remote link response: HTTP {r.status_code} — {r.text[:300]}")


def add_comment(issue_key):
    """Add a comment with the ADR link."""
    print(f"\nAdding comment with ADR reference to {issue_key}...")
    payload = {
        "body": {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "📄 ADR published: "},
                        {
                            "type": "text",
                            "text": ADR_TITLE,
                            "marks": [{"type": "link", "attrs": {"href": ADR_URL}}]
                        },
                        {"type": "text", "text": "\n\nThis story captures the documentation work for the VALIDATES_ARCHITECTURE_ELEMENT link type implementation (MR !257). The ADR covers all 13 consideration areas, 3-file code changes, data flow, and acceptance criteria."}
                    ]
                }
            ]
        }
    }
    r = requests.post(
        f"{JIRA_BASE}/rest/api/3/issue/{issue_key}/comment",
        json=payload,
        auth=auth,
        headers=jira_h,
        verify=False,
    )
    if r.status_code in (200, 201):
        print(f"  ✅ Comment added")
    else:
        print(f"  ⚠️  Comment response: HTTP {r.status_code} — {r.text[:300]}")


def main():
    key = create_story()
    if not key:
        return

    add_sprint(key)
    attach_adr_link(key)
    add_comment(key)

    print(f"\n{'='*60}")
    print(f"✅ Done!")
    print(f"   Story   : {JIRA_BASE}/browse/{key}")
    print(f"   Feature : {JIRA_BASE}/browse/{PARENT_KEY}")
    print(f"   ADR     : {ADR_URL}")
    print(f"   Sprint  : PMT 26.2 - S03 - BDRSP")


if __name__ == "__main__":
    main()
