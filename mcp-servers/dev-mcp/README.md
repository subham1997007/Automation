# dev-mcp

DevFlow MCP server for controlled Jira-to-code workflows.

## Tools

- `dev_plan_feature_stories` - learn a Feature end to end and return sprint-wise story planning guidance.
- `dev_bootstrap_feature_stories` - complete Feature bootstrap: learn context, draft BDRSP-1623 stories when none exist, require sprint, preview, and create after approval.
- `dev_create_feature_stories` - preview or create an approved batch of sprint-wise Feature stories.
- `dev_implement_story` - implement one approved Jira story through the gated DevFlow process.

## Feature Story Flow

When the user provides only a Feature key, such as `BDATART-686`, call:

```text
dev_bootstrap_feature_stories(feature_key="BDATART-686", confirm_create=false)
```

Show:

- Feature goal and status
- existing stories grouped by sprint
- missing phases such as analysis, documentation, UAT users, go-live, and support
- Confluence cache excerpts
- repo knowledge-graph summary
- mandatory story style contract

If the tool returns `sprint_required`, ask which sprint to attach. Do not guess.

For one sprint across all generated stories:

```text
dev_bootstrap_feature_stories(
  feature_key="BDATART-686",
  sprint_id="2184733",
  confirm_create=false
)
```

Create only after explicit approval by calling again with `confirm_create=true`.
