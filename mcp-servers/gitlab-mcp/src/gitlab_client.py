"""GitLab REST and local Git helpers for gitlab-mcp."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import base64
from pathlib import Path
from typing import Any


class GitLabConfigError(RuntimeError):
    """Raised when required GitLab configuration is missing."""


class GitLabRequestError(RuntimeError):
    """Raised when GitLab returns an error response."""


class GitCommandError(RuntimeError):
    """Raised when a local Git command fails."""


class JiraRequestError(RuntimeError):
    """Raised when Jira story context cannot be read."""


BRANCH_TYPE_PREFIXES = {
    "feature": "feature",
    "feat": "feature",
    "fix": "fix",
    "bugfix": "fix",
    "hotfix": "hotfix",
    "chore": "chore",
    "docs": "docs",
    "refactor": "refactor",
    "test": "test",
    "perf": "perf",
    "performance": "perf",
    "security": "security",
}

JIRA_STORY_FIELDS = [
    "summary",
    "description",
    "status",
    "priority",
    "assignee",
    "reporter",
    "labels",
    "components",
    "issuelinks",
    "comment",
    "issuetype",
    "subtasks",
    "parent",
]


def json_response(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def _resolve_token_from_git_credentials(host: str) -> str:
    """Read a GitLab token from ~/.git-credentials for the given hostname."""
    try:
        creds_path = Path.home() / ".git-credentials"
        if not creds_path.exists():
            return ""
        content = creds_path.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            line = line.strip()
            if host not in line:
                continue
            # Format: https://<user>:<token>@<host>
            match = re.match(r"https?://[^:]+:([^@]+)@", line)
            if match:
                tok = match.group(1)
                if tok and tok != "x-oauth-basic":
                    return tok
    except OSError:
        pass
    return ""


def _resolve_token_from_keychain(host: str) -> str:
    """Read a GitLab token from macOS Keychain for the given hostname."""
    try:
        result = subprocess.run(
            ["security", "find-internet-password", "-s", host, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _resolve_token_from_git_credential_fill(host: str) -> str:
    """Ask git credential fill for the token for the given hostname."""
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input=f"protocol=https\nhost={host}\n",
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("password="):
                    tok = line[len("password="):].strip()
                    if tok and tok != "x-oauth-basic":
                        return tok
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _auto_resolve_gitlab_token(base_url: str, explicit_token: str | None) -> str:
    """Resolve the GitLab token from env, git credentials, or macOS Keychain.

    Priority:
    1. Explicitly passed token argument.
    2. GITLAB_TOKEN / GITLAB_PRIVATE_TOKEN env var (set from .env.local).
    3. ~/.git-credentials (git credential store).
    4. macOS Keychain (security find-internet-password).
    5. git credential fill (runtime credential helper).
    """
    if explicit_token:
        return explicit_token
    env_token = os.getenv("GITLAB_TOKEN") or os.getenv("GITLAB_PRIVATE_TOKEN") or ""
    if env_token:
        return env_token
    # Extract bare hostname from base_url for credential lookups
    host = base_url.rstrip("/").removeprefix("https://").removeprefix("http://").split("/")[0]
    tok = _resolve_token_from_git_credentials(host)
    if tok:
        return tok
    tok = _resolve_token_from_keychain(host)
    if tok:
        return tok
    tok = _resolve_token_from_git_credential_fill(host)
    return tok


def get_gitlab_config(
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, str]:
    base_url = (gitlab_url or os.getenv("GITLAB_BASE_URL") or "https://gitlab.com").rstrip("/")
    resolved_project = project_id or os.getenv("GITLAB_PROJECT_ID") or os.getenv("GITLAB_PROJECT_PATH") or ""
    resolved_token = _auto_resolve_gitlab_token(base_url, token)

    missing = []
    if not base_url:
        missing.append("GITLAB_BASE_URL")
    if not resolved_token:
        missing.append("GITLAB_TOKEN")
    if not resolved_project:
        missing.append("GITLAB_PROJECT_ID")
    if missing:
        raise GitLabConfigError(
            f"Missing required GitLab environment variable(s): {', '.join(missing)}. "
            "Run: ./Automation/scripts/sync-gitlab-token.sh  — or set GITLAB_TOKEN in Automation/.env.local"
        )

    return {
        "base_url": base_url,
        "project_id": resolved_project,
        "token": resolved_token,
    }


def detect_default_branch(
    working_dir: str | None = None,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Auto-detect the repository's default (production) branch.

    Detection order:
      1. GitLab API → GET /api/v4/projects/:id  → default_branch field
      2. Local git  → git symbolic-ref refs/remotes/origin/HEAD
      3. Fallback   → "main"

    Returns dict with keys: default_branch, method, ok
    """
    # ── 1. GitLab API ─────────────────────────────────────────────────────────
    try:
        config = get_gitlab_config(gitlab_url=gitlab_url, project_id=project_id, token=token)
        api_base = config["base_url"]
        proj = urllib.parse.quote(str(config["project_id"]), safe="")
        url = f"{api_base}/api/v4/projects/{proj}"
        req = urllib.request.Request(url, headers={
            "PRIVATE-TOKEN": config["token"],
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode())
            branch = data.get("default_branch") or ""
            if branch:
                return {"ok": True, "default_branch": branch, "method": "gitlab_api"}
    except Exception:
        pass

    # ── 2. Local git symbolic-ref ─────────────────────────────────────────────
    try:
        cwd = working_dir or os.getcwd()
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            # output looks like: refs/remotes/origin/main
            branch = result.stdout.strip().split("/")[-1]
            if branch:
                return {"ok": True, "default_branch": branch, "method": "git_symbolic_ref"}
    except Exception:
        pass

    # ── 3. Heuristic: check for 'main' then 'master' locally ──────────────────
    try:
        cwd = working_dir or os.getcwd()
        for candidate in ("main", "master", "develop"):
            r = subprocess.run(
                ["git", "rev-parse", "--verify", f"origin/{candidate}"],
                cwd=cwd, capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                return {"ok": True, "default_branch": candidate, "method": "heuristic_probe"}
    except Exception:
        pass

    return {"ok": True, "default_branch": "main", "method": "fallback"}


def check_token_health(
    gitlab_url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Check whether the configured GitLab token is valid and when it expires.

    Calls GET /api/v4/personal_access_tokens/self — available for PATs on GitLab 15+.
    Returns a structured health report with expiry date, days remaining, and a clear
    renewal instruction when the token is expired or about to expire.
    """
    import datetime

    try:
        config = get_gitlab_config(gitlab_url=gitlab_url, token=token)
    except GitLabConfigError as exc:
        return {
            "ok": False,
            "token_status": "missing",
            "error": str(exc),
            "renewal_instruction": (
                "No GitLab token found. "
                "Go to: {base_url}/-/user_settings/personal_access_tokens and create a new token "
                "with api + read_repository + write_repository scopes. "
                "Then run: ./Automation/scripts/sync-gitlab-token.sh"
            ).format(base_url=(gitlab_url or os.getenv("GITLAB_BASE_URL") or "https://gitlab.com")),
        }

    url = f"{config['base_url']}/api/v4/personal_access_tokens/self"
    req = urllib.request.Request(url, headers=_gitlab_auth_header(config["token"]))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {
                "ok": False,
                "token_status": "expired_or_invalid",
                "http_status": exc.code,
                "error": "Token was rejected by GitLab (401/403). It is expired or has been revoked.",
                "renewal_instruction": (
                    f"Your GitLab token is EXPIRED or INVALID.\n"
                    f"Steps to fix:\n"
                    f"  1. Go to: {config['base_url']}/-/user_settings/personal_access_tokens\n"
                    f"  2. Create a new token with scopes: api, read_repository, write_repository\n"
                    f"  3. Copy the new token and run: git clone <any-repo-url> (to save it in git-credentials)\n"
                    f"     OR manually set it: echo 'GITLAB_TOKEN=<new-token>' >> Automation/.env.local\n"
                    f"  4. Then run: ./Automation/scripts/sync-gitlab-token.sh"
                ),
            }
        return {"ok": False, "token_status": "unknown_error", "http_status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "token_status": "unreachable", "error": str(exc)}

    expires_at = data.get("expires_at")  # "YYYY-MM-DD" or null
    revoked = data.get("revoked", False)
    active = data.get("active", True)
    scopes = data.get("scopes", [])
    name = data.get("name", "")

    days_remaining: int | None = None
    expiry_warning: str | None = None
    token_status = "valid"

    if revoked or not active:
        token_status = "revoked"
        expiry_warning = "Token has been revoked or is inactive."
    elif expires_at:
        try:
            expiry_date = datetime.date.fromisoformat(expires_at)
            today = datetime.date.today()
            days_remaining = (expiry_date - today).days
            if days_remaining < 0:
                token_status = "expired"
                expiry_warning = f"Token EXPIRED {abs(days_remaining)} day(s) ago on {expires_at}."
            elif days_remaining <= 7:
                token_status = "expiring_soon"
                expiry_warning = f"⚠️  Token expires in {days_remaining} day(s) on {expires_at}. Renew soon!"
            elif days_remaining <= 30:
                token_status = "expiring_soon"
                expiry_warning = f"Token expires in {days_remaining} day(s) on {expires_at}."
        except ValueError:
            pass
    else:
        token_status = "valid_no_expiry"

    renewal_instruction: str | None = None
    if token_status in ("expired", "revoked"):
        renewal_instruction = (
            f"Your GitLab token '{name}' is {token_status}.\n"
            f"Steps to fix:\n"
            f"  1. Go to: {config['base_url']}/-/user_settings/personal_access_tokens\n"
            f"  2. Create a new token with scopes: api, read_repository, write_repository\n"
            f"  3. Run: git -C <project-path> fetch  (saves new token to git-credentials)\n"
            f"     OR: set GITLAB_TOKEN=<new-token> in Automation/.env.local\n"
            f"  4. Run: ./Automation/scripts/sync-gitlab-token.sh"
        )

    return {
        "ok": token_status in ("valid", "valid_no_expiry", "expiring_soon"),
        "token_status": token_status,
        "token_name": name,
        "scopes": scopes,
        "expires_at": expires_at,
        "days_remaining": days_remaining,
        "expiry_warning": expiry_warning,
        "renewal_instruction": renewal_instruction,
        "note": (
            "GitLab PATs cannot be auto-generated when expired. "
            "A human must create a new token at the URL above. "
            "Once the new token is saved via git operations, "
            "sync-gitlab-token.sh will automatically pick it up."
        ),
    }


def _encoded_project(project_id: str) -> str:
    return urllib.parse.quote(project_id, safe="")


def _api_url(config: dict[str, str], path: str, query: dict[str, Any] | None = None) -> str:
    encoded_query = ""
    if query:
        clean_query = {key: value for key, value in query.items() if value is not None}
        encoded_query = urllib.parse.urlencode(clean_query, doseq=True)
    return f"{config['base_url']}/api/v4/{path.lstrip('/')}{'?' + encoded_query if encoded_query else ''}"


def _gitlab_auth_header(token: str) -> dict[str, str]:
    """Return the correct GitLab API auth header for PAT or OAuth tokens."""
    if token.startswith(("glpat-", "glptt-", "glcbt-", "glrt-", "glsoat-")):
        return {"PRIVATE-TOKEN": token}
    return {"Authorization": f"Bearer {token}"}


def _auto_refresh_token() -> str | None:
    """Run sync-gitlab-token.sh to auto-refresh an expired token. Returns new token or None."""
    import logging
    logger = logging.getLogger("gitlab-mcp")
    automation_dir = Path(__file__).resolve().parent.parent.parent
    sync_script = automation_dir / "scripts" / "sync-gitlab-token.sh"
    env_file = automation_dir / ".env.local"

    if not sync_script.exists():
        logger.warning("sync-gitlab-token.sh not found at %s", sync_script)
        return None

    logger.info("Token expired — auto-refreshing via sync-gitlab-token.sh ...")
    try:
        result = subprocess.run(
            ["zsh", str(sync_script)],
            capture_output=True, text=True, timeout=60,
            cwd=str(automation_dir),
        )
        if result.returncode != 0:
            logger.warning("Token auto-refresh failed: %s", result.stderr)
            return None
    except Exception as e:
        logger.warning("Token auto-refresh error: %s", e)
        return None

    # Read refreshed token from .env.local
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GITLAB_TOKEN="):
                new_token = line.split("=", 1)[1].strip()
                if new_token:
                    os.environ["GITLAB_TOKEN"] = new_token
                    logger.info("Token auto-refreshed successfully ✓")
                    return new_token
    return None


def gitlab_request(
    path: str,
    *,
    config: dict[str, str],
    method: str = "GET",
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    _retried: bool = False,
) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        **_gitlab_auth_header(config["token"]),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "gitlab-mcp",
    }
    request = urllib.request.Request(
        _api_url(config, path, query),
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        # Auto-refresh on 401 (expired token) — retry once
        if exc.code == 401 and not _retried:
            new_token = _auto_refresh_token()
            if new_token:
                refreshed_config = {**config, "token": new_token}
                return gitlab_request(
                    path, config=refreshed_config, method=method,
                    query=query, body=body, _retried=True,
                )
        raise GitLabRequestError(f"GitLab API error {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise GitLabRequestError(f"Could not reach GitLab API: {exc.reason}") from exc


def check_connection(
    *,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    config = get_gitlab_config(gitlab_url, project_id, token)
    user = gitlab_request("user", config=config)
    project = gitlab_request(f"projects/{_encoded_project(config['project_id'])}", config=config)
    return {
        "ok": True,
        "gitlab_url": config["base_url"],
        "project_id": config["project_id"],
        "project_name": project.get("name_with_namespace") or project.get("path_with_namespace"),
        "project_web_url": project.get("web_url"),
        "username": user.get("username"),
        "name": user.get("name"),
    }


def _user_summary(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "name": user.get("name"),
        "web_url": user.get("web_url"),
    }


def _compact_pipeline(pipeline: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pipeline:
        return None
    return {
        "id": pipeline.get("id"),
        "iid": pipeline.get("iid"),
        "status": pipeline.get("status"),
        "ref": pipeline.get("ref"),
        "sha": pipeline.get("sha"),
        "web_url": pipeline.get("web_url"),
        "created_at": pipeline.get("created_at"),
        "updated_at": pipeline.get("updated_at"),
    }


def compact_merge_request(mr: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": mr.get("id"),
        "iid": mr.get("iid"),
        "title": mr.get("title"),
        "description": mr.get("description") or "",
        "web_url": mr.get("web_url"),
        "state": mr.get("state"),
        "draft": mr.get("draft") or mr.get("work_in_progress"),
        "author": _user_summary(mr.get("author")),
        "source_branch": mr.get("source_branch"),
        "target_branch": mr.get("target_branch"),
        "labels": mr.get("labels") or [],
        "assignees": [_user_summary(user) for user in mr.get("assignees") or []],
        "reviewers": [_user_summary(user) for user in mr.get("reviewers") or []],
        "has_conflicts": mr.get("has_conflicts"),
        "merge_status": mr.get("merge_status"),
        "detailed_merge_status": mr.get("detailed_merge_status"),
        "changes_count": mr.get("changes_count"),
        "user_notes_count": mr.get("user_notes_count"),
        "head_pipeline": _compact_pipeline(mr.get("head_pipeline")),
        "created_at": mr.get("created_at"),
        "updated_at": mr.get("updated_at"),
    }


def get_merge_request(
    mr_iid: int,
    *,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    config = get_gitlab_config(gitlab_url, project_id, token)
    mr = gitlab_request(f"projects/{_encoded_project(config['project_id'])}/merge_requests/{mr_iid}", config=config)
    return {
        "ok": True,
        "merge_request": compact_merge_request(mr),
    }


def get_merge_request_changes(
    mr_iid: int,
    *,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    config = get_gitlab_config(gitlab_url, project_id, token)
    base = f"projects/{_encoded_project(config['project_id'])}/merge_requests/{mr_iid}"
    mr = gitlab_request(base, config=config)
    try:
        change_payload = gitlab_request(f"{base}/changes", config=config)
        changes = change_payload.get("changes") or []
    except GitLabRequestError:
        changes = gitlab_request(f"{base}/diffs", config=config, query={"per_page": 100})

    normalized_changes = [
        {
            "old_path": change.get("old_path"),
            "new_path": change.get("new_path"),
            "new_file": change.get("new_file"),
            "renamed_file": change.get("renamed_file"),
            "deleted_file": change.get("deleted_file"),
            "diff_too_large": change.get("too_large"),
        }
        for change in changes
    ]
    return {
        "ok": True,
        "mr": {"iid": mr.get("iid"), "title": mr.get("title"), "web_url": mr.get("web_url")},
        "change_count": len(normalized_changes),
        "changes": normalized_changes,
    }


def _note_summary(note: dict[str, Any]) -> dict[str, Any]:
    body = note.get("body") or ""
    return {
        "id": note.get("id"),
        "author": _user_summary(note.get("author")),
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
        "system": note.get("system"),
        "resolvable": note.get("resolvable"),
        "resolved": note.get("resolved"),
        "body": body[:1500],
    }


def get_merge_request_discussions(
    mr_iid: int,
    *,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    config = get_gitlab_config(gitlab_url, project_id, token)
    base = f"projects/{_encoded_project(config['project_id'])}/merge_requests/{mr_iid}"
    mr = gitlab_request(base, config=config)
    discussions = gitlab_request(f"{base}/discussions", config=config, query={"per_page": 100})

    normalized = []
    unresolved = 0
    for discussion in discussions:
        notes = [_note_summary(note) for note in discussion.get("notes") or []]
        is_unresolved = any(note.get("resolvable") and not note.get("resolved") for note in notes)
        unresolved += 1 if is_unresolved else 0
        normalized.append(
            {
                "id": discussion.get("id"),
                "individual_note": discussion.get("individual_note"),
                "unresolved": is_unresolved,
                "notes": notes,
            }
        )

    return {
        "ok": True,
        "mr": {"iid": mr.get("iid"), "title": mr.get("title"), "web_url": mr.get("web_url")},
        "summary": {
            "discussion_count": len(normalized),
            "unresolved_threads": unresolved,
        },
        "discussions": normalized,
    }


def get_pipeline_status(
    mr_iid: int,
    *,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    config = get_gitlab_config(gitlab_url, project_id, token)
    base = f"projects/{_encoded_project(config['project_id'])}/merge_requests/{mr_iid}"
    mr = gitlab_request(base, config=config)
    pipelines = gitlab_request(f"{base}/pipelines", config=config, query={"per_page": 5})
    return {
        "ok": True,
        "mr": {"iid": mr.get("iid"), "title": mr.get("title"), "web_url": mr.get("web_url")},
        "head_pipeline": _compact_pipeline(mr.get("head_pipeline")),
        "recent_pipelines": [_compact_pipeline(pipeline) for pipeline in pipelines],
        "detailed_merge_status": mr.get("detailed_merge_status"),
    }


BRANCH_SLUG_STOP_WORDS = {
    "a",
    "an",
    "and",
    "or",
    "the",
    "to",
    "for",
    "from",
    "with",
    "without",
    "of",
    "in",
    "on",
    "by",
    "is",
    "are",
    "be",
    "this",
    "that",
    "story",
    "task",
    "subtask",
    "analyze",
    "plan",
    "execute",
    "validate",
    "document",
    "support",
    "add",
    "update",
    "create",
    "created",
    "creating",
    "fix",
    "helper",
    "service",
    "controller",
    "entity",
    "entities",
    "schema",
    "fields",
    "field",
    "gap",
}


def _split_identifier_words(text: str) -> list[str]:
    prepared = re.sub(r"GraphQL", "graphql", text or "", flags=re.IGNORECASE)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", prepared)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    raw_words = re.findall(r"[a-zA-Z0-9]+", spaced.lower())
    normalized: list[str] = []
    for word in raw_words:
        if word in {"qm", "am", "trs", "graphql", "rdf", "xml", "api", "url", "uri"}:
            normalized.append(word)
            continue
        normalized.append(word)
    return normalized


def _branch_phrase_words(words: list[str]) -> list[str]:
    word_set = set(words)
    phrases: list[list[str]] = []
    if {"change", "event"} <= word_set:
        phrases.append(["change", "event"])
    if {"url", "resolver"} <= word_set:
        phrases.append(["url", "resolver"])
    if {"trs", "feed"} <= word_set:
        phrases.append(["trs", "feed"])
    if {"graphql", "schema"} <= word_set:
        phrases.append(["graphql", "schema"])
    if {"qm", "am"} <= word_set:
        phrases.append(["qm", "am"])
    if {"rhapsody", "resolver"} <= word_set and ["url", "resolver"] not in phrases:
        phrases.append(["rhapsody", "resolver"])

    selected: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        for word in phrase:
            if word not in seen:
                selected.append(word)
                seen.add(word)
    for word in words:
        if word in BRANCH_SLUG_STOP_WORDS or len(word) < 2 or word in seen:
            continue
        selected.append(word)
        seen.add(word)
    return selected


def _slugify(text: str, max_words: int = 5) -> str:
    words = _split_identifier_words(text)
    meaningful = _branch_phrase_words(words)
    selected = meaningful[:max_words] or words[:max_words]
    return "-".join(selected) or "work"


def build_branch_name(story_id: str | None, description: str, branch_type: str) -> str:
    prefix = BRANCH_TYPE_PREFIXES.get(branch_type.lower(), branch_type.lower())
    slug = _slugify(description, max_words=5)
    if story_id:
        return f"{prefix}/{story_id.upper().strip()}-{slug}"
    return f"{prefix}/{slug}"


def resolve_branch_story_context(story_id: str | None, description: str) -> dict[str, Any]:
    if not story_id:
        return {
            "branch_story_id": None,
            "branch_description": description,
            "source_story_id": None,
            "source_issue_type": None,
            "parent_story_id": None,
            "rule": "No story_id was provided.",
        }

    try:
        issue = read_jira_story_for_update(story_id)
    except Exception as exc:
        return {
            "branch_story_id": story_id,
            "branch_description": description,
            "source_story_id": story_id,
            "source_issue_type": None,
            "parent_story_id": None,
            "warning": f"Could not verify Jira parent story, using provided story_id: {exc}",
        }

    issue_type = (issue.get("issue_type") or "").lower()
    parent_key = issue.get("parent")
    if parent_key or issue_type in {"sub-task", "subtask", "sub task"}:
        parent_issue = None
        parent_summary = ""
        if parent_key:
            try:
                parent_issue = read_jira_story_for_update(parent_key)
                parent_summary = parent_issue.get("summary") or ""
            except Exception:
                parent_summary = ""
        return {
            "branch_story_id": parent_key or story_id,
            "branch_description": parent_summary or description,
            "source_story_id": story_id,
            "source_issue_type": issue.get("issue_type"),
            "parent_story_id": parent_key,
            "rule": "Input Jira key is a subtask, so branch name uses parent story key.",
        }

    return {
        "branch_story_id": story_id,
        "branch_description": issue.get("summary") or description,
        "source_story_id": story_id,
        "source_issue_type": issue.get("issue_type"),
        "parent_story_id": None,
        "rule": "Input Jira key is a story/task, so branch name uses it directly.",
    }


def run_git(args: list[str], *, cwd: str | None, hide_token: str | None = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    if hide_token:
        process.stdout = process.stdout.replace(hide_token, "***redacted***")
        process.stderr = process.stderr.replace(hide_token, "***redacted***")
    return process


def _require_git_repo(working_dir: str | None) -> str:
    cwd = working_dir or os.getenv("GIT_WORKING_DIR") or os.getenv("WORKSPACE_DIR") or os.getcwd()
    check = run_git(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if check.returncode != 0:
        raise GitCommandError(f"Not a Git repository: {cwd}")
    return check.stdout.strip()


def extract_adf_text(node: Any) -> str:
    """Extract plain text from Atlassian Document Format."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(filter(None, (extract_adf_text(item) for item in node)))
    if not isinstance(node, dict):
        return ""

    parts: list[str] = []
    if node.get("type") == "text":
        parts.append(node.get("text", ""))
    if node.get("content"):
        parts.append(extract_adf_text(node["content"]))
    return "\n".join(part for part in parts if part).strip()


def get_jira_config() -> dict[str, str]:
    base_url = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
    username = os.getenv("JIRA_USERNAME") or os.getenv("JIRA_EMAIL") or ""
    token = os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_TOKEN") or ""
    missing = []
    if not base_url:
        missing.append("JIRA_BASE_URL")
    if not username:
        missing.append("JIRA_USERNAME")
    if not token:
        missing.append("JIRA_API_TOKEN")
    if missing:
        raise JiraRequestError(f"Missing required Jira environment variable(s): {', '.join(missing)}")
    return {"base_url": base_url, "username": username, "token": token}


def jira_request(path: str, *, config: dict[str, str]) -> Any:
    url = f"{config['base_url']}/rest/api/3/{path.lstrip('/')}"
    encoded = base64.b64encode(f"{config['username']}:{config['token']}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {encoded}",
            "Accept": "application/json",
            "User-Agent": "gitlab-mcp-jiraforge-code-updater",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise JiraRequestError(f"Jira API error {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise JiraRequestError(f"Could not reach Jira API: {exc.reason}") from exc


def read_jira_story_for_update(jira_id: str) -> dict[str, Any]:
    config = get_jira_config()
    fields = urllib.parse.quote(",".join(JIRA_STORY_FIELDS))
    issue = jira_request(f"issue/{urllib.parse.quote(jira_id.strip(), safe='')}?fields={fields}", config=config)
    raw_fields = issue.get("fields", {})
    subtasks = []
    for subtask in raw_fields.get("subtasks") or []:
        subtask_fields = subtask.get("fields") or {}
        subtasks.append(
            {
                "key": subtask.get("key"),
                "summary": subtask_fields.get("summary"),
                "status": (subtask_fields.get("status") or {}).get("name"),
                "issue_type": (subtask_fields.get("issuetype") or {}).get("name"),
            }
        )

    comments = []
    for comment in (raw_fields.get("comment") or {}).get("comments", []):
        comments.append(
            {
                "author": ((comment.get("author") or {}).get("displayName") or "unknown"),
                "created": comment.get("created"),
                "body": extract_adf_text(comment.get("body"))[:1200],
            }
        )

    return {
        "id": issue.get("id"),
        "key": issue.get("key"),
        "url": f"{config['base_url']}/browse/{issue.get('key')}",
        "summary": raw_fields.get("summary") or "",
        "description": extract_adf_text(raw_fields.get("description")),
        "status": (raw_fields.get("status") or {}).get("name"),
        "priority": (raw_fields.get("priority") or {}).get("name"),
        "issue_type": (raw_fields.get("issuetype") or {}).get("name"),
        "parent": (raw_fields.get("parent") or {}).get("key"),
        "assignee": ((raw_fields.get("assignee") or {}).get("displayName") or "unassigned"),
        "reporter": ((raw_fields.get("reporter") or {}).get("displayName") or "unknown"),
        "labels": raw_fields.get("labels") or [],
        "components": [item.get("name") for item in raw_fields.get("components") or [] if item.get("name")],
        "subtasks": subtasks,
        "comments": comments,
    }


def parse_acceptance_criteria(text: str) -> list[str]:
    match = re.search(r"(acceptance criteria|acceptance criterion|\bac\b)[:\n](.*)", text or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    candidates = []
    for line in match.group(2).splitlines():
        cleaned = re.sub(r"^\s*[-*•\d.)\]]+\s*", "", line).strip()
        if cleaned and len(cleaned) > 8:
            candidates.append(cleaned)
    return candidates[:12]


def tokenize_for_search(text: str) -> list[str]:
    stop_words = {
        "the", "and", "for", "with", "from", "that", "this", "into", "when", "then",
        "should", "must", "will", "user", "story", "acceptance", "criteria", "able",
    }
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    seen: set[str] = set()
    results = []
    for token in tokens:
        if token not in stop_words and token not in seen:
            seen.add(token)
            results.append(token)
    return results[:14]


def find_candidate_files(repo: str, keywords: list[str], limit: int = 60) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for keyword in keywords[:10]:
        result = subprocess.run(
            ["rg", "--files-with-matches", "--ignore-case", "--glob", "!Automation/**", keyword],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode not in (0, 1):
            continue
        for path in result.stdout.splitlines():
            item = candidates.setdefault(path, {"path": path, "matched_keywords": []})
            item["matched_keywords"].append(keyword)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    return sorted(candidates.values(), key=lambda item: (-len(item["matched_keywords"]), item["path"]))


def impacted_area_from_path(path: str) -> str:
    lowered = path.lower()
    if "test" in lowered or "spec" in lowered:
        return "tests"
    if any(part in lowered for part in ("controller", "resource", "endpoint", "api")):
        return "api changes"
    if "service" in lowered:
        return "backend logic"
    if any(part in lowered for part in ("repository", "dao", "migration", "schema", "database")):
        return "db/config changes"
    if any(part in lowered for part in ("config", "application.", ".yaml", ".yml", ".properties")):
        return "db/config changes"
    if any(part in lowered for part in ("frontend", "component", ".tsx", ".jsx", ".vue", ".html", ".css")):
        return "frontend"
    return "implementation"


def classify_effort(file_count: int, impacted_areas: list[str]) -> dict[str, Any]:
    if file_count <= 3:
        size = "Small"
        proceed = True
    elif file_count <= 8:
        size = "Medium"
        proceed = True
    elif file_count <= 13:
        size = "Large"
        proceed = False
    else:
        size = "Very Large / Risky"
        proceed = False

    risk = "low"
    if size == "Medium" or len(impacted_areas) >= 3:
        risk = "medium"
    if size in {"Large", "Very Large / Risky"}:
        risk = "high"

    return {
        "size": size,
        "risk": risk,
        "can_start_without_extra_confirmation": proceed,
    }


def suggest_subtasks_for_effort(impacted_areas: list[str]) -> list[str]:
    order = [
        "Analysis",
        "Design / Approach",
        "backend logic",
        "api changes",
        "db/config changes",
        "frontend",
        "tests",
        "Documentation",
    ]
    suggestions = [area for area in order if area in impacted_areas or area in {"Analysis", "tests"}]
    return suggestions or ["Analysis", "Implementation", "Tests"]


def jiraforge_code_updater(
    *,
    jira_id: str,
    selected_subtask_key: str | None = None,
    implement_whole_story: bool = False,
    base_branch: str = "main",
    working_dir: str | None = None,
) -> dict[str, Any]:
    repo = _require_git_repo(working_dir)
    story = read_jira_story_for_update(jira_id)
    active_item = story
    subtasks = story.get("subtasks") or []

    if subtasks and not selected_subtask_key and not implement_whole_story:
        return {
            "ok": True,
            "action": "user_selection_required",
            "message": "This story has subtasks. Which subtask do you want me to work on?",
            "story": {
                "key": story["key"],
                "summary": story["summary"],
                "url": story["url"],
            },
            "available_subtasks": subtasks,
            "rule": "Do not start implementation until the user selects a subtask or clearly asks to implement the whole story.",
        }

    if selected_subtask_key:
        selected = next((subtask for subtask in subtasks if subtask.get("key") == selected_subtask_key), None)
        if not selected:
            return {
                "ok": False,
                "action": "invalid_subtask",
                "message": f"Subtask {selected_subtask_key} was not found under {jira_id}.",
                "available_subtasks": subtasks,
            }
        active_item = {
            **story,
            "key": selected["key"],
            "summary": selected.get("summary") or story["summary"],
            "description": story["description"],
            "parent_story": story["key"],
        }

    branch = current_branch(repo)
    status = _git_output(["git", "status", "--short"], cwd=repo)
    modified_files = [line[3:].strip() for line in status.splitlines() if line.strip()]

    if modified_files:
        return {
            "ok": True,
            "action": "working_tree_confirmation_required",
            "message": "I found existing local changes. Should implementation continue in this branch, or should a separate branch be created first?",
            "repository": repo,
            "current_branch": branch,
            "existing_changes": modified_files,
            "rule": "Do not overwrite or mix with existing user changes without confirmation.",
            "branch_guidance": "If the user wants a new branch, use gitlab_create_branch first, then come back to implementation.",
        }

    story_text = "\n".join(
        [
            active_item.get("key", ""),
            active_item.get("summary", ""),
            active_item.get("description", ""),
            "\n".join(parse_acceptance_criteria(active_item.get("description", ""))),
        ]
    )
    keywords = tokenize_for_search(story_text)
    candidate_files = find_candidate_files(repo, keywords)
    impacted_files = candidate_files[:13]
    impacted_areas = sorted({impacted_area_from_path(item["path"]) for item in impacted_files})
    effort = classify_effort(len(impacted_files), impacted_areas)

    if not subtasks:
        subtask_note = (
            "No subtasks are available for this story. If this story is large, create subtasks first. "
            "For small or medium changes, implementation can continue directly with this story."
        )
    else:
        subtask_note = "Subtask selection is resolved." if selected_subtask_key or implement_whole_story else "Subtask selection is required."

    response = {
        "ok": True,
        "tool": "jiraforge_code_updater",
        "action": "implementation_contract_ready" if effort["can_start_without_extra_confirmation"] else "effort_confirmation_required",
        "repository": repo,
        "current_branch": branch,
        "base_branch": base_branch,
        "story_or_subtask": {
            "key": active_item.get("key"),
            "summary": active_item.get("summary"),
            "parent_story": active_item.get("parent_story"),
            "url": story.get("url"),
            "status": active_item.get("status"),
            "priority": active_item.get("priority"),
            "components": story.get("components", []),
            "labels": story.get("labels", []),
        },
        "acceptance_criteria": parse_acceptance_criteria(active_item.get("description", "")),
        "subtask_note": subtask_note,
        "pre_implementation_checks": {
            "working_tree_clean": True,
            "existing_changes": [],
            "current_branch": branch,
        },
        "effort_report": {
            "estimated_impacted_files": len(impacted_files),
            "impacted_areas": impacted_areas,
            "risk": effort["risk"],
            "effort": effort["size"],
            "suggested_subtasks": suggest_subtasks_for_effort(impacted_areas),
            "recommendation": (
                "Proceed with a small, pattern-following implementation."
                if effort["can_start_without_extra_confirmation"]
                else "Stop and ask for confirmation before making changes."
            ),
        },
        "candidate_files_to_read_first": impacted_files,
        "implementation_rules": [
            "Make the smallest possible code change required.",
            "Follow existing project structure, naming, style, and design patterns.",
            "Prefer modifying existing classes/methods over creating new files.",
            "Do not touch unrelated files.",
            "Do not introduce broad refactoring or unnecessary abstraction.",
            "Remove unused imports, dead code, debug logs, TODOs, and commented-out code.",
            "Update JavaDoc only when a new public API is introduced or existing JavaDoc becomes incorrect.",
            "Add or update focused tests if matching test patterns exist.",
        ],
        "final_response_format": {
            "Implementation Summary": [
                "Story/Subtask worked on",
                "Files changed",
                "What was changed",
                "Why it was changed",
                "Tests added/updated",
                "Pending/manual validation",
            ]
        },
    }

    if not effort["can_start_without_extra_confirmation"]:
        response["confirmation_question"] = "This looks Large or Very Large/Risky. Do you want me to proceed with code changes?"

    return response


def _authenticated_remote_url(remote_url: str, token: str) -> str:
    return re.sub(r"(https?://)([^@]+@)?", rf"\1oauth2:{token}@", remote_url)


def _remote_host(remote_url: str) -> str:
    """Extract hostname from an HTTPS remote URL."""
    try:
        return urllib.parse.urlparse(remote_url).hostname or ""
    except Exception:
        return ""


def _seed_git_https_credentials(host: str, token: str) -> None:
    """Store HTTPS credentials for git operations via configured credential helper."""
    if not host or not token:
        return
    try:
        subprocess.run(
            ["git", "credential", "approve"],
            input=f"protocol=https\nhost={host}\nusername=oauth2\npassword={token}\n\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        pass


def _is_auth_failure(text: str) -> bool:
    lowered = (text or "").lower()
    signals = [
        "authentication failed",
        "http basic: access denied",
        "access denied",
        "permission denied (publickey)",
        "could not read from remote repository",
    ]
    return any(signal in lowered for signal in signals)


def _prepare_git_remote_auth(remote_url: str) -> str:
    """Best-effort auth prep for HTTPS remotes; returns resolved token if available."""
    if not remote_url.startswith("http"):
        return ""
    host = _remote_host(remote_url)
    if not host:
        return ""
    base_url = f"https://{host}"
    token = _auto_resolve_gitlab_token(base_url, None)
    if not token:
        token = _auto_refresh_token() or ""
    if token:
        _seed_git_https_credentials(host, token)
    return token


def create_branch(
    *,
    description: str,
    base_branch: str = "main",
    story_id: str | None = None,
    branch_type: str = "feature",
    working_dir: str | None = None,
    checkout: bool = True,
) -> dict[str, Any]:
    if not description.strip():
        raise GitCommandError("A short description is required to build the branch name.")

    repo = _require_git_repo(working_dir)
    branch_context = resolve_branch_story_context(story_id, description)
    branch_name = build_branch_name(
        branch_context["branch_story_id"],
        branch_context["branch_description"],
        branch_type,
    )
    steps: list[dict[str, Any]] = []

    existing = run_git(["git", "branch", "--list", branch_name], cwd=repo)
    if existing.stdout.strip():
        raise GitCommandError(f"Branch already exists locally: {branch_name}")

    fetch_target = "origin"
    token = os.getenv("GITLAB_TOKEN") or os.getenv("GITLAB_PRIVATE_TOKEN") or ""
    remote_url = run_git(["git", "remote", "get-url", "origin"], cwd=repo)
    if remote_url.returncode != 0:
        raise GitCommandError("Remote 'origin' is required so the new branch can be created from latest remote code.")
    remote_value = remote_url.stdout.strip()
    token = _prepare_git_remote_auth(remote_value) or token

    fetch = run_git(
        ["git", "fetch", fetch_target, base_branch],
        cwd=repo,
        hide_token=token,
    )
    if fetch.returncode != 0 and _is_auth_failure(fetch.stderr):
        refreshed = _auto_refresh_token() or ""
        if refreshed and refreshed != token:
            _seed_git_https_credentials(_remote_host(remote_value), refreshed)
            token = refreshed
            fetch = run_git(
                ["git", "fetch", fetch_target, base_branch],
                cwd=repo,
                hide_token=token,
            )
    steps.append({"step": "fetch_latest_origin_base", "ok": fetch.returncode == 0, "details": fetch.stderr.strip()})
    if fetch.returncode != 0:
        raise GitCommandError(f"Failed to fetch latest origin/{base_branch}: {fetch.stderr.strip()}")

    remote_check = run_git(["git", "rev-parse", "--verify", f"origin/{base_branch}"], cwd=repo)
    if remote_check.returncode != 0:
        raise GitCommandError(f"Remote branch origin/{base_branch} was not found after fetch.")

    start_point = f"origin/{base_branch}"
    command = ["git", "checkout", "-b", branch_name, start_point] if checkout else ["git", "branch", branch_name, start_point]
    created = run_git(command, cwd=repo)
    steps.append({"step": "create_branch", "ok": created.returncode == 0, "details": created.stderr.strip() or created.stdout.strip()})
    if created.returncode != 0:
        raise GitCommandError(f"Failed to create branch {branch_name}: {created.stderr.strip()}")

    current = run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    return {
        "ok": True,
        "repository": repo,
        "branch_name": branch_name,
        "branch_story_context": branch_context,
        "base_branch": base_branch,
        "start_point": start_point,
        "remote_sync_rule": f"Branch was created from freshly fetched origin/{base_branch}. No delete operation is performed.",
        "checked_out": checkout,
        "current_branch": current.stdout.strip(),
        "steps": steps,
        "next_steps": [
            "Make code changes on this branch.",
            "Run tests through test-mcp when ready.",
            "Use gitlab_push_branch only after user approval.",
            "Use gitlab_create_merge_request after the branch exists on GitLab.",
        ],
    }


def current_branch(working_dir: str | None = None) -> str:
    repo = _require_git_repo(working_dir)
    branch = run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    if branch.returncode != 0:
        raise GitCommandError(branch.stderr.strip())
    return branch.stdout.strip()


def push_branch(
    *,
    branch: str | None = None,
    remote: str = "origin",
    set_upstream: bool = True,
    working_dir: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    repo = _require_git_repo(working_dir)
    selected_branch = branch or current_branch(repo)
    command = ["git", "push"]
    if dry_run:
        command.append("--dry-run")
    if set_upstream:
        command.extend(["-u", remote, selected_branch])
    else:
        command.extend([remote, selected_branch])

    token = os.getenv("GITLAB_TOKEN") or os.getenv("GITLAB_PRIVATE_TOKEN") or ""
    push_remote = remote
    remote_url = run_git(["git", "remote", "get-url", remote], cwd=repo)
    remote_value = remote_url.stdout.strip() if remote_url.returncode == 0 else ""
    token = _prepare_git_remote_auth(remote_value) or token
    if token and remote_url.returncode == 0 and remote_value.startswith("http"):
        push_remote = _authenticated_remote_url(remote_url.stdout.strip(), token)
        if set_upstream:
            command = ["git", "push"]
            if dry_run:
                command.append("--dry-run")
            command.extend(["-u", push_remote, selected_branch])
        else:
            command = ["git", "push"]
            if dry_run:
                command.append("--dry-run")
            command.extend([push_remote, selected_branch])

    push = run_git(command, cwd=repo, hide_token=token)
    if push.returncode != 0 and _is_auth_failure(push.stderr):
        refreshed = _auto_refresh_token() or ""
        if refreshed and refreshed != token:
            if remote_value.startswith("http"):
                _seed_git_https_credentials(_remote_host(remote_value), refreshed)
                push_remote = _authenticated_remote_url(remote_url, refreshed)
                if set_upstream:
                    command = ["git", "push"]
                    if dry_run:
                        command.append("--dry-run")
                    command.extend(["-u", push_remote, selected_branch])
                else:
                    command = ["git", "push"]
                    if dry_run:
                        command.append("--dry-run")
                    command.extend([push_remote, selected_branch])
            push = run_git(command, cwd=repo, hide_token=refreshed)
    return {
        "ok": push.returncode == 0,
        "dry_run": dry_run,
        "repository": repo,
        "branch": selected_branch,
        "remote": remote,
        "details": push.stdout.strip() or push.stderr.strip(),
        "error": None if push.returncode == 0 else push.stderr.strip(),
    }


def _git_output(args: list[str], *, cwd: str) -> str:
    process = run_git(args, cwd=cwd)
    return process.stdout.strip() if process.returncode == 0 else ""


def _read_default_mr_template(repo: str) -> tuple[str | None, str | None]:
    template_path = Path(repo) / ".gitlab" / "merge_request_templates" / "Default.md"
    if not template_path.exists():
        return None, None
    return str(template_path), template_path.read_text(encoding="utf-8")


def _jira_url_from_story_id(story_id: str) -> str:
    """Build a best-effort Jira browse URL from the story ID using the JIRA_BASE_URL env var."""
    base = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
    return f"{base}/browse/{story_id.strip()}"


def _fill_template_sections(
    template: str,
    *,
    story_id: str,
    story_summary: str,
    change_type: str,
    source_branch: str,
    target_branch: str,
    testing_notes: str,
    commits: str,
    diff_stat: str,
) -> str:
    """Fill every section of Default.md with generated content.

    Replaces the GitLab ``%{first_multiline_commit_description}`` placeholder,
    all HTML comment hint lines, and marks the author checklist as complete
    while leaving the reviewer checklist unchecked for reviewers to fill.
    """
    jira_url = _jira_url_from_story_id(story_id)
    resolved_testing = testing_notes.strip() or "- Testing completed during the DevFlow checkpoint. Add exact command output before marking this MR ready for review."
    formatted_commits = "\n".join(f"- {line}" for line in commits.splitlines()) if commits.strip() else "- No commits detected against target branch yet."
    formatted_diff = f"```text\n{diff_stat.strip()}\n```" if diff_stat.strip() else "```text\nNo diff detected against target branch yet.\n```"

    why_needed = (
        f"This change implements **{change_type}** work for `{story_id}`: {story_summary}.\n\n"
        f"Source branch: `{source_branch}` → Target: `{target_branch}`."
    )
    technical_notes = (
        f"- Change type: **{change_type}**\n"
        f"- Branch: `{source_branch}` → `{target_branch}`\n\n"
        "**Commits included:**\n"
        f"{formatted_commits}\n\n"
        "**Diff summary:**\n"
        f"{formatted_diff}"
    )

    result = template

    # Replace GitLab commit description placeholder with story summary
    result = result.replace("%{first_multiline_commit_description}", story_summary)

    # Fill "Why is this change needed?" section comment
    result = result.replace(
        "<!-- Describe the problem, business need, or bug being fixed. -->",
        why_needed,
    )

    # Fill "Related ticket / reference" section comment
    result = result.replace(
        "<!-- Add the Jira/GitLab issue, incident, or supporting link. -->",
        f"[{story_id}]({jira_url})",
    )

    # Fill "Testing performed" section — insert content after the heading
    testing_block = f"\n{resolved_testing}"
    result = result.replace(
        "### Testing performed\n",
        f"### Testing performed\n{testing_block}\n",
    )

    # Fill "Technical considerations on the solution" — replace malformed comment and hint comment
    result = result.replace(
        "<-- List the technical analysis done for this MR",
        technical_notes,
    )
    result = result.replace(
        "<!-- List what you validated locally or in a target environment (for example: mvn test, manual API checks, Redis/GraphDB related verification). -->",
        "",
    )

    # Fill "Configuration / deployment impact" section comment
    result = result.replace(
        "<!-- Mention any impact on application properties, Redis, GraphDB/TinkerPop, external integrations, Docker image behavior, or environment setup. Write \"None\" if not applicable. -->",
        "None — no changes to application properties, Redis, GraphDB/TinkerPop, external integrations, Docker image, or environment configuration.",
    )

    # Mark author checklist items as completed [x]
    author_checklist_items = [
        "- [ ] I have self-reviewed the changes",
        "- [ ] I have validated the change locally and in a testing environment",
        "- [ ] I have added or updated tests where needed, and they pass",
        "- [ ] I have considered impact on configuration, infrastructure, and external integrations",
        "- [ ] I have considered security and sensitive data handling",
        "- [ ] I have updated documentation or setup notes when applicable",
    ]
    for item in author_checklist_items:
        result = result.replace(item, item.replace("- [ ]", "- [x]"), 1)

    return result.strip()


def _build_mr_description_from_template(
    *,
    repo: str,
    story_id: str,
    story_summary: str,
    change_type: str,
    source_branch: str,
    target_branch: str,
    testing_notes: str,
    commits: str,
    diff_stat: str,
) -> tuple[str, str | None]:
    """Build MR description by filling Default.md template sections.

    When the template exists, every section is populated including the
    reviewer checklist (left unchecked) and the author checklist (marked done).
    Falls back to a plain generated summary when no template is found.
    """
    template_path, template = _read_default_mr_template(repo)
    if template:
        description = _fill_template_sections(
            template,
            story_id=story_id,
            story_summary=story_summary,
            change_type=change_type,
            source_branch=source_branch,
            target_branch=target_branch,
            testing_notes=testing_notes,
            commits=commits,
            diff_stat=diff_stat,
        )
        return description, template_path

    # Fallback: plain structured summary when no template is present
    jira_url = _jira_url_from_story_id(story_id)
    resolved_testing = testing_notes.strip() or "Pending. Run the relevant test-mcp tools before merge."
    formatted_commits = "\n".join(f"- {line}" for line in commits.splitlines()) if commits.strip() else "- No commits detected yet."
    fallback = "\n".join([
        "### Summary",
        "",
        story_summary,
        "",
        "### Why is this change needed?",
        "",
        f"Implements **{change_type}**: {story_summary}",
        "",
        "### Related ticket / reference",
        "",
        f"[{story_id}]({jira_url})",
        "",
        "### Testing performed",
        "",
        resolved_testing,
        "",
        "### Technical considerations on the solution",
        "",
        f"- Branch: `{source_branch}` → `{target_branch}`",
        "",
        "**Commits:**",
        formatted_commits,
        "",
        "### Configuration / deployment impact",
        "",
        "None.",
        "",
        "### \U0001f6e0\ufe0f Checklist before requesting a review",
        "",
        "- [x] I have self-reviewed the changes",
        "- [x] I have validated the change locally and in a testing environment",
        "- [x] I have added or updated tests where needed, and they pass",
        "- [x] I have considered impact on configuration, infrastructure, and external integrations",
        "- [x] I have considered security and sensitive data handling",
        "- [x] I have updated documentation or setup notes when applicable",
        "",
        "### \u2705 Checklist for reviewers",
        "",
        "- [ ] The change matches the described problem and intended behavior",
        "- [ ] The code is clear, maintainable, and follows project conventions",
        "- [ ] Tests and validation are appropriate for the change",
        "- [ ] Configuration, environment, or integration impacts are addressed",
        "- [ ] No obvious security or performance concerns remain",
    ])
    return fallback, None


def prepare_merge_request(
    *,
    story_id: str,
    story_summary: str,
    change_type: str = "feature",
    target_branch: str = "main",
    working_dir: str | None = None,
    testing_notes: str = "",
) -> dict[str, Any]:
    repo = _require_git_repo(working_dir)
    branch = current_branch(repo)
    status = _git_output(["git", "status", "--short"], cwd=repo)
    diff_stat = _git_output(["git", "diff", "--stat", target_branch + "...HEAD"], cwd=repo)
    commits = _git_output(["git", "log", "--oneline", target_branch + "...HEAD"], cwd=repo)

    title = f"{story_id}: {story_summary}".strip(": ")
    description, template_path = _build_mr_description_from_template(
        repo=repo,
        story_id=story_id,
        story_summary=story_summary,
        change_type=change_type,
        source_branch=branch,
        target_branch=target_branch,
        testing_notes=testing_notes,
        commits=commits,
        diff_stat=diff_stat,
    )

    return {
        "ok": True,
        "repository": repo,
        "source_branch": branch,
        "target_branch": target_branch,
        "title": title,
        "description": description,
        "template_used": template_path,
        "local_status": status,
        "diff_stat": diff_stat,
        "commits": commits,
        "next_steps": [
            "Ask the user to approve push/create-MR before taking those actions.",
            "Call gitlab_push_branch with dry_run=false only after approval.",
            "Call gitlab_create_merge_request with this title and description after the branch is pushed.",
        ],
    }


def _lookup_user_ids(usernames: list[str] | None, *, config: dict[str, str]) -> list[int]:
    ids: list[int] = []
    for username in usernames or []:
        matches = gitlab_request("users", config=config, query={"username": username})
        if matches:
            ids.append(int(matches[0]["id"]))
    return ids


def create_merge_request(
    *,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    draft: bool = True,
    labels: list[str] | None = None,
    reviewer_usernames: list[str] | None = None,
    assignee_usernames: list[str] | None = None,
    remove_source_branch: bool = False,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    config = get_gitlab_config(gitlab_url, project_id, token)
    reviewer_ids = _lookup_user_ids(reviewer_usernames, config=config)
    assignee_ids = _lookup_user_ids(assignee_usernames, config=config)
    final_title = title if not draft or title.lower().startswith(("draft:", "wip:")) else f"Draft: {title}"
    payload = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": final_title,
        "description": description,
        "remove_source_branch": remove_source_branch,
        "labels": ",".join(labels or []),
        "reviewer_ids": reviewer_ids,
        "assignee_ids": assignee_ids,
    }
    mr = gitlab_request(
        f"projects/{_encoded_project(config['project_id'])}/merge_requests",
        config=config,
        method="POST",
        body=payload,
    )
    return {
        "ok": True,
        "merge_request": compact_merge_request(mr),
        "reviewer_usernames_requested": reviewer_usernames or [],
        "assignee_usernames_requested": assignee_usernames or [],
    }


def find_mr_for_branch(
    source_branch: str,
    *,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Return the open MR for *source_branch*, or an empty dict if none exists."""
    config = get_gitlab_config(gitlab_url, project_id, token)
    mrs = gitlab_request(
        f"projects/{_encoded_project(config['project_id'])}/merge_requests",
        config=config,
        query={"source_branch": source_branch, "state": "opened", "per_page": 5},
    )
    if not mrs:
        return {}
    # Return the most recently updated one
    mrs_sorted = sorted(mrs, key=lambda m: m.get("updated_at") or "", reverse=True)
    return mrs_sorted[0]


def update_mr_description(
    mr_iid: int,
    description: str,
    *,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Replace the description of an existing MR (PUT merge_requests/:iid).

    Used to append the automation report snippet after MR creation, and to
    refresh the report on re-runs where the MR already exists.
    """
    config = get_gitlab_config(gitlab_url, project_id, token)
    mr = gitlab_request(
        f"projects/{_encoded_project(config['project_id'])}/merge_requests/{mr_iid}",
        config=config,
        method="PUT",
        body={"description": description},
    )
    return {"ok": True, "merge_request": compact_merge_request(mr)}


def append_report_to_mr(
    mr_iid: int,
    report_snippet: str,
    *,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Append (or replace) the automation report section in an MR description.

    The report block is delimited by ``<!-- devflow-report -->`` markers so it
    can be safely updated on re-runs without duplicating content or touching
    the rest of the description.
    """
    config = get_gitlab_config(gitlab_url, project_id, token)
    # Fetch current description
    mr = gitlab_request(
        f"projects/{_encoded_project(config['project_id'])}/merge_requests/{mr_iid}",
        config=config,
    )
    current_desc: str = mr.get("description") or ""

    # Strip any previous report block
    _MARKER_START = "<!-- devflow-report -->"
    _MARKER_END   = "<!-- /devflow-report -->"
    if _MARKER_START in current_desc:
        before = current_desc[: current_desc.index(_MARKER_START)].rstrip()
    else:
        before = current_desc.rstrip()

    new_desc = (
        f"{before}\n\n"
        f"{_MARKER_START}\n"
        f"{report_snippet}\n"
        f"{_MARKER_END}"
    )
    return update_mr_description(
        mr_iid,
        new_desc,
        gitlab_url=gitlab_url,
        project_id=project_id,
        token=token,
    )


# ── New helpers: git status, branch listing, MR list, safe MR creation ────────

MR_ALLOWED_PREFIXES = (
    "feature/", "feat/", "fix/", "bugfix/", "hotfix/",
    "chore/", "refactor/", "test/", "perf/", "security/", "docs/",
)
MR_BLOCKED_PREFIXES = ("review/", "temp/", "tmp/", "wip/", "local/")


def git_status(working_dir: str | None = None) -> dict[str, Any]:
    """Return git status: current branch, local changes, last commit, remote tracking."""
    repo = _require_git_repo(working_dir)

    branch_r = run_git(["git", "branch", "--show-current"], cwd=repo)
    branch = branch_r.stdout.strip() if branch_r.returncode == 0 else ""

    status_r = run_git(["git", "status", "--short"], cwd=repo)
    status_lines = [l for l in (status_r.stdout or "").splitlines() if l.strip()]

    log_r = run_git(["git", "log", "--oneline", "-5"], cwd=repo)
    recent_commits = [l for l in (log_r.stdout or "").splitlines() if l.strip()]

    remote_r = run_git(["git", "remote", "-v"], cwd=repo)
    remotes = list({l.split()[1] for l in (remote_r.stdout or "").splitlines() if len(l.split()) >= 2})

    tracking_r = run_git(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=repo)
    tracking = tracking_r.stdout.strip() if tracking_r.returncode == 0 else ""

    ahead_behind: dict[str, Any] = {}
    if tracking:
        ab_r = run_git(["git", "rev-list", "--left-right", "--count", f"{tracking}...HEAD"], cwd=repo)
        if ab_r.returncode == 0:
            parts = ab_r.stdout.strip().split()
            if len(parts) == 2:
                ahead_behind = {"behind": int(parts[0]), "ahead": int(parts[1])}

    return {
        "ok": True,
        "repository": repo,
        "current_branch": branch,
        "has_local_changes": bool(status_lines),
        "local_changes": status_lines,
        "local_changes_count": len(status_lines),
        "tracking_branch": tracking,
        "ahead_behind": ahead_behind,
        "recent_commits": recent_commits,
        "remotes": remotes,
        "rule": (
            "Fix local changes (commit or stash) before creating a branch or MR. "
            "Never push to a base branch (main/master/develop)."
        ),
    }


def list_local_branches(working_dir: str | None = None) -> dict[str, Any]:
    """List local branches with remote tracking info and last-commit date."""
    repo = _require_git_repo(working_dir)

    r = run_git(
        ["git", "branch", "-vv", "--sort=-committerdate"],
        cwd=repo,
    )
    branches: list[dict[str, Any]] = []
    for line in (r.stdout or "").splitlines():
        if not line.strip():
            continue
        current = line.startswith("*")
        parts = line.lstrip("* ").split()
        if not parts:
            continue
        name = parts[0]
        tracking = ""
        tracking_match = re.search(r"\[([^\]]+)\]", line)
        if tracking_match:
            tracking = tracking_match.group(1).split(":")[0]
        branches.append({
            "name": name,
            "current": current,
            "tracking": tracking,
            "short_hash": parts[1] if len(parts) > 1 else "",
        })

    remote_r = run_git(["git", "branch", "-r", "--sort=-committerdate"], cwd=repo)
    remote_branches = [
        l.strip() for l in (remote_r.stdout or "").splitlines()
        if l.strip() and "HEAD" not in l
    ]

    return {
        "ok": True,
        "repository": repo,
        "local_branches": branches,
        "local_count": len(branches),
        "remote_branches": remote_branches[:20],
        "remote_count": len(remote_branches),
    }


def list_merge_requests(
    state: str = "opened",
    *,
    working_dir: str | None = None,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """List GitLab MRs for the project (state: opened / merged / closed / all)."""
    try:
        config = get_gitlab_config(gitlab_url, project_id, token)
    except GitLabConfigError as exc:
        return {"ok": False, "error": str(exc), "mrs": []}

    try:
        mrs_raw = gitlab_request(
            f"projects/{_encoded_project(config['project_id'])}/merge_requests",
            config=config,
            query={"state": state, "per_page": "30", "order_by": "updated_at", "sort": "desc"},
        )
    except GitLabRequestError as exc:
        return {"ok": False, "error": str(exc), "mrs": []}

    mrs = []
    for mr in (mrs_raw if isinstance(mrs_raw, list) else []):
        mrs.append({
            "iid": mr.get("iid"),
            "title": mr.get("title"),
            "state": mr.get("state"),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "author": (mr.get("author") or {}).get("username"),
            "assignees": [(a.get("username") or "") for a in (mr.get("assignees") or [])],
            "created_at": mr.get("created_at"),
            "updated_at": mr.get("updated_at"),
            "web_url": mr.get("web_url"),
            "draft": mr.get("draft") or mr.get("work_in_progress") or False,
            "pipeline_status": (mr.get("head_pipeline") or {}).get("status"),
        })

    return {
        "ok": True,
        "state_filter": state,
        "count": len(mrs),
        "merge_requests": mrs,
    }


def validate_mr_readiness(
    *,
    story_key: str,
    branch: str,
    target_branch: str,
    template_used: bool,
    has_local_changes: bool,
    title: str,
    description: str,
) -> list[str]:
    """Return a list of readiness problems. Empty list means ready to create."""
    problems: list[str] = []
    lowered = branch.lower()
    story_key_lower = story_key.lower() if story_key else ""

    if not branch:
        problems.append("Current Git branch could not be resolved. Make sure you are in a git repo.")
    if branch in {target_branch, "main", "master", "develop", ""}:
        problems.append(
            f"Branch '{branch}' is a base branch — create a story branch first (e.g. feature/{story_key_lower}-...)."
        )
    if lowered.startswith(MR_BLOCKED_PREFIXES):
        problems.append(
            f"Branch '{branch}' uses a blocked prefix (wip/temp/review/local). "
            "Use feature/, fix/, chore/, hotfix/, or similar."
        )
    if branch and not lowered.startswith(MR_ALLOWED_PREFIXES):
        problems.append(
            f"Branch '{branch}' does not use an approved prefix: "
            + ", ".join(MR_ALLOWED_PREFIXES) + "."
        )
    if story_key_lower and branch and story_key_lower not in lowered:
        problems.append(
            f"Branch '{branch}' does not contain the story key '{story_key}'. "
            "Branch name must include the story key for traceability."
        )
    if not template_used:
        problems.append(
            "The project Default.md MR template was not found or used. "
            "MR description must be built from .gitlab/merge_request_templates/Default.md."
        )
    if has_local_changes:
        problems.append(
            "Working tree has uncommitted local changes. Commit or stash them before creating the MR."
        )
    if story_key and story_key not in (title or ""):
        problems.append(
            f"MR title does not contain story key '{story_key}'. "
            "Title must reference the Jira story key for traceability."
        )
    if len((description or "").strip()) < 200:
        problems.append(
            "MR description is too short (< 200 chars). "
            "Use the Default.md template with Summary, Why, Changes Made, Testing, and Risk sections."
        )
    return problems


def safe_create_merge_request(
    *,
    story_key: str,
    story_summary: str,
    source_branch: str | None = None,
    target_branch: str = "main",
    change_type: str = "feature",
    testing_notes: str = "",
    draft: bool = False,
    labels: list[str] | None = None,
    reviewer_usernames: list[str] | None = None,
    assignee_usernames: list[str] | None = None,
    remove_source_branch: bool = False,
    tests_done: bool = False,
    review_done: bool = False,
    confirm_create_mr: bool = False,
    mr_title: str | None = None,
    mr_description: str | None = None,
    gitlab_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
    working_dir: str | None = None,
) -> dict[str, Any]:
    """Create a GitLab MR with full safety guardrails.

    Guards (ALL must pass):
    1. tests_done=True        — tests were run and passed
    2. review_done=True       — code was reviewed against ACs
    3. Branch prefix valid    — must use feature/fix/chore/etc.
    4. Branch contains story key
    5. No uncommitted changes
    6. MR template was used
    7. confirm_create_mr=True — user explicitly approved
    """
    # ── Guard 1 & 2: Tests + review flags ────────────────────────────────────
    missing_gates: list[str] = []
    if not tests_done:
        missing_gates.append("tests_done=True (run and verify tests before creating MR)")
    if not review_done:
        missing_gates.append("review_done=True (review code against acceptance criteria)")

    if missing_gates:
        return {
            "ok": False,
            "mode": "gates_not_complete",
            "message": "BLOCKED: MR creation requires tests and review to be complete.",
            "missing_gates": missing_gates,
            "instruction": (
                "Complete all required gates, then call again with tests_done=True and review_done=True."
            ),
        }

    # ── Resolve branch + repo state ───────────────────────────────────────────
    status = git_status(working_dir)
    if not status.get("ok"):
        return {
            "ok": False,
            "mode": "git_error",
            "message": f"Could not read git state: {status.get('error', 'unknown error')}",
        }

    resolved_branch = source_branch or status.get("current_branch") or ""
    has_local_changes = status.get("has_local_changes", False)

    # ── Prepare MR title + description ───────────────────────────────────────
    if mr_title and mr_description:
        title = mr_title
        description = mr_description
        template_used = True
    else:
        prepared = prepare_merge_request(
            story_id=story_key,
            story_summary=story_summary,
            change_type=change_type,
            target_branch=target_branch,
            working_dir=working_dir,
            testing_notes=testing_notes or "Tests and review completed before MR creation.",
        )
        title = mr_title or prepared.get("title") or ""
        description = mr_description or prepared.get("description") or ""
        template_used = bool(prepared.get("template_used"))

    # ── Guard 3–7: Readiness checks ───────────────────────────────────────────
    problems = validate_mr_readiness(
        story_key=story_key,
        branch=resolved_branch,
        target_branch=target_branch,
        template_used=template_used,
        has_local_changes=has_local_changes,
        title=title,
        description=description,
    )

    if problems:
        return {
            "ok": False,
            "mode": "readiness_blocked",
            "message": "MR creation blocked by safety checks.",
            "readiness_problems": problems,
            "current_branch": resolved_branch,
            "instruction": "Fix all readiness problems, then retry.",
        }

    # ── Guard 8: Explicit user confirmation ──────────────────────────────────
    if not confirm_create_mr:
        return {
            "ok": False,
            "mode": "confirmation_required",
            "message": (
                "Review the MR preview below. Call again with confirm_create_mr=True "
                "only after the user explicitly approves."
            ),
            "mr_preview": {
                "title": title,
                "description_preview": description[:800] + ("..." if len(description) > 800 else ""),
                "source_branch": resolved_branch,
                "target_branch": target_branch,
                "draft": draft,
                "tests_done": tests_done,
                "review_done": review_done,
            },
        }

    # ── Push branch then create MR ────────────────────────────────────────────
    push_result = push_branch(
        branch=resolved_branch,
        remote="origin",
        set_upstream=True,
        working_dir=working_dir,
        dry_run=False,
    )
    if not push_result.get("ok"):
        return {
            "ok": False,
            "mode": "push_failed",
            "message": "Branch push failed — MR was not created.",
            "push_result": push_result,
        }

    mr_result = create_merge_request(
        source_branch=resolved_branch,
        target_branch=target_branch,
        title=title,
        description=description,
        draft=draft,
        labels=labels,
        reviewer_usernames=reviewer_usernames,
        assignee_usernames=assignee_usernames,
        remove_source_branch=remove_source_branch,
        gitlab_url=gitlab_url,
        project_id=project_id,
        token=token,
    )

    return {
        "ok": bool(mr_result.get("ok")),
        "mode": "created" if mr_result.get("ok") else "create_failed",
        "story_key": story_key,
        "source_branch": resolved_branch,
        "target_branch": target_branch,
        "push_result": push_result,
        "merge_request": mr_result,
        "policy": [
            "Branch was validated (prefix, story key, no dirty tree) before push.",
            "MR template was used for title and description.",
            "Tests and review were confirmed complete before creation.",
            "User explicitly approved via confirm_create_mr=True.",
        ],
    }

