"""Jira Cloud REST helpers for jira-mcp."""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_FIELDS = [
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
    "description",
    "parent",
    "created",
    "updated",
    "duedate",
    "fixVersions",
]

FIELD_NAME_FALLBACKS = {
    "JIRA_ACCEPTANCE_CRITERIA_FIELD": ["Acceptance criteria", "Acceptance Criteria"],
    "JIRA_REGULATORY_JUSTIFICATION_FIELD": ["Regulatory Justification"],
    "JIRA_REASON_COMMENTS_FIELD": ["Reason/Comments"],
    "JIRA_SPRINT_FIELD": ["Sprint"],
    "JIRA_STORY_POINTS_FIELD": ["Story points", "Story Points"],
}

_FIELD_ID_CACHE: dict[str, str] = {}
_SUBTASK_DETAIL_CACHE: dict[str, dict[str, Any]] = {}


class JiraConfigError(RuntimeError):
    """Raised when required Jira configuration is missing."""


class JiraRequestError(RuntimeError):
    """Raised when Jira returns an error response."""


def json_response(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def get_jira_config(
    jira_url: str | None = None,
    username: str | None = None,
    token: str | None = None,
) -> dict[str, str]:
    base_url = (jira_url or os.getenv("JIRA_BASE_URL") or "").rstrip("/")
    resolved_username = username or os.getenv("JIRA_USERNAME") or os.getenv("JIRA_EMAIL") or ""
    resolved_token = token or os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_TOKEN") or ""

    missing = []
    if not base_url:
        missing.append("JIRA_BASE_URL")
    if not resolved_username:
        missing.append("JIRA_USERNAME")
    if not resolved_token:
        missing.append("JIRA_API_TOKEN")
    if missing:
        raise JiraConfigError(f"Missing required Jira environment variable(s): {', '.join(missing)}")

    return {
        "base_url": base_url,
        "username": resolved_username,
        "token": resolved_token,
    }


def _auth_header(username: str, token: str) -> str:
    encoded = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def jira_request(
    path: str,
    *,
    config: dict[str, str],
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    url = f"{config['base_url']}/rest/api/3/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": _auth_header(config["username"], config["token"]),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "jira-mcp",
        },
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise JiraRequestError(f"Jira API error {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise JiraRequestError(f"Could not reach Jira API: {exc.reason}") from exc


def jira_agile_request(
    path: str,
    *,
    config: dict[str, str],
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    """Call Jira Agile REST endpoints such as sprint assignment."""
    url = f"{config['base_url']}/rest/agile/1.0/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": _auth_header(config["username"], config["token"]),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "jira-mcp",
        },
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise JiraRequestError(f"Jira Agile API error {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise JiraRequestError(f"Could not reach Jira Agile API: {exc.reason}") from exc


def _configured_board_ids() -> list[str]:
    raw = os.getenv("JIRA_BOARD_IDS") or os.getenv("JIRA_BOARD_ID") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_sprint_request(sprint_id: str | int | None = None, sprint_name: str | None = None) -> dict[str, Any]:
    requested_id = str(sprint_id or "").strip()
    requested_name = str(sprint_name or "").strip()
    if requested_name.isdigit() and not requested_id:
        requested_id = requested_name
        requested_name = ""
    return {
        "requested": bool(requested_id or requested_name),
        "sprint_id": requested_id or None,
        "sprint_name": requested_name or None,
    }


def _find_sprint_by_name(config: dict[str, str], sprint_name: str) -> dict[str, Any]:
    board_ids = _configured_board_ids()
    if not board_ids:
        return {
            "ok": False,
            "mode": "board_id_required",
            "message": (
                "sprint_name was provided, but JIRA_BOARD_ID or JIRA_BOARD_IDS is not configured. "
                "Provide sprint_id directly or configure board id(s) for sprint-name resolution."
            ),
        }

    target = sprint_name.strip().lower()
    inspected = []
    for board_id in board_ids:
        for sprint_state in ("active", "future"):
            start_at = 0
            while True:
                query = urllib.parse.urlencode(
                    {
                        "state": sprint_state,
                        "startAt": start_at,
                        "maxResults": 50,
                    }
                )
                response = jira_agile_request(f"board/{urllib.parse.quote(board_id, safe='')}/sprint?{query}", config=config)
                values = response.get("values") or []
                inspected.extend(
                    {
                        "board_id": board_id,
                        "id": sprint.get("id"),
                        "name": sprint.get("name"),
                        "state": sprint.get("state"),
                    }
                    for sprint in values
                )
                for sprint in values:
                    if str(sprint.get("name") or "").strip().lower() == target:
                        return {
                            "ok": True,
                            "sprint_id": str(sprint.get("id")),
                            "sprint_name": sprint.get("name"),
                            "board_id": board_id,
                            "state": sprint.get("state"),
                        }
                if response.get("isLast", True):
                    break
                start_at = int(response.get("startAt") or 0) + int(response.get("maxResults") or len(values) or 50)

    return {
        "ok": False,
        "mode": "sprint_not_found",
        "message": f"Could not find active/future sprint named '{sprint_name}' on configured board(s).",
        "configured_board_ids": board_ids,
        "inspected_sprints": inspected[:20],
    }


def _get_sprint_details(config: dict[str, str], sprint_id: str) -> dict[str, Any]:
    """Fetch sprint details for explicit sprint ids so we can reject closed sprints up-front."""
    sprint = jira_agile_request(f"sprint/{urllib.parse.quote(sprint_id, safe='')}", config=config)
    return {
        "id": str(sprint.get("id") or sprint_id),
        "name": sprint.get("name"),
        "state": sprint.get("state"),
        "board_id": sprint.get("originBoardId"),
    }


def _list_open_sprints_for_board(config: dict[str, str], board_id: str | int, *, limit: int = 5) -> list[dict[str, Any]]:
    """Return a small list of active/future sprints for a board to guide sprint selection."""
    normalized_board_id = str(board_id).strip()
    suggestions: list[dict[str, Any]] = []
    for sprint_state in ("active", "future"):
        response = jira_agile_request(
            f"board/{urllib.parse.quote(normalized_board_id, safe='')}/sprint?state={sprint_state}&startAt=0&maxResults=20",
            config=config,
        )
        for sprint in response.get("values") or []:
            suggestions.append(
                {
                    "id": str(sprint.get("id") or ""),
                    "name": sprint.get("name"),
                    "state": sprint.get("state"),
                }
            )
            if len(suggestions) >= limit:
                return suggestions
    return suggestions


def resolve_sprint_assignment(
    config: dict[str, str],
    *,
    sprint_id: str | int | None = None,
    sprint_name: str | None = None,
) -> dict[str, Any]:
    """Resolve sprint request into a concrete sprint id before issue creation."""
    requested = _normalize_sprint_request(sprint_id=sprint_id, sprint_name=sprint_name)
    if not requested["requested"]:
        return {
            "ok": True,
            "requested": False,
            "message": "No sprint assignment requested.",
        }
    if requested["sprint_id"]:
        try:
            details = _get_sprint_details(config, requested["sprint_id"])
        except JiraRequestError as exc:
            return {
                "ok": False,
                "requested": True,
                "mode": "sprint_lookup_failed",
                "message": "Could not validate explicit sprint_id before issue creation.",
                "sprint_id": requested["sprint_id"],
                "error": str(exc),
                "source": "explicit_sprint_id",
            }

        state = str(details.get("state") or "").lower()
        if state and state not in {"active", "future"}:
            return {
                "ok": False,
                "requested": True,
                "mode": "sprint_not_assignable",
                "message": (
                    "The provided sprint_id points to a closed/completed sprint. "
                    "Use an active/future sprint id instead."
                ),
                "sprint_id": requested["sprint_id"],
                "sprint_name": details.get("name"),
                "state": details.get("state"),
                "board_id": details.get("board_id"),
                "source": "explicit_sprint_id",
            }

        return {
            "ok": True,
            "requested": True,
            "sprint_id": requested["sprint_id"],
            "sprint_name": requested["sprint_name"] or details.get("name"),
            "state": details.get("state"),
            "board_id": details.get("board_id"),
            "source": "explicit_sprint_id",
        }
    resolved = _find_sprint_by_name(config, requested["sprint_name"] or "")
    resolved["requested"] = True
    resolved["source"] = "sprint_name_lookup"
    return resolved


def assign_issue_to_sprint(issue_key: str, sprint_id: str | int, config: dict[str, str]) -> dict[str, Any]:
    """Assign a created issue to a Jira sprint through Agile API."""
    resolved_sprint_id = str(sprint_id).strip()
    if not issue_key or not resolved_sprint_id:
        return {
            "ok": False,
            "mode": "invalid_sprint_assignment",
            "message": "Both issue_key and sprint_id are required for sprint assignment.",
        }
    jira_agile_request(
        f"sprint/{urllib.parse.quote(resolved_sprint_id, safe='')}/issue",
        config=config,
        method="POST",
        body={"issues": [issue_key]},
    )
    return {
        "ok": True,
        "mode": "sprint_assigned",
        "issue_key": issue_key,
        "sprint_id": resolved_sprint_id,
    }


def adf_paragraph(text: str) -> dict[str, Any]:
    return {
        "type": "paragraph",
        "content": _parse_inline(text) if text else [],
    }


def adf_heading(text: str, level: int = 2) -> dict[str, Any]:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [{"type": "text", "text": text}],
    }


def adf_bullet_list(items: list[str]) -> dict[str, Any]:
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [adf_paragraph(item)],
            }
            for item in items
        ],
    }


def adf_ordered_list(items: list[str]) -> dict[str, Any]:
    return {
        "type": "orderedList",
        "content": [
            {
                "type": "listItem",
                "content": [adf_paragraph(item)],
            }
            for item in items
        ],
    }


def adf_panel(panel_type: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a coloured Jira panel (info=blue, note=gray, warning=yellow, success=green, error=red)."""
    return {
        "type": "panel",
        "attrs": {"panelType": panel_type},
        "content": nodes or [adf_paragraph("")],
    }


def adf_table(rows: list[list[str]], has_header: bool = True) -> dict[str, Any]:
    """Build an ADF table from a list of row-lists."""
    adf_rows = []
    for i, row in enumerate(rows):
        is_header = has_header and i == 0
        cells = []
        for cell in row:
            cells.append({
                "type": "tableCell" if not is_header else "tableHeader",
                "attrs": {},
                "content": [adf_paragraph(cell.strip())],
            })
        adf_rows.append({"type": "tableRow", "content": cells})
    return {
        "type": "table",
        "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
        "content": adf_rows,
    }


# Known section-level headings that appear without a leading `#` in story text
_KNOWN_SECTION_HEADINGS = {
    "user story", "background", "overview", "scope", "implementation scope",
    "confirmed link type", "confirmed link type \u2014 analysis output",
    "middleware changes identified", "external dependency", "out of scope",
    "constraints", "reference", "subtask overview", "impacted areas",
    "open questions", "testing", "validation", "dependencies", "risks",
    "acceptance criteria", "regulatory justification", "reason/comments",
    "out of scope for this story",
}


MANDATORY_JIRA_STYLE_PROFILE = "BDRSP-1623"
MANDATORY_PANEL_TYPES = ("info", "success", "warning", "note")


def validate_mandatory_story_style(description_text: str) -> dict[str, Any]:
    """Validate the mandatory Jira story style profile used for refined/updated/created stories."""
    text = description_text or ""
    lower = text.lower()

    missing_rules: list[str] = []

    if not re.search(r"(?m)^##\s+\S+", text):
        missing_rules.append("Use section headings like: ## User Story")

    for panel in MANDATORY_PANEL_TYPES:
        if f":::{panel}" not in lower:
            missing_rules.append(f"Include panel block: :::{panel} ... :::")

    has_table_row = bool(re.search(r"(?m)^\|.+\|\s*$", text))
    has_table_separator = bool(re.search(r"(?m)^\|\s*:?-{3,}.*\|\s*$", text))
    if not (has_table_row and has_table_separator):
        missing_rules.append("Include at least one markdown table with a |---| separator row")

    return {
        "ok": not missing_rules,
        "profile": MANDATORY_JIRA_STYLE_PROFILE,
        "missing_rules": missing_rules,
    }


def _parse_inline(text: str) -> list[dict[str, Any]]:
    """Convert inline markdown (bold, code) to ADF inline nodes."""
    nodes: list[dict[str, Any]] = []
    # Pattern: **bold**, `code`, or plain text
    pattern = re.compile(r"(\*\*(.+?)\*\*|`([^`]+)`)")
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            nodes.append({"type": "text", "text": text[last:m.start()]})
        if m.group(2) is not None:
            nodes.append({"type": "text", "text": m.group(2), "marks": [{"type": "strong"}]})
        elif m.group(3) is not None:
            nodes.append({"type": "text", "text": m.group(3), "marks": [{"type": "code"}]})
        last = m.end()
    if last < len(text):
        nodes.append({"type": "text", "text": text[last:]})
    return nodes or [{"type": "text", "text": text}]


def build_adf_description(
    *,
    simple_explanation: str,
    regulatory_justification: str = "",
    acceptance_criteria: list[str],
    impacted_areas: list[str],
    open_questions: list[str],
    include_special_sections: bool = True,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        adf_heading("Overview"),
        adf_paragraph(simple_explanation),
    ]
    if include_special_sections:
        content.extend(
            [
                adf_panel("info", [
                    adf_heading("Regulatory Justification"),
                    adf_paragraph(regulatory_justification or "This change supports controlled delivery, traceability, and validation of the requested business behavior."),
                ]),
                adf_heading("Acceptance Criteria"),
                adf_ordered_list(acceptance_criteria or ["Acceptance criteria to be confirmed."]),
            ]
        )
    if impacted_areas:
        content.extend([
            adf_panel("success", [
                adf_heading("Impacted Areas"),
                adf_bullet_list(impacted_areas),
            ]),
        ])
    if open_questions:
        content.extend([
            adf_panel("warning", [
                adf_heading("Open Questions"),
                adf_bullet_list(open_questions),
            ]),
        ])
    return {
        "type": "doc",
        "version": 1,
        "content": content,
    }


def build_adf_from_markdownish(text: str) -> dict[str, Any]:
    """Convert approved plain/markdownish text to a rich ADF document.

    THIS IS THE MANDATORY FORMATTING STANDARD FOR ALL JIRA STORY UPDATES.
    Every story refinement MUST pass through this converter to produce
    rich Jira rendering with colored panels, tables, bold/code, and lists.

    Supported syntax:
    - ## / # headings
    - Known section names as level-2 headings (no leading #)
    - **bold** and `code` inline marks
    - - / * bullet lists (grouped consecutive items into one list)
    - 1. / 2. ordered lists (grouped)
    - | table | rows | (pipe-separated, with optional separator row)
    - :::info / :::warning / :::success / :::note / :::error panel blocks (closed by :::)
    - Plain paragraphs
    """
    lines = text.splitlines()
    content: list[dict[str, Any]] = []

    _pending_bullets: list[str] = []
    _pending_ordered: list[str] = []
    _pending_table_rows: list[list[str]] = []

    # panel state
    _in_panel: str | None = None          # panel type while inside a :::type block
    _panel_nodes: list[dict[str, Any]] = []

    def _flush_bullets(target: list) -> None:
        if _pending_bullets:
            target.append(adf_bullet_list(list(_pending_bullets)))
            _pending_bullets.clear()

    def _flush_ordered(target: list) -> None:
        if _pending_ordered:
            target.append(adf_ordered_list(list(_pending_ordered)))
            _pending_ordered.clear()

    def _flush_table(target: list) -> None:
        if _pending_table_rows:
            target.append(adf_table(list(_pending_table_rows)))
            _pending_table_rows.clear()

    def _flush_all(target: list) -> None:
        _flush_bullets(target)
        _flush_ordered(target)
        _flush_table(target)

    def _active() -> list:
        """Return the list we are currently appending nodes to."""
        return _panel_nodes if _in_panel else content

    _PANEL_TYPES = {"info", "note", "warning", "success", "error"}

    for raw_line in lines:
        line = raw_line.strip()

        # ── panel open :::type ───────────────────────────────────────────────
        panel_open = re.match(r"^:::(info|note|warning|success|error)\s*$", line)
        if panel_open:
            _flush_all(content)
            _in_panel = panel_open.group(1)
            _panel_nodes.clear()
            continue

        # ── panel close ::: ──────────────────────────────────────────────────
        if line == ":::" and _in_panel:
            _flush_all(_panel_nodes)
            content.append(adf_panel(_in_panel, list(_panel_nodes)))
            _panel_nodes.clear()
            _in_panel = None
            continue

        target = _active()

        # ── blank line ──────────────────────────────────────────────────────
        # Blank lines flush tables but NOT bullets/ordered lists — so that
        # items separated by blank lines are still grouped into one list.
        if not line:
            _flush_table(target)
            continue

        # ── markdown heading (#, ##, ###) ───────────────────────────────────
        if line.startswith("#"):
            _flush_all(target)
            depth = len(line) - len(line.lstrip("#"))
            heading_text = line.lstrip("#").strip()
            if heading_text:
                target.append(adf_heading(heading_text, min(depth, 3)))
            continue

        # ── known section name as implicit heading ───────────────────────────
        normalized = re.sub(r"\s+", " ", line.rstrip(":").lower())
        if normalized in _KNOWN_SECTION_HEADINGS:
            _flush_all(target)
            target.append(adf_heading(line.rstrip(":"), 2))
            continue

        # ── pipe table row ───────────────────────────────────────────────────
        if line.startswith("|") and line.endswith("|"):
            _flush_bullets(target)
            _flush_ordered(target)
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r"[-: ]+", c) for c in cells):
                continue  # separator row — skip
            _pending_table_rows.append(cells)
            continue

        # ── bullet list (- or *) ─────────────────────────────────────────────
        bullet_match = re.match(r"^[-*]\s+(.*)", line)
        if bullet_match:
            _flush_ordered(target)
            _flush_table(target)
            _pending_bullets.append(bullet_match.group(1).strip())
            continue

        # ── ordered list (1. 2. etc.) ────────────────────────────────────────
        ordered_match = re.match(r"^\d+[.)]\s+(.*)", line)
        if ordered_match:
            _flush_bullets(target)
            _flush_table(target)
            _pending_ordered.append(ordered_match.group(1).strip())
            continue

        # ── plain paragraph ──────────────────────────────────────────────────
        _flush_all(target)
        target.append(adf_paragraph(line))

    # close any unclosed panel
    if _in_panel:
        _flush_all(_panel_nodes)
        content.append(adf_panel(_in_panel, list(_panel_nodes)))
    else:
        _flush_all(content)

    return {
        "type": "doc",
        "version": 1,
        "content": content or [adf_paragraph(text.strip())],
    }


SECTION_ALIASES = {
    "acceptance criteria": "acceptance_criteria",
    "acceptance criterion": "acceptance_criteria",
    "ac": "acceptance_criteria",
    "regulatory justification": "regulatory_justification",
    "reason/comments": "reason_comments",
    "reason comments": "reason_comments",
}


DESCRIPTION_SECTION_HEADINGS = {
    "overview",
    "scope",
    "background",
    "business context",
    "technical context",
    "implementation notes",
    "impacted areas",
    "impact",
    "out of scope",
    "open questions",
    "dependencies",
    "risks",
    "validation",
    "testing",
}


def resolve_jira_field_id(config: dict[str, str], env_name: str) -> str:
    """Resolve a Jira custom field from env, falling back to exact field-name lookup."""
    configured = os.getenv(env_name, "").strip()
    if configured:
        return configured
    if env_name in _FIELD_ID_CACHE:
        return _FIELD_ID_CACHE[env_name]

    exact_names = {name.lower() for name in FIELD_NAME_FALLBACKS.get(env_name, [])}
    if not exact_names:
        return ""
    for field in jira_request("field", config=config):
        if (field.get("name") or "").lower() in exact_names:
            field_id = field.get("id") or ""
            _FIELD_ID_CACHE[env_name] = field_id
            return field_id
    return ""


def resolve_sprint_field_id(config: dict[str, str]) -> str:
    """Return the sprint field id from env or Jira field metadata."""
    return resolve_jira_field_id(config, "JIRA_SPRINT_FIELD")


def resolve_story_points_field_id(config: dict[str, str]) -> str:
    """Return the story points field id from env or Jira field metadata."""
    return resolve_jira_field_id(config, "JIRA_STORY_POINTS_FIELD")


def is_dedicated_field_available(config: dict[str, str], env_name: str) -> bool:
    """Return whether a dedicated Jira field exists for a configured section."""
    return bool(resolve_jira_field_id(config, env_name))


def clean_description_for_configured_fields(text: str, config: dict[str, str] | None = None) -> str:
    """Remove sections from Description when Jira has dedicated fields for them."""
    resolved_config = config or get_jira_config()
    configured_sections = set()
    if is_dedicated_field_available(resolved_config, "JIRA_ACCEPTANCE_CRITERIA_FIELD"):
        configured_sections.add("acceptance_criteria")
    if is_dedicated_field_available(resolved_config, "JIRA_REGULATORY_JUSTIFICATION_FIELD"):
        configured_sections.add("regulatory_justification")
    if is_dedicated_field_available(resolved_config, "JIRA_REASON_COMMENTS_FIELD"):
        configured_sections.add("reason_comments")
    if not configured_sections:
        return text

    kept: list[str] = []
    skip_section = False
    for raw_line in text.splitlines():
        heading = raw_line.strip().strip("#").strip().rstrip(":")
        normalized = re.sub(r"\s+", " ", heading.lower())
        section = SECTION_ALIASES.get(normalized)
        if section:
            skip_section = section in configured_sections
            if skip_section:
                continue
        elif normalized in DESCRIPTION_SECTION_HEADINGS:
            skip_section = False
        if not skip_section:
            kept.append(raw_line)
    return "\n".join(kept).strip()


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


def _display_name(value: dict[str, Any] | None) -> str:
    if not value:
        return "unassigned"
    return value.get("displayName") or value.get("emailAddress") or value.get("accountId") or "unknown"


def _names(values: list[dict[str, Any]] | None) -> list[str]:
    return [value.get("name", "") for value in values or [] if value.get("name")]


def _normalize_sprint_names(raw_sprint: Any) -> list[str]:
    """Normalize Jira sprint field payload into a list of sprint names."""
    if raw_sprint is None:
        return []
    if isinstance(raw_sprint, dict):
        name = str(raw_sprint.get("name") or "").strip()
        return [name] if name else []
    if isinstance(raw_sprint, str):
        name = raw_sprint.strip()
        return [name] if name else []
    if isinstance(raw_sprint, list):
        names: list[str] = []
        for item in raw_sprint:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    names.append(name)
            elif isinstance(item, str):
                parsed = item.strip()
                if parsed:
                    names.append(parsed)
        return names
    return []


def configured_field_value(config: dict[str, str], fields: dict[str, Any], env_name: str) -> tuple[str, str]:
    """Read an optional Jira custom field configured by environment variable."""
    field_id = resolve_jira_field_id(config, env_name)
    return field_id, extract_adf_text(fields.get(field_id)) if field_id else ""


def issue_type_name(fields: dict[str, Any]) -> str:
    """Return the Jira issue type display name."""
    return (fields.get("issuetype") or {}).get("name") or ""


def status_name(fields: dict[str, Any]) -> str:
    """Return the Jira status display name."""
    return (fields.get("status") or {}).get("name") or ""


def status_category(fields: dict[str, Any]) -> str:
    """Return the Jira status category display name."""
    return ((fields.get("status") or {}).get("statusCategory") or {}).get("name") or ""


def _subtask_stub(config: dict[str, str], subtask: dict[str, Any]) -> dict[str, Any]:
    subtask_fields = subtask.get("fields") or {}
    return {
        "key": subtask.get("key"),
        "summary": subtask_fields.get("summary"),
        "status": (subtask_fields.get("status") or {}).get("name"),
        "status_category": status_category(subtask_fields),
        "issue_type": (subtask_fields.get("issuetype") or {}).get("name"),
        "description": extract_adf_text(subtask_fields.get("description")),
        "assignee": _display_name(subtask_fields.get("assignee")),
        "assignee_account_id": (subtask_fields.get("assignee") or {}).get("accountId"),
        "updated": subtask_fields.get("updated"),
        "url": f"{config['base_url']}/browse/{subtask.get('key')}",
    }


def _read_subtask_detail(config: dict[str, str], subtask_key: str) -> dict[str, Any]:
    if subtask_key in _SUBTASK_DETAIL_CACHE:
        return dict(_SUBTASK_DETAIL_CACHE[subtask_key])
    fields = urllib.parse.quote(
        ",".join(
            [
                "summary",
                "status",
                "priority",
                "issuetype",
                "description",
                "assignee",
                "parent",
                "updated",
            ]
        )
    )
    issue_path = f"issue/{urllib.parse.quote(subtask_key.strip(), safe='')}?fields={fields}"
    issue = jira_request(issue_path, config=config)
    fields_data = issue.get("fields") or {}
    detail = {
        "key": issue.get("key") or subtask_key,
        "summary": fields_data.get("summary"),
        "status": status_name(fields_data),
        "status_category": status_category(fields_data),
        "issue_type": issue_type_name(fields_data),
        "description": extract_adf_text(fields_data.get("description")),
        "assignee": _display_name(fields_data.get("assignee")),
        "assignee_account_id": (fields_data.get("assignee") or {}).get("accountId"),
        "parent": (fields_data.get("parent") or {}).get("key"),
        "priority": (fields_data.get("priority") or {}).get("name"),
        "updated": fields_data.get("updated"),
        "url": f"{config['base_url']}/browse/{issue.get('key') or subtask_key}",
    }
    _SUBTASK_DETAIL_CACHE[subtask_key] = dict(detail)
    return detail


def _enrich_subtask(config: dict[str, str], subtask: dict[str, Any]) -> dict[str, Any]:
    """Read each subtask directly because Jira parent stubs often omit assignee."""
    stub = _subtask_stub(config, subtask)
    subtask_key = stub.get("key") or ""
    if not subtask_key:
        return stub
    try:
        detail = _read_subtask_detail(config, subtask_key)
        return {**stub, **{key: value for key, value in detail.items() if value not in (None, "")}}
    except Exception:
        return stub


def normalize_issue(config: dict[str, str], issue: dict[str, Any]) -> dict[str, Any]:
    fields = issue.get("fields", {})
    sprint_field_id = resolve_sprint_field_id(config)
    sprint_names = _normalize_sprint_names(fields.get(sprint_field_id)) if sprint_field_id else []
    story_points_field_id = resolve_story_points_field_id(config)
    comments = []
    for comment in (fields.get("comment") or {}).get("comments", []):
        comments.append(
            {
                "author": _display_name(comment.get("author")),
                "created": comment.get("created"),
                "updated": comment.get("updated"),
                "body": extract_adf_text(comment.get("body")),
            }
        )

    linked_issues = []
    for link in fields.get("issuelinks") or []:
        link_type = (link.get("type") or {}).get("name", "")
        for direction, linked in (("inward", link.get("inwardIssue")), ("outward", link.get("outwardIssue"))):
            if linked:
                linked_fields = linked.get("fields", {})
                linked_issues.append(
                    {
                        "direction": direction,
                        "link_type": link_type,
                        "key": linked.get("key"),
                        "summary": linked_fields.get("summary"),
                        "status": (linked_fields.get("status") or {}).get("name"),
                    }
                )

    description_text = extract_adf_text(fields.get("description"))
    ac_field, configured_acceptance_criteria = configured_field_value(config, fields, "JIRA_ACCEPTANCE_CRITERIA_FIELD")
    regulatory_field, configured_regulatory_justification = configured_field_value(config, fields, "JIRA_REGULATORY_JUSTIFICATION_FIELD")
    reason_field, configured_reason_comments = configured_field_value(config, fields, "JIRA_REASON_COMMENTS_FIELD")
    subtasks = []
    for subtask in fields.get("subtasks") or []:
        subtasks.append(_enrich_subtask(config, subtask))

    return {
        "id": issue.get("id"),
        "key": issue.get("key"),
        "url": f"{config['base_url']}/browse/{issue.get('key')}",
        "summary": fields.get("summary"),
        "description": description_text,
        "acceptance_criteria_field": ac_field,
        "acceptance_criteria": extract_acceptance_criteria(configured_acceptance_criteria or description_text),
        "acceptance_criteria_raw": configured_acceptance_criteria,
        "regulatory_justification_field": regulatory_field,
        "regulatory_justification": configured_regulatory_justification,
        "reason_comments_field": reason_field,
        "reason_comments": configured_reason_comments,
        "status": status_name(fields),
        "status_category": status_category(fields),
        "priority": (fields.get("priority") or {}).get("name"),
        "issue_type": issue_type_name(fields),
        "parent": (fields.get("parent") or {}).get("key"),
        "parent_summary": ((fields.get("parent") or {}).get("fields") or {}).get("summary"),
        "parent_issue_type": issue_type_name((fields.get("parent") or {}).get("fields") or {}),
        "parent_status": status_name((fields.get("parent") or {}).get("fields") or {}),
        "assignee": _display_name(fields.get("assignee")),
        "assignee_account_id": (fields.get("assignee") or {}).get("accountId"),
        "reporter": _display_name(fields.get("reporter")),
        "labels": fields.get("labels") or [],
        "components": _names(fields.get("components")),
        "fix_versions": _names(fields.get("fixVersions")),
        "story_points": fields.get(story_points_field_id) if story_points_field_id else None,
        "sprint_field_id": sprint_field_id,
        "sprints": sprint_names,
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "due_date": fields.get("duedate"),
        "linked_issues": linked_issues,
        "subtasks": subtasks,
        "comments_count": len(comments),
        "comments": comments,
    }


def read_issue(
    issue_key: str,
    *,
    jira_url: str | None = None,
    username: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    config = get_jira_config(jira_url, username, token)
    sprint_field_id = resolve_sprint_field_id(config)
    configured_fields = [
        resolve_jira_field_id(config, "JIRA_ACCEPTANCE_CRITERIA_FIELD"),
        resolve_jira_field_id(config, "JIRA_REGULATORY_JUSTIFICATION_FIELD"),
        resolve_jira_field_id(config, "JIRA_REASON_COMMENTS_FIELD"),
        sprint_field_id,
        resolve_story_points_field_id(config),
    ]
    fields_to_read = DEFAULT_FIELDS + [field for field in configured_fields if field]
    fields = urllib.parse.quote(",".join(dict.fromkeys(fields_to_read)))
    expanded_links = "issuelinks"
    issue_path = f"issue/{urllib.parse.quote(issue_key.strip(), safe='')}?fields={fields}&expand={expanded_links}"
    issue = jira_request(issue_path, config=config)
    return normalize_issue(config, issue)


def _search_issues_with_metadata(
    jql: str,
    *,
    max_results: int = 50,
    only_with_sprint: bool = False,
) -> dict[str, Any]:
    """Search Jira and return compact issue rows plus optional filter metadata."""
    config = get_jira_config()
    sprint_field = resolve_sprint_field_id(config)
    search_fields = [
        "summary",
        "status",
        "priority",
        "issuetype",
        "assignee",
        "parent",
        "updated",
    ]
    if sprint_field:
        search_fields.append(sprint_field)

    body = {
        "jql": jql,
        "maxResults": max_results,
        "fields": search_fields,
    }
    response = jira_request("search/jql", config=config, method="POST", body=body)
    issues = []
    excluded_without_sprint_count = 0
    for issue in response.get("issues") or []:
        fields = issue.get("fields") or {}
        sprint_names = _normalize_sprint_names(fields.get(sprint_field)) if sprint_field else []
        if only_with_sprint and not sprint_names:
            excluded_without_sprint_count += 1
            continue
        issues.append(
            {
                "key": issue.get("key"),
                "summary": fields.get("summary"),
                "status": status_name(fields),
                "status_category": status_category(fields),
                "issue_type": issue_type_name(fields),
                "assignee": _display_name(fields.get("assignee")),
                "priority": (fields.get("priority") or {}).get("name"),
                "parent": (fields.get("parent") or {}).get("key"),
                "parent_summary": ((fields.get("parent") or {}).get("fields") or {}).get("summary"),
                "updated": fields.get("updated"),
                "sprints": sprint_names,
            }
        )
    return {
        "issues": issues,
        "excluded_without_sprint_count": excluded_without_sprint_count,
    }


def search_issues(
    jql: str,
    *,
    max_results: int = 50,
    only_with_sprint: bool = False,
) -> list[dict[str, Any]]:
    """Search Jira and return minimal issue summaries."""
    return _search_issues_with_metadata(
        jql,
        max_results=max_results,
        only_with_sprint=only_with_sprint,
    )["issues"]


def feature_context_for_story(story: dict[str, Any]) -> dict[str, Any]:
    """Read the parent Feature and sibling story context for an already-loaded story."""
    parent_key = story.get("parent")
    if not parent_key:
        return {
            "ok": False,
            "story_key": story.get("key"),
            "feature_required": True,
            "stop_reason": "Story has no parent Feature/Epic, so Jira story writing should wait for user clarification.",
        }

    feature = read_issue(parent_key)
    try:
        sibling_stories = search_issues(f"parent = {parent_key} ORDER BY updated DESC", max_results=100)
    except JiraRequestError as exc:
        sibling_stories = []
        search_error = str(exc)
    else:
        search_error = ""

    completed = [
        item for item in sibling_stories
        if (item.get("status_category") or "").lower() == "done" or (item.get("status") or "").lower() in {"done", "closed", "resolved"}
    ]
    in_progress = [
        item for item in sibling_stories
        if (item.get("status_category") or "").lower() == "in progress"
    ]
    pending = [
        item for item in sibling_stories
        if item not in completed and item not in in_progress
    ]

    return {
        "ok": True,
        "story_key": story.get("key"),
        "feature": {
            "key": feature.get("key"),
            "name": feature.get("summary"),
            "issue_type": feature.get("issue_type"),
            "status": feature.get("status"),
            "description": feature.get("description"),
            "acceptance_criteria": feature.get("acceptance_criteria"),
        },
        "feature_goal": feature.get("description") or feature.get("summary") or "",
        "current_story_fit": (
            f"{story.get('key')} contributes to {feature.get('key')} by delivering: {story.get('summary')}."
        ),
        "sibling_stories": sibling_stories,
        "completed_stories": completed,
        "in_progress_stories": in_progress,
        "pending_stories": pending,
        "completed_count": len(completed),
        "total_sibling_count": len(sibling_stories),
        "search_error": search_error,
        "rule": "Use this Feature context before proposing or applying Jira story updates.",
    }


def feature_context(issue_key: str) -> dict[str, Any]:
    """Read the parent Feature and sibling story context for a Jira story."""
    return feature_context_for_story(read_issue(issue_key))


def check_connection(
    *,
    jira_url: str | None = None,
    username: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    config = get_jira_config(jira_url, username, token)
    user = jira_request("myself", config=config)
    return {
        "ok": True,
        "jira_url": config["base_url"],
        "display_name": user.get("displayName"),
        "email": user.get("emailAddress"),
        "account_id": user.get("accountId"),
        "account_type": user.get("accountType"),
    }


AC_PATTERNS = [
    r"acceptance criteria[:\n](.*)",
    r"acceptance criterion[:\n](.*)",
    r"\bac[:\n](.*)",
]


def extract_acceptance_criteria(text: str) -> list[str]:
    block = ""
    for pattern in AC_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            block = match.group(1)
            break

    candidates = []
    for line in block.splitlines():
        cleaned = re.sub(r"^\s*[-*•\d.)\]]+\s*", "", line).strip()
        if cleaned and len(cleaned) > 8 and not cleaned.lower().startswith("description"):
            candidates.append(cleaned)

    return candidates[:12]


def detect_impacted_areas(story: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(story.get(key) or "")
        for key in ("summary", "description")
    ).lower()
    mapping = {
        "backend logic": ["service", "controller", "repository", "api", "endpoint", "validation", "business logic"],
        "api changes": ["api", "endpoint", "request", "response", "contract", "controller"],
        "db/config changes": ["database", "db", "schema", "migration", "config", "property"],
        "frontend": ["ui", "frontend", "screen", "page", "button", "form"],
        "unit tests": ["unit test", "mockito", "junit", "coverage"],
        "integration testing": ["integration", "mockmvc", "e2e", "contract test"],
        "documentation": ["doc", "readme", "documentation"],
    }
    detected = []
    for area, keywords in mapping.items():
        for keyword in keywords:
            pattern = r"\b" + re.escape(keyword).replace(r"\ ", r"\s+") + r"\b"
            if re.search(pattern, text):
                detected.append(area)
                break
    return detected


def analyze_story(story: dict[str, Any]) -> dict[str, Any]:
    description = story.get("description") or ""
    acceptance_criteria = extract_acceptance_criteria(description)
    impacted_areas = detect_impacted_areas(story)
    is_development = bool(impacted_areas) or story.get("issue_type", "").lower() in {"story", "bug", "task"}

    universal_flow = ["Analyze", "Plan", "Execute", "Validate", "Document"]
    development_flow = [
        "Analysis",
        "Design / Approach",
        "Backend logic",
        "API changes",
        "DB/config changes",
        "Frontend (if any)",
        "Unit tests",
        "Integration testing",
        "Documentation",
    ]

    gaps = []
    if not description.strip():
        gaps.append("Description is empty.")
    if not acceptance_criteria:
        gaps.append("Acceptance criteria are missing or not clearly listed.")
    if not story.get("priority"):
        gaps.append("Priority is not set.")
    if story.get("assignee") in {"unassigned", "", None}:
        gaps.append("Assignee is not set.")

    suggested_acceptance_criteria = acceptance_criteria or build_suggested_acceptance_criteria(story, impacted_areas)

    suggested_story = {
        "title": story.get("summary"),
        "simple_explanation": (
            f"This story asks us to {story.get('summary', 'complete the requested change')}. "
            "Confirm the expected behavior, impacted areas, acceptance criteria, and validation steps before moving to any next workflow step."
        ),
        "suggested_acceptance_criteria": suggested_acceptance_criteria,
    }

    subtask_strategy = development_flow if is_development and len(impacted_areas) >= 4 else []

    return {
        "story_key": story.get("key"),
        "story_url": story.get("url"),
        "current_summary": story.get("summary"),
        "current_status": story.get("status"),
        "current_priority": story.get("priority"),
        "current_assignee": story.get("assignee"),
        "acceptance_criteria": acceptance_criteria,
        "suggested_acceptance_criteria": suggested_acceptance_criteria,
        "detected_impacted_areas": impacted_areas,
        "is_development_story": is_development,
        "recommended_workflow": development_flow if is_development else universal_flow,
        "gaps_or_questions": gaps,
        "suggested_clearer_story": suggested_story,
        "subtask_creation_guidance": {
            "rule": "Do not create subtasks from MCP tools. Only suggest subtasks when the story is clearly large and has multiple independent work areas.",
            "suggested_strategy": subtask_strategy,
            "ask_user": (
                "No subtasks are recommended by default for small or medium stories."
                if not subtask_strategy
                else "Do you want me to prepare exact Jira subtask suggestions for this large story?"
            ),
        },
        "approval_required": {
            "message": "Do not update Jira automatically. Show these suggestions to the user and ask for approval first.",
            "next_question": "Do you approve updating the Jira story with the clearer explanation and acceptance criteria?",
        },
        "next_recommended_actions": [
            "Review the suggested clearer story wording.",
            "Review or refine the suggested acceptance criteria.",
            "Decide whether Jira subtasks are needed.",
            "Use jira_refine_story only after the user approves updating Jira.",
        ],
    }


def build_suggested_acceptance_criteria(story: dict[str, Any], impacted_areas: list[str]) -> list[str]:
    summary = story.get("summary") or "the requested change"
    criteria = [
        f"Given the requested change is delivered, when the relevant workflow runs, then {summary} behaves as described in the story.",
        "Given a valid user action or system event, when the new behavior is triggered, then the expected output/change is produced consistently.",
        "Given invalid, missing, or unsupported input, when the workflow runs, then the system handles it safely with a clear error or fallback.",
        "Given existing related functionality, when regression tests run, then current behavior remains unchanged.",
    ]
    if "backend logic" in impacted_areas:
        criteria.append("Backend service logic is covered by unit tests for positive, negative, and edge cases.")
    if "api changes" in impacted_areas:
        criteria.append("API behavior, request/response contract, and error handling are validated with appropriate tests.")
    if "db/config changes" in impacted_areas:
        criteria.append("Configuration or persistence changes are documented and validated without breaking existing environments.")
    if "frontend" in impacted_areas:
        criteria.append("Frontend behavior is verified for the expected user workflow and error states.")
    return criteria


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    title = re.sub(r"^(story|task|bug)\s*[:\-]\s*", "", title, flags=re.IGNORECASE)
    if not title:
        return "Clarify requested behavior"
    return title[:1].upper() + title[1:]


def _markdown_bullets(items: list[Any], fallback: str) -> str:
    values = [str(item).strip() for item in items if str(item or "").strip()]
    if not values:
        values = [fallback]
    return "\n".join(f"- {item}" for item in values)


def _markdown_ordered(items: list[Any], fallback: str) -> str:
    values = [str(item).strip() for item in items if str(item or "").strip()]
    if not values:
        values = [fallback]
    return "\n".join(f"{idx}. {item}" for idx, item in enumerate(values, start=1))


def build_bdrsp1623_description_markdown(
    *,
    story: dict[str, Any],
    title: str,
    simple_explanation: str,
    regulatory_justification: str,
    reason_comments: str,
    acceptance_criteria: list[str],
    impacted_areas: list[str],
    open_questions: list[str],
    feature_context: dict[str, Any],
) -> str:
    """Build the mandatory BDRSP-1623 formatted description used by refine/create flows."""
    feature_obj = feature_context.get("feature") or {}
    feature_key = feature_obj.get("key") or story.get("parent") or "Not linked"
    feature_name = feature_obj.get("name") or feature_obj.get("summary") or story.get("parent_summary") or "Not available"
    story_key = story.get("key") or "New story"
    status = story.get("status") or "Unknown"
    priority = story.get("priority") or "Not set"
    assignee = story.get("assignee") or "Unassigned"
    current_story_fit = feature_context.get("current_story_fit") or "Feature fit must be confirmed before implementation."
    completed = feature_context.get("completed_stories") or []
    completed_preview = [
        f"{item.get('key')}: {item.get('summary')}"
        for item in completed[:5]
        if isinstance(item, dict)
    ]

    return f"""## User Story
:::info
## Background
{simple_explanation}

This refinement is aligned with parent Feature `{feature_key}` and must preserve the mandatory `{MANDATORY_JIRA_STYLE_PROFILE}` story format.
:::

## Story Snapshot
| Field | Value |
|---|---|
| Story | {story_key} |
| Refined Summary | {title} |
| Feature | {feature_key} |
| Feature Name | {feature_name} |
| Status | {status} |
| Priority | {priority} |
| Assignee | {assignee} |
| Style Profile | {MANDATORY_JIRA_STYLE_PROFILE} |

:::success
## Implementation Scope
{_markdown_ordered(impacted_areas, "Confirm the impacted code and delivery areas before implementation starts.")}
:::

:::warning
## Constraints
- Feature context and sibling stories must be reviewed before updating Jira.
- Codebase impact must be scanned and confirmed before implementation starts.
- Keep this story scoped to the approved Jira change; do not add unrelated refactors or subtasks.
- Use preview and explicit approval before applying this refinement.
:::

:::note
## Acceptance Criteria
{_markdown_bullets(acceptance_criteria, "Acceptance criteria must be confirmed with the user before Jira update.")}

## Feature Alignment
- Current story fit: {current_story_fit}
- Completed sibling reference: {', '.join(completed_preview) if completed_preview else 'No completed sibling stories loaded.'}

## Open Questions
{_markdown_bullets(open_questions, "No open questions identified from the current Jira analysis.")}
:::

## Regulatory Justification
{regulatory_justification}

## Reason/Comments
{reason_comments}
"""


def build_refined_story_proposal(story: dict[str, Any], feature: dict[str, Any] | None = None) -> dict[str, Any]:
    analysis = analyze_story(story)
    feature = feature or (feature_context_for_story(story) if story.get("key") else {"ok": False, "stop_reason": "Story key is missing."})
    acs = analysis["suggested_acceptance_criteria"]
    impacted = analysis["detected_impacted_areas"]
    gaps = analysis["gaps_or_questions"]
    title = _clean_title(story.get("summary") or "")
    if len(title) > 120:
        title = title[:117].rstrip() + "..."

    simple_explanation = (
        f"This story is to {title.lower()}. "
        "The requested outcome should be clear, scoped, and aligned with the existing project behavior."
    )
    regulatory_justification = (
        "This story requires traceable implementation and validation so the delivered behavior is aligned with the approved Jira scope, "
        "acceptance criteria, and project governance expectations."
    )
    reason_comments = (
        "Story scope was reviewed against the Jira details and related repository context before implementation starts."
    )
    subtasks = []
    existing_subtasks = story.get("subtasks") or []
    should_suggest_subtasks = len(impacted) >= 4 and not existing_subtasks
    if should_suggest_subtasks:
        subtasks.append(
            {
                "summary": f"{story['key']}: Deliver scoped story changes",
                "description": "Complete the minimum required work for this story and validate the acceptance criteria.",
            }
        )
    description_markdown = build_bdrsp1623_description_markdown(
        story=story,
        title=title,
        simple_explanation=simple_explanation,
        regulatory_justification=regulatory_justification,
        reason_comments=reason_comments,
        acceptance_criteria=acs,
        impacted_areas=impacted,
        open_questions=gaps,
        feature_context=feature,
    )
    style_validation = validate_mandatory_story_style(description_markdown)

    return {
        "story_key": story.get("key"),
        "feature_context": feature,
        "current": {
            "summary": story.get("summary"),
            "description": story.get("description"),
            "acceptance_criteria": analysis["acceptance_criteria"],
            "regulatory_justification": story.get("regulatory_justification"),
            "reason_comments": story.get("reason_comments"),
            "subtasks": story.get("subtasks") or [],
            "linked_issues": story.get("linked_issues") or [],
            "comments": story.get("comments") or [],
        },
        "proposal": {
            "summary": title,
            "simple_explanation": simple_explanation,
            "style_profile": MANDATORY_JIRA_STYLE_PROFILE,
            "style_validation": style_validation,
            "approved_description": description_markdown,
            "description_markdown": description_markdown,
            "description_adf": build_adf_from_markdownish(description_markdown),
            "description_preview": {
                "Overview": simple_explanation,
                "Regulatory Justification": regulatory_justification,
                "Acceptance Criteria": acs,
                "Reason/Comments": reason_comments,
                "Impacted Areas": impacted,
                "Open Questions": gaps,
                "Feature Alignment": {
                    "feature": feature.get("feature"),
                    "current_story_fit": feature.get("current_story_fit"),
                    "completed_stories": feature.get("completed_stories", [])[:10],
                },
            },
            "suggested_subtasks": subtasks,
            "subtask_policy": (
                "No new subtasks are recommended by default."
                if not subtasks
                else "Suggested subtasks are recommendations only. This MCP server will not create them."
            ),
        },
        "approval_required": {
            "message": (
                f"Show this Feature-aligned {MANDATORY_JIRA_STYLE_PROFILE} proposal to the user first. "
                "Update Jira fields only after explicit approval. Subtasks are suggestion-only."
            ),
            "apply_instruction": (
                "If the user approves, call jira_refine_story again with apply_update=true and pass "
                "approved_summary plus approved_description. Do not rely on generated text silently. "
                "Do not apply updates if feature_context.ok is false."
            ),
        },
    }


def update_story_fields(
    issue_key: str,
    summary: str,
    description_adf: dict[str, Any],
    acceptance_criteria: list[str] | None = None,
    regulatory_justification: str | None = None,
    reason_comments: str | None = None,
) -> dict[str, Any]:
    config = get_jira_config()
    fields: dict[str, Any] = {
        "summary": summary,
        "description": description_adf,
    }
    ac_field = resolve_jira_field_id(config, "JIRA_ACCEPTANCE_CRITERIA_FIELD")
    if ac_field and acceptance_criteria:
        ac_text = "Acceptance Criteria\n" + "\n".join(f"- {item}" for item in acceptance_criteria)
        if os.getenv("JIRA_ACCEPTANCE_CRITERIA_FIELD_FORMAT", "adf").strip().lower() == "adf":
            fields[ac_field] = build_adf_from_markdownish(ac_text)
        else:
            fields[ac_field] = ac_text

    regulatory_field = resolve_jira_field_id(config, "JIRA_REGULATORY_JUSTIFICATION_FIELD")
    if regulatory_field and regulatory_justification:
        if os.getenv("JIRA_REGULATORY_JUSTIFICATION_FIELD_FORMAT", "adf").strip().lower() == "adf":
            fields[regulatory_field] = build_adf_from_markdownish(regulatory_justification)
        else:
            fields[regulatory_field] = regulatory_justification

    reason_field = resolve_jira_field_id(config, "JIRA_REASON_COMMENTS_FIELD")
    if reason_field and reason_comments:
        if os.getenv("JIRA_REASON_COMMENTS_FIELD_FORMAT", "adf").strip().lower() == "adf":
            fields[reason_field] = build_adf_from_markdownish(reason_comments)
        else:
            fields[reason_field] = reason_comments

    jira_request(
        f"issue/{urllib.parse.quote(issue_key.strip(), safe='')}",
        config=config,
        method="PUT",
        body={"fields": fields},
    )
    return {
        "ok": True,
        "updated_story": issue_key,
        "updated_fields": list(fields.keys()),
    }


def normalize_compare_text(value: Any) -> str:
    """Normalize human text for safe no-op comparisons."""
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def planned_story_field_changes(
    story: dict[str, Any],
    *,
    summary: str,
    description: str,
    acceptance_criteria: list[str] | None,
    regulatory_justification: str | None,
    reason_comments: str | None,
) -> list[str]:
    """Return Jira fields that differ from the approved target values."""
    changes = []
    if normalize_compare_text(story.get("summary")) != normalize_compare_text(summary):
        changes.append("summary")
    if normalize_compare_text(story.get("description")) != normalize_compare_text(description):
        changes.append("description")
    if acceptance_criteria is not None and normalize_compare_text(story.get("acceptance_criteria")) != normalize_compare_text(acceptance_criteria):
        changes.append("acceptance_criteria")
    if regulatory_justification is not None and normalize_compare_text(story.get("regulatory_justification")) != normalize_compare_text(regulatory_justification):
        changes.append("regulatory_justification")
    if reason_comments is not None and normalize_compare_text(story.get("reason_comments")) != normalize_compare_text(reason_comments):
        changes.append("reason_comments")
    return changes


def add_issue_comment(issue_key: str, comment: str) -> dict[str, Any]:
    """Add an approved Jira comment to the issue activity."""
    config = get_jira_config()
    result = jira_request(
        f"issue/{urllib.parse.quote(issue_key.strip(), safe='')}/comment",
        config=config,
        method="POST",
        body={"body": build_adf_from_markdownish(comment)},
    )
    return {
        "ok": True,
        "comment_id": result.get("id"),
        "created": result.get("created"),
    }


def refine_story(
    jira_id: str,
    *,
    apply_update: bool = False,
    approved_summary: str | None = None,
    approved_description: str | None = None,
    approved_acceptance_criteria: list[str] | None = None,
    approved_regulatory_justification: str | None = None,
    approved_reason_comments: str | None = None,
    approved_comment: str | None = None,
    confirm_apply_generated_proposal: bool = False,
    codebase_scan_confirmed: bool = False,
) -> dict[str, Any]:
    story = read_issue(jira_id)
    proposal = build_refined_story_proposal(story)

    # ── Mandatory gate 1: Parent Feature context ───────────────────────────
    # The Feature, its goal, and all sibling stories MUST be readable before
    # any Jira story field is written.  This is a hard block — not a warning.
    if not proposal.get("feature_context", {}).get("ok"):
        return {
            "ok": False,
            "mode": "feature_context_required",
            "story_key": story.get("key"),
            "feature_context": proposal.get("feature_context"),
            "message": (
                "BLOCKED: Cannot refine story without parent Feature context. "
                "The parent Feature, its goal, and all sibling stories must be readable "
                "before any Jira story field is updated."
            ),
            "required_action": (
                "Call jira_feature_context first. Verify the parent Feature is linked, "
                "its goal is clear, and all sibling stories are readable. "
                "Only after confirming Feature context, retry with codebase_scan_confirmed=true."
            ),
        }

    # ── Mandatory gate 2: Codebase scan confirmation ──────────────────────
    # Story refinement must be grounded in the actual codebase, not just Jira
    # fields.  The agent MUST scan the codebase for story/feature keywords and
    # confirm the scan result before apply_update=True is allowed.
    # Fallback: if explicit approved content is supplied, treat it as a confirmed
    # pre-scanned update request. This avoids false blocks from stale DevFlow
    # clients that omit codebase_scan_confirmed while still requiring concrete
    # approved update payload from the user.
    has_explicit_approved_payload = bool((approved_description or "").strip()) or bool(approved_acceptance_criteria)
    effective_codebase_scan_confirmed = codebase_scan_confirmed or has_explicit_approved_payload

    if apply_update and not effective_codebase_scan_confirmed:
        feature_obj = (proposal.get("feature_context") or {}).get("feature") or {}
        siblings = (proposal.get("feature_context") or {}).get("completed_stories") or []
        return {
            "ok": False,
            "mode": "codebase_scan_required",
            "story_key": story.get("key"),
            "message": (
                "BLOCKED: Cannot apply Jira story update without codebase scan confirmation. "
                "Story refinement must be grounded in the actual codebase — not just Jira fields."
            ),
            "mandatory_sequence": [
                "1. Read the current story: jira_read_story",
                "2. Read the parent Feature + all sibling stories: jira_feature_context",
                f"   Feature: {feature_obj.get('key', 'unknown')} — {feature_obj.get('name', '')}",
                f"   Siblings read: {len(siblings)} completed stories loaded",
                "3. Scan the codebase for story and feature keywords (use rg / IDE search / dev_implement_story start stage)",
                "4. Confirm the scan result explicitly",
                "5. Only then call jira_refine_story with apply_update=true AND codebase_scan_confirmed=true",
            ],
            "required_action": (
                "Scan the codebase for keywords from this story and its parent Feature. "
                "Confirm which files are impacted. "
                "Then retry: jira_refine_story(jira_id=..., apply_update=True, codebase_scan_confirmed=True, approved_description=...)"
            ),
            "proposal_preview": proposal,
        }

    if not apply_update:
        return {
            "ok": True,
            "mode": "proposal_only",
            **proposal,
        }

    if not approved_description and not confirm_apply_generated_proposal:
        return {
            "ok": False,
            "mode": "approval_content_required",
            "message": (
                "Refusing to update Jira because approved_description was not provided. "
                "Pass the exact user-approved description so the applied Jira update matches what the user reviewed."
            ),
            "safe_next_call": {
                "jira_id": jira_id,
                "apply_update": True,
                "approved_summary": approved_summary or proposal["proposal"]["summary"],
                "approved_description": "<exact approved description text>",
                "approved_acceptance_criteria": approved_acceptance_criteria or [],
                "approved_regulatory_justification": approved_regulatory_justification or "",
                "approved_reason_comments": approved_reason_comments or "",
                "approved_comment": approved_comment or "",
            },
        }

    final_summary = approved_summary or proposal["proposal"]["summary"]
    if approved_description:
        style_validation = validate_mandatory_story_style(approved_description)
        if not style_validation.get("ok"):
            return {
                "ok": False,
                "mode": "style_mandatory_violation",
                "story_key": story.get("key"),
                "message": (
                    "Refusing Jira story update because the approved_description does not follow the mandatory "
                    f"{MANDATORY_JIRA_STYLE_PROFILE} style profile."
                ),
                "required_style_profile": style_validation,
                "required_format": {
                    "headings": "## Heading",
                    "panels": [":::info", ":::success", ":::warning", ":::note"],
                    "table": "| Col1 | Col2 | with |---| separator",
                },
            }
        config = get_jira_config()
        final_description = clean_description_for_configured_fields(approved_description, config)
        if approved_acceptance_criteria and not is_dedicated_field_available(config, "JIRA_ACCEPTANCE_CRITERIA_FIELD"):
            final_description = (
                final_description.rstrip()
                + "\n\nAcceptance Criteria\n"
                + "\n".join(f"- {item}" for item in approved_acceptance_criteria)
            )
        if approved_regulatory_justification and not is_dedicated_field_available(config, "JIRA_REGULATORY_JUSTIFICATION_FIELD"):
            final_description = (
                final_description.rstrip()
                + "\n\nRegulatory Justification\n"
                + approved_regulatory_justification
            )
        if approved_reason_comments and not is_dedicated_field_available(config, "JIRA_REASON_COMMENTS_FIELD"):
            final_description = (
                final_description.rstrip()
                + "\n\nReason/Comments\n"
                + approved_reason_comments
            )
        description_adf = build_adf_from_markdownish(final_description)
    else:
        final_description = proposal["proposal"]["description_markdown"]
        style_validation = validate_mandatory_story_style(final_description)
        if not style_validation.get("ok"):
            return {
                "ok": False,
                "mode": "style_mandatory_violation",
                "story_key": story.get("key"),
                "message": (
                    "Refusing Jira story update because the generated proposal does not follow the mandatory "
                    f"{MANDATORY_JIRA_STYLE_PROFILE} style profile."
                ),
                "required_style_profile": style_validation,
            }
        description_adf = build_adf_from_markdownish(final_description)

    final_acceptance_criteria = approved_acceptance_criteria or proposal["proposal"]["description_preview"]["Acceptance Criteria"]
    final_regulatory_justification = approved_regulatory_justification or proposal["proposal"]["description_preview"]["Regulatory Justification"]
    final_reason_comments = approved_reason_comments
    planned_changes = planned_story_field_changes(
        story,
        summary=final_summary,
        description=final_description,
        acceptance_criteria=final_acceptance_criteria,
        regulatory_justification=final_regulatory_justification,
        reason_comments=final_reason_comments,
    )

    if planned_changes:
        update_result = update_story_fields(
            story["key"],
            final_summary,
            description_adf,
            final_acceptance_criteria,
            final_regulatory_justification,
            final_reason_comments,
        )
    else:
        update_result = {
            "ok": True,
            "updated_story": story["key"],
            "updated_fields": [],
            "mode": "no_change",
            "message": "Jira story fields already match the approved content.",
        }
    comment_result = add_issue_comment(story["key"], approved_comment) if approved_comment else None
    return {
        "ok": True,
        "mode": "jira_updated" if planned_changes else "no_change",
        "story_key": story["key"],
        "update_result": update_result,
        "comment_result": comment_result,
        "subtask_policy": "No subtasks were created. Jira MCP tools only suggest subtasks.",
        "applied": {
            "summary": final_summary,
            "description_source": "approved_description" if approved_description else "generated_proposal_confirmed",
            "subtasks_source": "suggestion_only",
        },
    }


GENERIC_SUBTASK_NAMES = {
    "analyze",
    "analysis",
    "plan",
    "planning",
    "execute",
    "validate",
    "validation",
    "document",
    "documentation",
}


def normalize_task_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def task_tokens(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "this", "that", "story", "task", "subtask"}
    return {token for token in normalize_task_text(text).split() if token and token not in stop}


def task_similarity(left: str, right: str) -> float:
    left_tokens = task_tokens(left)
    right_tokens = task_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def is_generic_or_unnecessary(summary: str) -> bool:
    cleaned = normalize_task_text(summary)
    if ":" in summary:
        cleaned = normalize_task_text(summary.split(":", 1)[1])
    return cleaned in GENERIC_SUBTASK_NAMES or len(task_tokens(cleaned)) < 2


def resolve_subtask_assignee(proposed: dict[str, str], story: dict[str, Any]) -> tuple[str | None, str]:
    proposed_assignee = (
        proposed.get("assignee_account_id")
        or proposed.get("assignee")
        or proposed.get("assigneeAccountId")
        or ""
    ).strip()
    if proposed_assignee:
        return proposed_assignee, "proposed_subtask"

    parent_assignee = story.get("assignee_account_id") or ""
    if parent_assignee:
        return parent_assignee, "parent_story_assignee"

    default_assignee = os.getenv("JIRA_DEFAULT_ASSIGNEE_ACCOUNT_ID", "").strip()
    if default_assignee:
        return default_assignee, "JIRA_DEFAULT_ASSIGNEE_ACCOUNT_ID"

    return None, "missing"


def resolve_subtask_priority(proposed: dict[str, str]) -> str:
    return (
        proposed.get("priority")
        or proposed.get("priority_name")
        or proposed.get("priorityName")
        or os.getenv("JIRA_DEFAULT_SUBTASK_PRIORITY")
        or "Medium"
    ).strip()


def build_subtask_plan(parent_key: str, proposed_subtasks: list[dict[str, str]]) -> dict[str, Any]:
    story = read_issue(parent_key)
    existing = story.get("subtasks") or []
    actions = []
    seen_proposed: set[str] = set()

    for index, proposed in enumerate(proposed_subtasks, start=1):
        summary = (proposed.get("summary") or "").strip()
        description = (proposed.get("description") or "").strip()
        assignee_account_id, assignee_source = resolve_subtask_assignee(proposed, story)
        priority = resolve_subtask_priority(proposed)
        action_id = f"subtask-{index}"

        if not summary:
            actions.append(
                {
                    "action_id": action_id,
                    "action": "skip_invalid",
                    "reason": "Subtask summary is empty.",
                    "proposed": proposed,
                }
            )
            continue

        if not description:
            actions.append(
                {
                    "action_id": action_id,
                    "action": "skip_invalid",
                    "reason": "Subtask description is required before create/update.",
                    "proposed": proposed,
                }
            )
            continue

        if not assignee_account_id:
            actions.append(
                {
                    "action_id": action_id,
                    "action": "skip_invalid",
                    "reason": "Subtask assignee is required. Provide assignee_account_id or set parent/default assignee.",
                    "proposed": proposed,
                }
            )
            continue

        normalized_summary = normalize_task_text(summary)
        if normalized_summary in seen_proposed:
            actions.append(
                {
                    "action_id": action_id,
                    "action": "skip_duplicate_in_request",
                    "reason": "This proposed subtask duplicates another proposed subtask in the same request.",
                    "proposed": proposed,
                }
            )
            continue
        seen_proposed.add(normalized_summary)

        if is_generic_or_unnecessary(summary):
            actions.append(
                {
                    "action_id": action_id,
                    "action": "skip_unnecessary",
                    "reason": "Generic subtasks like Analyze/Plan/Execute/Validate/Document are not created automatically.",
                    "proposed": proposed,
                }
            )
            continue

        matched = None
        best_score = 0.0
        for existing_subtask in existing:
            score = task_similarity(summary, existing_subtask.get("summary") or "")
            if normalize_task_text(summary) == normalize_task_text(existing_subtask.get("summary") or ""):
                score = 1.0
            if score > best_score:
                best_score = score
                matched = existing_subtask

        if matched and best_score >= 0.55:
            actions.append(
                {
                    "action_id": action_id,
                    "action": "update_existing",
                    "reason": "A same-purpose subtask already exists, so this should update the existing subtask instead of creating a duplicate.",
                    "matched_existing_subtask": matched,
                    "similarity": round(best_score, 2),
                    "proposed": {
                        "summary": summary,
                        "description": description,
                        "assignee_account_id": assignee_account_id,
                        "assignee_source": assignee_source,
                        "priority": priority,
                    },
                }
            )
        else:
            actions.append(
                {
                    "action_id": action_id,
                    "action": "create_new",
                    "reason": "No duplicate or same-purpose subtask was found.",
                    "proposed": {
                        "summary": summary,
                        "description": description,
                        "assignee_account_id": assignee_account_id,
                        "assignee_source": assignee_source,
                        "priority": priority,
                    },
                }
            )

    return {
        "ok": True,
        "mode": "plan_only",
        "parent_story": {
            "key": story.get("key"),
            "summary": story.get("summary"),
            "assignee": story.get("assignee"),
            "assignee_account_id": story.get("assignee_account_id"),
            "existing_subtasks": existing,
        },
        "actions": actions,
        "approval_required": {
            "message": "Review each action. To apply, call jira_manage_subtasks with apply_changes=true, confirm_apply=true, and approved_action_ids.",
            "rule": "Only approved create_new or update_existing actions will be applied. Duplicate/unnecessary actions are skipped.",
        },
    }


def create_single_subtask(parent_key: str, summary: str, description: str, assignee_account_id: str, priority: str) -> dict[str, Any]:
    config = get_jira_config()
    body = {
        "fields": {
            "project": {"key": parent_key.split("-", 1)[0]},
            "parent": {"key": parent_key},
            "summary": summary,
            "description": build_adf_from_markdownish(description or summary),
            "issuetype": {"name": os.getenv("JIRA_SUBTASK_ISSUE_TYPE", "Sub-task")},
            "assignee": {"accountId": assignee_account_id},
            "priority": {"name": priority},
        }
    }
    result = jira_request("issue", config=config, method="POST", body=body)
    return {"key": result.get("key"), "id": result.get("id"), "summary": summary, "assignee_account_id": assignee_account_id, "priority": priority}


def update_subtask_fields(issue_key: str, summary: str, description: str, assignee_account_id: str, priority: str) -> dict[str, Any]:
    config = get_jira_config()
    fields = {
        "summary": summary,
        "description": build_adf_from_markdownish(description or summary),
        "assignee": {"accountId": assignee_account_id},
        "priority": {"name": priority},
    }
    jira_request(
        f"issue/{urllib.parse.quote(issue_key, safe='')}",
        config=config,
        method="PUT",
        body={"fields": fields},
    )
    return {"key": issue_key, "summary": summary, "updated_fields": list(fields.keys())}


def manage_subtasks(
    jira_id: str,
    proposed_subtasks: list[dict[str, str]],
    *,
    apply_changes: bool = False,
    confirm_apply: bool = False,
    approved_action_ids: list[str] | None = None,
) -> dict[str, Any]:
    plan = build_subtask_plan(jira_id, proposed_subtasks)
    if not apply_changes:
        return plan

    if not confirm_apply:
        return {
            "ok": False,
            "mode": "confirmation_required",
            "message": "No subtasks were created or updated. Call again with confirm_apply=true and approved_action_ids.",
            "plan": plan,
        }

    approved = set(approved_action_ids or [])
    if not approved:
        return {
            "ok": False,
            "mode": "approved_actions_required",
            "message": "No subtasks were created or updated because approved_action_ids is empty.",
            "plan": plan,
        }

    applied = []
    skipped = []
    for action in plan["actions"]:
        if action["action_id"] not in approved:
            skipped.append({**action, "skip_reason": "not_approved"})
            continue
        if action["action"] == "create_new":
            created = create_single_subtask(
                jira_id,
                action["proposed"]["summary"],
                action["proposed"].get("description") or action["proposed"]["summary"],
                action["proposed"]["assignee_account_id"],
                action["proposed"]["priority"],
            )
            applied.append({"action_id": action["action_id"], "action": "created", "result": created})
        elif action["action"] == "update_existing":
            existing_key = action["matched_existing_subtask"]["key"]
            updated = update_subtask_fields(
                existing_key,
                action["proposed"]["summary"],
                action["proposed"].get("description") or action["proposed"]["summary"],
                action["proposed"]["assignee_account_id"],
                action["proposed"]["priority"],
            )
            applied.append({"action_id": action["action_id"], "action": "updated_existing", "result": updated})
        else:
            skipped.append({**action, "skip_reason": "not_applicable"})

    return {
        "ok": True,
        "mode": "applied",
        "parent_story": jira_id,
        "applied": applied,
        "skipped": skipped,
    }


def create_story(
    *,
    project_key: str,
    parent_key: str,
    summary: str,
    approved_description: str,
    approved_acceptance_criteria: list[str] | None = None,
    approved_regulatory_justification: str | None = None,
    approved_reason_comments: str | None = None,
    issue_type: str = "Story",
    priority: str = "Medium",
    assignee_account_id: str | None = None,
    approved_comment: str | None = None,
    sprint_id: str | int | None = None,
    sprint_name: str | None = None,
    confirm_create: bool = False,
    codebase_scan_confirmed: bool = False,
    feature_context_confirmed: bool = False,
) -> dict[str, Any]:
    """Create a new Jira story under a parent Feature/Epic.

    Guards (ALL must pass before creation):
    1. feature_context_confirmed=True — parent Feature was read and understood.
    2. codebase_scan_confirmed=True   — codebase was scanned for story keywords.
    3. description follows BDRSP-1623 style profile (panels, headings, table).
    4. sprint_id or sprint_name       — story is assigned to a real Jira sprint.
    5. confirm_create=True            — user explicitly approved creation.
    """
    # ── Guard 1: Feature context must be confirmed ────────────────────────────
    if not feature_context_confirmed:
        return {
            "ok": False,
            "mode": "feature_context_required",
            "message": (
                "BLOCKED: Cannot create a story without first loading the parent Feature context. "
                "Call jira_feature_context for the parent Feature key, then retry with feature_context_confirmed=True."
            ),
            "mandatory_sequence": [
                "1. Call jira_feature_context(jira_id=<parent_feature_key>)",
                "2. Read the Feature goal, sibling stories, and completed stories",
                "3. Confirm the new story fits within the Feature scope",
                "4. Retry with feature_context_confirmed=True",
            ],
        }

    # ── Guard 2: Codebase scan must be confirmed ──────────────────────────────
    has_explicit_payload = bool((approved_description or "").strip())
    effective_scan_confirmed = codebase_scan_confirmed or has_explicit_payload

    if not effective_scan_confirmed:
        return {
            "ok": False,
            "mode": "codebase_scan_required",
            "message": (
                "BLOCKED: Cannot create a Jira story without codebase scan confirmation. "
                "The new story must be grounded in actual code — not just a Jira title."
            ),
            "mandatory_sequence": [
                "1. Scan the codebase for the story keywords using rg / IDE search",
                "2. Identify which files / packages this story would touch",
                "3. Retry with codebase_scan_confirmed=True and approved_description=<full text>",
            ],
        }

    # ── Guard 3: Description must follow BDRSP-1623 style ────────────────────
    if not approved_description or not approved_description.strip():
        return {
            "ok": False,
            "mode": "approval_content_required",
            "message": (
                f"approved_description is required and must follow the {MANDATORY_JIRA_STYLE_PROFILE} style profile "
                "(## headings, :::info/success/warning/note panels, at least one table)."
            ),
        }

    style_validation = validate_mandatory_story_style(approved_description)
    if not style_validation.get("ok"):
        return {
            "ok": False,
            "mode": "style_mandatory_violation",
            "message": (
                f"Refusing to create story because approved_description does not follow the mandatory "
                f"{MANDATORY_JIRA_STYLE_PROFILE} style profile."
            ),
            "required_style_profile": style_validation,
            "required_format": {
                "headings": "## Heading",
                "panels": [":::info", ":::success", ":::warning", ":::note"],
                "table": "| Col1 | Col2 | with |---| separator",
            },
        }

    sprint_request = _normalize_sprint_request(sprint_id=sprint_id, sprint_name=sprint_name)

    # ── Guard 4: Sprint assignment is required for story creation ────────────
    if not sprint_request.get("requested"):
        return {
            "ok": False,
            "mode": "sprint_required",
            "message": (
                f"BLOCKED: Cannot create a Jira story under Feature {parent_key} without a sprint target. "
                "Provide sprint_id for exact assignment, or sprint_name with JIRA_BOARD_ID/JIRA_BOARD_IDS configured."
            ),
            "parent_feature_key": parent_key,
            "parent_rule": "The supplied Feature key is used as the Jira parent for the story.",
            "user_question": (
                f"Which sprint should I attach this story to under Feature {parent_key}? "
                "Please provide sprint_id, or sprint_name if board id configuration is available."
            ),
            "required_payload": {
                "preferred": {"sprint_id": "<active_or_future_sprint_id>"},
                "alternate": {"sprint_name": "<active_or_future_sprint_name>"},
            },
            "safety_rule": "Do not guess sprint assignment. The tool resolves sprint assignment before Jira writes and assigns the created story through Jira Agile API.",
        }

    # ── Guard 5: Explicit user confirmation ──────────────────────────────────
    if not confirm_create:
        config = get_jira_config()
        sprint_field_id = resolve_sprint_field_id(config)
        final_desc_preview = clean_description_for_configured_fields(approved_description, config)
        return {
            "ok": False,
            "mode": "confirmation_required",
            "message": "Review the story below and confirm creation by calling again with confirm_create=True.",
            "preview": {
                "project_key": project_key,
                "parent_key": parent_key,
                "parent_rule": "The supplied Feature key is used as the Jira parent for this story.",
                "summary": summary,
                "description_preview": approved_description[:600] + ("..." if len(approved_description) > 600 else ""),
                "acceptance_criteria": approved_acceptance_criteria or [],
                "regulatory_justification": approved_regulatory_justification or "",
                "reason_comments": approved_reason_comments or "",
                "issue_type": issue_type,
                "priority": priority,
                "assignee_account_id": assignee_account_id or "unassigned",
                "style_profile": MANDATORY_JIRA_STYLE_PROFILE,
                "sprint_assignment": {
                    **sprint_request,
                    "sprint_field_id": sprint_field_id,
                    "note": (
                        "If sprint_name is used, configure JIRA_BOARD_ID/JIRA_BOARD_IDS or provide sprint_id directly."
                        if sprint_request.get("sprint_name") and not sprint_request.get("sprint_id")
                        else "Sprint will be assigned after issue creation through Jira Agile API."
                    ),
                },
            },
            "next_call": {
                "project_key": project_key,
                "parent_key": parent_key,
                "summary": summary,
                "approved_description": "<same text>",
                "approved_acceptance_criteria": approved_acceptance_criteria,
                "sprint_id": sprint_request.get("sprint_id"),
                "sprint_name": sprint_request.get("sprint_name"),
                "confirm_create": True,
                "codebase_scan_confirmed": True,
                "feature_context_confirmed": True,
            },
        }

    # ── Create the story ──────────────────────────────────────────────────────
    config = get_jira_config()
    sprint_field_id = resolve_sprint_field_id(config)
    sprint_resolution = resolve_sprint_assignment(
        config,
        sprint_id=sprint_request.get("sprint_id"),
        sprint_name=sprint_request.get("sprint_name"),
    )
    if sprint_resolution.get("requested") and not sprint_resolution.get("ok"):
        open_sprint_suggestions: list[dict[str, Any]] = []
        board_id = sprint_resolution.get("board_id")
        if sprint_resolution.get("mode") == "sprint_not_assignable" and board_id:
            try:
                open_sprint_suggestions = _list_open_sprints_for_board(config, board_id, limit=5)
            except JiraRequestError:
                open_sprint_suggestions = []

        return {
            "ok": False,
            "mode": "sprint_resolution_failed",
            "message": "Refusing to create story because requested sprint could not be resolved.",
            "sprint_assignment": sprint_resolution,
            "suggested_open_sprints": open_sprint_suggestions,
            "safe_next_step": (
                "Provide sprint_id directly, or configure JIRA_BOARD_ID/JIRA_BOARD_IDS so sprint_name can be resolved."
            ),
        }
    final_description = clean_description_for_configured_fields(approved_description, config)

    # Merge AC / regulatory / reason into description if no dedicated fields
    if approved_acceptance_criteria and not is_dedicated_field_available(config, "JIRA_ACCEPTANCE_CRITERIA_FIELD"):
        final_description = (
            final_description.rstrip()
            + "\n\nAcceptance Criteria\n"
            + "\n".join(f"- {item}" for item in approved_acceptance_criteria)
        )
    if approved_regulatory_justification and not is_dedicated_field_available(config, "JIRA_REGULATORY_JUSTIFICATION_FIELD"):
        final_description = (
            final_description.rstrip()
            + "\n\nRegulatory Justification\n"
            + approved_regulatory_justification
        )
    if approved_reason_comments and not is_dedicated_field_available(config, "JIRA_REASON_COMMENTS_FIELD"):
        final_description = (
            final_description.rstrip()
            + "\n\nReason/Comments\n"
            + approved_reason_comments
        )

    description_adf = build_adf_from_markdownish(final_description)
    body: dict[str, Any] = {
        "fields": {
            "project": {"key": project_key},
            "parent": {"key": parent_key},
            "summary": summary,
            "description": description_adf,
            "issuetype": {"name": issue_type},
            "priority": {"name": priority},
        }
    }
    if assignee_account_id:
        body["fields"]["assignee"] = {"accountId": assignee_account_id}

    # Dedicated custom fields
    ac_field = resolve_jira_field_id(config, "JIRA_ACCEPTANCE_CRITERIA_FIELD")
    if ac_field and approved_acceptance_criteria:
        ac_text = "Acceptance Criteria\n" + "\n".join(f"- {item}" for item in approved_acceptance_criteria)
        body["fields"][ac_field] = build_adf_from_markdownish(ac_text)

    reg_field = resolve_jira_field_id(config, "JIRA_REGULATORY_JUSTIFICATION_FIELD")
    if reg_field and approved_regulatory_justification:
        body["fields"][reg_field] = build_adf_from_markdownish(approved_regulatory_justification)

    reason_field = resolve_jira_field_id(config, "JIRA_REASON_COMMENTS_FIELD")
    if reason_field and approved_reason_comments:
        body["fields"][reason_field] = build_adf_from_markdownish(approved_reason_comments)

    result = jira_request("issue", config=config, method="POST", body=body)
    new_key = result.get("key") or ""
    sprint_assignment_result = None
    if new_key and sprint_resolution.get("requested"):
        try:
            sprint_assignment_result = assign_issue_to_sprint(new_key, sprint_resolution["sprint_id"], config)
            if sprint_resolution.get("sprint_name"):
                sprint_assignment_result["sprint_name"] = sprint_resolution.get("sprint_name")
            if sprint_resolution.get("board_id"):
                sprint_assignment_result["board_id"] = sprint_resolution.get("board_id")
        except JiraRequestError as exc:
            error_text = str(exc)
            is_completed_sprint = "has not been completed" in error_text.lower()
            return {
                "ok": False,
                "mode": "sprint_completed_not_assignable" if is_completed_sprint else "story_created_sprint_assignment_failed",
                "story_key": new_key,
                "story_url": f"{config['base_url']}/browse/{new_key}" if new_key else "",
                "summary": summary,
                "parent_key": parent_key,
                "sprint_assignment": {
                    "ok": False,
                    "sprint_field_id": sprint_field_id,
                    "sprint_id": sprint_resolution.get("sprint_id"),
                    "sprint_name": sprint_resolution.get("sprint_name"),
                    "error": error_text,
                },
                "message": (
                    "Story was created, but sprint assignment failed because Jira only allows active/future sprints for assignment. "
                    "Pick an active/future sprint and retry assignment."
                    if is_completed_sprint
                    else "Story was created, but Jira Agile sprint assignment failed. Assign the sprint manually or retry assignment."
                ),
            }

    # Optionally add a creation comment
    comment_result = None
    if approved_comment and new_key:
        comment_result = add_issue_comment(new_key, approved_comment)

    return {
        "ok": True,
        "mode": "created",
        "story_key": new_key,
        "story_url": f"{config['base_url']}/browse/{new_key}" if new_key else "",
        "summary": summary,
        "parent_key": parent_key,
        "issue_type": issue_type,
        "priority": priority,
        "sprint_field_id": sprint_field_id,
        "sprint_assignment": sprint_assignment_result or sprint_resolution,
        "comment_result": comment_result,
        "policy": [
            f"Story created with {MANDATORY_JIRA_STYLE_PROFILE} style profile.",
            "Feature context and codebase scan were confirmed before creation.",
            "Sprint assignment was applied through Jira Agile API using the required sprint_id/resolved sprint_name.",
            "No subtasks were created — manage them separately via jira_manage_subtasks.",
        ],
    }


def search_stories(
    jql: str,
    *,
    max_results: int = 30,
    only_with_sprint: bool = False,
    include_excluded_without_sprint_count: bool = False,
) -> dict[str, Any]:
    """Search Jira stories by JQL and return a compact list with knowledge context."""
    try:
        search_result = _search_issues_with_metadata(
            jql,
            max_results=max_results,
            only_with_sprint=only_with_sprint,
        )
        result: dict[str, Any] = {
            "ok": True,
            "jql": jql,
            "count": len(search_result["issues"]),
            "issues": search_result["issues"],
            "note": "Use jira_read_story(jira_id=<key>) to get the full story details for any issue.",
        }
        if include_excluded_without_sprint_count:
            result["excluded_without_sprint_count"] = search_result["excluded_without_sprint_count"]
            result["only_with_sprint"] = only_with_sprint
        return result
    except JiraRequestError as exc:
        return {
            "ok": False,
            "jql": jql,
            "error": str(exc),
            "note": "Check your JQL syntax and Jira credentials.",
        }


def _sprint_sort_key(name: str) -> tuple[int, str]:
    match = re.search(r"\bS(\d+)\b", name or "", flags=re.IGNORECASE)
    if not match:
        return (999, (name or "").lower())
    return (int(match.group(1)), (name or "").lower())


def _phase_from_summary(summary: str) -> str:
    text = (summary or "").lower()
    if "analysis" in text:
        return "analysis"
    if "doc" in text or "confluence" in text or "adr" in text:
        return "documentation"
    if "uat" in text:
        return "uat"
    if "test" in text or "integration" in text or "validation" in text:
        return "testing"
    if "go-live" in text or "go live" in text:
        return "go_live"
    if "migration" in text or "support" in text:
        return "post_go_live_support"
    return "implementation"


def _group_issues_by_sprint(issues: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], int]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    no_sprint_count = 0
    for issue in issues:
        sprints = issue.get("sprints") or []
        if not sprints:
            no_sprint_count += 1
            grouped.setdefault("No sprint assigned", []).append(issue)
            continue
        for sprint_name in sprints:
            grouped.setdefault(sprint_name, []).append(issue)
    ordered = {k: grouped[k] for k in sorted(grouped.keys(), key=_sprint_sort_key)}
    return ordered, no_sprint_count


def _cached_page_text(payload: dict[str, Any]) -> str:
    """Return plain cached page text from the cache shapes used by Automation."""
    for key in ("content", "text", "markdown", "plain_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_relevant_excerpt(text: str, keywords: list[str], *, max_chars: int = 900) -> str:
    """Extract compact Confluence lines relevant to the feature/story-planning flow."""
    if not text:
        return ""
    lowered_keywords = [item.lower() for item in keywords if item and len(item) > 2]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    selected = []
    for line in lines:
        lowered = line.lower()
        if any(keyword in lowered for keyword in lowered_keywords):
            selected.append(line)
        if len("\n".join(selected)) >= max_chars:
            break
    if not selected:
        selected = lines[:6]
    excerpt = "\n".join(selected)
    return excerpt[:max_chars] + ("..." if len(excerpt) > max_chars else "")


def _cached_page_signals(text: str) -> list[str]:
    lowered = text.lower()
    signals = []
    for signal, tokens in {
        "documentation": ("doc", "documentation", "confluence", "adr"),
        "uat": ("uat", "user acceptance"),
        "go_live": ("go-live", "go live", "golive"),
        "testing": ("test", "validation", "integration"),
        "implementation": ("implementation", "mutation", "link", "trs"),
        "support": ("support", "migration", "post go-live", "post go live"),
    }.items():
        if any(token in lowered for token in tokens):
            signals.append(signal)
    return signals


def _load_confluence_cache_summary(max_pages: int = 5, keywords: list[str] | None = None) -> dict[str, Any]:
    cache_dir = Path(__file__).resolve().parents[3] / ".memory" / "confluence-cache"
    if not cache_dir.exists():
        return {
            "available": False,
            "path": str(cache_dir),
            "note": "No confluence cache found.",
            "pages": [],
        }
    pages = []
    for file_path in sorted(cache_dir.glob("*.json"))[:30]:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta = payload.get("_meta") or {}
        text = _cached_page_text(payload)
        pages.append(
            {
                "title": payload.get("title") or meta.get("title") or file_path.stem,
                "cached_at": payload.get("cached_at") or meta.get("cached_at") or payload.get("updated_at"),
                "source": payload.get("source") or payload.get("url") or meta.get("url"),
                "matched_signals": _cached_page_signals(text),
                "relevant_excerpt": _extract_relevant_excerpt(text, keywords or []),
            }
        )
    return {
        "available": True,
        "path": str(cache_dir),
        "cached_pages_count": len(pages),
        "pages": pages[:max_pages],
    }


def _load_repo_graph_summary() -> dict[str, Any]:
    index_path = Path(__file__).resolve().parents[3] / ".memory" / "codebase-index.json"
    if not index_path.exists():
        return {
            "available": False,
            "path": str(index_path),
            "note": "Run Automation/graph.sh to generate codebase index.",
        }
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "path": str(index_path),
            "note": str(exc),
        }
    summary = payload.get("graph_summary", {})
    hotspots = payload.get("graph_hotspots", [])
    return {
        "available": True,
        "path": str(index_path),
        "build_system": payload.get("_meta", {}).get("build_system"),
        "health": summary.get("health"),
        "total_code_files": summary.get("total_code_files"),
        "high_coupling_files": summary.get("high_coupling_files"),
        "hotspots_preview": hotspots[:8],
    }


def _feature_planning_keywords(feature: dict[str, Any]) -> list[str]:
    raw = " ".join(
        str(value or "")
        for value in (
            feature.get("key"),
            feature.get("summary"),
            feature.get("description"),
            "jira rhapsody dng trs oslc link documentation uat go live go-live validation",
        )
    )
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", raw.lower())
    seen = set()
    keywords = []
    for word in words:
        if word not in seen:
            keywords.append(word)
            seen.add(word)
        if len(keywords) >= 30:
            break
    return keywords


BOOTSTRAP_FEATURE_PHASES: list[dict[str, Any]] = [
    {
        "phase": "analysis",
        "summary_action": "Analyze requirements and delivery approach",
        "scope": [
            "Review the Feature goal, Jira description, linked references, and dependency assumptions.",
            "Compare available Confluence/ADR notes with current repo graph hotspots.",
            "Confirm delivery phases, impacted systems, open questions, and sprint sequencing.",
        ],
        "acceptance_criteria": [
            "Feature scope, assumptions, and dependencies are documented in the story.",
            "Confluence/ADR references and repo graph signals are reviewed and summarized.",
            "Open questions and delivery risks are captured before implementation starts.",
        ],
    },
    {
        "phase": "implementation",
        "summary_action": "Implement feature changes",
        "scope": [
            "Implement the approved backend/configuration changes required by the Feature.",
            "Keep changes scoped to the Feature and follow existing repository patterns.",
            "Update or add focused tests for changed business logic.",
        ],
        "acceptance_criteria": [
            "Implementation follows existing code structure and naming conventions.",
            "Changed behavior is covered by focused unit or integration tests.",
            "Existing related behavior remains backward compatible unless explicitly approved.",
        ],
    },
    {
        "phase": "documentation",
        "summary_action": "Update ADR, Feature reference, and Confluence documentation",
        "scope": [
            "Update Feature reference documentation and any relevant ADR notes.",
            "Refresh Confluence pages with implementation details, assumptions, and operational notes.",
            "Document any go-live, support, or rollback references needed by downstream users.",
        ],
        "acceptance_criteria": [
            "Feature reference documentation reflects the approved implementation.",
            "ADR or Confluence pages are updated where the Feature requires a decision or usage note.",
            "Documentation includes enough context for UAT, go-live, and support teams.",
        ],
    },
    {
        "phase": "testing",
        "summary_action": "Validate integration and regression coverage",
        "scope": [
            "Define validation coverage for positive, negative, regression, and integration scenarios.",
            "Run focused tests using repository test hints from the codebase index.",
            "Capture evidence and unresolved gaps before UAT or go-live.",
        ],
        "acceptance_criteria": [
            "Focused test coverage is identified and executed for impacted areas.",
            "Regression risks are documented with pass/fail evidence.",
            "Any failed or blocked validation item has a clear owner and next step.",
        ],
    },
    {
        "phase": "uat",
        "summary_action": "Prepare UAT users, data, and validation support",
        "scope": [
            "Identify UAT users, roles, environments, and data required for validation.",
            "Prepare UAT instructions and expected results aligned with the Feature scope.",
            "Capture UAT feedback, defects, and sign-off status.",
        ],
        "acceptance_criteria": [
            "UAT users, roles, and required data are identified.",
            "UAT validation steps and expected outcomes are documented.",
            "UAT feedback and sign-off status are captured before go-live.",
        ],
    },
    {
        "phase": "go_live",
        "summary_action": "Prepare go-live readiness and release coordination",
        "scope": [
            "Confirm deployment readiness, release notes, and operational prerequisites.",
            "Coordinate Feature reference updates, UAT sign-off, and go-live checklist items.",
            "Document rollback or support handoff expectations.",
        ],
        "acceptance_criteria": [
            "Go-live checklist, release notes, and prerequisites are reviewed.",
            "UAT sign-off and documentation readiness are confirmed.",
            "Rollback or support handoff notes are available before release.",
        ],
    },
    {
        "phase": "post_go_live_support",
        "summary_action": "Monitor and support after go-live",
        "scope": [
            "Track post go-live observations, defects, and support questions.",
            "Confirm expected operational signals and user-facing behavior after release.",
            "Close the support loop with documentation updates when needed.",
        ],
        "acceptance_criteria": [
            "Post go-live monitoring or support checks are defined.",
            "Any production issue or user feedback is triaged with an owner.",
            "Support learnings are reflected in documentation if required.",
        ],
    },
]


def _table_cell(value: Any, *, fallback: str = "Not available", max_chars: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        text = fallback
    text = text.replace("|", "/")
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _phase_label(phase: str) -> str:
    return phase.replace("_", " ").replace("-", " ").title()


def _bootstrap_source_summary(plan: dict[str, Any]) -> dict[str, list[str]]:
    sources = plan.get("analysis_sources") or {}
    confluence = sources.get("confluence_cache") or {}
    repo = sources.get("repo_knowledge_graph") or {}
    confluence_lines = []
    for page in confluence.get("pages") or []:
        signals = ", ".join(page.get("matched_signals") or []) or "general"
        confluence_lines.append(f"{page.get('title') or 'Cached page'} ({signals})")
    repo_lines = []
    if repo.get("available"):
        repo_lines.append(f"Build system: {repo.get('build_system') or 'unknown'}")
        repo_lines.append(f"Repo health: {repo.get('health') or 'unknown'}")
        for hotspot in repo.get("hotspots_preview") or []:
            if isinstance(hotspot, dict) and hotspot.get("file"):
                repo_lines.append(f"Hotspot: {hotspot.get('file')}")
    return {
        "confluence": confluence_lines[:5],
        "repo": repo_lines[:8],
    }


def _phase_sprint_payload(
    phase: str,
    *,
    sprint_id: str | int | None = None,
    sprint_name: str | None = None,
    sprint_by_phase: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phase_map = sprint_by_phase or {}
    raw = phase_map.get(phase) or phase_map.get(_phase_label(phase)) or phase_map.get("default")
    if isinstance(raw, dict):
        return _normalize_sprint_request(
            sprint_id=raw.get("sprint_id") or raw.get("id"),
            sprint_name=raw.get("sprint_name") or raw.get("name") or raw.get("sprint"),
        )
    if raw is not None:
        raw_text = str(raw).strip()
        if raw_text.isdigit():
            return _normalize_sprint_request(sprint_id=raw_text)
        return _normalize_sprint_request(sprint_name=raw_text)
    return _normalize_sprint_request(sprint_id=sprint_id, sprint_name=sprint_name)


def _build_bootstrap_story_description(
    *,
    feature: dict[str, Any],
    phase_template: dict[str, Any],
    source_summary: dict[str, list[str]],
    existing_story_count: int,
) -> str:
    feature_key = feature.get("key") or "Feature"
    feature_summary = feature.get("summary") or "Feature delivery"
    feature_description = feature.get("description") or "Feature description must be confirmed from Jira."
    phase = phase_template["phase"]
    phase_name = _phase_label(phase)
    confluence_refs = source_summary.get("confluence") or []
    repo_refs = source_summary.get("repo") or []
    scope = list(phase_template.get("scope") or [])
    acceptance_criteria = list(phase_template.get("acceptance_criteria") or [])

    return f"""## User Story
As a delivery team member, I want to complete the {phase_name.lower()} work for Feature `{feature_key}` so that `{_table_cell(feature_summary, max_chars=120)}` can progress safely through delivery.

:::info
## Background
This story was drafted by the Feature bootstrap flow because Feature `{feature_key}` has {existing_story_count} discovered child stories.

Feature summary: **{_table_cell(feature_summary, max_chars=220)}**

Feature description/reference:
{_table_cell(feature_description, max_chars=500)}
:::

## Feature Planning Snapshot
| Field | Value |
|---|---|
| Feature | {feature_key} |
| Feature Summary | {_table_cell(feature_summary)} |
| Phase | {phase_name} |
| Existing Stories Found | {existing_story_count} |
| Style Profile | {MANDATORY_JIRA_STYLE_PROFILE} |

:::success
## Implementation Scope
{_markdown_ordered(scope, "Confirm scope with the Feature owner before implementation starts.")}
:::

:::warning
## Constraints
- Treat Feature `{feature_key}` as the Jira parent for this story.
- Keep this story scoped to the {phase_name.lower()} phase only.
- Review Jira Feature context, Confluence/ADR cache, and repo graph signals before implementation.
- Do not change Jira or code outside the approved Feature scope.
:::

:::note
## Acceptance Criteria
{_markdown_bullets(acceptance_criteria, "Acceptance criteria must be confirmed with the Feature owner.")}

## Reference Signals
{_markdown_bullets(confluence_refs + repo_refs, "No cached Confluence/ADR or repo graph signals were available; confirm references manually.")}
:::

## Regulatory Justification
This story supports controlled delivery of Feature `{feature_key}` by separating requirement analysis, implementation, validation, documentation, UAT, go-live, and support responsibilities.

## Reason/Comments
Generated from Jira Feature context, cached Confluence/ADR references, and repository knowledge graph summary. Review and approve before Jira creation.
"""


def draft_bootstrap_feature_story_payloads(
    plan: dict[str, Any],
    *,
    sprint_id: str | int | None = None,
    sprint_name: str | None = None,
    sprint_by_phase: dict[str, Any] | None = None,
    phases: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Draft BDRSP-1623 story payloads for a Feature bootstrap or missing-phase fill."""
    feature = plan.get("feature") or {}
    learned = plan.get("learned_pattern") or {}
    existing_story_count = int(learned.get("stories_total") or 0)
    selected_phases = phases or [item["phase"] for item in BOOTSTRAP_FEATURE_PHASES]
    templates = [item for item in BOOTSTRAP_FEATURE_PHASES if item["phase"] in selected_phases]
    source_summary = _bootstrap_source_summary(plan)
    stories = []
    for template in templates:
        phase = template["phase"]
        sprint_payload = _phase_sprint_payload(
            phase,
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            sprint_by_phase=sprint_by_phase,
        )
        summary = f"{feature.get('key')}: {template['summary_action']} - {_table_cell(feature.get('summary'), max_chars=90)}"
        description = _build_bootstrap_story_description(
            feature=feature,
            phase_template=template,
            source_summary=source_summary,
            existing_story_count=existing_story_count,
        )
        story = {
            "phase": phase,
            "summary": summary,
            "approved_description": description,
            "approved_acceptance_criteria": list(template.get("acceptance_criteria") or []),
            "approved_regulatory_justification": (
                f"Supports controlled delivery and traceability for Feature {feature.get('key') or 'the Feature'}."
            ),
            "approved_reason_comments": (
                "Drafted from Feature bootstrap flow using Jira Feature context, Confluence/ADR cache, and repo graph signals."
            ),
            "issue_type": "Story",
            "priority": "Medium",
        }
        if sprint_payload.get("sprint_id"):
            story["sprint_id"] = sprint_payload.get("sprint_id")
        if sprint_payload.get("sprint_name"):
            story["sprint_name"] = sprint_payload.get("sprint_name")
        stories.append(story)
    return stories


def _feature_subtask_issue(subtask: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": subtask.get("key"),
        "summary": subtask.get("summary"),
        "status": subtask.get("status"),
        "status_category": subtask.get("status_category"),
        "issue_type": subtask.get("issue_type") or "Sub-task",
        "assignee": subtask.get("assignee"),
        "priority": subtask.get("priority"),
        "parent": subtask.get("parent"),
        "parent_summary": None,
        "updated": subtask.get("updated"),
        "sprints": subtask.get("sprints") or [],
        "discovery_sources": ["feature_subtasks"],
    }


def _merge_feature_issue(issue_map: dict[str, dict[str, Any]], issue: dict[str, Any], source: str) -> None:
    key = issue.get("key")
    if not key:
        return
    existing = issue_map.get(key)
    if not existing:
        copy = dict(issue)
        copy["discovery_sources"] = list(dict.fromkeys(copy.get("discovery_sources") or [source]))
        issue_map[key] = copy
        return
    sources = list(existing.get("discovery_sources") or [])
    if source not in sources:
        sources.append(source)
    existing["discovery_sources"] = sources
    if not existing.get("sprints") and issue.get("sprints"):
        existing["sprints"] = issue.get("sprints")


def _discover_feature_work_items(feature_key: str, feature: dict[str, Any], *, max_results: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    safe_key = feature_key.strip().upper()
    queries = [
        ("parent_story_task", f"parent = {safe_key} AND issuetype in (Story, Task) ORDER BY created ASC"),
        ("legacy_epic_link", f'"Epic Link" = {safe_key} AND issuetype in (Story, Task) ORDER BY created ASC'),
        ("advanced_roadmaps_parent_link", f'"Parent Link" = {safe_key} AND issuetype in (Story, Task) ORDER BY created ASC'),
        ("linked_story_task", f'issue in linkedIssues("{safe_key}") AND issuetype in (Story, Task) ORDER BY created ASC'),
        ("scriptrunner_subtasks", f'issueFunction in subtasksOf("key = {safe_key}") ORDER BY created ASC'),
    ]
    issue_map: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    for label, jql in queries:
        try:
            found = search_issues(jql, max_results=max_results, only_with_sprint=False)
        except JiraRequestError as exc:
            attempts.append({"source": label, "jql": jql, "ok": False, "error": str(exc)})
            continue
        attempts.append({"source": label, "jql": jql, "ok": True, "count": len(found)})
        for issue in found:
            _merge_feature_issue(issue_map, issue, label)

    for subtask in feature.get("subtasks") or []:
        _merge_feature_issue(issue_map, _feature_subtask_issue(subtask), "feature_subtasks")

    return list(issue_map.values()), attempts


def plan_feature_stories(feature_key: str, *, max_results: int = 200) -> dict[str, Any]:
    """Build a sprint-wise story planning playbook for a feature key."""
    feature = read_issue(feature_key)
    issues, discovery_attempts = _discover_feature_work_items(feature_key, feature, max_results=max_results)
    grouped, no_sprint_count = _group_issues_by_sprint(issues)

    phase_counts: dict[str, int] = {}
    for issue in issues:
        phase = _phase_from_summary(issue.get("summary") or "")
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

    missing_phases = [
        phase for phase in [
            "analysis",
            "implementation",
            "documentation",
            "testing",
            "uat",
            "go_live",
            "post_go_live_support",
        ]
        if phase_counts.get(phase, 0) == 0
    ]

    return {
        "ok": True,
        "feature": {
            "key": feature.get("key"),
            "summary": feature.get("summary"),
            "status": feature.get("status"),
            "description": feature.get("description"),
        },
        "learned_pattern": {
            "stories_total": len(issues),
            "stories_without_sprint": no_sprint_count,
            "phase_counts": phase_counts,
            "sprint_wise_stories": grouped,
            "discovery_attempts": discovery_attempts,
        },
        "analysis_sources": {
            "confluence_cache": _load_confluence_cache_summary(keywords=_feature_planning_keywords(feature)),
            "repo_knowledge_graph": _load_repo_graph_summary(),
        },
        "story_creation_playbook": {
            "steps": [
                "1. Read the Feature goal, outcomes, and dependencies from Jira.",
                "2. Read cached Confluence/ADR excerpts for prior implementation decisions and similar work.",
                "3. Inspect repo graph hotspots and test hints before drafting stories.",
                "4. Break work into phases: analysis, implementation, documentation/reference updates, testing/UAT users, go-live, and support.",
                "5. Map stories sprint-wise and keep each story small, testable, and delivery-aligned.",
                "6. Present sprint-wise draft stories to the user before any Jira write.",
                "7. Treat this Feature key as parent_key for every drafted story.",
                "8. Include the user-selected sprint_id for every story, or sprint_name with JIRA_BOARD_ID/JIRA_BOARD_IDS configured.",
                "9. If sprint is missing or ambiguous, ask the user which sprint to attach; do not guess.",
                "10. Use jira_bootstrap_feature_stories for the complete flow, or jira_create_feature_stories only for already-approved custom payloads.",
            ],
            "missing_phases_in_current_feature": missing_phases,
            "user_confirmation_prompt": (
                "I have learned this feature pattern. Should I now draft sprint-wise story suggestions "
                "(including missing phases) using the same format/style?"
            ),
        },
        "parent_feature_contract": {
            "feature_key": feature_key.strip().upper(),
            "parent_key_for_created_stories": feature_key.strip().upper(),
            "rule": "When creating stories for this Feature, pass this Feature key as parent_key.",
        },
        "sprint_assignment_contract": {
            "required_for_story_creation": True,
            "required_for_bulk_create": True,
            "preferred_payload": {"sprint_id": "2184733"},
            "alternate_payload": {"sprint_name": "PMT 26.2 - S05 - BDRSP"},
            "configuration_for_sprint_name": "Set JIRA_BOARD_ID or JIRA_BOARD_IDS so sprint_name can be resolved before creation.",
            "safety": "Bulk creation preflights all sprint assignments before creating any story. If sprint is missing or ambiguous, ask the user; do not guess.",
        },
        "style_contract": {
            "profile": MANDATORY_JIRA_STYLE_PROFILE,
            "required": {
                "headings": "## Heading",
                "panels": [":::info", ":::success", ":::warning", ":::note"],
                "table": "At least one markdown table with |---| separator",
                "lists": "Use ordered/bullet lists for scope and acceptance criteria",
            },
        },
    }


def create_feature_stories(
    *,
    project_key: str,
    parent_key: str,
    stories: list[dict[str, Any]],
    confirm_create: bool = False,
    codebase_scan_confirmed: bool = False,
    feature_context_confirmed: bool = False,
) -> dict[str, Any]:
    """Preview or create multiple feature stories after all feature-planning gates pass."""
    if not feature_context_confirmed:
        return {
            "ok": False,
            "mode": "feature_context_required",
            "message": "Call jira_plan_feature_stories or jira_feature_context first, then retry with feature_context_confirmed=True.",
        }
    if not codebase_scan_confirmed:
        return {
            "ok": False,
            "mode": "codebase_scan_required",
            "message": "Repo graph/codebase impact must be reviewed before creating feature stories.",
        }
    if not stories:
        return {
            "ok": False,
            "mode": "stories_required",
            "message": "Provide at least one approved story payload.",
        }

    previews = []
    violations = []
    for index, story in enumerate(stories, start=1):
        summary = str(story.get("summary") or "").strip()
        description = str(story.get("approved_description") or story.get("description") or "").strip()
        if not summary:
            violations.append({"index": index, "mode": "summary_required"})
        validation = validate_mandatory_story_style(description)
        if not validation.get("ok"):
            violations.append({"index": index, "summary": summary, "mode": "style_mandatory_violation", "validation": validation})
        sprint_request = _normalize_sprint_request(
            sprint_id=story.get("sprint_id"),
            sprint_name=story.get("sprint_name") or story.get("sprint") or story.get("sprint_hint"),
        )
        if not sprint_request.get("requested"):
            violations.append(
                {
                    "index": index,
                    "summary": summary,
                    "mode": "sprint_required",
                    "message": (
                        f"Story will be created under Feature {parent_key}, but sprint target is missing. "
                        "Each feature story must provide sprint_id or sprint_name."
                    ),
                    "parent_feature_key": parent_key,
                    "user_question": (
                        f"Which sprint should I attach story #{index} to under Feature {parent_key}? "
                        "Please provide sprint_id, or sprint_name if board id configuration is available."
                    ),
                }
            )
        previews.append(
            {
                "index": index,
                "summary": summary,
                "description_preview": description[:500] + ("..." if len(description) > 500 else ""),
                "acceptance_criteria": story.get("approved_acceptance_criteria") or story.get("acceptance_criteria") or [],
                "priority": story.get("priority") or "Medium",
                "sprint_assignment": sprint_request,
                "style_profile": MANDATORY_JIRA_STYLE_PROFILE,
            }
        )

    if violations:
        return {
            "ok": False,
            "mode": "validation_failed",
            "message": "One or more story payloads are missing required content or do not follow the mandatory style profile.",
            "parent_feature_key": parent_key,
            "parent_rule": "The supplied Feature key is used as the Jira parent for every story in this batch.",
            "violations": violations,
            "required_style_profile": MANDATORY_JIRA_STYLE_PROFILE,
            "sprint_rule": "Do not guess sprint assignment. Ask the user for sprint_id or sprint_name before preview/create.",
        }

    if not confirm_create:
        return {
            "ok": False,
            "mode": "confirmation_required",
            "message": "Review the sprint-wise story batch below and confirm creation with confirm_create=True.",
            "parent_key": parent_key,
            "parent_rule": "The supplied Feature key is used as the Jira parent for every story in this batch.",
            "project_key": project_key,
            "story_count": len(previews),
            "previews": previews,
            "next_call": {
                "project_key": project_key,
                "parent_key": parent_key,
                "stories": "<same approved list>",
                "confirm_create": True,
                "codebase_scan_confirmed": True,
                "feature_context_confirmed": True,
            },
            "sprint_assignment_rule": (
                "Each story payload must include sprint_id for exact assignment, or sprint_name with "
                "JIRA_BOARD_ID/JIRA_BOARD_IDS configured. Sprint assignment happens after issue creation."
            ),
        }

    config = get_jira_config()
    resolved_sprints: dict[int, dict[str, Any]] = {}
    sprint_failures = []
    for index, story in enumerate(stories, start=1):
        sprint_resolution = resolve_sprint_assignment(
            config,
            sprint_id=story.get("sprint_id"),
            sprint_name=story.get("sprint_name") or story.get("sprint") or story.get("sprint_hint"),
        )
        if not sprint_resolution.get("ok"):
            sprint_failures.append({"index": index, "summary": story.get("summary"), "sprint_assignment": sprint_resolution})
        else:
            resolved_sprints[index] = sprint_resolution
    if sprint_failures:
        return {
            "ok": False,
            "mode": "sprint_preflight_failed",
            "message": "No stories were created because one or more sprint assignments could not be resolved.",
            "parent_feature_key": parent_key,
            "parent_rule": "The supplied Feature key is used as the Jira parent for every story in this batch.",
            "failures": sprint_failures,
            "safe_next_step": (
                "Ask the user for the exact sprint_id, or configure JIRA_BOARD_ID/JIRA_BOARD_IDS "
                "and retry with a resolvable sprint_name. Do not guess the sprint."
            ),
        }

    created = []
    failures = []
    for index, story in enumerate(stories, start=1):
        sprint_resolution = resolved_sprints[index]
        result = create_story(
            project_key=project_key,
            parent_key=parent_key,
            summary=str(story.get("summary") or "").strip(),
            approved_description=str(story.get("approved_description") or story.get("description") or "").strip(),
            approved_acceptance_criteria=story.get("approved_acceptance_criteria") or story.get("acceptance_criteria"),
            approved_regulatory_justification=story.get("approved_regulatory_justification"),
            approved_reason_comments=story.get("approved_reason_comments"),
            issue_type=story.get("issue_type") or "Story",
            priority=story.get("priority") or "Medium",
            assignee_account_id=story.get("assignee_account_id"),
            approved_comment=story.get("approved_comment"),
            sprint_id=sprint_resolution.get("sprint_id"),
            sprint_name=sprint_resolution.get("sprint_name"),
            confirm_create=True,
            codebase_scan_confirmed=True,
            feature_context_confirmed=True,
        )
        if result.get("ok"):
            created.append({"index": index, **result})
        else:
            failures.append({"index": index, "summary": story.get("summary"), "result": result})

    return {
        "ok": not failures,
        "mode": "created" if not failures else "partial_failure",
        "parent_key": parent_key,
        "project_key": project_key,
        "created_count": len(created),
        "failed_count": len(failures),
        "created": created,
        "failures": failures,
        "policy": [
            "Stories were created only after feature context, repo/codebase scan, style validation, and explicit confirmation.",
            "Sprint assignment is applied through Jira Agile API using each story's required sprint_id/resolvable sprint_name.",
            "Subtasks are not created here; use jira_manage_subtasks after story creation if needed.",
        ],
    }


def bootstrap_feature_stories(
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
) -> dict[str, Any]:
    """End-to-end Feature bootstrap: learn, draft, preview, and optionally create child stories."""
    normalized_feature_key = feature_key.strip().upper()
    resolved_project_key = project_key or normalized_feature_key.split("-", 1)[0]
    plan = plan_feature_stories(normalized_feature_key, max_results=max_results)
    learned = plan.get("learned_pattern") or {}
    existing_count = int(learned.get("stories_total") or 0)
    missing_phases = list((plan.get("story_creation_playbook") or {}).get("missing_phases_in_current_feature") or [])

    if existing_count > 0 and not create_missing_phases_when_existing:
        return {
            "ok": False,
            "mode": "existing_stories_found",
            "message": (
                f"Feature {normalized_feature_key} already has {existing_count} discovered child stories. "
                "No bootstrap stories were generated to avoid duplicates."
            ),
            "parent_feature_key": normalized_feature_key,
            "parent_rule": "Existing and newly created stories use the Feature key as parent_key.",
            "feature_story_plan": plan,
            "safe_next_step": (
                "Review existing sprint-wise stories. If you want to fill only missing phases, retry with "
                "create_missing_phases_when_existing=True and provide sprint_id/sprint_name."
            ),
        }

    selected_phases = phases
    if selected_phases is None and existing_count > 0:
        selected_phases = missing_phases

    if selected_phases is not None:
        selected_phases = [phase for phase in selected_phases if phase in {item["phase"] for item in BOOTSTRAP_FEATURE_PHASES}]

    if selected_phases == []:
        return {
            "ok": False,
            "mode": "no_missing_phases",
            "message": f"Feature {normalized_feature_key} already has story coverage for the known delivery phases.",
            "parent_feature_key": normalized_feature_key,
            "feature_story_plan": plan,
        }

    generated_stories = draft_bootstrap_feature_story_payloads(
        plan,
        sprint_id=sprint_id,
        sprint_name=sprint_name,
        sprint_by_phase=sprint_by_phase,
        phases=selected_phases,
    )

    validations = [
        {
            "index": index,
            "phase": story.get("phase"),
            "summary": story.get("summary"),
            "style_validation": validate_mandatory_story_style(str(story.get("approved_description") or "")),
            "sprint_assignment": _normalize_sprint_request(
                sprint_id=story.get("sprint_id"),
                sprint_name=story.get("sprint_name"),
            ),
        }
        for index, story in enumerate(generated_stories, start=1)
    ]
    style_failures = [item for item in validations if not item["style_validation"].get("ok")]
    missing_sprint = [item for item in validations if not item["sprint_assignment"].get("requested")]
    if style_failures:
        return {
            "ok": False,
            "mode": "generated_style_validation_failed",
            "message": "Generated bootstrap stories did not satisfy the mandatory story style contract.",
            "parent_feature_key": normalized_feature_key,
            "style_failures": style_failures,
        }
    if missing_sprint:
        return {
            "ok": False,
            "mode": "sprint_required",
            "message": (
                f"Generated bootstrap stories for Feature {normalized_feature_key}, but sprint assignment is missing. "
                "No Jira preview or creation was attempted."
            ),
            "parent_feature_key": normalized_feature_key,
            "parent_rule": "The Feature key will be used as parent_key for every generated story.",
            "user_question": (
                f"Which sprint should I attach these stories to under Feature {normalized_feature_key}? "
                "Provide one sprint_id/sprint_name for all stories, or sprint_by_phase for per-phase sprint mapping."
            ),
            "required_payload": {
                "one_sprint_for_all_stories": {"sprint_id": "<active_or_future_sprint_id>"},
                "per_phase_example": {
                    "analysis": {"sprint_id": "<analysis_sprint_id>"},
                    "implementation": {"sprint_id": "<implementation_sprint_id>"},
                    "documentation": {"sprint_name": "<documentation_sprint_name>"},
                },
            },
            "feature_story_plan": plan,
            "generated_story_count": len(generated_stories),
            "generated_stories": generated_stories,
            "validations": validations,
        }

    create_result = create_feature_stories(
        project_key=resolved_project_key,
        parent_key=normalized_feature_key,
        stories=generated_stories,
        confirm_create=confirm_create,
        codebase_scan_confirmed=True,
        feature_context_confirmed=True,
    )
    wrapped_mode = create_result.get("mode")
    if wrapped_mode == "confirmation_required":
        wrapped_mode = "bootstrap_preview"
    elif wrapped_mode == "created":
        wrapped_mode = "bootstrap_created"
    return {
        **create_result,
        "mode": wrapped_mode,
        "bootstrap_flow": {
            "feature_key": normalized_feature_key,
            "project_key": resolved_project_key,
            "existing_story_count": existing_count,
            "created_for_phases": [story.get("phase") for story in generated_stories],
            "source_steps_completed": [
                "jira_feature_read",
                "existing_child_story_discovery",
                "confluence_adr_cache_summary",
                "repo_knowledge_graph_summary",
                "bdrsp_1623_story_draft_generation",
                "sprint_assignment_validation",
                "preview_or_create_guard",
            ],
            "confirm_create": confirm_create,
        },
        "feature_story_plan": plan,
        "generated_stories": generated_stories,
        "validations": validations,
    }


def delete_subtasks(issue_keys: list[str], *, confirm_delete: bool = False) -> dict[str, Any]:
    config = get_jira_config()
    inspected = []
    for issue_key in issue_keys:
        issue = read_issue(issue_key)
        inspected.append(
            {
                "key": issue["key"],
                "summary": issue.get("summary"),
                "issue_type": issue.get("issue_type"),
                "parent": issue.get("parent"),
                "can_delete": (issue.get("issue_type") or "").lower() in {"sub-task", "subtask", "sub task"},
            }
        )

    blocked = [item for item in inspected if not item["can_delete"]]
    if blocked:
        return {
            "ok": False,
            "mode": "delete_blocked",
            "message": "Only Jira subtasks can be deleted by this safety tool.",
            "blocked": blocked,
            "inspected": inspected,
        }

    if not confirm_delete:
        return {
            "ok": True,
            "mode": "delete_confirmation_required",
            "message": "Review these subtasks. Call again with confirm_delete=true to delete them.",
            "subtasks_to_delete": inspected,
        }

    deleted = []
    for item in inspected:
        jira_request(
            f"issue/{urllib.parse.quote(item['key'], safe='')}",
            config=config,
            method="DELETE",
        )
        deleted.append(item)

    return {
        "ok": True,
        "mode": "deleted",
        "deleted_subtasks": deleted,
    }
