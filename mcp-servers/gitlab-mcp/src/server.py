#!/usr/bin/env python3
"""GitLab MCP server for GitBridge Agent."""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.append(str(Path(__file__).resolve().parents[2] / "common"))
from gitlab_client import (
    check_connection,
    check_token_health,
    create_branch,
    create_merge_request,
    get_merge_request,
    get_merge_request_changes,
    get_merge_request_discussions,
    get_pipeline_status,
    git_status,
    json_response,
    list_local_branches,
    list_merge_requests,
    prepare_merge_request,
    push_branch,
    safe_create_merge_request,
)
from gitlab_client import jiraforge_code_updater as run_jiraforge_code_updater
from workflow_memory import execute_workflow


mcp = FastMCP("gitlab-mcp")


async def resolve_working_directory() -> str | None:
    """Resolve the current MCP root so local Git tools act on the user's project."""
    try:
        context = mcp.get_context()
        session = getattr(context, "session", None)
        if not session or not hasattr(session, "list_roots"):
            return None
        roots_result = await session.list_roots()
        roots = getattr(roots_result, "roots", []) or []
        if not roots:
            return None
        uri = getattr(roots[0], "uri", "")
        if isinstance(uri, str) and uri.startswith("file://"):
            return urllib.parse.unquote(uri[len("file://"):])
    except Exception:
        return None
    return None


# ── Connection & token ────────────────────────────────────────────────────────

@mcp.tool()
async def gitlab_check_connection(
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> str:
    """Validate GitLab credentials and project access."""
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_check_connection",
        memory_key="connection",
        inputs={"gitlab_url": gitlab_url, "project_id": project_id, "token": bool(token)},
        operation=lambda: check_connection(gitlab_url=gitlab_url, project_id=project_id, token=token),
    ))


@mcp.tool()
async def gitlab_check_token_health(
    gitlab_url: str | None = None,
    token: str | None = None,
) -> str:
    """Check whether the GitLab token is valid, when it expires, and how many days remain.

    Call this whenever a GitLab API call fails with 401/403 or when you suspect the token might be stale.
    Returns token_status (valid/expiring_soon/expired/revoked/missing), days_remaining, and renewal_instruction.
    """
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_check_token_health",
        memory_key="token-health",
        inputs={"gitlab_url": gitlab_url, "token": bool(token)},
        operation=lambda: check_token_health(gitlab_url=gitlab_url, token=token),
    ))


# ── Git local state ───────────────────────────────────────────────────────────

@mcp.tool()
async def gitlab_git_status() -> str:
    """Show current git status: branch, local changes, tracking branch, recent commits, ahead/behind."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_git_status",
        memory_key="git-status",
        inputs={"working_dir": working_dir},
        operation=lambda: git_status(working_dir),
    ))


@mcp.tool()
async def gitlab_list_branches() -> str:
    """List all local branches with tracking info, and top 20 remote branches."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_list_branches",
        memory_key="list-branches",
        inputs={"working_dir": working_dir},
        operation=lambda: list_local_branches(working_dir),
    ))


# ── Branch management ─────────────────────────────────────────────────────────

@mcp.tool()
async def gitlab_create_branch(
    description: str,
    base_branch: str = "main",
    story_id: str | None = None,
    branch_type: str = "feature",
    checkout: bool = True,
) -> str:
    """Create a local Git branch from freshly fetched origin/<base_branch>; never deletes branches."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_create_branch",
        memory_key=f"branch-{story_id or description}",
        inputs={"description": description, "base_branch": base_branch, "story_id": story_id,
                "branch_type": branch_type, "checkout": checkout, "working_dir": working_dir},
        operation=lambda: create_branch(
            description=description, base_branch=base_branch, story_id=story_id,
            branch_type=branch_type, working_dir=working_dir, checkout=checkout,
        ),
    ))


# ── Merge request tools ───────────────────────────────────────────────────────

@mcp.tool()
async def gitlab_list_merge_requests(
    state: str = "opened",
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> str:
    """List GitLab MRs for the project. state = opened | merged | closed | all."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_list_merge_requests",
        memory_key=f"list-mrs-{state}",
        inputs={"state": state, "gitlab_url": gitlab_url, "project_id": project_id, "token": bool(token)},
        operation=lambda: list_merge_requests(
            state, working_dir=working_dir, gitlab_url=gitlab_url,
            project_id=project_id, token=token,
        ),
    ))


@mcp.tool()
async def gitlab_get_merge_request(
    mr_iid: int,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> str:
    """Fetch merge request metadata, branches, reviewers, state, and pipeline summary."""
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_get_merge_request",
        memory_key=f"mr-{mr_iid}",
        inputs={"mr_iid": mr_iid, "gitlab_url": gitlab_url, "project_id": project_id, "token": bool(token)},
        operation=lambda: get_merge_request(mr_iid, gitlab_url=gitlab_url, project_id=project_id, token=token),
    ))


@mcp.tool()
async def gitlab_get_merge_request_changes(
    mr_iid: int,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> str:
    """Fetch changed files and diff metadata for a merge request."""
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_get_merge_request_changes",
        memory_key=f"mr-{mr_iid}-changes",
        inputs={"mr_iid": mr_iid, "gitlab_url": gitlab_url, "project_id": project_id, "token": bool(token)},
        operation=lambda: get_merge_request_changes(mr_iid, gitlab_url=gitlab_url, project_id=project_id, token=token),
    ))


@mcp.tool()
async def gitlab_get_merge_request_discussions(
    mr_iid: int,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> str:
    """Fetch merge request discussions and unresolved thread summary."""
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_get_merge_request_discussions",
        memory_key=f"mr-{mr_iid}-discussions",
        inputs={"mr_iid": mr_iid, "gitlab_url": gitlab_url, "project_id": project_id, "token": bool(token)},
        operation=lambda: get_merge_request_discussions(mr_iid, gitlab_url=gitlab_url, project_id=project_id, token=token),
    ))


@mcp.tool()
async def gitlab_get_pipeline_status(
    mr_iid: int,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> str:
    """Fetch the merge request head pipeline and recent pipeline statuses."""
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_get_pipeline_status",
        memory_key=f"mr-{mr_iid}-pipeline",
        inputs={"mr_iid": mr_iid, "gitlab_url": gitlab_url, "project_id": project_id, "token": bool(token)},
        operation=lambda: get_pipeline_status(mr_iid, gitlab_url=gitlab_url, project_id=project_id, token=token),
    ))


@mcp.tool()
async def gitlab_prepare_merge_request(
    story_id: str,
    story_summary: str,
    change_type: str = "feature",
    target_branch: str = "main",
    testing_notes: str = "",
) -> str:
    """Prepare a professional MR title and description from the current branch without pushing or creating the MR."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_prepare_merge_request",
        memory_key=f"prepare-mr-{story_id}",
        inputs={"story_id": story_id, "story_summary": story_summary, "change_type": change_type,
                "target_branch": target_branch, "testing_notes": testing_notes, "working_dir": working_dir},
        operation=lambda: prepare_merge_request(
            story_id=story_id, story_summary=story_summary, change_type=change_type,
            target_branch=target_branch, working_dir=working_dir, testing_notes=testing_notes,
        ),
    ))


@mcp.tool()
async def gitlab_push_branch(
    branch: str | None = None,
    remote: str = "origin",
    set_upstream: bool = True,
    dry_run: bool = True,
) -> str:
    """Push a local branch to GitLab. Defaults to dry_run=True; set dry_run=False only after user approval."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_push_branch",
        memory_key=f"push-{branch or 'current'}",
        inputs={"branch": branch, "remote": remote, "set_upstream": set_upstream,
                "working_dir": working_dir, "dry_run": dry_run},
        operation=lambda: push_branch(
            branch=branch, remote=remote, set_upstream=set_upstream,
            working_dir=working_dir, dry_run=dry_run,
        ),
    ))


@mcp.tool()
async def gitlab_safe_create_merge_request(
    story_key: str,
    story_summary: str,
    source_branch: str | None = None,
    target_branch: str = "main",
    change_type: str = "feature",
    testing_notes: str = "",
    draft: bool = False,
    labels: list[str] | None = None,
    reviewer_usernames: list[str] | None = None,
    assignee_usernames: list[str] | None = None,
    remove_source_branch: bool = False,
    tests_done: bool = False,
    review_done: bool = False,
    confirm_create_mr: bool = False,
    mr_title: str | None = None,
    mr_description: str | None = None,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> str:
    """Create a GitLab MR with FULL safety guardrails.

    ALL of the following must be satisfied before the MR is created:
    1. tests_done=True      — tests were run and passed
    2. review_done=True     — code was reviewed against ACs
    3. Branch uses an approved prefix (feature/, fix/, chore/, hotfix/, etc.)
    4. Branch name contains the story key
    5. No uncommitted local changes in the working tree
    6. Default.md MR template was used for the description
    7. confirm_create_mr=True — user explicitly approved after reviewing the preview

    Call with confirm_create_mr=False first to see the MR preview.
    Call with confirm_create_mr=True only after the user says YES.

    This tool pushes the branch AND creates the MR in one safe operation.
    Use gitlab_push_branch + gitlab_create_merge_request separately only for advanced use.
    """
    working_dir = await resolve_working_directory()
    inputs = {
        "story_key": story_key, "story_summary": story_summary,
        "source_branch": source_branch, "target_branch": target_branch,
        "change_type": change_type, "tests_done": tests_done,
        "review_done": review_done, "confirm_create_mr": confirm_create_mr,
        "draft": draft, "working_dir": working_dir,
    }
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_safe_create_merge_request",
        memory_key=f"safe-mr-{story_key}",
        inputs=inputs,
        operation=lambda: safe_create_merge_request(
            story_key=story_key, story_summary=story_summary,
            source_branch=source_branch, target_branch=target_branch,
            change_type=change_type, testing_notes=testing_notes,
            draft=draft, labels=labels,
            reviewer_usernames=reviewer_usernames, assignee_usernames=assignee_usernames,
            remove_source_branch=remove_source_branch,
            tests_done=tests_done, review_done=review_done,
            confirm_create_mr=confirm_create_mr,
            mr_title=mr_title, mr_description=mr_description,
            gitlab_url=gitlab_url, project_id=project_id, token=token,
            working_dir=working_dir,
        ),
    ))


@mcp.tool()
async def gitlab_create_merge_request(
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    draft: bool = True,
    labels: list[str] | None = None,
    reviewer_usernames: list[str] | None = None,
    assignee_usernames: list[str] | None = None,
    remove_source_branch: bool = False,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> str:
    """Create a GitLab MR directly (advanced use). Prefer gitlab_safe_create_merge_request for new MRs."""
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="gitlab_create_merge_request",
        memory_key=f"create-mr-{source_branch}-{target_branch}",
        inputs={"source_branch": source_branch, "target_branch": target_branch, "title": title,
                "draft": draft, "labels": labels, "reviewer_usernames": reviewer_usernames,
                "assignee_usernames": assignee_usernames, "remove_source_branch": remove_source_branch,
                "gitlab_url": gitlab_url, "project_id": project_id, "token": bool(token)},
        operation=lambda: create_merge_request(
            source_branch=source_branch, target_branch=target_branch,
            title=title, description=description, draft=draft, labels=labels,
            reviewer_usernames=reviewer_usernames, assignee_usernames=assignee_usernames,
            remove_source_branch=remove_source_branch,
            gitlab_url=gitlab_url, project_id=project_id, token=token,
        ),
    ))


@mcp.tool()
async def jiraforge_code_updater(
    jira_id: str,
    selected_subtask_key: str | None = None,
    implement_whole_story: bool = False,
    base_branch: str = "main",
) -> str:
    """Prepare a safe Jira story/subtask implementation contract for lightweight code updates."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(
        server_name="gitlab-mcp", tool_name="jiraforge_code_updater",
        memory_key=f"code-updater-{jira_id}",
        inputs={"jira_id": jira_id, "selected_subtask_key": selected_subtask_key,
                "implement_whole_story": implement_whole_story, "base_branch": base_branch,
                "working_dir": working_dir},
        operation=lambda: run_jiraforge_code_updater(
            jira_id=jira_id, selected_subtask_key=selected_subtask_key,
            implement_whole_story=implement_whole_story, base_branch=base_branch,
            working_dir=working_dir,
        ),
    ))


# ── Prompts ───────────────────────────────────────────────────────────────────

@mcp.prompt()
async def gitlab_full_workflow() -> str:
    """Full GitLab profile workflow — git status, branches, MRs, safe creation — with knowledge + guardrails."""
    return """# GitBridge — Full GitLab Workflow (gitlab profile)

You are connected via the **gitlab profile**. You can inspect git state, manage branches, read/analyze MRs, and create MRs with full safety guardrails.

## Knowledge Packet — always present in every response

Every tool response includes:
- `runtime_build_marker: "knowledge-packet-v1"` — confirms memory is loaded
- `local_brain` — routing decision, repo graph health, story memory, confluence cache
- `knowledge_packet_sections` — sections loaded (repo_graph, story_memory, confluence_cache)
- `workflow.repo_cognition_preflight` — repo health, hotspots, architectural layers

**Always read `local_brain` from every response before deciding next action:**
- `local_brain.repo_graph.health` — repo coupling and health signal
- `local_brain.repo_graph.hotspots` — high-risk files to test carefully
- `local_brain.story_memory.stage` — where this story is in the DevFlow lifecycle
- `local_brain.story_memory.next_gate` — what was the last approved gate
- `local_brain.confluence_summary` — cached ADR pages available for context

---

## Available Tools

| Tool | Purpose |
|---|---|
| `gitlab_check_connection` | Verify GitLab credentials + project access |
| `gitlab_check_token_health` | Check token validity + days until expiry |
| `gitlab_git_status` | Current branch, local changes, tracking, ahead/behind |
| `gitlab_list_branches` | All local + top 20 remote branches |
| `gitlab_create_branch` | Create story branch from fresh origin/<base> |
| `gitlab_list_merge_requests` | List open/merged/closed MRs |
| `gitlab_get_merge_request` | Full MR metadata |
| `gitlab_get_merge_request_changes` | Changed files + diff |
| `gitlab_get_merge_request_discussions` | Discussions + unresolved threads |
| `gitlab_get_pipeline_status` | CI pipeline status |
| `gitlab_prepare_merge_request` | Build MR title + description from Default.md |
| `gitlab_push_branch` | Push branch (dry_run=True by default) |
| `gitlab_safe_create_merge_request` | **Preferred** — push + create MR with 7 guardrails |
| `gitlab_create_merge_request` | Advanced: create MR directly (no safety gates) |
| `jiraforge_code_updater` | Implementation contract from Jira story |

---

## Workflow A — Check git state before anything

:::info
Always call `gitlab_git_status` first when the user asks about branches, changes, or MRs.
- If `has_local_changes=true` → warn user to commit/stash before branch/push/MR operations
- If `current_branch` is a base branch (main/master/develop) → ask user which story branch to use
- Read `ahead_behind` to know if the branch needs a pull before push
:::

---

## Workflow B — Create a story branch

:::success
### Sequence
1. `gitlab_git_status` — confirm clean working tree + on base branch
2. `gitlab_create_branch(description=<story summary>, story_id=<JIRA-KEY>, base_branch="main")`
   - Branch is auto-named: `feature/BDRSP-XXXX-<slug>`
   - Created from freshly fetched `origin/main` — never from stale local state
3. Make code changes in the new branch
:::

---

## Workflow C — Create a Merge Request (safe path)

:::warning
### ALL 7 guards must pass before MR is created

| Guard | Parameter | Blocked if |
|---|---|---|
| Tests done | `tests_done=True` | Not set |
| Review done | `review_done=True` | Not set |
| Branch prefix | auto-checked | Not feature/fix/chore/etc. |
| Story key in branch | auto-checked | Branch doesn't contain story key |
| Clean working tree | auto-checked | Uncommitted changes exist |
| Default.md template | auto-checked | Template not found/used |
| Explicit approval | `confirm_create_mr=True` | Not set |
:::

:::success
### Step-by-step
1. `gitlab_git_status` — confirm branch + no local changes
2. `gitlab_prepare_merge_request(story_id, story_summary)` — build title + description from Default.md
3. Show preview to user — ask: *"Tests and review are done. Do you approve creating this MR?"*
4. `gitlab_safe_create_merge_request(story_key=..., story_summary=..., tests_done=True, review_done=True, confirm_create_mr=False)` — show preview
5. `gitlab_safe_create_merge_request(..., confirm_create_mr=True)` — create after YES

This tool pushes the branch AND creates the MR in one safe operation.
:::

---

## Workflow D — Review an existing MR

:::note
1. `gitlab_list_merge_requests(state="opened")` — find the MR iid
2. `gitlab_get_merge_request(mr_iid)` — metadata + pipeline
3. `gitlab_get_merge_request_changes(mr_iid)` — changed files
4. `gitlab_get_merge_request_discussions(mr_iid)` — unresolved threads
5. `gitlab_get_pipeline_status(mr_iid)` — CI status

Return: what changed / merge state / CI status / unresolved threads / next actions
:::

---

## Workflow E — Token expired

:::error
If any tool returns 401/403 or `token_status=expired`:
1. `gitlab_check_token_health` — see expiry details + renewal instructions
2. Follow the `renewal_instruction` in the response:
   - Go to `GITLAB_BASE_URL/-/user_settings/personal_access_tokens`
   - Create new token with `api + read_repository + write_repository` scopes
   - Run `./Automation/scripts/sync-gitlab-token.sh`
3. Retry the original operation
:::

---

## Hard rules that always apply

:::warning
- **Never** call `gitlab_push_branch(dry_run=False)` without explicit user YES
- **Never** call `gitlab_create_merge_request` without prior `gitlab_prepare_merge_request` and user approval
- **Always** use `gitlab_safe_create_merge_request` for new MRs — it enforces all guards
- **Never** push directly to `main`, `master`, or `develop`
- **Always** check `gitlab_git_status` for local changes before branch/push operations
- **Always** check `local_brain.repo_graph.hotspots` — high-coupling files need extra test coverage
- If `local_brain.story_memory.readiness_blockers` is non-empty → show blockers before proceeding
:::
"""


if __name__ == "__main__":
    mcp.run()
