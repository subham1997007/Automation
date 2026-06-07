"""End-to-end development workflow helpers for dev-mcp."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

try:
    from langgraph.graph import END, StateGraph
except ImportError:
    END = "__end__"
    StateGraph = None

# ── LangChain helpers (optional — degrade gracefully if not installed) ─────────
try:
    from langchain_helpers.confluence_loader import ConfluenceCacheLoader as _ConfluenceCacheLoader
    from langchain_helpers.code_vectorstore import CodeVectorStore as _CodeVectorStore
    from langchain_helpers.story_analyzer import StoryAnalyzer as _StoryAnalyzer
    from langchain_helpers.parallel_runner import run_parallel as _run_parallel
    from langchain_helpers.jira_cache import JiraCache as _JiraCache
    from langchain_helpers.local_brain import classify_request as _classify_local_request
    from langchain_helpers.context_manager import (
        compact_mcp_response as _compact_mcp_response,
        trim_tool_response as _trim_tool_response,
        rag_extract as _rag_extract,
        is_cache_fresh as _is_cache_fresh,
    )
    _LANGCHAIN_AVAILABLE = True
except Exception:
    _ConfluenceCacheLoader = None  # type: ignore[assignment,misc]
    _CodeVectorStore = None  # type: ignore[assignment,misc]
    _StoryAnalyzer = None  # type: ignore[assignment,misc]
    _run_parallel = None  # type: ignore[assignment,misc]
    _JiraCache = None  # type: ignore[assignment,misc]
    _classify_local_request = None  # type: ignore[assignment,misc]
    _compact_mcp_response = None  # type: ignore[assignment,misc]
    _trim_tool_response = None  # type: ignore[assignment,misc]
    _rag_extract = None  # type: ignore[assignment,misc]
    _is_cache_fresh = None  # type: ignore[assignment,misc]
    _LANGCHAIN_AVAILABLE = False


class DevFlowError(RuntimeError):
    """Raised when the DevFlow workflow cannot continue safely."""


def json_response(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def automation_dir() -> Path:
    """Return the Automation/ directory — auto-detected via RepoResolver."""
    try:
        from langchain_helpers.repo_resolver import get_resolver
        return get_resolver().automation_dir
    except Exception:
        return Path(__file__).resolve().parents[3]


def memory_dir() -> Path:
    """Return Automation/.memory/devflow/stories/, creating it if needed."""
    try:
        from langchain_helpers.repo_resolver import get_resolver
        path = get_resolver().memory_path("devflow", "stories")
        return path
    except Exception:
        path = automation_dir() / ".memory" / "devflow" / "stories"
        path.mkdir(parents=True, exist_ok=True)
        return path


def memory_file(jira_id: str) -> Path:
    safe_key = "".join(ch for ch in jira_id.upper() if ch.isalnum() or ch in {"-", "_"})
    return memory_dir() / f"{safe_key}.json"


def load_story_memory(jira_id: str) -> dict[str, Any]:
    path = memory_file(jira_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_story_memory(jira_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = memory_file(jira_id)
    current = load_story_memory(jira_id)
    current.update(payload)
    current["updated_at_epoch"] = int(time.time())
    path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(path), "updated_at_epoch": current["updated_at_epoch"]}


def load_repo_cognition_index() -> dict[str, Any]:
    """Load the graph-backed repo cognition memory for DevFlow."""
    index_path = automation_dir() / ".memory" / "codebase-index.json"
    if not index_path.exists():
        return {
            "available": False,
            "path": str(index_path),
            "status": "missing",
            "message": "Run Automation/graph.sh to generate the repo cognition index.",
        }
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "path": str(index_path),
            "status": "unreadable",
            "message": str(exc),
        }
    return {
        "available": True,
        "path": str(index_path),
        "meta": index.get("_meta", {}),
        "summary": index.get("graph_summary", {}),
        "layers": index.get("graph_layers", {}),
        "hotspots": index.get("graph_hotspots", []),
        "test_command_patterns": index.get("test_command_patterns", {}),
    }


def repo_cognition_summary(index: dict[str, Any]) -> dict[str, Any]:
    """Return a compact cognition summary suitable for MCP output."""
    if not index.get("available"):
        return index
    meta = index.get("meta") or {}
    summary = index.get("summary") or {}
    layers = index.get("layers") or {}
    return {
        "available": True,
        "status": "ready",
        "path": index.get("path"),
        "rule": meta.get("graph_rule"),
        "build_system": meta.get("build_system"),
        "total_code_files": summary.get("total_code_files"),
        "total_dependencies": summary.get("total_dependencies"),
        "high_coupling_files": summary.get("high_coupling_files"),
        "health": summary.get("health"),
        "layers": {
            name: {
                "description": data.get("description"),
                "file_count": data.get("file_count"),
                "sample_files": (data.get("files") or [])[:5],
            }
            for name, data in list(layers.items())[:10]
        },
        "hotspots": (index.get("hotspots") or [])[:8],
    }


def cognition_matches_for_files(index: dict[str, Any], files: list[str]) -> dict[str, Any]:
    """Explain graph risk and test hints for the files found by code scan."""
    if not index.get("available"):
        return repo_cognition_summary(index)
    file_set = set(files)
    hotspots = [
        item for item in index.get("hotspots", [])
        if item.get("file") in file_set
    ]
    mappings = []
    for item in (index.get("test_command_patterns") or {}).get("mappings", []):
        source_path = item.get("source_path")
        if source_path in file_set or any(source_path and source_path.endswith(Path(f).name) for f in file_set):
            mappings.append(item)
    result = repo_cognition_summary(index)
    result.update(
        {
            "matched_hotspots": hotspots,
            "targeted_test_hints": mappings[:10],
            "impact_rule": (
                "Use matched_hotspots for risk and targeted_test_hints for focused validation before code changes."
            ),
        }
    )
    return result


def _worktree_manager():
    """Return a WorktreeManager instance, importing from scripts/worktree_manager.py."""
    try:
        import importlib.util as _ilu
        _wm_path = automation_dir() / "scripts" / "worktree_manager.py"
        spec = _ilu.spec_from_file_location("worktree_manager", _wm_path)
        if not spec or not spec.loader:
            return None
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.WorktreeManager(
            repo_root=automation_dir().parent,
            automation_dir=automation_dir(),
        )
    except Exception:
        return None


def _setup_story_worktree(jira_id: str, branch_name: str, base_branch: str) -> Any:
    """Create an isolated git worktree for the story branch. Returns WorktreeInfo or a fallback dict."""
    wm = _worktree_manager()
    if wm is None:
        return {"status": "unavailable", "path": None, "branch": branch_name}
    try:
        return wm.create(jira_id, branch_name, base_branch)
    except Exception as exc:
        return {"status": "error", "path": None, "error": str(exc), "branch": branch_name}


def _resolve_story_workspace(jira_id: str, working_dir: str | None) -> str | None:
    """Return the worktree path for the story if available, else fall back to working_dir."""
    try:
        wm = _worktree_manager()
        if wm is None:
            return working_dir
        info = wm.get(jira_id)
        if info and info.exists():
            return info.path
    except Exception:
        pass
    return working_dir


def _cleanup_story_worktree(jira_id: str) -> dict[str, Any]:
    """Remove the story worktree after push. Safe to call even if no worktree exists."""
    try:
        wm = _worktree_manager()
        if wm is None:
            return {"ok": True, "skipped": True, "reason": "worktree_manager unavailable"}
        return wm.remove(jira_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _load_report_generator(jira_id: str):
    """Import and return a ReportGenerator from scripts/report_generator.py."""
    try:
        import importlib.util as _ilu
        _rg_path = automation_dir() / "scripts" / "report_generator.py"
        spec = _ilu.spec_from_file_location("report_generator", _rg_path)
        if not spec or not spec.loader:
            return None
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.ReportGenerator(jira_id, automation_dir=automation_dir())
    except Exception:
        return None


def _record_stage_timing(jira_id: str, stage: str, elapsed_s: float, actor: str = "ai",
                         status: str = "done", notes: str = "") -> None:
    """Persist stage timing into story memory for later report generation."""
    mem = load_story_memory(jira_id)
    timings: list[dict[str, Any]] = mem.get("stage_timings", [])
    timings.append({
        "stage": stage,
        "elapsed_s": elapsed_s,
        "actor": actor,
        "status": status,
        "notes": notes,
        "recorded_at": int(time.time()),
    })
    save_story_memory(jira_id, {"stage_timings": timings})
    _refresh_analytics()


def _detect_default_branch(working_dir: str | None) -> str:
    """Auto-detect the repo's default branch via GitLab API → git → heuristic."""
    try:
        gitlab = import_helper("gitlab-mcp", "gitlab_client")
        result = gitlab.detect_default_branch(working_dir=working_dir)
        return result.get("default_branch") or "main"
    except Exception:
        return "main"


def _refresh_analytics() -> None:
    """Re-generate devflow-analytics.html in a background thread — fire and forget."""
    import threading

    def _run() -> None:
        try:
            import importlib.util as _ilu
            _ga_path = automation_dir() / "scripts" / "generate_analytics.py"
            if not _ga_path.exists():
                return
            spec = _ilu.spec_from_file_location("generate_analytics", _ga_path)
            if not spec or not spec.loader:
                return
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.generate()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="analytics-refresh").start()


def _dev_worktree_enabled() -> bool:
    value = (os.getenv("AUTOMATION_DEV_WORKTREE") or "").strip().lower()
    return value in {"1", "true", "yes", "on", "enabled"}


def _feature_memory_dir() -> Path:
    path = automation_dir() / ".memory" / "devflow" / "features"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _feature_memory_file(feature_key: str) -> Path:
    safe_key = "".join(ch for ch in feature_key.upper() if ch.isalnum() or ch in {"-", "_"})
    return _feature_memory_dir() / f"{safe_key}.json"


def _save_feature_memory(feature_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = _feature_memory_file(feature_key)
    payload = {**payload, "updated_at_epoch": int(time.time())}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(path), "updated_at_epoch": payload["updated_at_epoch"]}


def _load_feature_memory(feature_key: str) -> dict[str, Any]:
    path = _feature_memory_file(feature_key)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _detect_phase(summary: str) -> str:
    text = (summary or "").lower()
    if "analys" in text:
        return "analysis"
    if "concept" in text or "design" in text:
        return "concept"
    if "implement" in text:
        return "implementation"
    if "test" in text or "validat" in text or "e2e" in text:
        return "testing"
    if "uat" in text:
        return "uat"
    if "go-live" in text or "go live" in text or "release" in text:
        return "go_live"
    if "doc" in text or "confluence" in text or "adr" in text:
        return "documentation"
    if "support" in text or "hypercare" in text:
        return "support"
    return "other"


def _build_story_description(feature_key: str, feature_summary: str, story_summary: str, phase: str, sprint_label: str) -> str:
    return (
        f"## User Story\n"
        f"As a platform engineer, I want to deliver **{story_summary}** so that feature `{feature_key}` can progress safely.\n\n"
        ":::info\n"
        "## Background\n"
        f"Feature: **{feature_summary}**\n"
        f"Planned phase: **{phase}**\n"
        f"Planned sprint: **{sprint_label}**\n"
        ":::"
        "\n\n## Key Data\n"
        "| Property | Value |\n"
        "|---|---|\n"
        f"| Feature Key | {feature_key} |\n"
        f"| Story Focus | {story_summary} |\n"
        f"| Phase | {phase} |\n"
        f"| Sprint | {sprint_label} |\n\n"
        ":::success\n"
        "## Implementation Scope\n"
        "1. Implement only the scoped behavior for this phase.\n"
        "2. Keep changes idempotent and observable.\n"
        "3. Add/update focused tests for changed behavior.\n"
        ":::"
        "\n\n:::warning\n"
        "## Constraints\n"
        "- Do not introduce unrelated refactors.\n"
        "- Preserve existing APIs unless explicitly required.\n"
        "- Keep error messages actionable for support users.\n"
        ":::"
        "\n\n:::note\n"
        "## Testing Notes\n"
        "- Validate happy path, permission failures, and idempotent re-run behavior.\n"
        "- Capture evidence needed for UAT/go-live checklist where relevant.\n"
        ":::"
    )


def _missing_phase_candidates(feature: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = ["analysis", "concept", "implementation", "testing", "uat", "documentation", "go_live", "support"]
    covered = {_detect_phase(item.get("summary") or "") for item in existing}
    missing = [phase for phase in required if phase not in covered]
    templates = {
        "testing": "End-to-end validation for automated prerequisite setup",
        "uat": "UAT execution and user sign-off for prerequisite automation",
        "documentation": "Confluence and runbook update for prerequisite automation",
        "go_live": "Go-live checklist and release readiness for prerequisite automation",
        "support": "Hypercare and support handover for prerequisite automation",
    }
    candidates = []
    for phase in missing:
        summary = templates.get(phase)
        if not summary:
            continue
        candidates.append(
            {
                "phase": phase,
                "summary": summary,
                "acceptance_criteria": [
                    "Scope and expected outcome are clearly documented and testable.",
                    "Failure paths and fallback handling are validated.",
                    "Evidence is captured for operational readiness and audits.",
                ],
            }
        )
    return candidates


def _apply_sprint_mapping(
    candidates: list[dict[str, Any]],
    *,
    sprint_id: int | None,
    sprint_name: str | None,
    sprint_by_phase: dict[str, dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sprint_by_phase:
        assigned = []
        missing = []
        for candidate in candidates:
            mapping = sprint_by_phase.get(candidate["phase"]) or {}
            if not mapping.get("sprint_id") and not mapping.get("sprint_name"):
                missing.append(candidate["phase"])
            assigned.append({**candidate, **mapping})
        return assigned, {"ok": not missing, "missing_phases": missing}
    if sprint_id is None and not (sprint_name or "").strip():
        return candidates, {"ok": False, "missing_phases": [c["phase"] for c in candidates]}
    assigned = [{**candidate, "sprint_id": sprint_id, "sprint_name": (sprint_name or "").strip()} for candidate in candidates]
    return assigned, {"ok": True, "missing_phases": []}


def dev_bootstrap_feature_stories(
    *,
    feature_key: str,
    confirm_create: bool = False,
    sprint_id: int | None = None,
    sprint_name: str | None = None,
    sprint_by_phase: dict[str, dict[str, Any]] | None = None,
    max_results: int = 200,
) -> dict[str, Any]:
    """Bootstrap feature stories in Dev profile with preview-first approval gates."""
    jira = import_helper("jira-mcp", "jira_client")
    feature = jira.read_issue(feature_key)
    existing = jira.search_issues(
        f"parent = {feature_key} AND issuetype = Story ORDER BY created ASC",
        max_results=max_results,
        only_with_sprint=False,
    )
    candidates = _missing_phase_candidates(feature, existing)
    assigned_candidates, sprint_check = _apply_sprint_mapping(
        candidates,
        sprint_id=sprint_id,
        sprint_name=sprint_name,
        sprint_by_phase=sprint_by_phase,
    )
    preview = []
    for item in assigned_candidates:
        sprint_label = item.get("sprint_name") or (f"Sprint-ID:{item['sprint_id']}" if item.get("sprint_id") is not None else "UNASSIGNED")
        preview.append(
            {
                "phase": item["phase"],
                "summary": item["summary"],
                "sprint_name": item.get("sprint_name"),
                "sprint_id": item.get("sprint_id"),
                "approved_acceptance_criteria": item["acceptance_criteria"],
                "approved_description": _build_story_description(
                    feature_key=feature_key,
                    feature_summary=feature.get("summary") or "",
                    story_summary=item["summary"],
                    phase=item["phase"],
                    sprint_label=sprint_label,
                ),
            }
        )
    _save_feature_memory(
        feature_key,
        {
            "feature": {"key": feature.get("key"), "summary": feature.get("summary"), "status": feature.get("status")},
            "existing_stories": existing,
            "preview_candidates": preview,
            "sprint_check": sprint_check,
        },
    )
    if existing:
        if not candidates:
            return {
                "ok": True,
                "mode": "existing_stories_complete",
                "feature": {"key": feature.get("key"), "summary": feature.get("summary")},
                "existing_story_count": len(existing),
                "existing_stories": existing,
                "message": "Feature already has stories with core phases. No new story creation is suggested by default.",
            }
        if not sprint_check["ok"]:
            return {
                "ok": False,
                "mode": "sprint_assignment_required",
                "feature": {"key": feature.get("key"), "summary": feature.get("summary")},
                "existing_story_count": len(existing),
                "existing_stories": existing,
                "missing_phase_candidates": [c["phase"] for c in candidates],
                "required": {
                    "accepted_inputs": ["sprint_id", "sprint_name", "sprint_by_phase"],
                    "rule": "Provide sprint mapping before preview/create. Sprint guessing is not allowed.",
                },
            }
        return {
            "ok": True,
            "mode": "preview_fill_missing_phases",
            "feature": {"key": feature.get("key"), "summary": feature.get("summary")},
            "existing_story_count": len(existing),
            "existing_stories": existing,
            "preview_candidates": preview,
            "approval_required": {
                "message": "Feature already has stories. These are only missing-phase candidates. Create only after explicit approval.",
                "next_call": "dev_create_feature_stories(feature_key=..., confirm_create=true)",
            },
        }
    if not sprint_check["ok"]:
        return {
            "ok": False,
            "mode": "sprint_assignment_required",
            "feature": {"key": feature.get("key"), "summary": feature.get("summary")},
            "existing_story_count": 0,
            "required": {
                "accepted_inputs": ["sprint_id", "sprint_name", "sprint_by_phase"],
                "rule": "Provide sprint mapping before preview/create. Sprint guessing is not allowed.",
            },
            "suggested_phases": [c["phase"] for c in candidates],
        }
    return {
        "ok": True,
        "mode": "preview_new_feature_stories",
        "feature": {"key": feature.get("key"), "summary": feature.get("summary")},
        "preview_candidates": preview,
        "approval_required": {
            "message": "Preview generated. No Jira write happened. Confirm before creation.",
            "next_call": "dev_create_feature_stories(feature_key=..., confirm_create=true)",
        },
        "confirm_create_requested": confirm_create,
    }


def _try_assign_sprint_to_issue(jira: Any, issue_key: str, sprint_id: int | None) -> dict[str, Any]:
    if sprint_id is None:
        return {"ok": False, "skipped": True, "reason": "sprint_id_not_provided"}
    try:
        config = jira.get_jira_config()
        sprint_field = jira.resolve_jira_field_id(config, "JIRA_SPRINT_FIELD") or "customfield_10020"
        jira.jira_request(
            f"issue/{issue_key}",
            config=config,
            method="PUT",
            body={"fields": {sprint_field: int(sprint_id)}},
        )
        return {"ok": True, "issue_key": issue_key, "sprint_id": int(sprint_id)}
    except Exception as exc:
        return {"ok": False, "issue_key": issue_key, "sprint_id": sprint_id, "error": str(exc)}


def dev_create_feature_stories(
    *,
    feature_key: str,
    confirm_create: bool = False,
    approved_phase_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create approved sprint-wise feature stories from the last bootstrap preview."""
    mem = _load_feature_memory(feature_key)
    preview = mem.get("preview_candidates") or []
    if not preview:
        return {
            "ok": False,
            "mode": "bootstrap_required",
            "message": "No preview candidates found. Run dev_bootstrap_feature_stories first.",
        }
    selected = preview
    if approved_phase_ids:
        approved = set(approved_phase_ids)
        selected = [item for item in preview if item.get("phase") in approved]
    if not confirm_create:
        return {
            "ok": True,
            "mode": "preview_only",
            "feature_key": feature_key,
            "selected_candidates": selected,
            "approval_required": {
                "message": "No Jira stories created yet. Confirm_create=true is required.",
                "next_call": "dev_create_feature_stories(feature_key=..., confirm_create=true)",
            },
        }
    jira = import_helper("jira-mcp", "jira_client")
    project_key = feature_key.split("-", 1)[0]
    created = []
    for item in selected:
        result = jira.create_story(
            project_key=project_key,
            parent_key=feature_key,
            summary=item["summary"],
            approved_description=item["approved_description"],
            approved_acceptance_criteria=item.get("approved_acceptance_criteria") or [],
            issue_type="Story",
            priority="Medium",
            confirm_create=True,
            codebase_scan_confirmed=True,
            feature_context_confirmed=True,
        )
        sprint_assignment = None
        story_key = result.get("story_key")
        if story_key:
            sprint_assignment = _try_assign_sprint_to_issue(jira, story_key, item.get("sprint_id"))
        created.append(
            {
                "phase": item.get("phase"),
                "summary": item.get("summary"),
                "create_result": result,
                "sprint_assignment": sprint_assignment,
                "planned_sprint_name": item.get("sprint_name"),
            }
        )
    return {
        "ok": True,
        "mode": "created",
        "feature_key": feature_key,
        "created_count": len(created),
        "created": created,
    }


def import_helper(server: str, module: str):
    path = automation_dir() / "mcp-servers" / server / "src" / f"{module}.py"
    spec = importlib.util.spec_from_file_location(f"devflow_{server}_{module}", path)
    if not spec or not spec.loader:
        raise DevFlowError(f"Could not load helper module: {path}")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def run_command(args: list[str], *, cwd: str | None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(args=args, returncode=127, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "Command timed out",
        )


def resolve_project_dir(working_dir: str | None = None) -> str:
    cwd = working_dir or os.getenv("DEV_WORKING_DIR") or os.getenv("GIT_WORKING_DIR") or os.getenv("WORKSPACE_DIR") or os.getcwd()
    path = Path(cwd).resolve()
    result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=str(path), timeout=20)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return str(path)


def git_snapshot(working_dir: str | None, base_branch: str) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    branch = run_command(["git", "branch", "--show-current"], cwd=repo, timeout=20)
    status = run_command(["git", "status", "--short"], cwd=repo, timeout=20)
    remote = run_command(["git", "remote"], cwd=repo, timeout=20)
    compare_ref = None
    for candidate in (f"origin/{base_branch}", base_branch):
        exists = run_command(["git", "rev-parse", "--verify", candidate], cwd=repo, timeout=20)
        if exists.returncode == 0:
            compare_ref = candidate
            break
    return {
        "repository": repo,
        "current_branch": branch.stdout.strip() if branch.returncode == 0 else "",
        "working_tree_status": status.stdout.strip() if status.returncode == 0 else "",
        "has_local_changes": bool(status.stdout.strip()) if status.returncode == 0 else False,
        "remotes": remote.stdout.splitlines() if remote.returncode == 0 else [],
        "compare_ref": compare_ref,
    }


def safe_call(func: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    try:
        return func(**kwargs)
    except TypeError:
        raise
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": exc.__class__.__name__}


def story_payload(jira_id: str, force_refresh: bool = False) -> dict[str, Any]:
    """Fetch the full Jira story context, using a short TTL cache when available.

    The cache avoids redundant Jira API round-trips within the same dev session.
    Pass force_refresh=True after a Jira update to guarantee fresh data.
    """
    # ── Try cache first (skip on force_refresh or after Jira writes) ──────────
    if _LANGCHAIN_AVAILABLE and _JiraCache is not None and not force_refresh:
        cache = _JiraCache()
        cached = cache.get(jira_id)
        if cached is not None:
            return cached

    # ── Fetch fresh from Jira ──────────────────────────────────────────────────
    jira = import_helper("jira-mcp", "jira_client")
    story = jira.read_issue(jira_id)
    feature = jira.feature_context_for_story(story)
    analysis = jira.analyze_story(story)
    refinement = {
        "ok": True,
        "mode": "proposal_only",
        **jira.build_refined_story_proposal(story, feature),
    }
    payload = {
        "story": story,
        "feature_context": feature,
        "analysis": analysis,
        "refinement_proposal": refinement,
    }

    # ── Store in cache for next call ────────────────────────────────────────────
    if _LANGCHAIN_AVAILABLE and _JiraCache is not None:
        _JiraCache().set(jira_id, payload)

    return payload


def codebase_analysis(story: dict[str, Any], working_dir: str | None, feature_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Scan the codebase for story terms so Jira writing is grounded in the project.

    Uses LangChain FAISS vector store when available (semantic, fast after first index).
    Falls back to ripgrep keyword scan when langchain/faiss are not installed.
    """
    repo = resolve_project_dir(working_dir)
    cognition_index = load_repo_cognition_index()
    feature = (feature_context or {}).get("feature") or {}
    completed = feature_context.get("completed_stories") if feature_context else []
    text_parts = [
        story.get("key", ""),
        story.get("summary", ""),
        story.get("description", ""),
        feature.get("key", ""),
        feature.get("name", ""),
        feature.get("description", ""),
        " ".join(item.get("summary", "") for item in completed or []),
        story.get("regulatory_justification", ""),
        story.get("reason_comments", ""),
        " ".join(story.get("acceptance_criteria") or []),
        " ".join(comment.get("body", "") for comment in story.get("comments") or []),
        " ".join(item.get("summary", "") for item in story.get("subtasks") or []),
        " ".join(item.get("summary", "") for item in story.get("linked_issues") or []),
    ]
    text = " ".join(str(part) for part in text_parts if part)
    words = []
    for token in text.replace("-", " ").replace("_", " ").split():
        cleaned = "".join(ch for ch in token.lower() if ch.isalnum())
        if len(cleaned) >= 4 and cleaned not in {"this", "that", "with", "from", "story", "change", "should"}:
            words.append(cleaned)
    keywords = list(dict.fromkeys(words))[:8]

    query = story.get("summary", "") + " " + " ".join(keywords[:4])
    files: list[str] = []
    scan_method = "ripgrep"
    vectorstore_metadata: dict[str, Any] = {}

    # ── LangChain vector store scan (preferred) ────────────────────────────────
    if _LANGCHAIN_AVAILABLE and _CodeVectorStore is not None:
        try:
            from langchain_helpers.repo_resolver import get_resolver
            vs_repo = str(get_resolver().repo_root)
            vs = _CodeVectorStore(repo_path=vs_repo)
            vectorstore_metadata = vs.metadata_summary()
            results = vs.search(query, k=10)
            if results:
                files = list(dict.fromkeys(r["file"] for r in results))[:24]
                scan_method = "faiss_vectorstore"
        except Exception:
            pass  # fall through to ripgrep

    # ── Ripgrep fallback ───────────────────────────────────────────────────────
    if not files and keywords:
        rg_args = ["rg", "--files-with-matches", "--ignore-case", "--glob", "!Automation/**", "--glob", "!target/**"]
        for keyword in keywords[:6]:
            rg_args.extend(["-e", keyword])
        result = run_command(rg_args, cwd=repo, timeout=15)
        if result.returncode == 0 and result.stdout.strip():
            files = result.stdout.splitlines()[:24]
            scan_method = "ripgrep"

    if not files and cognition_index.get("available"):
        layers = cognition_index.get("layers") or {}
        scored_files: dict[str, int] = {}
        for layer in layers.values():
            for file_path in layer.get("files") or []:
                lower_path = str(file_path).lower()
                score = sum(1 for keyword in keywords[:8] if keyword in lower_path)
                if score:
                    scored_files[file_path] = max(scored_files.get(file_path, 0), score)
        if scored_files:
            files = [
                file_path
                for file_path, _ in sorted(scored_files.items(), key=lambda item: (-item[1], item[0]))[:24]
            ]
            scan_method = "repo_cognition_index"

    matches = [{"keywords": keywords[:6], "files": files}] if files else []
    confidence = "high" if len(files) >= 4 else "medium" if files else "low"
    return {
        "repository": repo,
        "keywords": keywords,
        "matches": matches,
        "confidence": confidence,
        "scan_method": scan_method,
        "vectorstore_metadata": vectorstore_metadata,
        "repo_cognition_graph": cognition_matches_for_files(cognition_index, files),
        "can_write_story": confidence != "low" and bool((story.get("description") or "").strip()),
        "rule": "Do not update Jira story fields if the Feature context, Jira story, and codebase scan do not provide enough context.",
    }


def _excerpt(text: str, limit: int = 420) -> str:
    compact = " ".join((text or "").split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _compact_stage_response(payload: dict[str, Any], stage: str = "") -> dict[str, Any]:
    """Trim a stage response so large payloads don't bloat the IDE context window.

    Strategy:
    1. Call compact_mcp_response() when context_manager is available.
    2. Otherwise apply a simple per-field char limit to known large fields.

    This function should be called on every dict returned from dev_implement_story()
    before json_response() serialises it.
    """
    if _compact_mcp_response is not None:
        try:
            return _compact_mcp_response(payload, stage=stage)
        except Exception:
            pass

    # Minimal fallback: trim the most common bloat sources
    _LARGE_KEYS = {"description", "body", "content", "stdout", "stderr", "diff", "full_diff"}
    _CHAR_LIMIT = 800

    def _trim(obj: Any, depth: int = 0) -> Any:
        if depth > 5:
            return obj
        if isinstance(obj, dict):
            return {
                k: (_trim(v, depth + 1) if k not in _LARGE_KEYS else (v[:_CHAR_LIMIT] + "…" if isinstance(v, str) and len(v) > _CHAR_LIMIT else v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_trim(i, depth + 1) for i in obj[:20]]
        if isinstance(obj, str) and len(obj) > _CHAR_LIMIT * 2:
            return obj[: _CHAR_LIMIT * 2] + "…"
        return obj

    return _trim(payload)


def build_local_brain_knowledge_packet(
    *,
    story_context: dict[str, Any],
    code_context: dict[str, Any],
    memory_context: dict[str, Any] | None,
    repo_cognition: dict[str, Any] | None,
    confluence_content: dict[str, str] | None,
) -> dict[str, Any]:
    """Build a condensed knowledge feed for local_brain prompt injection.

    Confluence pages are now RAG-extracted (keyword-relevant lines only)
    instead of being injected as raw full-text excerpts.  This prevents
    per-call context growth when the same Confluence pages are re-used.
    """
    story = story_context.get("story") or {}
    feature_ctx = story_context.get("feature_context") or {}
    feature = feature_ctx.get("feature") or {}
    completed = feature_ctx.get("completed_stories") or []
    hotspots = (repo_cognition or {}).get("hotspots") or []
    mem = memory_context or {}
    stage_timings = mem.get("stage_timings") or []

    # RAG-extract confluence pages using code-context keywords
    keywords: list[str] = list((code_context.get("keywords") or [])[:6])
    confluence_pages = []
    for url, text in list((confluence_content or {}).items())[:6]:
        if _rag_extract is not None and keywords:
            snippet = _rag_extract(text, keywords, max_chars=360)
        else:
            snippet = _excerpt(text, 360)
        confluence_pages.append({"url": url, "excerpt": snippet})

    first_match = (code_context.get("matches") or [{}])[0]

    return {
        "story": {
            "key": story.get("key"),
            "summary": story.get("summary"),
            "status": story.get("status"),
            "acceptance_criteria_count": len(story.get("acceptance_criteria") or []),
        },
        "feature": {
            "key": feature.get("key"),
            "name": feature.get("name"),
            "status": feature.get("status"),
            "completed_stories_count": len(completed),
        },
        "code_context": {
            "confidence": code_context.get("confidence"),
            "scan_method": code_context.get("scan_method"),
            "keywords": (code_context.get("keywords") or [])[:8],
            "matched_files": (first_match.get("files") or [])[:12],
        },
        "repo_graph": {
            "available": bool((repo_cognition or {}).get("available")),
            "health": ((repo_cognition or {}).get("summary") or {}).get("health"),
            "high_coupling_files": ((repo_cognition or {}).get("summary") or {}).get("high_coupling_files"),
            "hotspots": [
                {
                    "file": item.get("file"),
                    "risk": item.get("risk"),
                    "note": item.get("note"),
                }
                for item in hotspots[:8]
            ],
        },
        "story_memory": {
            "stage": mem.get("stage"),
            "next_gate": mem.get("next_gate"),
            "readiness_blockers": mem.get("readiness_blockers") or [],
            "scan_method": mem.get("scan_method"),
            "updated_at_epoch": mem.get("updated_at_epoch"),
            "recent_stage_timings": stage_timings[-5:],
        },
        "confluence_pages": confluence_pages,
    }


def apply_story_refinement(
    *,
    jira_id: str,
    approved_story_summary: str | None,
    approved_story_description: str | None,
    approved_acceptance_criteria: list[str] | None,
    approved_regulatory_justification: str | None = None,
    approved_reason_comments: str | None = None,
    approved_comment: str | None = None,
) -> dict[str, Any]:
    jira = import_helper("jira-mcp", "jira_client")
    result = jira.refine_story(
        jira_id,
        apply_update=True,
        codebase_scan_confirmed=True,
        approved_summary=approved_story_summary,
        approved_description=approved_story_description,
        approved_acceptance_criteria=approved_acceptance_criteria,
        approved_regulatory_justification=approved_regulatory_justification,
        approved_reason_comments=approved_reason_comments,
        approved_comment=approved_comment,
    )
    refreshed = story_payload(jira_id) if result.get("ok") else None
    return {"update_result": result, "refreshed_story_context": refreshed}


def build_single_subtask_proposal(story: dict[str, Any]) -> dict[str, str]:
    """Build the one allowed DevFlow subtask proposal for the story."""
    key = story.get("key") or "STORY"
    summary = story.get("summary") or "requested story scope"
    return {
        "summary": f"{key}: Implement and validate scoped story change",
        "description": (
            f"Implement the minimum required change for {key}: {summary}. "
            "Validate the acceptance criteria, capture test evidence, and keep the work aligned with the approved story scope."
        ),
        "assignee_account_id": story.get("assignee_account_id") or os.getenv("JIRA_DEFAULT_ASSIGNEE_ACCOUNT_ID", ""),
        "priority": story.get("priority") or os.getenv("JIRA_DEFAULT_SUBTASK_PRIORITY") or "Medium",
    }


def apply_exactly_one_subtask(*, jira_id: str, story: dict[str, Any]) -> dict[str, Any]:
    """Create one subtask only when none exists; otherwise update one existing subtask."""
    jira = import_helper("jira-mcp", "jira_client")
    proposed = build_single_subtask_proposal(story)
    existing = story.get("subtasks") or []

    if existing:
        target = existing[0]
        current_description = target.get("description") or ""
        if (
            target.get("summary") == proposed["summary"]
            and (not current_description or current_description == proposed["description"])
            and (target.get("priority") or proposed["priority"]) == proposed["priority"]
        ):
            return {
                "ok": True,
                "mode": "no_change_existing_subtask",
                "rule": "Existing subtask already matches the one-subtask policy, so DevFlow did not update it.",
                "target_subtask": target,
                "existing_subtasks_count": len(existing),
            }
        assignee_account_id = (
            target.get("assignee_account_id")
            or story.get("assignee_account_id")
            or os.getenv("JIRA_DEFAULT_ASSIGNEE_ACCOUNT_ID", "")
        )
        if not assignee_account_id:
            return {
                "ok": False,
                "mode": "existing_subtask_update_blocked",
                "rule": "Existing subtask found, so DevFlow will not create a new subtask.",
                "target_subtask": target,
                "stop_reason": "Existing subtask cannot be updated because no assignee account id is available.",
            }
        updated = jira.update_subtask_fields(
            target["key"],
            proposed["summary"],
            proposed["description"],
            assignee_account_id,
            proposed["priority"],
        )
        return {
            "ok": True,
            "mode": "updated_existing_only",
            "rule": "Existing subtask was present, so DevFlow updated one existing subtask and did not create a new one.",
            "updated_subtask": updated,
            "existing_subtasks_count": len(existing),
        }

    plan = jira.manage_subtasks(
        jira_id,
        [proposed],
        apply_changes=True,
        confirm_apply=True,
        approved_action_ids=["subtask-1"],
    )
    return {
        "ok": bool(plan.get("ok")),
        "mode": "created_exactly_one" if plan.get("ok") else "create_one_blocked",
        "rule": "No existing subtask was present, so DevFlow attempted to create exactly one subtask.",
        "subtask_result": plan,
    }


def apply_approved_jira_updates(
    *,
    jira_id: str,
    story_context: dict[str, Any],
    approved_story_summary: str | None,
    approved_story_description: str | None,
    approved_acceptance_criteria: list[str] | None,
    approved_regulatory_justification: str | None,
    approved_reason_comments: str | None,
    approved_comment: str | None,
) -> dict[str, Any]:
    """Apply approved Jira story fields and the one-subtask policy."""
    story = story_context["story"]
    update_result = apply_story_refinement(
        jira_id=jira_id,
        approved_story_summary=approved_story_summary,
        approved_story_description=approved_story_description,
        approved_acceptance_criteria=approved_acceptance_criteria,
        approved_regulatory_justification=approved_regulatory_justification,
        approved_reason_comments=approved_reason_comments,
        approved_comment=approved_comment,
    )["update_result"]

    # Single refresh after both story + subtask writes (was 2 API calls before)
    refreshed_context = story_payload(jira_id, force_refresh=True) if update_result.get("ok") else story_context
    refreshed_story = refreshed_context["story"]
    subtask_result = apply_exactly_one_subtask(jira_id=jira_id, story=refreshed_story)
    # Reuse refreshed_context — subtask data is already embedded in refreshed_story
    final_context = refreshed_context
    return {
        "story_update_result": update_result,
        "subtask_result": subtask_result,
        "refreshed_story_context": final_context,
        "policy": [
            "Summary, Description, Acceptance Criteria, Regulatory Justification, Reason/Comments, and optional comments are updated only after approval.",
            "Exactly one subtask is created only when no subtask exists.",
            "If any subtask exists, DevFlow updates one existing subtask and does not create a new one.",
            "Linked work items are read for context; DevFlow does not create or change links without explicit link type and target key support.",
        ],
    }


def extract_acceptance_criteria(story: dict[str, Any], analysis: dict[str, Any]) -> list[str]:
    ac = story.get("acceptance_criteria")
    if isinstance(ac, list):
        return [str(item) for item in ac if str(item).strip()]
    proposal = analysis.get("proposed_acceptance_criteria") or analysis.get("acceptance_criteria") or []
    if isinstance(proposal, list):
        return [str(item) for item in proposal if str(item).strip()]
    return []


def branch_or_contract(
    *,
    story: dict[str, Any],
    base_branch: str,
    branch_type: str,
    working_dir: str | None,
    use_worktree: bool = False,
) -> dict[str, Any]:
    gitlab = import_helper("gitlab-mcp", "gitlab_client")
    summary = story.get("summary") or story.get("fields", {}).get("summary") or story.get("key") or ""
    snapshot = git_snapshot(working_dir, base_branch)
    branch = snapshot["current_branch"]
    on_base = branch in {base_branch, "main", "master", "develop", ""}

    result: dict[str, Any] = {
        "git_snapshot_before": snapshot,
        "branch_action": "reuse_current_branch",
        "branch_result": None,
    }
    if snapshot["has_local_changes"]:
        result["branch_action"] = "stop_existing_local_changes"
        result["stop_reason"] = "Existing local changes are present. Ask user whether these changes belong to this story or create a separate clean branch first."
        return result
    if use_worktree and not on_base:
        result["branch_action"] = "stop_non_base_branch_for_worktree"
        result["stop_reason"] = (
            "Isolated worktree mode requires the main repository to stay on the base branch. "
            f"Current branch is '{branch}'. Ask the user to switch to {base_branch} or provide a clean base workspace."
        )
        return result
    if on_base:
        result["branch_action"] = "create_story_branch"
        result["branch_result"] = safe_call(
            gitlab.create_branch,
            description=summary,
            base_branch=base_branch,
            story_id=story.get("key"),
            branch_type=branch_type,
            working_dir=snapshot["repository"],
            checkout=not use_worktree,
        )
    return result


def implementation_contract(story: dict[str, Any], analysis: dict[str, Any], base_branch: str) -> dict[str, Any]:
    ac = extract_acceptance_criteria(story, analysis)
    return {
        "story": {
            "key": story.get("key"),
            "summary": story.get("summary"),
            "status": story.get("status"),
            "priority": story.get("priority"),
            "acceptance_criteria": ac,
        },
        "coding_rules": [
            "Make the smallest code change required for the selected story or subtask.",
            "Read only the files needed for the implementation.",
            "Follow the existing project structure, naming, logging, validation, and error handling style.",
            "Prefer modifying existing classes and methods over adding new files unless required.",
            "Do not perform unrelated cleanup, refactoring, renaming, formatting churn, or dependency changes.",
            "Do not leave unused imports, debug logs, TODOs, commented-out code, or extra blank lines.",
            "Add or update focused tests when behavior changes and existing test patterns are present.",
        ],
        "implementation_order": [
            "Search the codebase for story keywords and existing related flow.",
            "Identify the minimal impacted files.",
            "Implement backend/API/config/frontend changes only if required by the story.",
            "Add or update focused tests.",
            "Run the recommended test command.",
            "Call dev_implement_story stage='after_code_changes' after code changes and tests.",
        ],
        "large_change_gate": "If the implementation appears to touch more than 8 files or multiple layers, stop and ask the user before continuing.",
        "base_branch": base_branch,
    }


def build_effort_report(
    *,
    story: dict[str, Any],
    analysis: dict[str, Any],
    code_context: dict[str, Any],
    base_branch: str,
) -> dict[str, Any]:
    """Build a pre-code effort and impact report shown to the user BEFORE code changes start.

    This report must be read and explicitly approved by the user before stage='after_story_approval'
    is called. It covers estimated effort, impacted files, acceptance criteria, and implementation plan.
    """
    matched_files: list[str] = []
    for match in code_context.get("matches") or []:
        matched_files.extend(match.get("files") or [])
    file_count = len(matched_files)

    # Effort estimation: low / medium / high based on matched files and AC count
    ac = extract_acceptance_criteria(story, analysis)
    ac_count = len(ac)
    if file_count == 0:
        effort_level = "unknown"
        effort_reason = "No codebase files matched the story keywords. Scope cannot be estimated yet."
    elif file_count <= 3 and ac_count <= 3:
        effort_level = "low"
        effort_reason = f"{file_count} file(s) matched and {ac_count} acceptance criteria — small, focused change expected."
    elif file_count <= 7 and ac_count <= 6:
        effort_level = "medium"
        effort_reason = f"{file_count} file(s) matched and {ac_count} acceptance criteria — moderate change across a few classes."
    else:
        effort_level = "high"
        effort_reason = f"{file_count} file(s) matched and {ac_count} acceptance criteria — larger cross-cutting change, proceed carefully."

    risk_flags: list[str] = []
    if file_count > 8:
        risk_flags.append(f"More than 8 files matched ({file_count}). This may be a large change — verify scope before starting.")
    if file_count == 0:
        risk_flags.append("No files matched story keywords. Manual codebase exploration required before coding.")
    if not ac:
        risk_flags.append("No acceptance criteria found. Cannot verify completeness of the change.")
    if (story.get("description") or "").strip() == "":
        risk_flags.append("Story description is empty. Implementation scope is unclear.")

    contract = implementation_contract(story, analysis, base_branch)

    return {
        "effort_estimate": {
            "level": effort_level,
            "reason": effort_reason,
            "matched_file_count": file_count,
            "acceptance_criteria_count": ac_count,
        },
        "impacted_files_preview": matched_files[:12],
        "keywords_used_for_scan": code_context.get("keywords") or [],
        "codebase_confidence": code_context.get("confidence"),
        "acceptance_criteria": ac,
        "risk_flags": risk_flags,
        "implementation_plan": contract,
        "approval_gate": {
            "name": "Code change approval required",
            "instruction": (
                "STOP — Show this full effort report to the user. "
                "The user must read the effort level, impacted files, acceptance criteria, risk flags, "
                "and implementation plan BEFORE approving code changes. "
                "Do not call stage='after_story_approval' until the user explicitly says YES."
            ),
            "required_user_question": (
                "I have completed the Jira update and analysed the codebase. "
                "Here is the full effort report including estimated effort, impacted files, "
                "acceptance criteria, and implementation plan. "
                "Please review everything above. Do you approve starting the code changes?"
            ),
        },
    }


def run_tests_in_workflow(
    *,
    working_dir: str | None,
    command: str | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Autonomously detect project type, select the best test command, and execute it.

    Uses ProjectDetector to pick the right test command for the project type
    (Maven, Gradle, npm, pytest, go test, etc.) before falling back to the
    test-mcp auto-discovery.
    """
    test_client = import_helper("test-mcp", "test_client")
    repo = test_client.resolve_project_dir(working_dir)

    # ── Step 1: ProjectDetector gives us the right command for this project ─────
    selected_command = command
    if not selected_command and _LANGCHAIN_AVAILABLE:
        try:
            from langchain_helpers.project_detector import detect_project
            detector = detect_project(repo)
            selected_command = detector.test_command
        except Exception:
            pass

    # ── Step 2: Fallback to test-mcp auto-discovery ───────────────────────────
    if not selected_command:
        discovered = safe_call(test_client.discover_test_commands, working_dir=repo)
        selected_command = discovered.get("recommended_unit_command") if discovered.get("ok") else None

    if not selected_command:
        return {
            "ok": False,
            "executed": False,
            "reason": "No test command could be detected. Pass command=... explicitly.",
        }

    # Step 2: run the tests via subprocess — fully autonomous, no IDE terminal needed
    execution = safe_call(
        test_client.execute_test_command,
        selected_command,
        working_dir=repo,
        timeout_seconds=timeout_seconds,
    )

    # Trim stdout/stderr to prevent test output from bloating IDE context
    _MAX_TEST_OUTPUT = int(os.getenv("AUTOMATION_CTX_TOOL_MAX_CHARS", "800"))
    passed = execution.get("ok", False)
    raw_stdout = execution.get("stdout", "")
    raw_stderr = execution.get("stderr", "")
    trimmed_stdout = raw_stdout[-_MAX_TEST_OUTPUT:] if len(raw_stdout) > _MAX_TEST_OUTPUT else raw_stdout
    trimmed_stderr = raw_stderr[-_MAX_TEST_OUTPUT:] if len(raw_stderr) > _MAX_TEST_OUTPUT else raw_stderr
    combined_output = "\n".join(part for part in (raw_stdout, raw_stderr) if part)

    # Step 3: post-execution failure analysis
    failure_analysis = (
        safe_call(test_client.analyze_failure_text, combined_output)
        if not passed
        else {"status": "passed", "summary": "All tests passed."}
    )

    # Step 4: collect any report artifacts (surefire XML etc.)
    reports = safe_call(test_client.collect_test_reports, working_dir=repo, max_files=10)

    return {
        "ok": passed,
        "executed": True,
        "autonomous": True,
        "repository": repo,
        "command_used": selected_command,
        "exit_code": execution.get("exit_code", -1),
        "stdout": trimmed_stdout,
        "stderr": trimmed_stderr,
        "verdict": "passed" if passed else "failed",
        "failure_analysis": failure_analysis,
        "test_reports": {
            "count": (reports.get("report_count") or 0) if reports.get("ok") else 0,
            "paths": [r["path"] for r in (reports.get("reports") or [])] if reports.get("ok") else [],
        },
        "next_actions": (
            []
            if passed
            else [
                "Review the failure_analysis.important_lines for the root cause.",
                "Fix the failing test or source code before creating the MR.",
                "Re-run via dev_implement_story(stage='after_code_changes', run_tests=True) after fixing.",
            ]
        ),
    }


def after_code_report(
    *,
    story: dict[str, Any],
    analysis: dict[str, Any],
    base_branch: str,
    test_output: str,
    working_dir: str | None,
    run_tests: bool = False,
    test_command: str | None = None,
    test_timeout_seconds: int = 600,
) -> dict[str, Any]:
    test_client = import_helper("test-mcp", "test_client")
    review_client = import_helper("review-mcp", "review_client")
    ac = extract_acceptance_criteria(story, analysis)
    summary = story.get("summary") or ""

    # Autonomous test execution via subprocess (no IDE terminal needed)
    autonomous_test_result: dict[str, Any] | None = None
    effective_test_output = test_output
    if run_tests:
        autonomous_test_result = run_tests_in_workflow(
            working_dir=working_dir,
            command=test_command,
            timeout_seconds=test_timeout_seconds,
        )
        if autonomous_test_result.get("executed"):
            parts = [autonomous_test_result.get("stdout", ""), autonomous_test_result.get("stderr", "")]
            effective_test_output = "\n".join(p for p in parts if p) or test_output

    # ── LangChain-powered failure analysis (when tests fail) ───────────────────
    if (
        _LANGCHAIN_AVAILABLE
        and _StoryAnalyzer is not None
        and autonomous_test_result is not None
        and not autonomous_test_result.get("ok", True)
    ):
        try:
            analyzer = _StoryAnalyzer()
            ai_analysis = analyzer.analyze_test_failure(
                failure_output=effective_test_output,
                source_snippets=[],
            )
            if autonomous_test_result.get("failure_analysis"):
                autonomous_test_result["failure_analysis"]["ai_root_cause"] = ai_analysis
            else:
                autonomous_test_result["failure_analysis"] = {"ai_root_cause": ai_analysis}
        except Exception:
            pass  # keep original failure_analysis on any error

    return {
        "autonomous_test_execution": autonomous_test_result,
        "test_report": safe_call(
            test_client.current_change_test_report,
            base_branch=base_branch,
            latest_output=effective_test_output,
            working_dir=working_dir,
        ),
        "review_report": safe_call(
            review_client.full_current_change_review,
            story_id=story.get("key") or "",
            story_summary=summary,
            acceptance_criteria=ac,
            base_branch=base_branch,
            working_dir=working_dir,
        ),
        "stop_point": {
            "name": "MR approval required",
            "message": "Code changes, testing, and review report are ready. Ask the user whether to create the merge request.",
            "next_call": "dev_implement_story(stage='create_mr', create_mr_approved=true, tests_done=true, review_done=true)",
        },
    }


def validate_mr_readiness(*, story_key: str, branch: str, target_branch: str, prepared: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    allowed_prefixes = ("feature/", "feat/", "fix/", "bugfix/", "hotfix/", "chore/", "refactor/", "test/", "perf/", "security/", "docs/")
    blocked_prefixes = ("review/", "temp/", "tmp/", "wip/", "local/")
    lowered = branch.lower()
    story_key_lower = story_key.lower()

    if not branch:
        problems.append("Current Git branch could not be resolved.")
    if branch in {target_branch, "main", "master", "develop"}:
        problems.append(f"Current branch '{branch}' is a base branch. Create/use a story branch before MR creation.")
    if lowered.startswith(blocked_prefixes):
        problems.append(f"Current branch '{branch}' is not a development branch. Use feature/fix/chore style branch naming.")
    if not lowered.startswith(allowed_prefixes):
        problems.append(f"Current branch '{branch}' does not use an approved MR branch prefix: {', '.join(allowed_prefixes)}.")
    if story_key_lower and story_key_lower not in lowered:
        problems.append(f"Current branch '{branch}' does not contain story key {story_key}.")
    if not prepared.get("template_used"):
        problems.append("Project Default.md merge request template was not found/read. MR creation must use .gitlab/merge_request_templates/Default.md.")

    title = prepared.get("title") or prepared.get("merge_request", {}).get("title") or ""
    description = prepared.get("description") or prepared.get("merge_request", {}).get("description") or ""
    if story_key and story_key not in title:
        problems.append(f"Prepared MR title does not contain story key {story_key}.")
    if len(description.strip()) < 200:
        problems.append("Prepared MR description is too short. Use the project Default.md template with summary, implementation, tests, and risk notes.")
    if prepared.get("local_status"):
        problems.append("Working tree still has local uncommitted changes. Commit or intentionally handle them before MR creation.")
    return problems


def create_mr_flow(
    *,
    story: dict[str, Any],
    base_branch: str,
    target_branch: str,
    draft_mr: bool,
    mr_title: str | None,
    mr_description: str | None,
    testing_notes: str,
    working_dir: str | None,
) -> dict[str, Any]:
    gitlab = import_helper("gitlab-mcp", "gitlab_client")
    repo = resolve_project_dir(working_dir)
    story_key = story.get("key") or ""
    summary = story.get("summary") or ""
    branch = git_snapshot(repo, base_branch)["current_branch"]
    prepared = safe_call(
        gitlab.prepare_merge_request,
        story_id=story.get("key") or "",
        story_summary=summary,
        change_type="feature",
        target_branch=target_branch,
        working_dir=repo,
        testing_notes=testing_notes or "DevFlow code, test, and review checkpoints were completed before MR creation.",
    )
    readiness_problems = validate_mr_readiness(story_key=story_key, branch=branch, target_branch=target_branch, prepared=prepared)
    if readiness_problems:
        return {
            "ok": False,
            "current_branch": branch,
            "prepared_merge_request": prepared,
            "stop_reason": "MR creation blocked by DevFlow safety checks.",
            "readiness_problems": readiness_problems,
            "required_fix": "Move the code to a proper story branch and rerun create_mr after tests/review are complete.",
        }
    title = mr_title or prepared.get("title")
    description = prepared.get("description")
    if not title or not description:
        return {
            "ok": False,
            "prepared_merge_request": prepared,
            "stop_reason": "MR title/description could not be resolved from the project Default.md template.",
        }
    push = safe_call(gitlab.push_branch, branch=branch, remote="origin", set_upstream=True, working_dir=repo, dry_run=False)
    if not push.get("ok"):
        return {"ok": False, "prepared_merge_request": prepared, "push_result": push, "stop_reason": "Branch push failed. Fix push issue before creating MR."}
    mr = safe_call(
        gitlab.create_merge_request,
        source_branch=branch,
        target_branch=target_branch,
        title=title,
        description=description,
        draft=draft_mr,
        labels=None,
        reviewer_usernames=None,
        assignee_usernames=None,
        remove_source_branch=False,
    )
    return {"ok": bool(mr.get("ok")), "prepared_merge_request": prepared, "push_result": push, "merge_request_result": mr}


def start_response(
    *,
    jira_id: str,
    user_request: str,
    story_context: dict[str, Any],
    code_context: dict[str, Any],
    memory_context: dict[str, Any] | None,
    repo_cognition: dict[str, Any] | None,
    confluence_content: dict[str, str] | None,
    working_dir: str | None,
    base_branch: str,
) -> dict[str, Any]:
    """Build the start-stage response from deterministic workflow state."""
    story = story_context["story"]
    feature = story_context["feature_context"]
    proposal = story_context["refinement_proposal"].get("proposal", {})
    knowledge_packet = build_local_brain_knowledge_packet(
        story_context=story_context,
        code_context=code_context,
        memory_context=memory_context,
        repo_cognition=repo_cognition,
        confluence_content=confluence_content,
    )
    readiness_blockers = []
    if not feature.get("ok"):
        readiness_blockers.append(feature.get("stop_reason") or "Parent Feature context is required before Jira story writing.")
    if not (story.get("description") or "").strip():
        readiness_blockers.append("Jira story description is empty or too thin.")
    if code_context["confidence"] == "low":
        readiness_blockers.append("Codebase scan found too little project context to safely rewrite the story.")
    local_brain = (
        _classify_local_request(
            user_request,
            story=story,
            code_context=code_context,
            knowledge_packet=knowledge_packet,
        )
        if _classify_local_request is not None
        else {
            "engine": "unavailable",
            "task_size": "complex",
            "route": "devflow_main_agent",
            "can_handle_locally": False,
            "approval_required": True,
            "reason": "Local brain helper is unavailable.",
        }
    )

    return {
        "ok": True,
        "tool": "dev_implement_story",
        "stage": "start",
        "runtime_build_marker": "knowledge-packet-v1",
        "local_brain": local_brain,
        "story_context": story_context,
        "feature_context": feature,
        "codebase_analysis": code_context,
        "knowledge_packet_sections": list(knowledge_packet.keys()),
        "proposed_jira_update": {
            "summary": proposal.get("summary"),
            "description": proposal.get("description_preview", {}).get("Overview"),
            "acceptance_criteria": proposal.get("description_preview", {}).get("Acceptance Criteria", []),
            "regulatory_justification": proposal.get("description_preview", {}).get("Regulatory Justification"),
            "reason_comments": proposal.get("description_preview", {}).get("Reason/Comments"),
            "subtask_policy": "After approval, create exactly one subtask only when none exists; otherwise update one existing subtask.",
            "linked_work_items": story.get("linked_issues") or [],
            "feature_alignment": {
                "feature": feature.get("feature"),
                "current_story_fit": feature.get("current_story_fit"),
                "completed_stories": feature.get("completed_stories", [])[:10],
                "total_sibling_count": feature.get("total_sibling_count"),
            },
            "implementation_basis": "Use the parent Feature goal, completed sibling stories, current story scope, and codebase matches before writing Jira fields.",
            "optional_activity_comment": "Only add a Jira activity comment if the user approves exact comment text.",
        },
        "readiness_blockers": readiness_blockers,
        "write_allowed": not readiness_blockers,
        "git_snapshot": git_snapshot(working_dir, base_branch),
        "stop_point": {
            "name": "Jira story update approval required",
            "message": (
                "Review the parent Feature, completed sibling stories, full Jira story, codebase analysis, and proposed Jira field updates. "
                "If write_allowed=false, do not update Jira; ask the user for clarification. "
                "If write_allowed=true, ask the user to approve updating title, Description, Acceptance Criteria, Regulatory Justification, Reason/Comments, and the one-subtask policy."
            ).format(jira_id=jira_id),
            "next_call": "dev_implement_story(stage='apply_story_update', story_update_approved=true, approved_story_description=..., approved_acceptance_criteria=..., approved_regulatory_justification=..., approved_reason_comments=...)",
            "required_question_order": [
                "I read the parent Feature, completed sibling stories, full Jira story, and scanned the codebase. Here is the proposed Jira update. Do you approve updating the Jira story fields and one subtask?",
                "If the story/code context is unclear, ask for clarification instead of updating Jira.",
            ],
        },
    }


def run_start_workflow(*, jira_id: str, user_request: str, working_dir: str | None, base_branch: str) -> dict[str, Any]:
    """Run the start stage through LangGraph.

    Graph topology (parallel fetch):

        load_memory
             │
       ┌─────┴──────┐
       ▼            ▼
    load_context  load_memory_kv   ← runs concurrently
       │
    scan_codebase ← uses LangChain vector store when available
       │
    build_response
       │
    save_memory
    """

    def load_memory_node(state: dict[str, Any]) -> dict[str, Any]:
        state["memory"] = load_story_memory(jira_id)
        return state

    def load_context_node(state: dict[str, Any]) -> dict[str, Any]:
        if not state.get("story_context"):
            state["story_context"] = story_payload(jira_id)
        return state

    def load_repo_cognition_node(state: dict[str, Any]) -> dict[str, Any]:
        if not state.get("repo_cognition"):
            state["repo_cognition"] = load_repo_cognition_index()
        return state

    def scan_codebase_node(state: dict[str, Any]) -> dict[str, Any]:
        story_context = state["story_context"]
        state["code_context"] = codebase_analysis(
            story_context["story"], working_dir, story_context["feature_context"]
        )
        return state

    def enrich_with_confluence_node(state: dict[str, Any]) -> dict[str, Any]:
        """Fetch linked ADR pages via ConfluenceCacheLoader (TTL-cached)."""
        if not (_LANGCHAIN_AVAILABLE and _ConfluenceCacheLoader is not None):
            state["confluence_content"] = {}
            return state
        story = state["story_context"]["story"]
        description = story.get("description") or ""
        # Extract Confluence/wiki URLs from the story description
        import re
        _jira_base = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
        _wiki_host = re.escape(_jira_base) if _jira_base else r"https://[\w.-]+\.atlassian\.net"
        urls = re.findall(rf"{_wiki_host}/wiki/[^\s\)\"']+", description)
        loader = _ConfluenceCacheLoader()
        content: dict[str, str] = {}
        for url in urls[:3]:  # limit to 3 ADR pages max
            try:
                text = loader.load_url(url)
                if text:
                    content[url] = text
            except Exception:
                pass

        # Pull cached Confluence memory even when story has no explicit links.
        if len(content) < 3:
            try:
                index_path = loader.cache_dir / "index.json"
                if index_path.exists():
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                    pages = index.get("cached_pages") or []
                    ranked: list[tuple[int, str, Path]] = []
                    for entry in pages:
                        page_url = str(entry.get("url") or "")
                        cache_name = str(entry.get("cache_file") or "")
                        if not page_url or not cache_name:
                            continue
                        cache_path = loader.cache_dir / cache_name
                        if not cache_path.exists():
                            continue
                        try:
                            payload = json.loads(cache_path.read_text(encoding="utf-8"))
                            ts = int((payload.get("_meta") or {}).get("cached_at_epoch") or 0)
                            ranked.append((ts, page_url, cache_path))
                        except Exception:
                            continue
                    for _, page_url, cache_path in sorted(ranked, key=lambda item: item[0], reverse=True):
                        if len(content) >= 3:
                            break
                        if page_url in content:
                            continue
                        try:
                            payload = json.loads(cache_path.read_text(encoding="utf-8"))
                            cached_text = str(payload.get("content") or "")
                            if cached_text:
                                content[page_url] = cached_text
                        except Exception:
                            continue
            except Exception:
                pass
        state["confluence_content"] = content
        return state

    def build_response_node(state: dict[str, Any]) -> dict[str, Any]:
        response = start_response(
            jira_id=jira_id,
            user_request=user_request,
            story_context=state["story_context"],
            code_context=state["code_context"],
            memory_context=state.get("memory"),
            repo_cognition=state.get("repo_cognition"),
            confluence_content=state.get("confluence_content"),
            working_dir=working_dir,
            base_branch=base_branch,
        )
        # Attach confluence content to the codebase_analysis block for downstream use
        if state.get("confluence_content"):
            response["codebase_analysis"]["confluence_context"] = {
                url: text[:500] for url, text in state["confluence_content"].items()
            }
        response["repo_cognition_preflight"] = repo_cognition_summary(
            state.get("repo_cognition") or load_repo_cognition_index()
        )
        state["response"] = response
        return state

    def save_memory_node(state: dict[str, Any]) -> dict[str, Any]:
        response = state["response"]
        state["memory_write"] = save_story_memory(
            jira_id,
            {
                "jira_id": jira_id,
                "stage": "start",
                "story": state["story_context"].get("story"),
                "story_updated": (state["story_context"].get("story") or {}).get("updated"),
                "feature_key": ((state["story_context"].get("feature_context") or {}).get("feature") or {}).get("key"),
                "feature_context": state["story_context"].get("feature_context"),
                "repo_cognition": repo_cognition_summary(state.get("repo_cognition") or {}),
                "codebase_analysis": state["code_context"],
                "local_brain": response.get("local_brain"),
                "confluence_content_keys": list(state.get("confluence_content", {}).keys()),
                "write_allowed": response.get("write_allowed"),
                "readiness_blockers": response.get("readiness_blockers"),
                "next_gate": response.get("stop_point", {}).get("name"),
                "langchain_available": _LANGCHAIN_AVAILABLE,
                "scan_method": state["code_context"].get("scan_method", "ripgrep"),
            },
        )
        response["workflow"] = {
            "engine": "langgraph" if StateGraph else "sequential_fallback",
            "langchain_enabled": _LANGCHAIN_AVAILABLE,
            "memory": state["memory_write"],
            "loaded_memory": bool(state.get("memory")),
        }
        return state

    # ── Build and run the LangGraph ────────────────────────────────────────────
    initial_state: dict[str, Any] = {
        "jira_id": jira_id,
        "user_request": user_request,
        "working_dir": working_dir,
        "base_branch": base_branch,
        "repo_cognition": {},
        "story_context": {},
        "code_context": {},
        "confluence_content": {},
        "memory": {},
        "response": {},
    }

    if _LANGCHAIN_AVAILABLE and _run_parallel is not None:
        # ── Fast path: Jira + memory + git fetch all run in parallel threads ───
        parallel_results = _run_parallel(
            ("story_context",    lambda: story_payload(jira_id)),
            ("memory",           lambda: load_story_memory(jira_id)),
            ("repo_cognition",   load_repo_cognition_index),
            ("git",              lambda: git_snapshot(working_dir, base_branch)),
        )
        initial_state["story_context"] = parallel_results.get("story_context") or {}
        initial_state["memory"] = parallel_results.get("memory") or {}
        initial_state["repo_cognition"] = parallel_results.get("repo_cognition") or {}

    if StateGraph:
        graph = StateGraph(dict)
        graph.add_node("load_memory", load_memory_node)
        graph.add_node("load_context", load_context_node)
        graph.add_node("load_repo_cognition", load_repo_cognition_node)
        graph.add_node("enrich_with_confluence", enrich_with_confluence_node)
        graph.add_node("scan_codebase", scan_codebase_node)
        graph.add_node("build_response", build_response_node)
        graph.add_node("save_memory", save_memory_node)

        # Keep graph updates linear to avoid concurrent dict-state merge collisions
        # (INVALID_CONCURRENT_GRAPH_UPDATE on __root__).
        graph.set_entry_point("load_memory")
        graph.add_edge("load_memory", "load_context")
        graph.add_edge("load_context", "load_repo_cognition")
        graph.add_edge("load_repo_cognition", "enrich_with_confluence")
        graph.add_edge("enrich_with_confluence", "scan_codebase")
        graph.add_edge("scan_codebase", "build_response")
        graph.add_edge("build_response", "save_memory")
        graph.add_edge("save_memory", END)

        state = graph.compile().invoke(initial_state)
    else:
        state = initial_state
        for node_fn in (
            load_memory_node,
            load_context_node,
            load_repo_cognition_node,
            enrich_with_confluence_node,
            scan_codebase_node,
            build_response_node,
            save_memory_node,
        ):
            state = node_fn(state)

    return state["response"]


def _load_confluence_for_story(story_context: dict[str, Any]) -> dict[str, str]:
    """Load Confluence content for the story (from cache/URLs). Standalone helper."""
    if not (_LANGCHAIN_AVAILABLE and _ConfluenceCacheLoader is not None):
        return {}
    import re
    story = story_context.get("story") or {}
    description = story.get("description") or ""
    _jira_base = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
    _wiki_host = re.escape(_jira_base) if _jira_base else r"https://[\w.-]+\.atlassian\.net"
    urls = re.findall(rf"{_wiki_host}/wiki/[^\s\)\"']+", description)
    loader = _ConfluenceCacheLoader()
    content: dict[str, str] = {}
    for url in urls[:3]:
        try:
            text = loader.load_url(url)
            if text:
                content[url] = text
        except Exception:
            pass
    if len(content) < 3:
        try:
            index_path = loader.cache_dir / "index.json"
            if index_path.exists():
                index = json.loads(index_path.read_text(encoding="utf-8"))
                pages = sorted(
                    [p for p in (index.get("cached_pages") or []) if p.get("url") and p.get("cache_file")],
                    key=lambda p: int(((json.loads((loader.cache_dir / p["cache_file"]).read_text(encoding="utf-8")).get("_meta") or {}).get("cached_at_epoch") or 0)) if (loader.cache_dir / p["cache_file"]).exists() else 0,
                    reverse=True,
                )
                for entry in pages:
                    if len(content) >= 3:
                        break
                    page_url = str(entry.get("url") or "")
                    if page_url in content:
                        continue
                    cache_path = loader.cache_dir / str(entry["cache_file"])
                    if not cache_path.exists():
                        continue
                    try:
                        payload = json.loads(cache_path.read_text(encoding="utf-8"))
                        cached_text = str(payload.get("content") or "")
                        if cached_text:
                            content[page_url] = cached_text[:2000]
                    except Exception:
                        continue
        except Exception:
            pass
    return content


def _knowledge_fields_for_stage(
    jira_id: str,
    story_context: dict[str, Any],
    working_dir: str | None,
    user_request: str,
) -> dict[str, Any]:
    """Build lightweight knowledge metadata for non-start stages.

    Performance strategy (fast path first):
    1. If local brain is disabled → skip full codebase scan entirely.
       Return only the cached analysis already saved in story memory from start stage.
    2. If local brain is enabled → run full scan (needed for LLM classification).
    3. Never re-fetch Confluence on non-start stages (use cached keys from memory).

    This avoids running expensive FAISS / ripgrep scans on every gate stage.
    """
    # ── Fast path: local brain disabled → use cached analysis from start stage ─
    local_brain_on = (os.getenv("AUTOMATION_LOCAL_BRAIN", "false").strip().lower()
                      in {"1", "true", "yes", "on", "enabled", "auto"})

    if not local_brain_on:
        mem = load_story_memory(jira_id)
        # Reuse codebase_analysis saved during start stage — free, no scan
        cached_code = mem.get("codebase_analysis") or {}
        kp_sections = ["story", "feature", "code_context", "story_memory"]
        return {
            "runtime_build_marker": "knowledge-packet-v1",
            "knowledge_packet_sections": kp_sections,
            "local_brain": {
                "engine": "rules",
                "local_model_used": False,
                "task_size": "complex",
                "route": "devflow_main_agent",
                "can_handle_locally": False,
                "approval_required": True,
                "cached_code_confidence": cached_code.get("confidence", "unknown"),
                "cached_scan_method": cached_code.get("scan_method", "none"),
            },
        }

    # ── Full path: local brain enabled → run complete scan for LLM ────────────
    try:
        story = story_context.get("story") or {}
        feature_ctx = story_context.get("feature_context") or {}
        code_context = codebase_analysis(story, working_dir, feature_ctx)
        mem = load_story_memory(jira_id)
        repo_cog = load_repo_cognition_index()
        # Skip Confluence re-fetch — use cached content keys from memory
        confluence_content: dict[str, str] = {}
        kp = build_local_brain_knowledge_packet(
            story_context=story_context,
            code_context=code_context,
            memory_context=mem,
            repo_cognition=repo_cog,
            confluence_content=confluence_content,
        )
        lb = (
            _classify_local_request(
                user_request or f"stage: {jira_id}",
                story=story,
                code_context=code_context,
                knowledge_packet=kp,
            )
            if _classify_local_request is not None
            else {"engine": "unavailable"}
        )
    except Exception as _exc:
        kp = {}
        lb = {"engine": "unavailable", "error": str(_exc)}
    return {
        "runtime_build_marker": "knowledge-packet-v1",
        "knowledge_packet_sections": list(kp.keys()),
        "local_brain": lb,
    }


def dev_plan_feature_stories(
    *,
    feature_key: str,
    user_request: str = "",
    working_dir: str | None = None,
    max_results: int = 200,
) -> dict[str, Any]:
    """Plan feature stories sprint-wise using Jira feature context + repo cognition."""
    jira = import_helper("jira-mcp", "jira_client")
    plan = jira.plan_feature_stories(feature_key=feature_key, max_results=max_results)

    repo_cognition = repo_cognition_summary(load_repo_cognition_index())
    plan["devflow_context"] = {
        "working_dir": working_dir or "not_provided",
        "repo_cognition": repo_cognition,
        "request": user_request or f"Plan stories for feature {feature_key}",
        "next_gate": {
            "message": (
                "Should I run the Feature bootstrap flow now to generate sprint-wise BDRSP-1623 story candidates "
                "when no child stories exist?"
            ),
            "if_yes": (
                "Use dev_bootstrap_feature_stories(confirm_create=false), treating this Feature key as parent_key. "
                "If sprint is missing or ambiguous, ask the user before preview/create."
            ),
        },
    }
    return {
        "ok": True,
        "feature_key": feature_key,
        "feature_story_plan": plan,
    }


def dev_create_feature_stories(
    *,
    project_key: str,
    parent_key: str,
    stories: list[dict[str, Any]],
    confirm_create: bool = False,
    codebase_scan_confirmed: bool = False,
    feature_context_confirmed: bool = False,
    working_dir: str | None = None,
) -> dict[str, Any]:
    """Preview or create a feature story batch through Jira with DevFlow context."""
    jira = import_helper("jira-mcp", "jira_client")
    result = jira.create_feature_stories(
        project_key=project_key,
        parent_key=parent_key,
        stories=stories,
        confirm_create=confirm_create,
        codebase_scan_confirmed=codebase_scan_confirmed,
        feature_context_confirmed=feature_context_confirmed,
    )
    result["devflow_context"] = {
        "working_dir": working_dir or "not_provided",
        "repo_cognition": repo_cognition_summary(load_repo_cognition_index()),
        "feature_key": parent_key,
        "approval_rule": (
            "Use confirm_create=False first. Create the batch only after the user approves the exact "
            "sprint-wise story payloads, with parent_key as the Feature and sprint_id or resolvable sprint_name on every story."
        ),
        "next_gate": (
            "If the preview is acceptable, ask the user for explicit approval before retrying "
            "with confirm_create=True."
        ),
    }
    return result


def dev_bootstrap_feature_stories(
    *,
    feature_key: str,
    project_key: str | None = None,
    sprint_id: str | int | None = None,
    sprint_name: str | None = None,
    sprint_by_phase: dict[str, Any] | None = None,
    phases: list[str] | None = None,
    create_missing_phases_when_existing: bool = False,
    confirm_create: bool = False,
    max_results: int = 200,
    working_dir: str | None = None,
) -> dict[str, Any]:
    """Run the complete Feature story bootstrap flow through Jira with DevFlow context."""
    jira = import_helper("jira-mcp", "jira_client")
    result = jira.bootstrap_feature_stories(
        feature_key=feature_key,
        project_key=project_key,
        sprint_id=sprint_id,
        sprint_name=sprint_name,
        sprint_by_phase=sprint_by_phase,
        phases=phases,
        create_missing_phases_when_existing=create_missing_phases_when_existing,
        confirm_create=confirm_create,
        max_results=max_results,
    )
    result["devflow_context"] = {
        "working_dir": working_dir or "not_provided",
        "repo_cognition": repo_cognition_summary(load_repo_cognition_index()),
        "feature_key": feature_key,
        "rule": (
            "This is the full Feature bootstrap path: learn Feature, inspect existing stories, "
            "use Confluence/ADR cache and repo graph, generate BDRSP-1623 story payloads, "
            "require sprint assignment, preview first, then create only after approval."
        ),
        "next_gate": (
            "If mode=sprint_required, ask for sprint_id/sprint_name. If mode=bootstrap_preview, "
            "show the preview and ask for explicit approval before confirm_create=True."
        ),
    }
    return result


def dev_implement_story(
    *,
    jira_id: str,
    user_request: str = "",
    stage: str = "start",
    story_approved: bool = False,
    code_changes_done: bool = False,
    tests_done: bool = False,
    review_done: bool = False,
    create_mr_approved: bool = False,
    story_update_approved: bool = False,
    apply_story_update: bool = False,
    approved_story_summary: str | None = None,
    approved_story_description: str | None = None,
    approved_acceptance_criteria: list[str] | None = None,
    approved_regulatory_justification: str | None = None,
    approved_reason_comments: str | None = None,
    approved_comment: str | None = None,
    base_branch: str = "main",
    branch_type: str = "feature",
    test_output: str = "",
    run_tests: bool = False,
    run_full_pipeline: bool = False,
    test_command: str | None = None,
    test_timeout_seconds: int = 600,
    mr_title: str | None = None,
    mr_description: str | None = None,
    mr_preview_approved: bool = False,
    testing_notes: str = "",
    target_branch: str = "main",
    draft_mr: bool = False,
    working_dir: str | None = None,
) -> dict[str, Any]:
    stage = stage.strip().lower()
    allowed = {"start", "apply_story_update", "after_story_approval", "after_code_changes", "preview_mr", "create_mr"}
    if stage not in allowed:
        raise DevFlowError(f"Unknown stage '{stage}'. Use one of: {', '.join(sorted(allowed))}.")

    if stage == "start":
        request = user_request or f"Implement Jira story {jira_id}"
        result = run_start_workflow(jira_id=jira_id, user_request=request, working_dir=working_dir, base_branch=base_branch)
        return _compact_stage_response(result, stage="start")

    _t0 = time.time()
    story_context = story_payload(jira_id)
    story = story_context["story"]
    feature = story_context["feature_context"]
    analysis = story_context["analysis"]

    # ── Knowledge packet — always present on every non-start stage ────────────
    _kf = _knowledge_fields_for_stage(
        jira_id=jira_id,
        story_context=story_context,
        working_dir=working_dir,
        user_request=user_request,
    )

    def _enrich(resp: dict[str, Any]) -> dict[str, Any]:
        """Merge knowledge fields into any stage response dict, then compact it."""
        resp.setdefault("runtime_build_marker", _kf["runtime_build_marker"])
        resp.setdefault("knowledge_packet_sections", _kf["knowledge_packet_sections"])
        resp.setdefault("local_brain", _kf["local_brain"])
        return _compact_stage_response(resp, stage=stage)

    if stage == "apply_story_update":
        if not story_update_approved:
            return _enrich({
                "ok": False,
                "stage": stage,
                "stop_reason": "User approval is required before updating Jira story fields or subtasks.",
                "required_user_question": "Do you approve updating the Jira story fields and one subtask based on the reviewed proposal?",
            })
        if not feature.get("ok"):
            return _enrich({
                "ok": False,
                "stage": stage,
                "feature_context": feature,
                "stop_reason": "DevFlow requires parent Feature context before updating Jira story fields.",
                "required_user_question": "This story has no readable parent Feature context. Can you confirm the Feature goal and related completed stories before I update Jira?",
            })
        code_context = codebase_analysis(story, working_dir, feature)
        if not code_context["can_write_story"]:
            return _enrich({
                "ok": False,
                "stage": stage,
                "codebase_analysis": code_context,
                "stop_reason": "DevFlow does not have enough Jira/codebase context to safely update the story.",
                "required_user_question": "The story context is not clear enough to update Jira. Can you clarify the expected behavior and impacted area?",
            })
        story_update = apply_approved_jira_updates(
            jira_id=jira_id,
            story_context=story_context,
            approved_story_summary=approved_story_summary or story.get("summary"),
            approved_story_description=approved_story_description,
            approved_acceptance_criteria=approved_acceptance_criteria,
            approved_regulatory_justification=approved_regulatory_justification,
            approved_reason_comments=approved_reason_comments,
            approved_comment=approved_comment,
        )
        # Refresh story after Jira update so the effort report uses final content
        refreshed_context = story_update.get("refreshed_story_context") or story_context
        refreshed_story = refreshed_context.get("story") or story
        refreshed_analysis = refreshed_context.get("analysis") or analysis
        # Invalidate Jira cache so next call fetches updated story
        if _LANGCHAIN_AVAILABLE and _JiraCache is not None:
            _JiraCache().invalidate(jira_id)
        # Build the full effort + impact report the user MUST read before code starts
        effort_report = build_effort_report(
            story=refreshed_story,
            analysis=refreshed_analysis,
            code_context=code_context,
            base_branch=base_branch,
        )
        return _enrich({
            "ok": bool(story_update["story_update_result"].get("ok")),
            "stage": stage,
            "story_update": story_update,
            "effort_report": effort_report,
            "stop_point": {
                "name": "Code change approval required",
                "message": (
                    f"Jira story {jira_id} has been updated. "
                    "A full effort report is included above (effort_report field). "
                    "Show the user: effort level, impacted files, acceptance criteria, risk flags, and implementation plan. "
                    "Wait for explicit user approval before calling stage='after_story_approval'."
                ),
                "next_call": "dev_implement_story(stage='after_story_approval', story_approved=true)",
                "rule": "Do NOT start code changes until the user has read the effort_report and said YES.",
            },
        })

    if stage == "after_story_approval":
        if not story_approved:
            return _enrich({
                "ok": False,
                "stage": stage,
                "stop_reason": "Story approval is required before branch creation or code changes.",
                "required_user_question": "Story is ready. Should I proceed with branch setup and code changes?",
            })
        story_update = None
        if apply_story_update:
            story_update = apply_story_refinement(
                jira_id=jira_id,
                approved_story_summary=approved_story_summary,
                approved_story_description=approved_story_description,
                approved_acceptance_criteria=approved_acceptance_criteria,
                approved_regulatory_justification=approved_regulatory_justification,
                approved_reason_comments=approved_reason_comments,
                approved_comment=approved_comment,
            )
            if not story_update["update_result"].get("ok"):
                return _enrich({
                    "ok": False,
                    "stage": stage,
                    "story_update": story_update,
                    "stop_reason": "Story update was requested but Jira refused the update. Provide exact approved story description and ACs.",
                })
            story_context = story_update["refreshed_story_context"] or story_context
            story = story_context["story"]
            analysis = story_context["analysis"]
        worktree_enabled = _dev_worktree_enabled()
        branch_setup = branch_or_contract(
            story=story,
            base_branch=base_branch,
            branch_type=branch_type,
            working_dir=working_dir,
            use_worktree=worktree_enabled,
        )
        if str(branch_setup.get("branch_action", "")).startswith("stop_"):
            return _enrich({"ok": False, "stage": stage, **branch_setup})

        repo_dir = (branch_setup.get("branch_result") or {}).get("repository") or resolve_project_dir(working_dir)
        branch_name = (branch_setup.get("branch_result") or {}).get("branch_name") or ""
        if worktree_enabled and branch_name:
            worktree = _setup_story_worktree(jira_id, branch_name, base_branch)
            worktree_dict = worktree.to_dict() if hasattr(worktree, "to_dict") else dict(worktree)
            if worktree_dict.get("status") != "active":
                return _enrich({
                    "ok": False,
                    "stage": stage,
                    "branch_setup": branch_setup,
                    "workspace": {
                        "mode": "isolated_worktree",
                        "enabled_by": "Automation/register_mcp.sh dev",
                        "worktree": worktree_dict,
                    },
                    "stop_reason": "Could not create the isolated story worktree. Fix the worktree error before code changes.",
                })
            workspace_info = {
                "workspace_path": worktree_dict.get("path"),
                "mode": "isolated_worktree",
                "enabled_by": "Automation/register_mcp.sh dev",
                "branch": branch_name,
                "main_repo": repo_dir,
                "instruction": (
                    "Make ALL code changes inside workspace_path only. "
                    "Do not edit the main repository while isolated worktree mode is enabled."
                ),
            }
        else:
            workspace_info = {
                "workspace_path": repo_dir,
                "mode": "main_repo",
                "branch": branch_name,
                "instruction": (
                    "Make ALL code changes directly in the repo (workspace_path). "
                    "The story branch is already checked out — your IDE shows changes in real time."
                ),
            }

        return _enrich({
            "ok": True,
            "stage": stage,
            "story_update": story_update,
            "branch_setup": branch_setup,
            "workspace": workspace_info,
            "implementation_contract": implementation_contract(story, analysis, base_branch),
            "next_action": (
                "Make code changes in the isolated worktree, then call stage='after_code_changes'."
                if workspace_info["mode"] == "isolated_worktree"
                else "Make code changes in the repo (IDE will show them live), then call stage='after_code_changes'."
            ),
        })

    if stage == "after_code_changes":
        if not code_changes_done:
            return _enrich({
                "ok": False,
                "stage": stage,
                "stop_reason": "Code changes must be completed before this checkpoint.",
            })
        # ── Resolve workspace: prefer active worktree, fall back to main repo ───
        # Dev profile worktree mode returns the isolated worktree path; other
        # profiles/defaults fall back to working_dir (main repo).
        story_workspace = _resolve_story_workspace(jira_id, working_dir)

        # ── Full validation pipeline or targeted test ─────────────────────────
        if run_full_pipeline:
            test_client = import_helper("test-mcp", "test_client")
            full_pipeline = safe_call(
                test_client.run_full_validation_pipeline,
                working_dir=story_workspace,
                test_command=test_command,
                timeout_seconds=test_timeout_seconds,
            )
            # Mirror autonomous_test_execution shape from full pipeline test_result
            autonomous_from_pipeline = {
                "ok": full_pipeline.get("ok", False),
                "executed": True,
                "autonomous": True,
                "verdict": full_pipeline.get("verdict", "failed"),
                "command_used": (full_pipeline.get("test_result") or {}).get("command", ""),
                "stdout": (full_pipeline.get("test_result") or {}).get("stdout", ""),
                "stderr": (full_pipeline.get("test_result") or {}).get("stderr", ""),
                "failure_analysis": (full_pipeline.get("test_result") or {}).get("analysis") or {},
                "full_pipeline": full_pipeline,
            }
            report = {
                "autonomous_test_execution": autonomous_from_pipeline,
                "full_validation_pipeline": full_pipeline,
                "test_report": safe_call(
                    import_helper("test-mcp", "test_client").current_change_test_report,
                    base_branch=base_branch, latest_output="", working_dir=story_workspace,
                ),
                "review_report": safe_call(
                    import_helper("review-mcp", "review_client").full_current_change_review,
                    story_id=story.get("key") or "",
                    story_summary=story.get("summary") or "",
                    acceptance_criteria=extract_acceptance_criteria(story, analysis),
                    base_branch=base_branch, working_dir=story_workspace,
                ),
                "stop_point": {
                    "name": "MR preview required",
                    "message": "Full validation pipeline complete. Call stage='preview_mr' to review MR title/description before creation.",
                    "next_call": "dev_implement_story(stage='preview_mr')",
                },
            }
        else:
            report = after_code_report(
                story=story,
                analysis=analysis,
                base_branch=base_branch,
                test_output=test_output,
                working_dir=story_workspace,
                run_tests=run_tests,
                test_command=test_command,
                test_timeout_seconds=test_timeout_seconds,
            )
            report["stop_point"]["next_call"] = "dev_implement_story(stage='preview_mr')"

        report["workspace_used"] = story_workspace
        report["completion_flags"] = {
            "code_changes_done": code_changes_done,
            "tests_done": tests_done or bool((report.get("autonomous_test_execution") or {}).get("ok")),
            "review_done": review_done,
        }
        report["mr_gate"] = "Do not create an MR until the user approves the MR preview (stage='preview_mr')."
        _record_stage_timing(jira_id, "after_code_changes", time.time() - _t0, actor="ai")
        return _enrich({"ok": True, "stage": stage, **report})

    # ── preview_mr — show MR title/description for human approval ────────────
    if stage == "preview_mr":
        gitlab = import_helper("gitlab-mcp", "gitlab_client")
        story_workspace = _resolve_story_workspace(jira_id, working_dir)
        repo = resolve_project_dir(story_workspace)
        snapshot = git_snapshot(story_workspace, base_branch)
        prepared = safe_call(
            gitlab.prepare_merge_request,
            story_id=story.get("key") or "",
            story_summary=story.get("summary") or "",
            change_type="feature",
            target_branch=target_branch,
            working_dir=repo,
            testing_notes=testing_notes or "DevFlow code, test, and review checkpoints completed.",
        )
        mem = load_story_memory(jira_id)
        timings = mem.get("stage_timings", [])
        ai_total = sum(t["elapsed_s"] for t in timings if t.get("actor") == "ai")
        human_total = sum(t["elapsed_s"] for t in timings if t.get("actor") == "human")
        _record_stage_timing(jira_id, "preview_mr_wait", 0, actor="human", status="pending")
        return _enrich({
            "ok": True,
            "stage": stage,
            "mr_preview": {
                "title": prepared.get("title") or mr_title,
                "description": prepared.get("description") or mr_description,
                "source_branch": snapshot.get("current_branch"),
                "target_branch": target_branch,
                "template_used": prepared.get("template_used"),
                "workspace": repo,
            },
            "effort_so_far": {
                "ai_total_s": round(ai_total, 1),
                "human_total_s": round(human_total, 1),
                "stages_completed": len(timings),
            },
            "stop_point": {
                "name": "MR preview approval required",
                "message": (
                    "Show the user the MR title and description above. "
                    "Ask: 'Does this MR title and description look correct? Should I create the MR?'"
                ),
                "next_call": "dev_implement_story(stage='create_mr', create_mr_approved=true, tests_done=true, review_done=true)",
            },
        })

    if not create_mr_approved:
        return _enrich({
            "ok": False,
            "stage": stage,
            "stop_reason": "User approval is required before pushing and creating a merge request.",
            "required_user_question": "Testing and review are done. Should I push the branch and create the merge request?",
        })
    if not tests_done or not review_done:
        return _enrich({
            "ok": False,
            "stage": stage,
            "stop_reason": "MR creation requires explicit tests_done=true and review_done=true.",
            "required_user_question": "Please confirm tests and review are complete before creating the merge request.",
        })

    _record_stage_timing(jira_id, "mr_approval", 0, actor="human", status="approved")
    story_workspace = _resolve_story_workspace(jira_id, working_dir)
    mr_result = create_mr_flow(
        story=story,
        base_branch=base_branch,
        target_branch=target_branch,
        draft_mr=draft_mr,
        mr_title=mr_title,
        mr_description=mr_description,
        testing_notes=testing_notes or test_output,
        working_dir=story_workspace,
    )

    # ── Generate automation analytics report ──────────────────────────────────
    automation_report: dict[str, Any] = {}
    try:
        rg = _load_report_generator(jira_id)
        if rg is not None:
            rg.set_story(story, story_context.get("feature_context"))
            mem = load_story_memory(jira_id)
            for t in mem.get("stage_timings", []):
                rg.record_stage(t["stage"], t["elapsed_s"], t.get("actor", "ai"), t.get("status", "done"))
            # Collect changed files from git
            repo = resolve_project_dir(story_workspace)
            snap = git_snapshot(story_workspace, base_branch)
            test_client = import_helper("test-mcp", "test_client")
            changed = safe_call(test_client.current_changed_files, repo=repo, base_branch=base_branch)
            files_changed = [f["path"] for f in (changed.get("changed_files") or [])] if isinstance(changed, dict) else []
            rg.set_files_changed(files_changed)
            mr_url = (mr_result.get("merge_request_result") or {}).get("web_url") or ""
            current_branch = snap.get("current_branch") or ""
            rg.set_mr(mr_url, mr_title or "", branch=current_branch)
            automation_report = rg.generate()
            # ── Attach report to MR description ──────────────────────────────
            # Works for both new MRs (just created above) and existing MRs
            # (re-run after a previous create_mr stage).  The report block is
            # wrapped in <!-- devflow-report --> markers so re-runs replace
            # rather than duplicate the previous report block.
            if automation_report.get("md"):
                snippet = rg.mr_report_snippet()
                automation_report["mr_snippet"] = snippet
                # Resolve MR iid — from new MR result or by searching GitLab
                _mr_iid: int | None = None
                _new_mr = (mr_result.get("merge_request_result") or {})
                _mr_iid = _new_mr.get("iid") or None
                if not _mr_iid and current_branch:
                    # MR already existed (re-run) — find it by source branch
                    try:
                        gitlab = import_helper("gitlab-mcp", "gitlab_client")
                        _existing = gitlab.find_mr_for_branch(current_branch)
                        _mr_iid = (_existing or {}).get("iid")
                    except Exception:
                        pass
                if _mr_iid:
                    try:
                        gitlab = import_helper("gitlab-mcp", "gitlab_client")
                        attach_result = gitlab.append_report_to_mr(int(_mr_iid), snippet)
                        automation_report["report_attached_to_mr"] = attach_result.get("ok", False)
                        automation_report["report_attach_mr_iid"] = _mr_iid
                    except Exception as _attach_exc:
                        automation_report["report_attach_error"] = str(_attach_exc)
    except Exception as _exc:
        automation_report = {"error": str(_exc)}

    # ── Cleanup isolated worktree (no-op in IDE mode) ─────────────────────────
    worktree_cleanup = _cleanup_story_worktree(jira_id)
    _record_stage_timing(jira_id, "create_mr", time.time() - _t0, actor="ai")

    return _enrich({
        "ok": True,
        "stage": stage,
        "mr_flow": mr_result,
        "automation_report": automation_report,
        "worktree_cleanup": worktree_cleanup,
    })
