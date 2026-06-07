# gitlab-mcp

Focused MCP server for GitLab-related tools used by GitBridge Agent.

## Purpose

`gitlab-mcp` owns GitLab, Git branch workflow, and the lightweight Jira-to-code implementation gate for GitBridge Agent. It does not run tests and it does not perform deep code review analysis. Keep those concerns in `test-mcp` and `review-mcp` so Copilot loads only the context it needs.

## Required Environment

Add these values to `Automation/.env.local`:

```bash
GITLAB_BASE_URL=https://gitlab.com
GITLAB_PROJECT_ID=group/project
GITLAB_TOKEN=your-gitlab-token

# Optional fallback for local Git branch/push tools.
GIT_WORKING_DIR=/path/to/your/project
```

`GITLAB_PROJECT_ID` can be either a numeric GitLab project id or a URL path such as `group/subgroup/project`.

## Tools

Read-only / inspection:

- `gitlab_check_connection`
- `gitlab_get_merge_request`
- `gitlab_get_merge_request_changes`
- `gitlab_get_merge_request_discussions`
- `gitlab_get_pipeline_status`

Workflow actions:

- `gitlab_create_branch`
- `gitlab_prepare_merge_request`
- `gitlab_push_branch`
- `gitlab_create_merge_request`
- `jiraforge_code_updater`

## Safety Model

The older Automation server had one large `create_merge_request` tool that could stage, commit, push, and create an MR in one call. This server intentionally splits that workflow:

1. `gitlab_prepare_merge_request` builds title/description only.
2. `gitlab_push_branch` defaults to `dry_run=true`.
3. `gitlab_create_merge_request` creates the MR through GitLab API only.

Ask the user for approval before calling any tool that changes local branches, pushes code, or creates an MR.

Important MR flow:

```text
prepare merge request -> gitlab_prepare_merge_request only
create merge request  -> gitlab_push_branch if needed, then gitlab_create_merge_request
```

`gitlab_create_branch` is only for starting work on a new branch. It must not be called during MR preparation or MR creation unless the user explicitly asks to create a branch.

`gitlab_create_merge_request` must use the finalized title and description from `gitlab_prepare_merge_request`, including the content generated from `.gitlab/merge_request_templates/Default.md`.

`gitlab_create_branch` always fetches the latest `origin/<base_branch>` first and creates the new branch from that freshly fetched remote branch. It does not delete branches.

Branch names use this compact format:

```text
<type>/<STORY-ID>-<short-readable-summary>
```

Example:

```text
feature/BDRSP-1418-change-event-qm-am
```

`gitlab_prepare_merge_request` reads the project template from:

```text
.gitlab/merge_request_templates/Default.md
```

If the template exists, the tool uses it and fills common placeholders such as `{{story_id}}`, `{{story_summary}}`, `{{source_branch}}`, `{{target_branch}}`, `{{testing_notes}}`, `{{commits}}`, and `{{diff_stat}}`. It also adds a clear generated summary, story, branch, testing, commit, and diff context before the template so the MR description is ready for review.

## JiraForge Code Updater

`jiraforge_code_updater` is the safety gate before lightweight implementation. It reads the Jira story, checks subtasks, checks the current Git branch/status, estimates effort, and returns an implementation contract.

It does not commit, push, create an MR, or silently start large changes.

Behavior:

- If the Jira story has subtasks, it asks the user to select a subtask unless `implement_whole_story=true`.
- If the working tree has local changes, it asks whether to continue in the current branch or create/use another branch first.
- If effort is Small or Medium, it returns an implementation-ready contract.
- If effort is Large or Very Large/Risky, it returns an effort report and asks for confirmation.
- It lists candidate files to read first and rules for safe minimal code updates.
