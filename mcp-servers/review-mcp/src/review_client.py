"""Code review helpers for review-mcp (ReviewSmith Agent)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def json_response(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def _run(args: list[str], *, cwd: str | None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(args, returncode=127, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, returncode=124, stdout="", stderr="timed out")


def _resolve_repo(working_dir: str | None) -> str:
    cwd = working_dir or os.getenv("DEV_WORKING_DIR") or os.getenv("GIT_WORKING_DIR") or os.getcwd()
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, timeout=15)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else str(Path(cwd).resolve())


def _diff_stat(repo: str, base_branch: str) -> dict[str, Any]:
    for ref in (f"origin/{base_branch}", base_branch):
        check = _run(["git", "rev-parse", "--verify", ref], cwd=repo, timeout=10)
        if check.returncode == 0:
            stat = _run(["git", "diff", "--stat", ref, "HEAD"], cwd=repo, timeout=20)
            name_only = _run(["git", "diff", "--name-only", ref, "HEAD"], cwd=repo, timeout=20)
            files = [f for f in name_only.stdout.splitlines() if f.strip()]
            return {
                "base_ref": ref,
                "changed_files": files,
                "diff_stat": stat.stdout.strip() if stat.returncode == 0 else "",
                "file_count": len(files),
            }
    stat = _run(["git", "status", "--short"], cwd=repo, timeout=15)
    files = [line[3:].strip() for line in stat.stdout.splitlines() if line.strip()]
    return {"base_ref": base_branch, "changed_files": files, "diff_stat": stat.stdout.strip(), "file_count": len(files)}


def _recent_commits(repo: str, base_branch: str, limit: int = 10) -> list[str]:
    for ref in (f"origin/{base_branch}", base_branch):
        check = _run(["git", "rev-parse", "--verify", ref], cwd=repo, timeout=10)
        if check.returncode == 0:
            log = _run(["git", "log", "--oneline", f"{ref}..HEAD", f"-{limit}"], cwd=repo, timeout=15)
            if log.returncode == 0:
                return [l for l in log.stdout.splitlines() if l.strip()]
    return []


def _risk_signals(files: list[str]) -> list[str]:
    signals: list[str] = []
    lower_files = [f.lower() for f in files]
    has_test = any("test" in f for f in lower_files)
    src_only = any("src/main" in f for f in lower_files)
    if src_only and not has_test:
        signals.append("⚠️ Source files changed but no test files detected — consider adding tests.")
    for label, keywords in {
        "security": ["security", "auth", "oauth", "jwt", "token", "credential", "password", "secret"],
        "data_migration": ["migration", "flyway", "liquibase", "schema", "changelog"],
        "config": ["application.yml", "application.properties", "bootstrap"],
    }.items():
        if any(kw in f for f in lower_files for kw in keywords):
            signals.append(f"⚠️ Potentially sensitive area: {label} — review carefully.")
    if len(files) > 8:
        signals.append(f"⚠️ Large change: {len(files)} files modified — verify scope matches the story.")
    return signals


# ── Public API ─────────────────────────────────────────────────────────────────

def analyze_current_changes(*, base_branch: str = "main", working_dir: str | None = None) -> dict[str, Any]:
    """Analyze current Git changes: changed files, diff stat, risk signals, recent commits."""
    repo = _resolve_repo(working_dir)
    diff = _diff_stat(repo, base_branch)
    commits = _recent_commits(repo, base_branch)
    signals = _risk_signals(diff["changed_files"])
    groups: dict[str, list[str]] = {}
    for f in diff["changed_files"]:
        if "test" in f.lower():
            groups.setdefault("tests", []).append(f)
        elif "src/main/java" in f:
            groups.setdefault("source", []).append(f)
        elif "src/main/resources" in f or f.endswith((".yml", ".yaml", ".properties", ".xml")):
            groups.setdefault("config/resources", []).append(f)
        else:
            groups.setdefault("other", []).append(f)
    return {
        "ok": True,
        "repository": repo,
        "base_branch": base_branch,
        "diff_stat": diff["diff_stat"],
        "file_count": diff["file_count"],
        "changed_files": diff["changed_files"],
        "files_by_area": groups,
        "recent_commits": commits,
        "risk_signals": signals,
    }


def check_acceptance_criteria_coverage(
    *, acceptance_criteria: list[str], story_summary: str = "",
    base_branch: str = "main", working_dir: str | None = None,
) -> dict[str, Any]:
    """Check signal coverage for each acceptance criterion based on changed files."""
    repo = _resolve_repo(working_dir)
    diff = _diff_stat(repo, base_branch)
    file_text = " ".join(diff["changed_files"]).lower()
    coverage: list[dict[str, Any]] = []
    for ac in (acceptance_criteria or []):
        tokens = [t.lower() for t in ac.replace("-", " ").replace("_", " ").split() if len(t) >= 4]
        matched = [t for t in tokens[:6] if t in file_text]
        coverage.append({
            "criterion": ac,
            "status": "covered" if matched else "not_detected",
            "matched_keywords": matched,
            "note": (f"Keywords {matched} found in changed files." if matched
                     else "No direct keyword match in changed files — manual review needed."),
        })
    covered = sum(1 for c in coverage if c["status"] == "covered")
    total = len(acceptance_criteria or [])
    return {
        "ok": True, "repository": repo, "base_branch": base_branch, "story_summary": story_summary,
        "acceptance_criteria_count": total, "covered_count": covered,
        "coverage_ratio": f"{covered}/{total or 1}", "coverage": coverage,
        "verdict": "all_covered" if covered == total else "partial_coverage" if covered > 0 else "no_coverage",
    }


def suggest_improvements(
    *, story_summary: str, acceptance_criteria: list[str] | None = None,
    base_branch: str = "main", working_dir: str | None = None,
) -> dict[str, Any]:
    """Suggest review improvements based on changes, risks, and AC coverage gaps."""
    repo = _resolve_repo(working_dir)
    changes = analyze_current_changes(base_branch=base_branch, working_dir=working_dir)
    coverage = check_acceptance_criteria_coverage(
        acceptance_criteria=acceptance_criteria or [], story_summary=story_summary,
        base_branch=base_branch, working_dir=working_dir,
    )
    suggestions: list[str] = []
    source_files = changes["files_by_area"].get("source", [])
    test_files = changes["files_by_area"].get("tests", [])
    if source_files and not test_files:
        suggestions.append("Add unit tests for the changed source files — no test changes detected.")
    elif len(source_files) > len(test_files) * 2:
        suggestions.append(f"Consider more tests: {len(source_files)} source file(s) vs {len(test_files)} test file(s).")
    for c in coverage["coverage"]:
        if c["status"] == "not_detected":
            suggestions.append(f"No code signal for AC: '{c['criterion'][:80]}' — verify this is addressed.")
    suggestions.extend(changes.get("risk_signals", []))
    if not suggestions:
        suggestions.append("No major improvement signals detected. Review looks clean.")
    return {
        "ok": True, "repository": repo, "story_summary": story_summary, "suggestions": suggestions,
        "risk_signals": changes.get("risk_signals", []), "coverage_verdict": coverage.get("verdict"),
        "covered": f"{coverage['covered_count']}/{coverage['acceptance_criteria_count']} ACs have code signals",
    }


def generate_change_summary(
    *, story_id: str, story_summary: str, acceptance_criteria: list[str] | None = None,
    base_branch: str = "main", working_dir: str | None = None,
) -> dict[str, Any]:
    """Generate a concise review report for the current changes against a Jira story."""
    repo = _resolve_repo(working_dir)
    changes = analyze_current_changes(base_branch=base_branch, working_dir=working_dir)
    coverage = check_acceptance_criteria_coverage(
        acceptance_criteria=acceptance_criteria or [], story_summary=story_summary,
        base_branch=base_branch, working_dir=working_dir,
    )
    improvements = suggest_improvements(
        story_summary=story_summary, acceptance_criteria=acceptance_criteria,
        base_branch=base_branch, working_dir=working_dir,
    )
    return {
        "ok": True, "story_id": story_id, "story_summary": story_summary, "repository": repo,
        "base_branch": base_branch,
        "summary": {"files_changed": changes["file_count"], "commits": len(changes["recent_commits"]), "diff_stat": changes["diff_stat"]},
        "acceptance_criteria_coverage": coverage["coverage"],
        "coverage_verdict": coverage["verdict"],
        "risk_signals": changes["risk_signals"],
        "suggestions": improvements["suggestions"],
        "next_actions": [
            "Verify all acceptance criteria are met before MR creation.",
            "Ensure all tests pass locally.",
            "Review risk signals above before merging.",
        ],
    }


def inspect_story_implementation(
    *, story_id: str, story_summary: str, story_description: str = "",
    acceptance_criteria: list[str] | None = None, base_branch: str = "main",
    working_dir: str | None = None,
) -> dict[str, Any]:
    """Inspect whether the current repo already has code related to the given story."""
    repo = _resolve_repo(working_dir)
    text = f"{story_id} {story_summary} {story_description} " + " ".join(acceptance_criteria or [])
    words = []
    for token in text.replace("-", " ").replace("_", " ").split():
        cleaned = "".join(ch for ch in token.lower() if ch.isalnum())
        if len(cleaned) >= 4 and cleaned not in {"this", "that", "with", "from", "story", "change", "should", "have"}:
            words.append(cleaned)
    keywords = list(dict.fromkeys(words))[:8]
    files: list[str] = []
    if keywords:
        rg_args = ["rg", "--files-with-matches", "--ignore-case", "--glob", "!Automation/**", "--glob", "!target/**"]
        for kw in keywords[:6]:
            rg_args.extend(["-e", kw])
        result = _run(rg_args, cwd=repo, timeout=15)
        if result.returncode == 0:
            files = result.stdout.splitlines()[:20]
    changes = analyze_current_changes(base_branch=base_branch, working_dir=working_dir)
    return {
        "ok": True, "story_id": story_id, "story_summary": story_summary, "repository": repo,
        "keywords_used": keywords, "matching_existing_files": files,
        "existing_code_found": bool(files),
        "implementation_started": len(changes["changed_files"]) > 0,
        "current_changed_files": changes["changed_files"],
        "note": ("Implementation appears to be in progress." if changes["changed_files"]
                 else "No code changes detected against the base branch yet."),
    }


def full_current_change_review(
    *, story_id: str = "", story_summary: str = "", acceptance_criteria: list[str] | None = None,
    base_branch: str = "main", working_dir: str | None = None,
) -> dict[str, Any]:
    """Full review: changes + risk + AC coverage + suggestions in one call."""
    repo = _resolve_repo(working_dir)
    changes = analyze_current_changes(base_branch=base_branch, working_dir=working_dir)
    coverage = check_acceptance_criteria_coverage(
        acceptance_criteria=acceptance_criteria or [], story_summary=story_summary,
        base_branch=base_branch, working_dir=working_dir,
    )
    improvements = suggest_improvements(
        story_summary=story_summary, acceptance_criteria=acceptance_criteria,
        base_branch=base_branch, working_dir=working_dir,
    )
    overall_verdict = "ready"
    blockers: list[str] = []
    if changes["file_count"] == 0:
        overall_verdict = "not_started"
        blockers.append("No code changes detected.")
    elif coverage["verdict"] == "no_coverage" and acceptance_criteria:
        overall_verdict = "needs_review"
        blockers.append("No acceptance criteria have code signals.")
    elif changes["risk_signals"]:
        overall_verdict = "needs_review"
    return {
        "ok": True, "story_id": story_id, "story_summary": story_summary,
        "repository": repo, "base_branch": base_branch,
        "overall_verdict": overall_verdict, "blockers": blockers,
        "changes": {
            "file_count": changes["file_count"], "files_by_area": changes["files_by_area"],
            "diff_stat": changes["diff_stat"], "recent_commits": changes["recent_commits"],
        },
        "acceptance_criteria_coverage": coverage["coverage"],
        "coverage_verdict": coverage["verdict"],
        "risk_signals": changes["risk_signals"],
        "suggestions": improvements["suggestions"],
        "engineering_guidance": [
            "Keep changes small and scoped to the story — avoid unrelated refactors.",
            "Ensure all public methods have unit tests.",
            "Review logs: no passwords, tokens, or PII should be logged.",
            "Check error handling: typed exceptions, original cause preserved.",
            "Verify no unused imports, debug logs, TODOs, or commented-out code.",
        ],
    }

