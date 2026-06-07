#!/usr/bin/env python3
"""Publish ADR-002.3 markdown document to Confluence as a child page of the ADR folder."""

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

BASE_URL = os.environ.get("CONFLUENCE_BASE_URL", "https://mercedes-benz.atlassian.net")
USERNAME = os.environ.get("CONFLUENCE_USERNAME", "")
TOKEN = os.environ.get("CONFLUENCE_API_TOKEN", "")
SPACE_KEY = os.environ.get("CONFLUENCE_SPACE_KEY", "BDATARTINT")
PARENT_ID = "2717732568"  # ADR folder

auth = HTTPBasicAuth(USERNAME, TOKEN)
headers = {"Accept": "application/json", "Content-Type": "application/json"}

# ──────────────────────────────────────────────────────
# ADR content in Confluence Storage Format (HTML-like)
# ──────────────────────────────────────────────────────
TITLE = "ADR-002.3: Extending link management for MCP Jira/Xray (QM) → Rhapsody Architecture Elements (AM)"

BODY = """
<table>
  <tbody>
    <tr><th>Field</th><th>Value</th></tr>
    <tr><td>Status</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Blue</ac:parameter><ac:parameter ac:name="title">Proposed</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>Date</td><td>2026-05-28</td></tr>
    <tr><td>Author</td><td>Subham Kumar</td></tr>
    <tr><td>Feature</td><td><a href="https://mercedes-benz.atlassian.net/browse/BDATART-686">BDATART-686</a></td></tr>
    <tr><td>Story</td><td><a href="https://mercedes-benz.atlassian.net/browse/BDRSP-1418">BDRSP-1418</a></td></tr>
  </tbody>
</table>

<h2>Context</h2>
<p>Linking Middleware already supports bi-directional traceability for the following use cases (ADR-002):</p>
<table>
  <tbody>
    <tr><th>Source Domain</th><th>Target Domain</th><th>Link Types</th></tr>
    <tr><td>CM (Jira)</td><td>RM (DNG)</td><td>IMPLEMENTS, TRACKS_REQUIREMENT, AFFECTS_REQUIREMENT</td></tr>
    <tr><td>QM (Jira)</td><td>RM (DNG)</td><td>VALIDATES</td></tr>
    <tr><td>AM (CCL)</td><td>AM (Rhapsody)</td><td>TRACKS_ARCHITECTURE_ELEMENT, REALIZES_ARCHITECTURE_ELEMENT, ALLOCATES_ARCHITECTURE_ELEMENT</td></tr>
  </tbody>
</table>
<p>The missing use case is <strong>QM (Jira/Xray) &rarr; AM (Rhapsody Architecture Elements)</strong> with link type <strong>VALIDATES_ARCHITECTURE_ELEMENT</strong>.</p>

<h3>Why this use case is needed</h3>
<p>The model-based systems engineering (MBSE) approach for ASPICE Level 3+ autonomous driving function development requires that test cases in MCP Jira/Xray be explicitly traceable to individual model elements in Rhapsody. This includes states, transitions, decision nodes, and entire scenarios depicted via path flows in activity diagrams.</p>
<p>Without this linking:</p>
<ul>
  <li>Impact analysis when an architecture element changes requires manual effort to identify affected test cases.</li>
  <li>ASPICE compliance cannot be demonstrated through tooling — reviewers must rely on manual documentation.</li>
  <li>Migration from STARC-Classic (Codebeamer &rarr; MCP Jira) cannot transfer existing <em>Validates Architectural Element</em> links to the new platform.</li>
</ul>

<h3>Stakeholders and consumers</h3>
<table>
  <tbody>
    <tr><th>Consumer</th><th>Role</th></tr>
    <tr><td>Co//ab</td><td>Creates links via Delegated UI from MCP Jira side</td></tr>
    <tr><td>BDA</td><td>Creates and migrates links programmatically</td></tr>
    <tr><td>RAM</td><td>Creates and migrates links programmatically</td></tr>
    <tr><td>STARC Migration Tool</td><td>Transfers Codebeamer links to MCP Jira via LMW GraphQL endpoints</td></tr>
    <tr><td>ADAS (Antonios Liamis)</td><td>Key stakeholder requesting the feature</td></tr>
  </tbody>
</table>

<h3>Constraints</h3>
<ul>
  <li>Links can <strong>only be created from MCP Jira</strong> (source) to Rhapsody (target). The Rhapsody AM delegated UI does not open inside LMW, so the reverse OSLC flow (PUT-driven from Rhapsody) is <strong>not applicable</strong> for this use case.</li>
  <li>Link creation and deletion go exclusively through the <strong>GraphQL mutation endpoints</strong> (<code>createNewLink</code>, <code>deleteLink</code>).</li>
</ul>

<h2>Decision</h2>
<p>Extend the Linking Middleware to support the <code>VALIDATES_ARCHITECTURE_ELEMENT</code> link type for the <strong>QM (Jira/Xray) &rarr; AM (Rhapsody Architecture Elements)</strong> use case via GraphQL mutations only.</p>
<p>The extension reuses all existing infrastructure:</p>
<ul>
  <li>The <code>JIRA_TO_RHAPSODY_QM_MUTATION_USE_CASE</code> routing constant (already present in <code>LinkService</code>)</li>
  <li>The <code>createQmToAmLink</code> / <code>deleteQmToAmLink</code> methods in <code>LinkCreationService</code></li>
  <li>The existing AM (Rhapsody) vertex model in the graph database</li>
  <li>The existing <code>QualityManagementResourceDto</code> and <code>ArchitecturalManagementResourceDto</code></li>
</ul>
<p>The required change is confined to <strong>enabling the routing gate</strong> that was previously returning <code>INVALID_MUTATION_USECASE</code> for this combination, plus registering the link type in the GraphQL schema and query filter.</p>

<h2>Considerations</h2>
<table>
  <tbody>
    <tr><th>#</th><th>Area</th><th>Change Required</th><th>Status</th></tr>
    <tr><td>1</td><td>Neptune DB Schema</td><td>No changes — QM vertex and AM (Rhapsody) vertex models already exist. The VALIDATES_ARCHITECTURE_ELEMENT link type is a valid edge label.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>2</td><td>Observability / Use Case Constants</td><td>JIRA_TO_RHAPSODY_QM_MUTATION_USE_CASE = "[qm]jira-rhapsody-mutation" already exists in UseCaseConstants. No new constant needed.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>3</td><td>UsecaseUtil — Mutation Routing</td><td>The routing gate at fetchUsecaseBasedOnSourceAndTargetAndLinkType() was returning INVALID_MUTATION_USECASE for Jira source + Rhapsody AM target + VALIDATES_ARCHITECTURE_ELEMENT. Fixed: now returns JIRA_TO_RHAPSODY_QM_MUTATION_USE_CASE.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>4</td><td>OSLC GET Endpoint</td><td>Not applicable. Links are created from MCP Jira side via GraphQL, not via OSLC PUT from Rhapsody.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>5</td><td>OSLC PUT Endpoint</td><td>Not applicable. Rhapsody delegated UI does not open inside LMW.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>6</td><td>Preview Endpoint</td><td>No changes required. Preview URL is constructed from target URI using existing constructPreviewUrl() logic.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>7</td><td>Compact Resource</td><td>No changes required.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>8</td><td>TRS Feed</td><td>No changes required. Link creation triggers existing TRS change event pipeline automatically.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>9</td><td>Authentication</td><td>No changes required. GraphQL endpoint protected by OAuth 2.0 client credentials (2LO).</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>10</td><td>GraphQL Schema</td><td>VALIDATES_ARCHITECTURE_ELEMENT was missing from the LinkType enum in schema.graphqls, causing serialization error "Can't serialize value: Unknown value 'VALIDATES_ARCHITECTURE_ELEMENT'". Fixed: value added to the enum with Javadoc.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>11</td><td>GraphQL Mutation — Creation</td><td>LinkService.createNewLink() already had a JIRA_TO_RHAPSODY_QM_MUTATION_USE_CASE case with full QM&rarr;AM creation logic. No change needed.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>12</td><td>GraphQL Mutation — Deletion</td><td>LinkService.executeDeleteLink() already had a JIRA_TO_RHAPSODY_QM_MUTATION_USE_CASE case with full QM&rarr;AM deletion logic. No change needed.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
    <tr><td>13</td><td>GraphQL Query</td><td>EntityToDtoTransformerService.isArchitectureTargetLinkType() was missing VALIDATES_ARCHITECTURE_ELEMENT — links found in DB were silently skipped during findLinksBySourceUri. Fixed: value added to the filter method.</td><td><ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Green</ac:parameter><ac:parameter ac:name="title">complete</ac:parameter></ac:structured-macro></td></tr>
  </tbody>
</table>

<h2>Architectural changes</h2>

<h3>1. Usecase routing fix — UsecaseUtil.java</h3>
<p><strong>Location</strong>: <code>src/main/java/com/mercedesbenz/cdplinkingmiddleware/observability/UsecaseUtil.java</code><br/>
<strong>Method</strong>: <code>fetchUsecaseBasedOnSourceAndTargetAndLinkType()</code></p>
<p>Added routing branch for Jira source + Rhapsody AM target + VALIDATES_ARCHITECTURE_ELEMENT:</p>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">java</ac:parameter><ac:plain-text-body><![CDATA[} else if (jiraSourceUrlPattern.matcher(sourceUrl).matches()
        && mbseRhapsodyTargetUrlPattern.matcher(targetUrl).matches()
        && linkType.equals(LinkType.VALIDATES_ARCHITECTURE_ELEMENT)) {
    return UseCaseConstants.JIRA_TO_RHAPSODY_QM_MUTATION_USE_CASE;
}]]></ac:plain-text-body></ac:structured-macro>
<p>The route is resolved by matching:</p>
<ul>
  <li>Source URL against the Jira issue base URL pattern (e.g. <code>https://&lt;jira&gt;/browse/PROJ-123</code>)</li>
  <li>Target URL against the Rhapsody AM base URL pattern (e.g. <code>https://&lt;mbse&gt;/am/web/...</code>)</li>
  <li>Link type equals <code>VALIDATES_ARCHITECTURE_ELEMENT</code></li>
</ul>

<h3>2. GraphQL schema — schema.graphqls</h3>
<p><strong>Location</strong>: <code>src/main/resources/graphql/schema.graphqls</code></p>
<p>Added <code>VALIDATES_ARCHITECTURE_ELEMENT</code> to the <code>LinkType</code> enum:</p>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">graphql</ac:parameter><ac:plain-text-body><![CDATA[# Indicates that a quality-management resource validates an architecture element.
VALIDATES_ARCHITECTURE_ELEMENT]]></ac:plain-text-body></ac:structured-macro>
<p>Without this, returning the enum value from a mutation caused: <em>Can't serialize value: Unknown value 'VALIDATES_ARCHITECTURE_ELEMENT'</em></p>

<h3>3. Query filter fix — EntityToDtoTransformerService.java</h3>
<p><strong>Location</strong>: <code>src/main/java/com/mercedesbenz/cdplinkingmiddleware/service/EntityToDtoTransformerService.java</code><br/>
<strong>Method</strong>: <code>isArchitectureTargetLinkType(String linkType)</code></p>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">java</ac:parameter><ac:plain-text-body><![CDATA[private static boolean isArchitectureTargetLinkType(String linkType) {
    return LinkType.TRACKS_ARCHITECTURE_ELEMENT.toString().equals(linkType)
        || LinkType.REALIZES_ARCHITECTURE_ELEMENT.toString().equals(linkType)
        || LinkType.ALLOCATES_ARCHITECTURE_ELEMENT.toString().equals(linkType)
        || LinkType.VALIDATES_ARCHITECTURE_ELEMENT.toString().equals(linkType);
}]]></ac:plain-text-body></ac:structured-macro>

<h2>GraphQL API contract</h2>

<h3>Mutation — createNewLink</h3>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">graphql</ac:parameter><ac:plain-text-body><![CDATA[mutation CreateValidatesArchitectureElementLink {
  createNewLink(input: {
    sourceUri: "https://<jira-base>/browse/<PROJ-KEY>",
    targetUri: "https://<mbse-base>/am/web/<project>/<resource-id>",
    linkType: "VALIDATES_ARCHITECTURE_ELEMENT",
    configurationUri: "https://<mbse-base>/am/<config-id>"
  }) {
    id
    sourceUri
    targetUri
    linkType
    createdOn
    errors {
      message
    }
  }
}]]></ac:plain-text-body></ac:structured-macro>

<h3>Query — findLinksBySourceUri</h3>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">graphql</ac:parameter><ac:plain-text-body><![CDATA[query FindLinks {
  findLinksBySourceUri(
    sourceUri: "https://<jira-base>/browse/<PROJ-KEY>",
    resourceType: "Quality Management Resource"
  ) {
    id
    sourceUri
    targetUri
    linkType
    createdOn
  }
}]]></ac:plain-text-body></ac:structured-macro>

<h2>Data flow</h2>
<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">text</ac:parameter><ac:plain-text-body><![CDATA[MCP Jira / Co//ab
       |
       |  GraphQL POST /lmw/graphql
       |  mutation: createNewLink
       |  linkType: VALIDATES_ARCHITECTURE_ELEMENT
       v
GraphQLController
       |
       v
LinkService.createNewLink()
       |  UsecaseUtil resolves:
       |  Jira source + Rhapsody AM target + VALIDATES_ARCHITECTURE_ELEMENT
       |  -> JIRA_TO_RHAPSODY_QM_MUTATION_USE_CASE
       v
LinkCreationService.createQmToAmLink()
       |
       +---> Graph DB (Neptune / TinkerGraph)
       |       Creates QM vertex (Jira test case)
       |       Creates AM vertex (Rhapsody element)
       |       Creates edge: VALIDATES_ARCHITECTURE_ELEMENT
       |
       +---> Redis (TRS Change Event)
               Publishes creation event for LDX]]></ac:plain-text-body></ac:structured-macro>

<h2>Consequences</h2>
<h3>Positive</h3>
<ul>
  <li><strong>ASPICE traceability</strong>: Test cases in MCP Jira/Xray can now be explicitly linked to Rhapsody architecture elements (states, transitions, decision nodes, scenarios).</li>
  <li><strong>Migration enablement</strong>: The STARC Migration Tool (Codebeamer &rarr; MCP Jira) can now transfer existing <em>Validates Architectural Element</em> links via LMW GraphQL endpoints.</li>
  <li><strong>Minimal blast radius</strong>: Only 3 files changed, reusing all existing infrastructure for JIRA_TO_RHAPSODY_QM_MUTATION_USE_CASE.</li>
  <li><strong>Query correctness</strong>: findLinksBySourceUri now returns VALIDATES_ARCHITECTURE_ELEMENT links alongside other AM-target link types.</li>
  <li><strong>No schema migration required</strong>: The graph database schema is unchanged; the new link type is a valid edge label on the existing AM vertex model.</li>
</ul>
<h3>Negative</h3>
<ul>
  <li><strong>Unidirectional link creation only</strong>: Links can only be created from MCP Jira (QM) to Rhapsody (AM) via GraphQL. The reverse direction is not possible because the Rhapsody delegated UI does not render inside LMW.</li>
  <li><strong>No OSLC PUT/GET support</strong>: Unlike CM&rarr;RM and QM&rarr;RM use cases, there is no OSLC-driven link creation for this path. Consumers must use the GraphQL API.</li>
</ul>

<h2>References</h2>
<table>
  <tbody>
    <tr><th>Reference</th><th>Description</th></tr>
    <tr><td><a href="https://mercedes-benz.atlassian.net/browse/BDATART-686">BDATART-686</a></td><td>Feature: Implement linking between Rhapsody Architecture Elements and MCP X-Ray test cases</td></tr>
    <tr><td><a href="https://mercedes-benz.atlassian.net/browse/BDRSP-1418">BDRSP-1418</a></td><td>Story: Implementation picked up as BDRSP-1418</td></tr>
    <tr><td><a href="https://mercedes-benz.atlassian.net/browse/BDRSP-1387">BDRSP-1387</a></td><td>ForgeApp new LinkType configuration (Resolved dependency)</td></tr>
    <tr><td><a href="https://mercedes-benz.atlassian.net/wiki/spaces/BDATARTINT/pages/2717732629">ADR-002</a></td><td>Link Management: Creation, Deletion, and Storage (parent ADR)</td></tr>
    <tr><td><a href="https://mercedes-benz.atlassian.net/wiki/spaces/BDATARTINT/pages/2717732595">ADR-001</a></td><td>Linking Middleware Architecture</td></tr>
    <tr><td>MR !257</td><td>fix/BDATART-686-linking-implement-between-rhapsody-architecture</td></tr>
  </tbody>
</table>
"""


def main():
    print(f"Publishing ADR to Confluence...")
    print(f"  Space  : {SPACE_KEY}")
    print(f"  Parent : {PARENT_ID} (ADR folder)")
    print(f"  Title  : {TITLE}")
    print()

    payload = {
        "type": "page",
        "title": TITLE,
        "ancestors": [{"id": PARENT_ID}],
        "space": {"key": SPACE_KEY},
        "body": {
            "storage": {
                "value": BODY,
                "representation": "storage"
            }
        }
    }

    resp = requests.post(
        f"{BASE_URL}/wiki/rest/api/content",
        json=payload,
        auth=auth,
        headers=headers,
        verify=False,
    )

    if resp.status_code in (200, 201):
        data = resp.json()
        page_id = data["id"]
        url = f"{BASE_URL}/wiki/spaces/{SPACE_KEY}/pages/{page_id}"
        print(f"✅ ADR published successfully!")
        print(f"   Page ID : {page_id}")
        print(f"   URL     : {url}")
    else:
        print(f"❌ Failed to publish: HTTP {resp.status_code}")
        print(resp.text[:1000])
        sys.exit(1)


if __name__ == "__main__":
    main()

