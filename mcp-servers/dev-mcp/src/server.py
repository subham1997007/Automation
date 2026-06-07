#!/usr/bin/env python3
"""DevFlow MCP server with one end-to-end development orchestration tool."""

from __future__ import annotations

import urllib.parse

from mcp.server.fastmcp import FastMCP

from dev_client import dev_create_feature_stories as run_dev_create_feature_stories
from dev_client import dev_bootstrap_feature_stories as run_dev_bootstrap_feature_stories
from dev_client import dev_implement_story as run_dev_implement_story
from dev_client import dev_bootstrap_feature_stories as run_dev_bootstrap_feature_stories
from dev_client import dev_create_feature_stories as run_dev_create_feature_stories
from dev_client import dev_plan_feature_stories as run_dev_plan_feature_stories
from dev_client import json_response


mcp = FastMCP("dev-mcp")


def _prewarm_langchain() -> None:
    """Pre-warm sentence-transformer model and FAISS index on server startup.

    This eliminates the 10-15s cold-start penalty on the first story request.
    Runs in a background thread so it does not delay server boot.
    """
    import threading

    def _warm() -> None:
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(__file__))
            from langchain_helpers.repo_resolver import get_resolver
            from langchain_helpers.code_vectorstore import CodeVectorStore

            # Auto-detect repo root — works in any project
            resolver = get_resolver()
            repo = str(resolver.repo_root)

            vs = CodeVectorStore(repo_path=repo)
            vs.search("link type mutation usecase routing", k=1)
        except Exception:
            pass

    threading.Thread(target=_warm, daemon=True, name="langchain-prewarm").start()


_prewarm_langchain()


async def resolve_working_directory() -> str | None:
    """Resolve the current MCP root so Git helpers act on the user's project."""
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
            return urllib.parse.unquote(uri[len("file://") :])
    except Exception:
        return None
    return None


@mcp.tool()
async def dev_bootstrap_feature_stories(
    feature_key: str,
    confirm_create: bool = False,
    sprint_id: int | None = None,
    sprint_name: str | None = None,
    sprint_by_phase: dict[str, dict[str, str | int]] | None = None,
    max_results: int = 200,
) -> str:
    """Bootstrap feature stories in DevFlow: learn context, detect existing stories, preview missing-phase stories, and gate creation by approval."""
    return json_response(
        run_dev_bootstrap_feature_stories(
            feature_key=feature_key,
            confirm_create=confirm_create,
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            sprint_by_phase=sprint_by_phase,
            max_results=max_results,
        )
    )


@mcp.tool()
async def dev_create_feature_stories(
    feature_key: str,
    confirm_create: bool = False,
    approved_phase_ids: list[str] | None = None,
) -> str:
    """Create approved feature stories from the latest DevFlow bootstrap preview."""
    return json_response(
        run_dev_create_feature_stories(
            feature_key=feature_key,
            confirm_create=confirm_create,
            approved_phase_ids=approved_phase_ids,
        )
    )


@mcp.tool()
async def dev_plan_feature_stories(
    feature_key: str,
    user_request: str = "",
    max_results: int = 200,
) -> str:
    """Learn a feature and return sprint-wise story planning guidance for DevFlow."""
    working_dir = await resolve_working_directory()
    return json_response(
        run_dev_plan_feature_stories(
            feature_key=feature_key,
            user_request=user_request,
            working_dir=working_dir,
            max_results=max_results,
        )
    )


@mcp.tool()
async def dev_create_feature_stories(
    project_key: str,
    parent_key: str,
    stories: list[dict],
    confirm_create: bool = False,
    codebase_scan_confirmed: bool = False,
    feature_context_confirmed: bool = False,
) -> str:
    """Preview or create approved sprint-wise stories for a Feature through DevFlow."""
    working_dir = await resolve_working_directory()
    return json_response(
        run_dev_create_feature_stories(
            project_key=project_key,
            parent_key=parent_key,
            stories=stories,
            confirm_create=confirm_create,
            codebase_scan_confirmed=codebase_scan_confirmed,
            feature_context_confirmed=feature_context_confirmed,
            working_dir=working_dir,
        )
    )


@mcp.tool()
async def dev_bootstrap_feature_stories(
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
    """Complete Feature bootstrap flow: learn, draft BDRSP-1623 stories, preview, and optionally create."""
    working_dir = await resolve_working_directory()
    return json_response(
        run_dev_bootstrap_feature_stories(
            feature_key=feature_key,
            project_key=project_key,
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            sprint_by_phase=sprint_by_phase,
            phases=phases,
            create_missing_phases_when_existing=create_missing_phases_when_existing,
            confirm_create=confirm_create,
            max_results=max_results,
            working_dir=working_dir,
        )
    )


@mcp.tool()
async def dev_implement_story(
    jira_id: str,
    user_request: str = "",
    stage: str = "start",
    story_approved: bool = False,
    code_changes_done: bool = False,
    tests_done: bool = False,
    review_done: bool = False,
    create_mr_approved: bool = False,
    story_update_approved: bool = False,
    apply_story_update: bool = False,
    approved_story_summary: str | None = None,
    approved_story_description: str | None = None,
    approved_acceptance_criteria: list[str] | None = None,
    approved_regulatory_justification: str | None = None,
    approved_reason_comments: str | None = None,
    approved_comment: str | None = None,
    base_branch: str = "main",
    branch_type: str = "feature",
    test_output: str = "",
    run_tests: bool = False,
    run_full_pipeline: bool = False,
    test_command: str | None = None,
    test_timeout_seconds: int = 600,
    mr_title: str | None = None,
    mr_description: str | None = None,
    mr_preview_approved: bool = False,
    testing_notes: str = "",
    target_branch: str = "main",
    draft_mr: bool = False,
) -> str:
    """Run the gated end-to-end development workflow for a Jira story.

    Set run_tests=True at stage='after_code_changes' to execute tests autonomously
    via subprocess directly from the MCP server — no IDE terminal required.
    Optionally supply test_command to override auto-detection, and test_timeout_seconds
    to extend the timeout for slow Maven/Gradle suites.
    """
    working_dir = await resolve_working_directory()
    return json_response(
        run_dev_implement_story(
            jira_id=jira_id,
            user_request=user_request,
            stage=stage,
            story_approved=story_approved,
            code_changes_done=code_changes_done,
            tests_done=tests_done,
            review_done=review_done,
            create_mr_approved=create_mr_approved,
            story_update_approved=story_update_approved,
            apply_story_update=apply_story_update,
            approved_story_summary=approved_story_summary,
            approved_story_description=approved_story_description,
            approved_acceptance_criteria=approved_acceptance_criteria,
            approved_regulatory_justification=approved_regulatory_justification,
            approved_reason_comments=approved_reason_comments,
            approved_comment=approved_comment,
            base_branch=base_branch,
            branch_type=branch_type,
            test_output=test_output,
            run_tests=run_tests,
            run_full_pipeline=run_full_pipeline,
            test_command=test_command,
            test_timeout_seconds=test_timeout_seconds,
            mr_title=mr_title,
            mr_description=mr_description,
            mr_preview_approved=mr_preview_approved,
            testing_notes=testing_notes,
            target_branch=target_branch,
            draft_mr=draft_mr,
            working_dir=working_dir,
        )
    )


@mcp.prompt()
async def dev_story_implementation_workflow() -> str:
    return """Use DevFlow for end-to-end Jira story implementation.

Rules:
0. If the user provides only a Feature key or asks to create stories for a Feature, call dev_bootstrap_feature_stories with confirm_create=false. The bootstrap flow learns the Feature, discovers existing child stories, reads Confluence/ADR cache, reads repo graph, treats the Feature key as parent_key, generates BDRSP-1623 story payloads when no stories exist, requires sprint_id/sprint_name/sprint_by_phase, previews first, and creates only after explicit approval with confirm_create=true. If sprint is missing or ambiguous, ask which sprint to attach and do not guess.
1. Use dev_bootstrap_feature_stories for complete Feature story bootstrapping. Use dev_plan_feature_stories / dev_create_feature_stories only for lower-level planning or already-approved custom payloads. Use dev_implement_story for single-story implementation.
2. Start with stage="start" when the user says they want to implement a Jira story.
3. During start, DevFlow reads the parent Feature, sibling stories, completed stories, the full Jira story, and the codebase. It returns a grounded Jira update proposal only; it must not update Jira in start.
4. If Feature/Jira/codebase context is unclear, ask for clarification and do not update Jira.
5. If the proposal is clear, ask approval to update title, Description, Acceptance Criteria, Regulatory Justification, Reason/Comments, and the one-subtask policy. Add an activity comment only when the user approves exact comment text.
6. After approval, call stage="apply_story_update" with story_update_approved=true and exact approved field values.
7. The one-subtask policy is strict: create exactly one subtask only when none exists; if a subtask exists, update one existing subtask and do not create a new one.
        8. After Jira update succeeds, the response contains an effort_report field. Show the user ALL of: effort level, impacted files preview, acceptance criteria, risk flags, and implementation plan. Do NOT ask for code approval before showing the complete effort_report.
        9. After the user has read the full effort_report and explicitly says YES, call stage="after_story_approval" with story_approved=true.
10. Use the returned implementation contract to make code changes in the project. Keep changes small, scoped, and pattern-following.
11. After code changes, call stage="after_code_changes" with code_changes_done=true and run_tests=true. This runs tests autonomously via subprocess inside the MCP server — no IDE terminal needed. Show autonomous_test_execution.verdict and failure_analysis to the user. If tests fail, fix and repeat before proceeding to MR creation.
12. Stop after test/review report. Ask the user whether to create the merge request.
13. Only when the user approves MR creation, call stage="create_mr" with create_mr_approved=true.
14. Never create a branch, push, or create an MR silently.
"""


if __name__ == "__main__":
    mcp.run()
