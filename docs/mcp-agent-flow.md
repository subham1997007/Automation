# Controlled Agentic Development

This document explains how the Automation folder enables controlled development acceleration through AI agents, MCP servers, MCP tools, approval gates, and guided request flow.

## High-Level Architecture

```mermaid
flowchart LR
    User["User"]
    IDE["IDE AI Agent<br/>Copilot or Windsurf Cascade"]
    Config["MCP Config<br/>Copilot mcp.json<br/>Windsurf mcp_config.json"]

    subgraph Automation["Automation Folder"]
        Setup["setup_copilot_mcp.sh<br/>profile selector"]
        Runner["bin/run_mcp_server.sh<br/>server launcher"]

        subgraph Profiles["Profiles"]
            JiraProfile["jira profile"]
            GitProfile["gitlab profile"]
            TestProfile["test profile"]
            ReviewProfile["review profile"]
            DevProfile["dev profile"]
        end

        subgraph Agents["Agents"]
            JiraAgent["JiraForge Agent"]
            GitAgent["GitBridge Agent"]
            TestAgent["TestPilot Agent"]
            ReviewAgent["ReviewSmith Agent"]
            DevAgent["DevFlow Agent"]
        end

        subgraph Servers["MCP Servers"]
            JiraMcp["jira-mcp"]
            GitMcp["gitlab-mcp"]
            TestMcp["test-mcp"]
            ReviewMcp["review-mcp"]
            DevMcp["dev-mcp"]
        end

        subgraph Tools["MCP Tools"]
            JiraTools["Jira tools<br/>read, analyze, refine, subtasks"]
            GitTools["GitLab tools<br/>branch, push, prepare MR, create MR"]
            TestTools["Test tools<br/>detect, run, analyze reports"]
            ReviewTools["Review tools<br/>diff review, AC coverage, suggestions"]
            DevTool["dev_implement_story<br/>one end-to-end workflow tool"]
        end
    end

    User --> IDE
    IDE --> Config
    Setup --> Config
    Config --> Runner

    JiraProfile --> JiraMcp
    GitProfile --> GitMcp
    TestProfile --> TestMcp
    ReviewProfile --> ReviewMcp
    DevProfile --> DevMcp

    JiraAgent --> JiraMcp --> JiraTools
    GitAgent --> GitMcp --> GitTools
    TestAgent --> TestMcp --> TestTools
    ReviewAgent --> ReviewMcp --> ReviewTools
    DevAgent --> DevMcp --> DevTool

    DevTool -. reuses helper logic .-> JiraTools
    DevTool -. reuses helper logic .-> GitTools
    DevTool -. reuses helper logic .-> TestTools
    DevTool -. reuses helper logic .-> ReviewTools
```

## Profile Mapping

```mermaid
flowchart TB
    ProfileCommand["./Automation/setup_copilot_mcp.sh <profile>"]

    ProfileCommand --> Jira["jira"]
    ProfileCommand --> Gitlab["gitlab"]
    ProfileCommand --> Test["test"]
    ProfileCommand --> Review["review"]
    ProfileCommand --> Dev["dev"]

    Jira --> JiraServer["Loads only jira-mcp"]
    Gitlab --> GitServer["Loads only gitlab-mcp"]
    Test --> TestServer["Loads only test-mcp"]
    Review --> ReviewServer["Loads only review-mcp"]
    Dev --> DevServer["Loads only dev-mcp"]

    DevServer --> DevOnlyTool["Exposes dev_plan_feature_stories, dev_bootstrap_feature_stories, dev_create_feature_stories, and dev_implement_story"]
```

## DevFlow End-To-End Request Processing

```mermaid
sequenceDiagram
    actor User
    participant AI as IDE AI Agent<br/>Copilot or Cascade
    participant Dev as dev-mcp<br/>dev_implement_story
    participant Jira as Jira helper logic
    participant Git as GitLab/Git helper logic
    participant Code as Project Codebase
    participant Test as Test helper logic
    participant Review as Review helper logic
    participant MR as GitLab Merge Request

    User->>AI: Implement Jira story BDRSP-1234
    AI->>Dev: stage=start
    Dev->>Jira: Read parent Feature, completed sibling stories, title, Description, Regulatory, ACs, comments, subtasks, links
    Dev->>Code: Scan codebase for related implementation context
    Dev-->>AI: Feature-aligned proposed title, Description, Regulatory, ACs, Reason/Comments, one-subtask action
    AI-->>User: Ask approval before Jira update
    User->>AI: Approved Jira update
    AI->>Dev: stage=apply_story_update<br/>story_update_approved=true
    Dev->>Jira: Apply approved fields and one-subtask policy
    Dev-->>AI: Refreshed story and implementation scope report
    AI-->>User: Ask approval before code changes

    User->>AI: Approved, proceed
    AI->>Dev: stage=after_story_approval<br/>story_approved=true
    Dev->>Git: Check branch and working tree
    Git-->>Dev: Branch status or new story branch
    Dev-->>AI: Implementation contract
    AI->>Code: Make minimal code changes

    AI->>Dev: stage=after_code_changes<br/>code_changes_done=true
    Dev->>Test: Build test scope and collect reports
    Dev->>Review: Review current changes and AC coverage
    Test-->>Dev: Test report
    Review-->>Dev: Review report
    Dev-->>AI: Test and review checkpoint
    AI-->>User: Ask approval before MR creation

    User->>AI: Approved, create MR
    AI->>Dev: stage=create_mr<br/>create_mr_approved=true<br/>tests_done=true<br/>review_done=true
    Dev->>Git: Validate branch rules
    Dev->>Git: Read .gitlab/merge_request_templates/Default.md
    Dev->>Git: Prepare final MR title and description
    Dev->>Git: Push branch
    Dev->>MR: Create merge request
    MR-->>AI: MR link and status
    AI-->>User: Final summary
```

## DevFlow Approval Gates

```mermaid
flowchart TD
    Start["User asks to implement Jira story"]
    Analyze["Read/analyze Jira story"]
    Gate1{"Gate 1<br/>Story ready?"}
    Branch["Check/create story branch"]
    Code["AI agent applies code changes"]
    TestReview["Run/collect test and review reports"]
    Gate2{"Gate 2<br/>Create MR?"}
    Template["Read Default.md MR template"]
    CreateMR["Push branch and create MR"]
    Stop["Stop and wait for user"]

    Start --> Analyze --> Gate1
    Gate1 -- No --> Stop
    Gate1 -- Yes --> Branch --> Code --> TestReview --> Gate2
    Gate2 -- No --> Stop
    Gate2 -- Yes --> Template --> CreateMR
```

## Important Rules

1. When the `dev` profile is selected, Copilot gets one entry point: `dev-mcp` with the `dev_implement_story` tool. This keeps the workflow simple and avoids exposing unrelated tools.
2. DevFlow first reads the full Jira story and scans the codebase before proposing any Jira writing.
3. DevFlow does not update Jira until the user approves the proposed Description, Acceptance Criteria, Regulatory Justification, and one-subtask action.
4. If no subtask exists, DevFlow creates exactly one subtask. If any subtask exists, DevFlow updates one existing subtask and does not create a new one.
5. After approved Jira updates, DevFlow asks for approval to start code changes.
6. DevFlow does not create a merge request until the implementation is complete, tests have been run, review has been performed, and the user gives final approval.
7. The merge request description must be prepared from the project template at `.gitlab/merge_request_templates/Default.md`, so every MR follows the team's standard format.
8. Merge request creation is blocked from unsafe or unclear branches, including `review`, `temp`, `wip`, base branches, or branches that do not match the Jira story key.
9. DevFlow is the orchestrator. Internally, it reuses the focused helper logic from JiraForge, GitBridge, TestPilot, and ReviewSmith instead of duplicating those responsibilities.
