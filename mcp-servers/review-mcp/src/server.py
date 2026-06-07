#!/usr/bin/env python3
"""Code review MCP server for ReviewSmith Agent."""

from __future__ import annotations

import urllib.parse
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.append(str(Path(__file__).resolve().parents[2] / "common"))
from review_client import (
    analyze_current_changes,
    check_acceptance_criteria_coverage,
    generate_change_summary,
    full_current_change_review,
    inspect_story_implementation,
    json_response,
    suggest_improvements,
)
from workflow_memory import execute_workflow


mcp = FastMCP("review-mcp")


async def resolve_working_directory() -> str | None:
    """Resolve the current MCP root so review tools inspect the user's project."""
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
async def review_inspect_story_implementation(
    story_id: str,
    story_summary: str,
    story_description: str = "",
    acceptance_criteria: list[str] | None = None,
    base_branch: str = "main",
) -> str:
    """Inspect whether the current repo already has code related to a Jira story."""
    working_dir = await resolve_working_directory()
    return json_response(
        execute_workflow(
            server_name="review-mcp",
            tool_name="review_inspect_story_implementation",
            memory_key=f"inspect-{story_id}-{base_branch}",
            inputs={
                "story_id": story_id,
                "story_summary": story_summary,
                "story_description": story_description,
                "acceptance_criteria": acceptance_criteria,
                "base_branch": base_branch,
                "working_dir": working_dir,
            },
            operation=lambda: inspect_story_implementation(
                story_id=story_id,
                story_summary=story_summary,
                story_description=story_description,
                acceptance_criteria=acceptance_criteria,
                base_branch=base_branch,
                working_dir=working_dir,
            ),
        )
    )


@mcp.tool()
async def review_analyze_current_changes(base_branch: str = "main") -> str:
    """Analyze current Git changes, changed areas, risk signals, commits, and diff stat."""
    working_dir = await resolve_working_directory()
    return json_response(
        execute_workflow(
            server_name="review-mcp",
            tool_name="review_analyze_current_changes",
            memory_key=f"changes-{base_branch}",
            inputs={"base_branch": base_branch, "working_dir": working_dir},
            operation=lambda: analyze_current_changes(base_branch=base_branch, working_dir=working_dir),
        )
    )


@mcp.tool()
async def review_check_acceptance_criteria_coverage(
    acceptance_criteria: list[str],
    story_summary: str = "",
    base_branch: str = "main",
) -> str:
    """Check whether current changes show signals for each acceptance criterion."""
    working_dir = await resolve_working_directory()
    return json_response(
        execute_workflow(
            server_name="review-mcp",
            tool_name="review_check_acceptance_criteria_coverage",
            memory_key=f"coverage-{base_branch}",
            inputs={
                "acceptance_criteria": acceptance_criteria,
                "story_summary": story_summary,
                "base_branch": base_branch,
                "working_dir": working_dir,
            },
            operation=lambda: check_acceptance_criteria_coverage(
                acceptance_criteria=acceptance_criteria,
                story_summary=story_summary,
                base_branch=base_branch,
                working_dir=working_dir,
            ),
        )
    )


@mcp.tool()
async def review_suggest_improvements(
    story_summary: str,
    acceptance_criteria: list[str] | None = None,
    base_branch: str = "main",
) -> str:
    """Suggest review improvements based on current changes, risks, and AC coverage signals."""
    working_dir = await resolve_working_directory()
    return json_response(
        execute_workflow(
            server_name="review-mcp",
            tool_name="review_suggest_improvements",
            memory_key=f"suggest-{base_branch}",
            inputs={
                "story_summary": story_summary,
                "acceptance_criteria": acceptance_criteria,
                "base_branch": base_branch,
                "working_dir": working_dir,
            },
            operation=lambda: suggest_improvements(
                story_summary=story_summary,
                acceptance_criteria=acceptance_criteria,
                base_branch=base_branch,
                working_dir=working_dir,
            ),
        )
    )


@mcp.tool()
async def review_generate_change_summary(
    story_id: str,
    story_summary: str,
    acceptance_criteria: list[str] | None = None,
    base_branch: str = "main",
) -> str:
    """Generate a review report for the current changes against a Jira story."""
    working_dir = await resolve_working_directory()
    return json_response(
        execute_workflow(
            server_name="review-mcp",
            tool_name="review_generate_change_summary",
            memory_key=f"summary-{story_id}-{base_branch}",
            inputs={
                "story_id": story_id,
                "story_summary": story_summary,
                "acceptance_criteria": acceptance_criteria,
                "base_branch": base_branch,
                "working_dir": working_dir,
            },
            operation=lambda: generate_change_summary(
                story_id=story_id,
                story_summary=story_summary,
                acceptance_criteria=acceptance_criteria,
                base_branch=base_branch,
                working_dir=working_dir,
            ),
        )
    )


@mcp.tool()
async def review_full_current_changes(
    story_id: str = "",
    story_summary: str = "",
    acceptance_criteria: list[str] | None = None,
    base_branch: str = "main",
) -> str:
    """Read current code changes and produce a full review with risks, suggestions, and modern engineering guidance."""
    working_dir = await resolve_working_directory()
    return json_response(
        execute_workflow(
            server_name="review-mcp",
            tool_name="review_full_current_changes",
            memory_key=f"full-{story_id or 'current'}-{base_branch}",
            inputs={
                "story_id": story_id,
                "story_summary": story_summary,
                "acceptance_criteria": acceptance_criteria,
                "base_branch": base_branch,
                "working_dir": working_dir,
            },
            operation=lambda: full_current_change_review(
                story_id=story_id,
                story_summary=story_summary,
                acceptance_criteria=acceptance_criteria,
                base_branch=base_branch,
                working_dir=working_dir,
            ),
        )
    )


@mcp.prompt()
async def review_jira_story_implementation() -> str:
    return """Review a Jira story implementation.

Use these tools in order:
1. review_inspect_story_implementation
2. review_analyze_current_changes
3. review_check_acceptance_criteria_coverage
4. review_suggest_improvements
5. review_generate_change_summary

Return:
1. Whether implementation appears started
2. Changed files grouped by area
3. AC coverage status
4. Risks and missing tests
5. Recommended next actions
"""


if __name__ == "__main__":
    mcp.run()
