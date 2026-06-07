# Project-Specific Scripts

These scripts contain **hardcoded content specific to this project** (cdp-linking-middleware / BDRSP).
They are **NOT part of the portable Automation framework** and should NOT be copied to other repos.

| Script | What it does |
|--------|-------------|
| `publish_adr_to_confluence.py` | Publishes ADR-002.3 and ADR-002.4 to the BDATARTINT Confluence space |
| `create_adr_story.py` | Creates a Jira story linked to ADR-002.3 in the BDRSP project |
| `jira_inspect_adr.py` | Inspects BDATART-686 ADR-specific Jira issue |

When copying the `Automation/` folder to a new repo, **exclude this directory**.

