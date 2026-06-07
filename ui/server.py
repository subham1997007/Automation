#!/usr/bin/env python3
"""Local PO/SM operations UI server.

This server is intentionally started only by Automation/start.sh. It serves the
PO/SM console and exposes a small command API that reuses the existing
Automation Jira and GitLab helper logic.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
INDEX_FILE = DOCS_DIR / "po-sm-operations-ui.html"

sys.path.append(str(ROOT / "mcp-servers" / "jira-mcp" / "src"))
sys.path.append(str(ROOT / "mcp-servers" / "gitlab-mcp" / "src"))

from jira_client import (  # type: ignore  # noqa: E402
    JiraConfigError,
    JiraRequestError,
    analyze_story,
    feature_context,
    manage_subtasks,
    read_issue,
    refine_story,
)
from gitlab_client import (  # type: ignore  # noqa: E402
    GitCommandError,
    GitLabConfigError,
    GitLabRequestError,
    check_connection as gitlab_check_connection,
    compact_merge_request,
    get_gitlab_config,
    get_merge_request,
    get_merge_request_discussions,
    get_pipeline_status,
    gitlab_request,
    _encoded_project,
)


JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
MR_RE = re.compile(r"(?:!|mr\s*#?|merge request\s*)\s*(\d+)", re.IGNORECASE)
MR_URL_RE = re.compile(r"/merge_requests/(\d+)")


def load_local_env() -> None:
    """Load Automation/.env.local for direct local server runs."""
    env_file = ROOT / ".env.local"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def json_safe(value: Any) -> Any:
    """Return a JSON-serializable value."""
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [json_safe(item) for item in value]
        return str(value)


def extract_jira_key(command: str) -> str | None:
    match = JIRA_KEY_RE.search(command.upper())
    return match.group(0) if match else None


def extract_mr_iid(command: str) -> int | None:
    match = MR_RE.search(command)
    return int(match.group(1)) if match else None


def extract_story_mrs(story: dict[str, Any]) -> list[int]:
    """Extract GitLab MR ids mentioned in Jira comments or linked text."""
    found: list[int] = []
    text_parts = [story.get("description") or ""]
    text_parts.extend(comment.get("body") or "" for comment in story.get("comments") or [])
    for text in text_parts:
        for match in MR_URL_RE.finditer(text):
            mr_iid = int(match.group(1))
            if mr_iid not in found:
                found.append(mr_iid)
    return found


def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Count Jira-like items by broad status bucket."""
    done = 0
    cancelled = 0
    in_progress = 0
    for item in items:
        status = (item.get("status") or "").lower()
        if status in {"done", "closed", "resolved"}:
            done += 1
        elif status in {"cancelled", "canceled"}:
            cancelled += 1
        elif status in {"in progress", "review", "selected for development"}:
            in_progress += 1
    return {
        "total": len(items),
        "done": done,
        "cancelled": cancelled,
        "in_progress": in_progress,
        "open": max(len(items) - done - cancelled, 0),
    }


def search_gitlab_merge_requests_for_story(jira_key: str) -> dict[str, Any]:
    """Find GitLab merge requests related to a Jira key without requiring MR number input."""
    try:
        config = get_gitlab_config()
        project = _encoded_project(config["project_id"])
        searched = gitlab_request(
            f"projects/{project}/merge_requests",
            config=config,
            query={"state": "all", "search": jira_key, "per_page": 20},
        )
        broad = gitlab_request(
            f"projects/{project}/merge_requests",
            config=config,
            query={"state": "all", "per_page": 100, "order_by": "updated_at", "sort": "desc"},
        )
        merged: dict[int, dict[str, Any]] = {}
        for mr in list(searched or []) + list(broad or []):
            text = " ".join(
                str(value or "")
                for value in (
                    mr.get("title"),
                    mr.get("description"),
                    mr.get("source_branch"),
                    mr.get("target_branch"),
                    mr.get("web_url"),
                )
            ).upper()
            if jira_key.upper() in text:
                merged[int(mr.get("iid") or mr.get("id"))] = compact_merge_request(mr)
        return {"ok": True, "merge_requests": list(merged.values())}
    except Exception as exc:
        return {"ok": False, "merge_requests": [], "error": str(exc)}


def build_story_scope(
    story: dict[str, Any],
    feature: dict[str, Any] | None = None,
    gitlab_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create PO/SM-friendly scoped display data for one Jira story."""
    subtasks = story.get("subtasks") or []
    subtask_counts = status_counts(subtasks)
    acceptance_criteria = story.get("acceptance_criteria") or []
    linked_mrs = extract_story_mrs(story)
    # Only trigger a live GitLab search when no evidence dict was supplied at all
    # (None means "search now"; an explicit empty dict {} means "skip search").
    if gitlab_evidence is None:
        gitlab_evidence = search_gitlab_merge_requests_for_story(story.get("key") or "")
    searched_mrs = gitlab_evidence.get("merge_requests") or []
    for mr in searched_mrs:
        mr_iid = mr.get("iid")
        if mr_iid and mr_iid not in linked_mrs:
            linked_mrs.append(mr_iid)
    feature = feature or {}
    feature_info = feature.get("feature") or {}
    completed = feature.get("completed_count") or 0
    total = feature.get("total_sibling_count") or 0
    progress = round((completed / total) * 100) if total else (100 if (story.get("status_category") or "").lower() == "done" else 0)

    jira_cards = [
        {"label": "Story", "value": story.get("key"), "note": story.get("summary")},
        {"label": "Status", "value": story.get("status"), "note": story.get("assignee")},
        {"label": "Subtasks", "value": str(subtask_counts["total"]), "note": f"{subtask_counts['done']} done, {subtask_counts['cancelled']} cancelled"},
        {"label": "Acceptance Criteria", "value": str(len(acceptance_criteria)), "note": "separate Jira field" if story.get("acceptance_criteria_field") else "read from description"},
    ]
    gitlab_cards = [
        {"label": "GitLab Evidence", "value": f"{len(linked_mrs)} MR(s)", "note": ", ".join(f"!{mr}" for mr in linked_mrs) or gitlab_evidence.get("error") or "No MR found by Jira key"},
        {"label": "Latest MR", "value": f"!{linked_mrs[-1]}" if linked_mrs else "Not linked", "note": "from Jira comments and GitLab search"},
        {"label": "Local Branch", "value": git_summary().get("branch"), "note": "local repository signal"},
    ]
    epic_cards = [
        {"label": "Feature / Epic", "value": feature_info.get("key") or story.get("parent") or "Not linked", "note": feature_info.get("name") or story.get("parent_summary") or "No parent Feature/Epic found"},
        {"label": "Progress", "value": f"{progress}%", "note": f"{completed} of {total} sibling stories done" if total else "based on this story status"},
        {"label": "Current Story Fit", "value": story.get("key"), "note": feature.get("current_story_fit") or "Feature context unavailable"},
    ]
    report_cards = [
        {"label": "Delivery Status", "value": story.get("status"), "note": f"Updated {story.get('updated') or '-'}"},
        {"label": "Quality Signal", "value": "Ready" if acceptance_criteria else "Needs AC review", "note": f"{len(acceptance_criteria)} acceptance criteria found"},
        {"label": "PO/SM Attention", "value": "Low" if subtask_counts["open"] == 0 else "Review", "note": f"{subtask_counts['open']} open/non-cancelled subtask(s)"},
    ]

    request_flow = [
        {"title": f"Read {story.get('key')}", "detail": "Loaded story fields, status, acceptance criteria, subtasks, comments, and linked work.", "tag": "Jira"},
        {"title": "Check GitLab evidence", "detail": gitlab_cards[0]["note"], "tag": "GitLab"},
        {"title": "Review feature context", "detail": epic_cards[0]["note"], "tag": "Epic"},
        {"title": "Prepare PO/SM action", "detail": "No Jira update is applied unless the user explicitly approves it.", "tag": "Gate"},
    ]
    actions = [
        {"label": "Story status", "value": f"{story.get('key')} is {story.get('status')}."},
        {"label": "Subtasks", "value": f"{subtask_counts['done']} done, {subtask_counts['cancelled']} cancelled, {subtask_counts['open']} open/non-cancelled."},
        {"label": "GitLab", "value": gitlab_cards[0]["note"]},
        {"label": "Next step", "value": "Review report only." if (story.get("status_category") or "").lower() == "done" else "Review gaps before approving any update."},
    ]

    return {
        "scope": "story",
        "jira_summary": {
            "key": story.get("key"),
            "title": story.get("summary"),
            "status": story.get("status"),
            "owner": story.get("assignee"),
            "feature": feature_info.get("key") or story.get("parent") or "Not linked",
            "feature_name": feature_info.get("name") or story.get("parent_summary") or "No parent Feature/Epic found",
            "approval": (
                "No Jira update needed unless PO/SM requests correction"
                if (story.get("status_category") or "").lower() == "done"
                else "Approval required before update"
            ),
        },
        "story_key": story.get("key"),
        "story_title": story.get("summary"),
        "story_status": story.get("status"),
        "assignee": story.get("assignee"),
        "linked_merge_requests": linked_mrs,
        "gitlab_merge_requests": searched_mrs,
        "gitlab_search_error": gitlab_evidence.get("error"),
        "subtask_counts": subtask_counts,
        "jira_cards": jira_cards,
        "gitlab_cards": gitlab_cards,
        "epic_cards": epic_cards,
        "report_cards": report_cards,
        "request_flow": request_flow,
        "actions": actions,
        "acceptance_criteria": acceptance_criteria,
        "subtasks": subtasks,
    }


def git_summary() -> dict[str, Any]:
    """Return local Git status signals without changing the repo."""
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=ROOT.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        return {
            "branch": branch.stdout.strip() or "(detached)",
            "changed_files": len([line for line in status.stdout.splitlines() if line.strip()]),
            "status_lines": status.stdout.splitlines()[:12],
        }
    except Exception as exc:  # pragma: no cover - defensive UI fallback.
        return {"error": str(exc)}


def story_payload(jira_key: str) -> dict[str, Any]:
    story = read_issue(jira_key)
    analysis = analyze_story(story)
    try:
        feature = feature_context(jira_key)
    except Exception as exc:
        feature = {"ok": False, "stop_reason": str(exc)}
    return {"story": story, "analysis": analysis, "feature_context": feature}


def command_read_story(jira_key: str) -> dict[str, Any]:
    payload = story_payload(jira_key)
    story = payload["story"]
    # Pass an empty gitlab_evidence dict so build_story_scope skips the blocking
    # GitLab search during a simple story-read command.  The GitLab cards will
    # show "No MR found" which is accurate until the user explicitly requests a
    # GitLab report for the story.
    display = build_story_scope(story, payload.get("feature_context"), gitlab_evidence={})
    return {
        "view": "jira",
        "message": f"Showing {jira_key}: {story.get('summary') or '(no summary)'}.",
        "summary": {
            "jira_story": story.get("key"),
            "story_status": story.get("status"),
            "assignee": story.get("assignee"),
            "subtasks": len(story.get("subtasks") or []),
        },
        "display": display,
        **payload,
    }


def command_update_story_preview(jira_key: str) -> dict[str, Any]:
    proposal = refine_story(jira_key, apply_update=False)
    return {
        "view": "jira",
        "message": (
            f"Prepared a Jira update proposal for {jira_key}. Nothing was written to Jira. "
            "PO/SM approval is required before applying any change."
        ),
        "approval_required": True,
        "proposal": proposal,
    }


def command_subtask_status(jira_key: str) -> dict[str, Any]:
    story = read_issue(jira_key)
    subtasks = story.get("subtasks") or []
    done = [item for item in subtasks if (item.get("status") or "").lower() in {"done", "closed", "resolved"}]
    return {
        "view": "jira",
        "message": f"Found {len(subtasks)} subtask(s) under {jira_key}.",
        "story": story,
        "display": build_story_scope(story),
        "subtasks": subtasks,
        "summary": {
            "total_subtasks": len(subtasks),
            "done_subtasks": len(done),
            "open_subtasks": len(subtasks) - len(done),
        },
    }


def command_create_subtask_preview(jira_key: str, command: str) -> dict[str, Any]:
    summary = f"{jira_key}: Implement and validate scoped story change"
    description = "Complete the minimum required implementation and validation for the approved Jira story scope."
    plan = manage_subtasks(
        jira_key,
        [{"summary": summary, "description": description}],
        apply_changes=False,
        confirm_apply=False,
    )
    return {
        "view": "jira",
        "message": (
            f"Prepared a subtask action plan for {jira_key}. No subtask was created or updated. "
            "Approve the exact action before applying it."
        ),
        "approval_required": True,
        "subtask_plan": plan,
        "original_command": command,
    }


def command_feature_progress(jira_key: str, *, view: str = "epic") -> dict[str, Any]:
    story = read_issue(jira_key)
    try:
        context = feature_context(jira_key)
    except Exception as exc:
        context = {"ok": False, "stop_reason": str(exc)}
    total = context.get("total_sibling_count") or 0
    completed = context.get("completed_count") or 0
    percent = round((completed / total) * 100) if total else 0
    return {
        "view": view,
        "message": f"Loaded feature and epic progress for {jira_key}.",
        "story": story,
        "feature_context": context,
        "display": build_story_scope(story, context),
        "summary": {
            "feature": (context.get("feature") or {}).get("key"),
            "feature_name": (context.get("feature") or {}).get("name"),
            "completed_stories": completed,
            "total_stories": total,
            "progress_percent": percent,
        },
    }


def command_gitlab_report(command: str, jira_key: str | None = None) -> dict[str, Any]:
    mr_iid = extract_mr_iid(command)
    payload: dict[str, Any] = {
        "view": "gitlab",
        "message": "Loaded GitLab project and local repository status.",
        "local_git": git_summary(),
    }
    if jira_key:
        # Load Jira story and build the scoped display (includes live GitLab MR search).
        # Any Jira failure is caught and surfaced as a partial response — the GitLab view
        # is still returned so the frontend can render whatever data was collected.
        try:
            story = read_issue(jira_key)
            payload["story"] = story
            payload["display"] = build_story_scope(story)
            linked_mrs = payload["display"]["linked_merge_requests"]
            if not mr_iid and linked_mrs:
                mr_iid = linked_mrs[-1]
            payload["message"] = f"Showing GitLab evidence for {jira_key}."
        except (JiraConfigError, JiraRequestError) as exc:
            payload["jira_error"] = str(exc)
            payload["message"] = f"Could not load Jira story {jira_key}: {exc}"
    try:
        payload["connection"] = gitlab_check_connection()
    except Exception as exc:
        payload["connection"] = {"ok": False, "error": str(exc)}
    if mr_iid:
        # Fetch detailed MR data.  Wrap each call so that a missing GitLab config or
        # an unavailable MR does not discard the Jira display that was already built.
        try:
            payload["merge_request"] = get_merge_request(mr_iid)
            payload["pipeline"] = get_pipeline_status(mr_iid)
            payload["discussions"] = get_merge_request_discussions(mr_iid)
            payload["message"] = f"Loaded GitLab report for merge request !{mr_iid}."
        except (GitLabConfigError, GitLabRequestError, GitCommandError) as exc:
            payload["mr_error"] = str(exc)
            if jira_key:
                payload["message"] = (
                    f"Jira story {jira_key} loaded. "
                    f"GitLab MR detail for !{mr_iid} unavailable: {exc}"
                )
            else:
                payload["message"] = f"GitLab MR !{mr_iid} could not be fetched: {exc}"
    return payload


def handle_command(command: str) -> dict[str, Any]:
    """Route a PO/SM command to real Automation helper logic."""
    normalized = command.lower()
    jira_key = extract_jira_key(command)

    try:
        if jira_key and "report" in normalized and any(token in normalized for token in ("feature", "epic", "progress")):
            return command_feature_progress(jira_key, view="reports")
        if any(token in normalized for token in ("gitlab", "evidence", "merge request", "pipeline", " mr", "report", "!")):
            return command_gitlab_report(command, jira_key)
        if not jira_key:
            return {
                "view": "overview",
                "message": "Please include a Jira key such as BDRSP-1413 or a GitLab MR number such as !482.",
                "needs_input": True,
            }
        if "create" in normalized and "subtask" in normalized:
            return command_create_subtask_preview(jira_key, command)
        if "subtask" in normalized:
            return command_subtask_status(jira_key)
        if any(token in normalized for token in ("feature", "epic", "progress")):
            return command_feature_progress(jira_key)
        if any(token in normalized for token in ("update", "modify", "refine", "rewrite")):
            return command_update_story_preview(jira_key)
        return command_read_story(jira_key)
    except (JiraConfigError, JiraRequestError, GitLabConfigError, GitLabRequestError, GitCommandError) as exc:
        return {
            "view": "overview",
            "message": str(exc),
            "error": True,
            "error_type": exc.__class__.__name__,
        }
    except Exception as exc:  # pragma: no cover - UI safety boundary.
        return {
            "view": "overview",
            "message": f"Automation UI command failed: {exc}",
            "error": True,
            "error_type": exc.__class__.__name__,
        }


class OperationsHandler(BaseHTTPRequestHandler):
    """Serve the local UI and command API."""

    server_version = "AutomationOperationsUI/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(self.path.split("?", 1)[0])
        if path in {"/", "/index.html"}:
            self.send_file(INDEX_FILE, "text/html; charset=utf-8")
            return
        if path == "/health":
            self.send_json({"ok": True, "service": "po-sm-operations-ui"})
            return
        candidate = (DOCS_DIR / path.lstrip("/")).resolve()
        if candidate.is_file() and DOCS_DIR.resolve() in candidate.parents:
            self.send_file(candidate, content_type_for(candidate))
            return
        self.send_json({"ok": False, "message": "Not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/command":
            self.send_json({"ok": False, "message": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            raw_body = self.rfile.read(length).decode("utf-8")
            body = json.loads(raw_body or "{}")
            command = str(body.get("command") or "").strip()
            if not command:
                self.send_json({"ok": False, "message": "Command is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            result = handle_command(command)
            self.send_json({"ok": not result.get("error"), "command": command, "result": json_safe(result)})
        except json.JSONDecodeError:
            self.send_json({"ok": False, "message": "Invalid JSON body"}, status=HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[automation-ui] {self.address_string()} - {format % args}")

    def send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".png":
        return "image/png"
    return "application/octet-stream"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the local PO/SM operations UI.")
    parser.add_argument("--host", default=os.getenv("AUTOMATION_UI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AUTOMATION_UI_PORT", "8787")))
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), OperationsHandler)
    print(f"[automation-ui] PO/SM Operations UI: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[automation-ui] Stopping UI server...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
