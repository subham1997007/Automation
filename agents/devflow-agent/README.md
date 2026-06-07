# DevFlow Agent

DevFlow Agent is the single-agent profile for end-to-end development.

It uses one MCP server:

```text
dev-mcp
```

It exposes one tool:

```text
dev_implement_story
```

Use it when the user says:

```text
I want to implement this story BDRSP-1234
```

The agent stops only at the required approval gates:

1. Jira story fields and one-subtask policy are applied, then the user confirms satisfaction and approves code changes.
2. Code changes are made, then `stage="after_code_changes"` is called with `run_tests=true` — tests are executed **autonomously via subprocess inside the MCP server** (no IDE terminal needed). The result includes real stdout/stderr, exit code, failure analysis, and surefire report paths.
3. After tests pass and review is reported, the user must approve MR creation.

Use `test_command` to target a specific test class, and `test_timeout_seconds` to extend the timeout for slow Maven/Gradle suites.
