# JiraForge Agent

JiraForge Agent is responsible for reading Jira stories and preparing their details for future automation workflows.

This agent will use one MCP server:

```text
jira-mcp
```

## Capabilities

- Read a Jira story or issue by Jira ID, such as `PROJ-123`.
- Read Jira comments.
- Extract acceptance criteria.
- Analyze and suggest clearer story wording.
- Suggest minimal subtasks.
- Refine story title, description, ACs, and subtasks after user approval.

## Tools

- `jira_check_connection`
- `jira_read_story`
- `jira_analyze_story`
- `jira_plan_subtasks`
- `jira_refine_story`
- `jira_delete_subtasks`
- `jira_manage_subtasks`

## Environment

The Jira MCP server expects these environment variables:

```bash
export JIRA_BASE_URL="https://your-domain.atlassian.net"
export JIRA_USERNAME="your-email@example.com"
export JIRA_API_TOKEN="your-jira-api-token"
```

## Run

From the project root:

```bash
./start.sh
```

## Course Mapping

- Host: `Automation/start.py`
- Agent: `JiraForge Agent`
- MCP Server: `jira-mcp`
- MCP Tools: Jira story read, analyze, plan subtasks, and refine story
- Transport: `stdio`
