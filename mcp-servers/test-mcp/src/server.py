#!/usr/bin/env python3
"""Testing MCP server for TestPilot Agent."""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.append(str(Path(__file__).resolve().parents[2] / "common"))
from test_client import (
    analyze_failures,
    collect_test_reports,
    current_change_test_report,
    detect_project_type,
    discover_test_commands,
    generate_test_summary,
    json_response,
    run_integration_tests,
    run_specific_test,
    run_unit_tests,
)
from workflow_memory import execute_workflow


mcp = FastMCP("test-mcp")


async def resolve_working_directory() -> str | None:
    """Resolve the current MCP root so test tools run against the user's project."""
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
async def test_detect_project_type() -> str:
    """Detect the project build/test ecosystem such as Maven, Gradle, Node, Python, Go, or .NET."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(server_name="test-mcp", tool_name="test_detect_project_type", memory_key="project-type", inputs={"working_dir": working_dir}, operation=lambda: detect_project_type(working_dir=working_dir)))


@mcp.tool()
async def test_discover_test_commands() -> str:
    """Discover likely unit, integration, and specific-test commands from project files."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(server_name="test-mcp", tool_name="test_discover_test_commands", memory_key="test-commands", inputs={"working_dir": working_dir}, operation=lambda: discover_test_commands(working_dir=working_dir)))


@mcp.tool()
async def test_run_unit_tests(command: str | None = None, timeout_seconds: int = 600) -> str:
    """Run unit tests using the detected command, or an explicit command when provided."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(server_name="test-mcp", tool_name="test_run_unit_tests", memory_key="unit-tests", inputs={"command": command, "working_dir": working_dir, "timeout_seconds": timeout_seconds}, operation=lambda: run_unit_tests(command=command, working_dir=working_dir, timeout_seconds=timeout_seconds)))


@mcp.tool()
async def test_run_integration_tests(command: str | None = None, timeout_seconds: int = 900) -> str:
    """Run integration tests using the detected command, or an explicit command when provided."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(server_name="test-mcp", tool_name="test_run_integration_tests", memory_key="integration-tests", inputs={"command": command, "working_dir": working_dir, "timeout_seconds": timeout_seconds}, operation=lambda: run_integration_tests(command=command, working_dir=working_dir, timeout_seconds=timeout_seconds)))


@mcp.tool()
async def test_run_specific_test(
    test_target: str,
    command_template: str | None = None,
    timeout_seconds: int = 600,
) -> str:
    """Run one focused test class, file, method, or test target."""
    working_dir = await resolve_working_directory()
    return json_response(
        execute_workflow(
            server_name="test-mcp",
            tool_name="test_run_specific_test",
            memory_key=f"specific-{test_target}",
            inputs={"test_target": test_target, "command_template": command_template, "working_dir": working_dir, "timeout_seconds": timeout_seconds},
            operation=lambda: run_specific_test(
            test_target=test_target,
            command_template=command_template,
            working_dir=working_dir,
            timeout_seconds=timeout_seconds,
            ),
        )
    )


@mcp.tool()
async def test_analyze_failures(test_output: str = "") -> str:
    """Analyze test failure output or available report snippets and explain likely causes."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(server_name="test-mcp", tool_name="test_analyze_failures", memory_key="failure-analysis", inputs={"test_output": test_output, "working_dir": working_dir}, operation=lambda: analyze_failures(test_output=test_output, working_dir=working_dir)))


@mcp.tool()
async def test_collect_test_reports(max_files: int = 30) -> str:
    """Collect test report file paths and snippets from common report locations."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(server_name="test-mcp", tool_name="test_collect_test_reports", memory_key="test-reports", inputs={"working_dir": working_dir, "max_files": max_files}, operation=lambda: collect_test_reports(working_dir=working_dir, max_files=max_files)))


@mcp.tool()
async def test_generate_test_summary(latest_output: str = "") -> str:
    """Generate a concise testing report with detected project type, commands, reports, and next steps."""
    working_dir = await resolve_working_directory()
    return json_response(execute_workflow(server_name="test-mcp", tool_name="test_generate_test_summary", memory_key="test-summary", inputs={"working_dir": working_dir, "latest_output": latest_output}, operation=lambda: generate_test_summary(working_dir=working_dir, latest_output=latest_output)))


@mcp.tool()
async def test_current_change_report(base_branch: str = "main", latest_output: str = "") -> str:
    """Read current code changes and produce a test scope/report with recommended commands and report evidence."""
    working_dir = await resolve_working_directory()
    return json_response(
        execute_workflow(
            server_name="test-mcp",
            tool_name="test_current_change_report",
            memory_key=f"current-change-{base_branch}",
            inputs={"base_branch": base_branch, "latest_output": latest_output, "working_dir": working_dir},
            operation=lambda: current_change_test_report(
            base_branch=base_branch,
            latest_output=latest_output,
            working_dir=working_dir,
            ),
        )
    )


@mcp.prompt()
async def validate_story_changes() -> str:
    return """Validate code changes for a Jira story.

Use these tools in order:
1. test_detect_project_type
2. test_discover_test_commands
3. test_run_unit_tests
4. test_run_integration_tests only when needed
5. test_analyze_failures if any command fails
6. test_generate_test_summary

Return:
1. Commands run
2. Pass/fail result
3. Failure explanation if any
4. Whether integration tests are still needed
5. Recommended next action
"""


if __name__ == "__main__":
    mcp.run()
