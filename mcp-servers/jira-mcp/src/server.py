#!/usr/bin/env python3
"""Jira MCP server for JiraForge Agent."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.append(str(Path(__file__).resolve().parents[2] / "common"))
from jira_client import (
    analyze_story,
    bootstrap_feature_stories,
    check_connection,
    create_feature_stories,
    create_story,
    delete_subtasks,
    feature_context,
    json_response,
    manage_subtasks,
    plan_feature_stories,
    read_issue,
    refine_story,
    search_stories,
)
from workflow_memory import execute_workflow


mcp = FastMCP("jira-mcp")


@mcp.tool()
async def jira_check_connection() -> str:
    """Validate Jira credentials and return the connected user."""
    return json_response(execute_workflow(server_name="jira-mcp", tool_name="jira_check_connection", memory_key="connection", inputs={}, operation=check_connection))


@mcp.tool()
async def jira_read_story(jira_id: str) -> str:
    """Fetch full Jira issue/story details by Jira ID/key."""
    return json_response(execute_workflow(server_name="jira-mcp", tool_name="jira_read_story", memory_key=jira_id, inputs={"jira_id": jira_id}, operation=lambda: read_issue(jira_id)))


@mcp.tool()
async def jira_analyze_story(jira_id: str) -> str:
    """Fetch and analyze a Jira story, including ACs, gaps, and clearer wording suggestions."""
    def operation() -> dict:
        story = read_issue(jira_id)
        return {"story": story, "analysis": analyze_story(story)}

    return json_response(execute_workflow(server_name="jira-mcp", tool_name="jira_analyze_story", memory_key=f"{jira_id}-analysis", inputs={"jira_id": jira_id}, operation=operation))


@mcp.tool()
async def jira_feature_context(jira_id: str) -> str:
    """Fetch the parent Feature and sibling story context before story writing."""
    return json_response(execute_workflow(server_name="jira-mcp", tool_name="jira_feature_context", memory_key=f"{jira_id}-feature", inputs={"jira_id": jira_id}, operation=lambda: feature_context(jira_id)))


@mcp.tool()
async def jira_plan_subtasks(jira_id: str) -> str:
    """Suggest minimal subtasks for a Jira story without creating them."""
    def operation() -> dict:
        story = read_issue(jira_id)
        analysis = analyze_story(story)
        strategy = analysis["subtask_creation_guidance"]["suggested_strategy"]
        if not strategy:
            return {
                "story_key": story.get("key"),
                "rule": "No subtasks are recommended by default for this story.",
                "subtasks": [],
                "recommendation": "Keep this as one story unless the user identifies specific independent work items.",
                "approval_required": "Ask the user before creating any Jira subtask.",
            }
        subtasks = [{"title": f"{story['key']}: {step}", "purpose": f"Handle the {step.lower()} part of the story only if this work is actually required."} for step in strategy]
        return {"story_key": story.get("key"), "rule": "These are suggestions only. Jira MCP tools do not create subtasks.", "subtasks": subtasks, "approval_required": "Ask the user whether these suggestions are useful. Create them manually in Jira if approved."}

    return json_response(execute_workflow(server_name="jira-mcp", tool_name="jira_plan_subtasks", memory_key=f"{jira_id}-subtasks-plan", inputs={"jira_id": jira_id}, operation=operation))


@mcp.tool()
async def jira_refine_story(
    jira_id: str,
    apply_update: bool = False,
    codebase_scan_confirmed: bool = False,
    approved_summary: str | None = None,
    approved_description: str | None = None,
    approved_acceptance_criteria: list[str] | None = None,
    approved_regulatory_justification: str | None = None,
    approved_reason_comments: str | None = None,
    approved_comment: str | None = None,
    confirm_apply_generated_proposal: bool = False,
) -> str:
    """Suggest a professional Jira title, BDRSP-1623 description, and ACs; update story fields only when approved.

    MANDATORY before apply_update=True:
    1. jira_feature_context must be called — parent Feature, Feature goal, and ALL sibling stories must be loaded.
    2. Codebase must be scanned for story and Feature keywords — which files are impacted must be known.
    3. Set codebase_scan_confirmed=True only after both checks above are done.

    Calling with apply_update=True and codebase_scan_confirmed=False is BLOCKED — the tool returns a
    mandatory_sequence with step-by-step instructions.

    MANDATORY FORMATTING for proposal and approved_description (BDRSP-1623 style profile):
    The description MUST use rich markdown syntax so Jira renders colored panels, tables, and formatted lists.
    This style profile is enforced in proposal mode and at update time.
    Use these formatting rules ALWAYS:

    - Section headings: ## Heading
    - Colored panels: :::info (blue), :::success (green), :::warning (yellow), :::note (gray), :::error (red)
      Close with ::: on its own line.
    - Tables: | Col1 | Col2 | with |---| separator row
    - Bullet lists: - item (grouped, no blank lines between items)
    - Ordered lists: 1. item
    - Bold: **text**
    - Inline code: `text`

    Example approved_description structure:
      ## User Story
      As a developer, I want to ...

      :::info
      ## Background
      Context here with **bold** and `code`.
      :::

      ## Key Data
      | Property | Value |
      |---|---|
      | Name | value |

      :::success
      ## Implementation Scope
      1. File one
      2. File two
      :::

      :::warning
      ## Constraints
      - Constraint one
      - Constraint two
      :::

      :::note
      ## Acceptance Criteria
      - Criteria one
      - Criteria two
      :::
    """
    inputs = {
        "jira_id": jira_id,
        "apply_update": apply_update,
        "codebase_scan_confirmed": codebase_scan_confirmed,
        "approved_summary": approved_summary,
        "approved_description": approved_description,
        "approved_acceptance_criteria": approved_acceptance_criteria,
        "approved_regulatory_justification": approved_regulatory_justification,
        "approved_reason_comments": approved_reason_comments,
        "approved_comment": bool(approved_comment),
        "confirm_apply_generated_proposal": confirm_apply_generated_proposal,
    }
    return json_response(
        execute_workflow(
            server_name="jira-mcp",
            tool_name="jira_refine_story",
            memory_key=f"{jira_id}-refine",
            inputs=inputs,
            operation=lambda: refine_story(
                jira_id,
                apply_update=apply_update,
                codebase_scan_confirmed=codebase_scan_confirmed,
                approved_summary=approved_summary,
                approved_description=approved_description,
                approved_acceptance_criteria=approved_acceptance_criteria,
                approved_regulatory_justification=approved_regulatory_justification,
                approved_reason_comments=approved_reason_comments,
                approved_comment=approved_comment,
                confirm_apply_generated_proposal=confirm_apply_generated_proposal,
            ),
        )
    )


@mcp.tool()
async def jira_search_stories(
    jql: str,
    max_results: int = 30,
    only_with_sprint: bool = False,
    include_excluded_without_sprint_count: bool = False,
) -> str:
    """Search Jira stories by JQL (e.g. 'project = BDRSP AND status = "In Progress"').
    Returns a compact list. Use jira_read_story for full details on any result.

    Optional filters:
    - only_with_sprint=True: include only issues that have at least one sprint.
    - include_excluded_without_sprint_count=True: include transparency count for filtered-out issues.
    """
    return json_response(
        execute_workflow(
            server_name="jira-mcp",
            tool_name="jira_search_stories",
            memory_key=f"search-{jql[:40]}",
            inputs={
                "jql": jql,
                "max_results": max_results,
                "only_with_sprint": only_with_sprint,
                "include_excluded_without_sprint_count": include_excluded_without_sprint_count,
            },
            operation=lambda: search_stories(
                jql,
                max_results=max_results,
                only_with_sprint=only_with_sprint,
                include_excluded_without_sprint_count=include_excluded_without_sprint_count,
            ),
        )
    )


@mcp.tool()
async def jira_plan_feature_stories(feature_key: str, max_results: int = 200) -> str:
    """Learn a Feature end-to-end and return sprint-wise story planning guidance.

    Includes:
    - feature understanding from Jira
    - sprint-wise current stories
    - confluence cache summary
    - repo knowledge-graph summary
    - story creation playbook and style contract
    """
    return json_response(
        execute_workflow(
            server_name="jira-mcp",
            tool_name="jira_plan_feature_stories",
            memory_key=f"feature-plan-{feature_key}",
            inputs={"feature_key": feature_key, "max_results": max_results},
            operation=lambda: plan_feature_stories(feature_key, max_results=max_results),
        )
    )


@mcp.tool()
async def jira_bootstrap_feature_stories(
    feature_key: str,
    project_key: str | None = None,
    sprint_id: str | None = None,
    sprint_name: str | None = None,
    sprint_by_phase: dict | None = None,
    phases: list[str] | None = None,
    create_missing_phases_when_existing: bool = False,
    confirm_create: bool = False,
    max_results: int = 200,
) -> str:
    """Complete Feature story bootstrap flow.

    Use this when the user says "create stories for this Feature" and no stories are already defined.

    Flow:
    1. Read the Feature from Jira and treat feature_key as parent_key.
    2. Discover existing child stories through parent, Epic Link, Parent Link, linked issues, and Feature subtasks.
    3. Read cached Confluence/ADR context and repo knowledge graph summary.
    4. If no child stories exist, generate the complete BDRSP-1623 story set across delivery phases.
    5. Require sprint_id/sprint_name or sprint_by_phase before preview/create. If sprint is missing, ask the user.
    6. Preview first with confirm_create=False.
    7. Create only after explicit user approval with confirm_create=True.
    """
    return json_response(
        execute_workflow(
            server_name="jira-mcp",
            tool_name="jira_bootstrap_feature_stories",
            memory_key=f"feature-bootstrap-stories-{feature_key}",
            inputs={
                "feature_key": feature_key,
                "project_key": project_key,
                "has_sprint_id": bool(sprint_id),
                "has_sprint_name": bool(sprint_name),
                "has_sprint_by_phase": bool(sprint_by_phase),
                "phase_count": len(phases or []),
                "create_missing_phases_when_existing": create_missing_phases_when_existing,
                "confirm_create": confirm_create,
                "max_results": max_results,
            },
            operation=lambda: bootstrap_feature_stories(
                feature_key=feature_key,
                project_key=project_key,
                sprint_id=sprint_id,
                sprint_name=sprint_name,
                sprint_by_phase=sprint_by_phase,
                phases=phases,
                create_missing_phases_when_existing=create_missing_phases_when_existing,
                confirm_create=confirm_create,
                max_results=max_results,
            ),
        )
    )


@mcp.tool()
async def jira_create_feature_stories(
    project_key: str,
    parent_key: str,
    stories: list[dict],
    confirm_create: bool = False,
    codebase_scan_confirmed: bool = False,
    feature_context_confirmed: bool = False,
) -> str:
    """Preview or create all approved stories for a Feature.

    Required flow:
    1. Treat parent_key as the parent Feature key and call jira_plan_feature_stories(parent_key).
    2. Review Confluence cache excerpts and repo graph/codebase impact.
    3. Provide approved story payloads in the mandatory BDRSP-1623 style.
    4. Each story payload must include the user-selected sprint_id, or sprint_name with JIRA_BOARD_ID/JIRA_BOARD_IDS configured.
    5. If sprint is missing or ambiguous, ask the user which sprint to attach; do not guess.
    6. Call with confirm_create=False first for preview.
    7. Call with confirm_create=True only after explicit user approval.
    """
    return json_response(
        execute_workflow(
            server_name="jira-mcp",
            tool_name="jira_create_feature_stories",
            memory_key=f"feature-create-stories-{parent_key}",
            inputs={
                "project_key": project_key,
                "parent_key": parent_key,
                "story_count": len(stories or []),
                "confirm_create": confirm_create,
                "codebase_scan_confirmed": codebase_scan_confirmed,
                "feature_context_confirmed": feature_context_confirmed,
            },
            operation=lambda: create_feature_stories(
                project_key=project_key,
                parent_key=parent_key,
                stories=stories,
                confirm_create=confirm_create,
                codebase_scan_confirmed=codebase_scan_confirmed,
                feature_context_confirmed=feature_context_confirmed,
            ),
        )
    )


@mcp.tool()
async def jira_create_story(
    project_key: str,
    parent_key: str,
    summary: str,
    approved_description: str,
    approved_acceptance_criteria: list[str] | None = None,
    approved_regulatory_justification: str | None = None,
    approved_reason_comments: str | None = None,
    issue_type: str = "Story",
    priority: str = "Medium",
    assignee_account_id: str | None = None,
    approved_comment: str | None = None,
    sprint_id: str | None = None,
    sprint_name: str | None = None,
    confirm_create: bool = False,
    codebase_scan_confirmed: bool = False,
    feature_context_confirmed: bool = False,
) -> str:
    """Create a new Jira story under a parent Feature/Epic with full guardrails.

    MANDATORY SEQUENCE before confirm_create=True:
    1. Treat parent_key as the parent Feature key, then call jira_feature_context(parent_key).
    2. Scan the codebase for the story keywords — know which files it touches.
    3. Set feature_context_confirmed=True and codebase_scan_confirmed=True.
    4. Write approved_description following the BDRSP-1623 style profile
       (## headings, :::info/success/warning/note panels, at least one table).
    5. Provide the user-selected sprint_id, or sprint_name with JIRA_BOARD_ID/JIRA_BOARD_IDS configured. Sprint assignment is mandatory.
    6. If sprint is missing or ambiguous, ask the user which sprint to attach; do not guess.
    7. Call with confirm_create=False first to see the preview.
    8. Call with confirm_create=True only after the user explicitly approves.

    BLOCKED if any of the above steps are skipped.
    """
    inputs = {
        "project_key": project_key,
        "parent_key": parent_key,
        "summary": summary,
        "has_description": bool(approved_description),
        "has_ac": bool(approved_acceptance_criteria),
        "confirm_create": confirm_create,
        "codebase_scan_confirmed": codebase_scan_confirmed,
        "feature_context_confirmed": feature_context_confirmed,
        "issue_type": issue_type,
        "priority": priority,
        "sprint_id": sprint_id,
        "sprint_name": sprint_name,
    }
    return json_response(
        execute_workflow(
            server_name="jira-mcp",
            tool_name="jira_create_story",
            memory_key=f"{parent_key}-create-story",
            inputs=inputs,
            operation=lambda: create_story(
                project_key=project_key,
                parent_key=parent_key,
                summary=summary,
                approved_description=approved_description,
                approved_acceptance_criteria=approved_acceptance_criteria,
                approved_regulatory_justification=approved_regulatory_justification,
                approved_reason_comments=approved_reason_comments,
                issue_type=issue_type,
                priority=priority,
                assignee_account_id=assignee_account_id,
                approved_comment=approved_comment,
                sprint_id=sprint_id,
                sprint_name=sprint_name,
                confirm_create=confirm_create,
                codebase_scan_confirmed=codebase_scan_confirmed,
                feature_context_confirmed=feature_context_confirmed,
            ),
        )
    )


@mcp.prompt()
async def jira_story_readiness_workflow() -> str:
    """Full Jira profile workflow — read, search, create, refine, subtasks — with memory + guardrails."""
    return """# JiraForge — Full Story Workflow (jira profile)

You are connected via the **jira profile**. You can read, search, create, refine, and manage subtasks for any Jira story.

## Knowledge Packet — always present in every response

Every tool response includes:
- `runtime_build_marker: "knowledge-packet-v1"` — confirms memory is loaded
- `local_brain` — routing decision, repo graph health, story memory, confluence cache
- `knowledge_packet_sections` — which memory sections were loaded (story, repo_graph, confluence_cache, etc.)
- `workflow.repo_cognition_preflight` — repo health, hotspots, layer index

**Always read `local_brain` from every response** before deciding next action:
- `local_brain.story_memory.stage` — where this story is in the DevFlow lifecycle
- `local_brain.story_memory.next_gate` — what gate was last hit
- `local_brain.repo_graph.health` — repo coupling/health signal
- `local_brain.confluence_summary` — cached ADR pages available for context

---

## Available Tools

| Tool | Purpose |
|---|---|
| `jira_check_connection` | Verify Jira credentials |
| `jira_read_story` | Full story details by key |
| `jira_search_stories` | JQL search — list matching stories |
| `jira_analyze_story` | Gaps, AC suggestions, impacted areas |
| `jira_plan_feature_stories` | Learn a Feature and current sprint-wise story pattern |
| `jira_bootstrap_feature_stories` | Complete Feature story bootstrap: learn, draft, preview, create after approval |
| `jira_feature_context` | Parent Feature + all sibling stories |
| `jira_refine_story` | Propose + apply story field updates |
| `jira_create_story` | Create a new story under a Feature |
| `jira_create_feature_stories` | Preview + create approved sprint-wise Feature story batch |
| `jira_plan_subtasks` | Suggest subtasks (no creation) |
| `jira_manage_subtasks` | Create/update subtasks after approval |
| `jira_delete_subtasks` | Preview + delete subtasks after confirmation |

---

## Workflow A — Refine / Update an existing story

:::info
### Mandatory sequence (every step is non-negotiable)
1. `jira_read_story(jira_id)` — load current fields
2. `jira_feature_context(jira_id)` — load parent Feature goal + ALL sibling stories
   - If `ok=false` → STOP. Ask user to confirm which Feature this story belongs to.
3. Scan the codebase for story + Feature keywords
   - If no files match → STOP. Tell user: "I need more codebase context before updating Jira."
4. `jira_refine_story(jira_id, apply_update=False)` — show the BDRSP-1623 formatted proposal to user
5. Ask user: *"I loaded Feature [KEY]: [name], [N] sibling stories, and scanned the codebase. Do you approve applying these changes?"*
6. `jira_refine_story(jira_id, apply_update=True, codebase_scan_confirmed=True, approved_summary=..., approved_description=<exact BDRSP-1623 description>, approved_acceptance_criteria=[...], ...)` — apply only after YES
:::

:::warning
### refinement description MUST follow BDRSP-1623 style profile
- `## Heading` for every section
- `:::info` (blue), `:::success` (green), `:::warning` (yellow), `:::note` (gray) panels — MANDATORY
- At least one `| table |` with `|---|` separator
- `**bold**` and `` `code` `` for emphasis
- Close every panel with `:::` on its own line
- NEVER send plain prose — every major section MUST be inside a colored panel
:::

---

## Workflow B — Create a new story

:::success
### Mandatory sequence for feature-level story creation
1. `jira_bootstrap_feature_stories(feature_key, confirm_create=False)` — learn Feature goal, discover existing child stories, read Confluence/ADR cache, read repo graph, and draft BDRSP-1623 stories when no stories exist
2. Treat the Feature key as `parent_key` for every generated story
3. If `mode=existing_stories_found` → show existing sprint-wise stories and ask whether to fill missing phases
4. If `mode=sprint_required` → ask for `sprint_id`, `sprint_name`, or `sprint_by_phase`; never guess
5. If `mode=bootstrap_preview` → show the exact preview and ask for explicit YES
6. `jira_bootstrap_feature_stories(..., confirm_create=True)` — create approved batch after YES
:::

:::note
**Blocked if skipped:**
- `feature_context_confirmed=False` → blocked until Feature is read
- `codebase_scan_confirmed=False` and no `approved_description` → blocked until scan done
- missing `sprint_id` / resolvable `sprint_name` → blocked until sprint target is supplied
- unclear sprint target → ask the user; do not infer or guess
- `confirm_create=False` → always shows preview first, never creates silently
:::

For a single story under a Feature, use the same gates with `jira_create_story`.

---

## Workflow C — Manage subtasks

1. `jira_plan_subtasks(jira_id)` — shows suggestions, never creates
2. Show suggestions to user — ask which ones to create
3. `jira_manage_subtasks(jira_id, proposed_subtasks=[...], apply_changes=False)` — plan with duplicate check
4. Show plan with `action_id`s to user — ask to approve specific IDs
5. `jira_manage_subtasks(jira_id, ..., apply_changes=True, confirm_apply=True, approved_action_ids=[...])` — apply approved only

---

## Hard rules that always apply

:::warning
- **Never** call `apply_update=True` or `confirm_create=True` without **explicit user YES**
- **Never** skip `jira_feature_context` — Feature context is mandatory before any write
- **Never** create subtasks automatically — always plan first, then apply approved IDs only
- **Never** delete subtasks without previewing them and getting key-by-key confirmation
- **Never** write a story description as plain text — BDRSP-1623 panel format is mandatory
- **Never** create a Jira story without `sprint_id` or resolvable `sprint_name`
- A story update/create must be aligned with the **parent Feature goal**, **completed sibling stories**, and **codebase reality**
- If `local_brain.story_memory.readiness_blockers` is non-empty → show blockers to user before proceeding
:::
"""


@mcp.tool()
async def jira_delete_subtasks(issue_keys: list[str], confirm_delete: bool = False) -> str:
    """Preview and delete Jira subtasks only after explicit confirmation."""
    return json_response(execute_workflow(server_name="jira-mcp", tool_name="jira_delete_subtasks", memory_key="delete-subtasks", inputs={"issue_keys": issue_keys, "confirm_delete": confirm_delete}, operation=lambda: delete_subtasks(issue_keys, confirm_delete=confirm_delete)))


@mcp.tool()
async def jira_manage_subtasks(
    jira_id: str,
    proposed_subtasks: list[dict[str, str]],
    apply_changes: bool = False,
    confirm_apply: bool = False,
    approved_action_ids: list[str] | None = None,
) -> str:
    """Plan and safely create/update Jira subtasks after duplicate checks and explicit per-action approval."""
    return json_response(
        execute_workflow(
            server_name="jira-mcp",
            tool_name="jira_manage_subtasks",
            memory_key=f"{jira_id}-manage-subtasks",
            inputs={"jira_id": jira_id, "proposed_subtasks": proposed_subtasks, "apply_changes": apply_changes, "confirm_apply": confirm_apply, "approved_action_ids": approved_action_ids},
            operation=lambda: manage_subtasks(
            jira_id,
            proposed_subtasks,
            apply_changes=apply_changes,
            confirm_apply=confirm_apply,
            approved_action_ids=approved_action_ids,
            ),
        )
    )


if __name__ == "__main__":
    mcp.run()
