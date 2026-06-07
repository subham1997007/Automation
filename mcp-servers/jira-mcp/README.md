# jira-mcp

Focused MCP server for JiraForge Agent.

## Pattern

This follows the MCP course model:

- Host: Copilot / IDE
- Client: Copilot MCP client
- Server: `jira-mcp`
- Tools: model-controlled Jira read/analyze actions

## Tools

- `jira_check_connection`
- `jira_read_story`
- `jira_analyze_story`
- `jira_plan_feature_stories`
- `jira_bootstrap_feature_stories`
- `jira_create_feature_stories`
- `jira_plan_subtasks`
- `jira_refine_story`
- `jira_feature_context`
- `jira_delete_subtasks`
- `jira_manage_subtasks`

## Behavior

- Read Jira first.
- Analyze the story in simple human language.
- Extract or suggest acceptance criteria.
- Suggest clearer story wording.
- Do not suggest subtasks by default for small or medium stories.
- Create or update subtasks only through `jira_manage_subtasks`, after duplicate checks and explicit per-action approval.
- Delete subtasks only after explicit confirmation.
- Learn feature-level sprint patterns with `jira_plan_feature_stories` before drafting or creating new stories.
- Use `jira_bootstrap_feature_stories` for the complete "create stories for this Feature" flow when no child stories exist.
- Create feature stories through `jira_bootstrap_feature_stories` for complete Feature bootstrapping, or `jira_create_feature_stories` for already-approved custom payloads; both require preview and explicit confirmation.

## Story Refinement

`jira_refine_story` reads the Jira story, analyzes it, and proposes:

- professional story title
- clear `BDRSP-1623` formatted description
- acceptance criteria
- impacted areas
- open questions
- suggested subtasks

By default it runs in proposal mode only. To update Jira after user approval, pass the exact approved content:

```text
jira_refine_story(
  jira_id="PROJECT-123",
  apply_update=true,
  approved_summary="Exact approved summary",
  approved_description="Exact approved description"
)
```

`jira_feature_context` reads the parent Feature, sibling stories, completed stories, and current story fit. Use it before any Jira story writing so updates are aligned with the larger Feature.

`jira_refine_story` also includes `feature_context` in its proposal and refuses to write when the parent Feature cannot be resolved. This keeps the Jira-only profile aligned with the same rule as DevFlow: no story writing before Feature context.

For refinement, the proposed and approved Description must keep the `BDRSP-1623` style: `##` headings, `:::info`, `:::success`, `:::warning`, `:::note` panels, and at least one markdown table.

If a user asks to create Jira stories for a Feature, call `jira_bootstrap_feature_stories(feature_key, confirm_create=false)`. The tool learns the Feature, discovers existing child stories, reads cached Confluence/ADR context, reads the repo graph summary, and generates BDRSP-1623 story payloads when no stories exist. The Feature key becomes `parent_key` for each story.

Create the approved batch only through preview-first flow:

```text
jira_bootstrap_feature_stories(
  feature_key="BDATART-686",
  sprint_id="2184733",
  confirm_create=false
)
```

Call again with `confirm_create=true` only after the user approves the exact preview.

For sprint assignment, every story creation payload must include the user-selected sprint target. Prefer `sprint_id`. If using `sprint_name`, configure `JIRA_BOARD_ID` or `JIRA_BOARD_IDS`; the tool resolves the sprint before creating anything and assigns each created story through Jira Agile API. For per-phase sprint assignment, pass `sprint_by_phase`. If the sprint is missing or ambiguous, ask the user which sprint to attach; do not guess.

`jira_refine_story` and `jira_plan_subtasks` do not create subtasks.

To create or update subtasks safely, first preview the plan:

```text
jira_manage_subtasks(
  jira_id="PROJECT-123",
  proposed_subtasks=[
    {"summary": "PROJECT-123: Add QM to AM TRS change event", "description": "Persist and expose QM to AM TRS change events."}
  ]
)
```

The tool checks:

- duplicate subtasks
- same-purpose existing subtasks
- generic/unnecessary subtasks
- required description
- required assignee

Then apply only approved actions:

```text
jira_manage_subtasks(
  jira_id="PROJECT-123",
  proposed_subtasks=[...same list...],
  apply_changes=true,
  confirm_apply=true,
  approved_action_ids=["subtask-1"]
)
```

Each proposed subtask should include:

```text
summary
description
assignee_account_id
priority
```

If `assignee_account_id` is not passed, the tool uses the parent story assignee. If the parent story has no assignee, set:

```bash
JIRA_DEFAULT_ASSIGNEE_ACCOUNT_ID=712020:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
JIRA_DEFAULT_SUBTASK_PRIORITY=Medium
```

If priority is not provided, the tool sets subtask priority to `Medium`.

To delete accidentally created subtasks, preview first:

```text
jira_delete_subtasks(issue_keys=["PROJECT-1", "PROJECT-2"])
```

Then delete only after confirmation:

```text
jira_delete_subtasks(issue_keys=["PROJECT-1", "PROJECT-2"], confirm_delete=true)
```

If your Jira project has a separate Acceptance Criteria custom field, configure it in `Automation/.env.local`:

```bash
JIRA_ACCEPTANCE_CRITERIA_FIELD=customfield_12345
JIRA_ACCEPTANCE_CRITERIA_FIELD_FORMAT=adf
```

For separate Regulatory Justification or Reason/Comments fields, configure their Jira custom field IDs too:

```bash
JIRA_REGULATORY_JUSTIFICATION_FIELD=customfield_67890
JIRA_REGULATORY_JUSTIFICATION_FIELD_FORMAT=adf
JIRA_REASON_COMMENTS_FIELD=customfield_24680
JIRA_REASON_COMMENTS_FIELD_FORMAT=adf
```
