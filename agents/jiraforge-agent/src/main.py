#!/usr/bin/env python3
"""JiraForge Agent placeholder runtime.

The real agent logic will be added once its behavior is defined. For now this
entrypoint documents the owned MCP server and gives the root launcher something
stable to start when the agent is enabled.
"""

from __future__ import annotations


def main() -> int:
    print("JiraForge Agent is configured.")
    print("Expected MCP server: jira-mcp")
    print("Tools: jira_check_connection, jira_read_story, jira_analyze_story, jira_feature_context, jira_plan_subtasks, jira_refine_story, jira_delete_subtasks, jira_manage_subtasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
