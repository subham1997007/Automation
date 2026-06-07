#!/usr/bin/env python3
"""Automation Analytics Report Generator.

Generates per-story HTML + Markdown reports capturing:
  - Story key, title, Feature/Epic
  - Time spent per DevFlow stage (AI vs Human effort)
  - Files changed
  - Tests run + pass/fail verdict
  - MR link
  - Automation decisions vs manual approvals

Output:
    Automation/reports/<story-id>/automation-report.html
    Automation/reports/<story-id>/automation-report.md

Usage (from dev_client.py):
    from report_generator import ReportGenerator
    rg = ReportGenerator(story_id="BDRSP-1413")
    rg.set_story(story_dict, feature_dict)
    rg.record_stage("start", elapsed_s=2.1, actor="ai")
    rg.set_test_result(autonomous_test_execution_dict)
    rg.set_mr(mr_url, mr_title)
    paths = rg.generate()
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _automation_dir() -> Path:
    try:
        from langchain_helpers.repo_resolver import get_resolver  # type: ignore[import]
        return get_resolver().automation_dir
    except Exception:
        return Path(__file__).resolve().parent.parent


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


_STATUS_ICON = {
    "passed": "✅",
    "failed": "❌",
    "skipped": "⏭",
    "pending": "⏳",
    "ai": "🤖",
    "human": "👤",
}


# ---------------------------------------------------------------------------
# ReportGenerator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Collect DevFlow execution data and render HTML + Markdown reports."""

    def __init__(self, story_id: str, automation_dir: Path | None = None) -> None:
        self.story_id = story_id.upper()
        self._auto_dir = automation_dir or _automation_dir()
        self._repo_name = self._auto_dir.parent.name
        self._report_dir = self._auto_dir / "reports" / self.story_id
        self._report_dir.mkdir(parents=True, exist_ok=True)

        self._story: dict[str, Any] = {}
        self._feature: dict[str, Any] = {}
        self._stages: list[dict[str, Any]] = []
        self._files_changed: list[str] = []
        self._test_result: dict[str, Any] = {}
        self._build_result: dict[str, Any] = {}
        self._review_result: dict[str, Any] = {}
        self._mr_url: str = ""
        self._mr_title: str = ""
        self._branch: str = ""
        self._generated_at: str = _now_iso()

    # ── Setters ───────────────────────────────────────────────────────────────

    def set_story(self, story: dict[str, Any], feature: dict[str, Any] | None = None) -> None:
        self._story = story or {}
        self._feature = feature or {}

    def record_stage(self, stage: str, elapsed_s: float = 0.0, actor: str = "ai",
                     status: str = "done", notes: str = "") -> None:
        self._stages.append({
            "stage": stage,
            "elapsed_s": elapsed_s,
            "actor": actor,
            "status": status,
            "notes": notes,
            "recorded_at": _now_iso(),
        })

    def set_files_changed(self, files: list[str]) -> None:
        self._files_changed = files or []

    def set_test_result(self, result: dict[str, Any] | None) -> None:
        self._test_result = result or {}

    def set_build_result(self, result: dict[str, Any] | None) -> None:
        self._build_result = result or {}

    def set_review_result(self, result: dict[str, Any] | None) -> None:
        self._review_result = result or {}

    def set_mr(self, mr_url: str, mr_title: str = "", branch: str = "") -> None:
        self._mr_url = mr_url or ""
        self._mr_title = mr_title or ""
        self._branch = branch or ""

    # ── Computed metrics ─────────────────────────────────────────────────────

    def _ai_total(self) -> float:
        return sum(s["elapsed_s"] for s in self._stages if s.get("actor") == "ai")

    def _human_total(self) -> float:
        return sum(s["elapsed_s"] for s in self._stages if s.get("actor") == "human")

    def _test_verdict(self) -> str:
        if not self._test_result:
            return "not_run"
        return self._test_result.get("verdict") or ("passed" if self._test_result.get("ok") else "failed")

    def _build_verdict(self) -> str:
        if not self._build_result:
            return "not_run"
        return "passed" if self._build_result.get("ok") else "failed"

    # ── Markdown report ───────────────────────────────────────────────────────

    def render_md(self) -> str:
        story_key = self._story.get("key") or self.story_id
        story_title = self._story.get("summary") or "—"
        feature_key = (self._feature.get("feature") or {}).get("key") or "—"
        feature_title = (self._feature.get("feature") or {}).get("summary") or "—"

        ai_t = _fmt_duration(self._ai_total())
        hu_t = _fmt_duration(self._human_total())
        tv = self._test_verdict()
        bv = self._build_verdict()

        lines: list[str] = [
            f"# 🤖 Automation Report — {story_key}",
            f"",
            f"> Generated: {self._generated_at}  ",
            f"> Branch: `{self._branch}`",
            f"",
            f"## 📋 Story",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Key | [{story_key}]({self._story.get('url') or '#'}) |",
            f"| Title | {story_title} |",
            f"| Feature/Epic | [{feature_key}] {feature_title} |",
            f"| Status | {self._story.get('status') or '—'} |",
            f"| Priority | {self._story.get('priority') or '—'} |",
            f"",
            f"## ⏱ Effort Breakdown",
            f"| Actor | Time |",
            f"|-------|------|",
            f"| 🤖 AI (automated) | {ai_t} |",
            f"| 👤 Human (reviews/approvals) | {hu_t} |",
            f"",
            f"## 🔄 Stage Timeline",
            f"| Stage | Actor | Duration | Status |",
            f"|-------|-------|----------|--------|",
        ]
        for s in self._stages:
            icon = _STATUS_ICON.get(s["actor"], "")
            lines.append(
                f"| {s['stage']} | {icon} {s['actor']} | {_fmt_duration(s['elapsed_s'])} | {s['status']} |"
            )

        lines += [
            f"",
            f"## 📁 Files Changed ({len(self._files_changed)})",
        ]
        for f in self._files_changed[:30]:
            lines.append(f"- `{f}`")
        if len(self._files_changed) > 30:
            lines.append(f"- _… and {len(self._files_changed) - 30} more_")

        lines += [
            f"",
            f"## 🧪 Validation",
            f"| Check | Result |",
            f"|-------|--------|",
            f"| Tests | {_STATUS_ICON.get(tv, '❓')} {tv} |",
            f"| Build | {_STATUS_ICON.get(bv, '❓')} {bv} |",
        ]

        if self._test_result.get("command_used"):
            lines.append(f"| Test Command | `{self._test_result['command_used']}` |")

        if self._mr_url:
            lines += [
                f"",
                f"## 🔗 Merge Request",
                f"[{self._mr_title or self._mr_url}]({self._mr_url})",
            ]

        lines += [
            f"",
            f"---",
            f"_Generated by DevFlow Automation — {self._repo_name}_",
        ]
        return "\n".join(lines)

    # ── HTML report ───────────────────────────────────────────────────────────

    def render_html(self) -> str:
        story_key = self._story.get("key") or self.story_id
        story_title = self._story.get("summary") or "—"
        feature_key = (self._feature.get("feature") or {}).get("key") or "—"
        feature_title = (self._feature.get("feature") or {}).get("summary") or "—"
        ai_t = _fmt_duration(self._ai_total())
        hu_t = _fmt_duration(self._human_total())
        tv = self._test_verdict()
        bv = self._build_verdict()
        test_icon = _STATUS_ICON.get(tv, "❓")
        build_icon = _STATUS_ICON.get(bv, "❓")
        mr_link = f'<a href="{self._mr_url}" target="_blank">{self._mr_title or self._mr_url}</a>' if self._mr_url else "—"

        stage_rows = ""
        for s in self._stages:
            actor_icon = _STATUS_ICON.get(s["actor"], "")
            cls = "ai" if s["actor"] == "ai" else "human"
            stage_rows += f"""
            <tr>
              <td>{s["stage"]}</td>
              <td class="{cls}">{actor_icon} {s["actor"]}</td>
              <td>{_fmt_duration(s["elapsed_s"])}</td>
              <td>{s["status"]}</td>
            </tr>"""

        file_items = "".join(f"<li><code>{f}</code></li>" for f in self._files_changed[:30])
        if len(self._files_changed) > 30:
            file_items += f"<li><em>… and {len(self._files_changed)-30} more</em></li>"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Automation Report — {story_key}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:#0d1117;color:#e6edf3;margin:0;padding:24px;}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px;margin-bottom:20px;}}
  h1{{font-size:1.6rem;margin:0 0 4px;}}
  h2{{font-size:1.1rem;color:#8b949e;border-bottom:1px solid #30363d;padding-bottom:8px;}}
  table{{width:100%;border-collapse:collapse;font-size:0.9rem;}}
  th{{text-align:left;padding:8px;background:#21262d;color:#8b949e;}}
  td{{padding:8px;border-top:1px solid #21262d;}}
  .ai{{color:#58a6ff;}}.human{{color:#f78166;}}
  .passed{{color:#3fb950;}}.failed{{color:#f85149;}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:.8rem;font-weight:600;}}
  .badge-pass{{background:#1a4731;color:#3fb950;}}
  .badge-fail{{background:#4a1a1a;color:#f85149;}}
  .badge-skip{{background:#2a2a1a;color:#d29922;}}
  .effort-box{{display:flex;gap:16px;}}
  .effort-item{{flex:1;background:#21262d;border-radius:6px;padding:16px;text-align:center;}}
  .effort-item .val{{font-size:1.8rem;font-weight:700;}}
  .effort-item .lbl{{font-size:.8rem;color:#8b949e;margin-top:4px;}}
  .file-list{{column-count:2;column-gap:20px;padding:0;list-style:none;}}
  .file-list li{{font-size:.82rem;padding:2px 0;color:#79c0ff;word-break:break-all;}}
  a{{color:#58a6ff;}}
  .meta{{color:#8b949e;font-size:.85rem;}}
</style>
</head>
<body>
<div class="card">
  <h1>🤖 Automation Report — {story_key}</h1>
  <p class="meta">Generated: {self._generated_at} &nbsp;|&nbsp; Branch: <code>{self._branch}</code></p>
</div>

<div class="card">
  <h2>📋 Story</h2>
  <table>
    <tr><th>Field</th><th>Value</th></tr>
    <tr><td>Key</td><td><a href="{self._story.get('url','#')}" target="_blank">{story_key}</a></td></tr>
    <tr><td>Title</td><td>{story_title}</td></tr>
    <tr><td>Feature / Epic</td><td>[{feature_key}] {feature_title}</td></tr>
    <tr><td>Status</td><td>{self._story.get('status','—')}</td></tr>
    <tr><td>Priority</td><td>{self._story.get('priority','—')}</td></tr>
  </table>
</div>

<div class="card">
  <h2>⏱ Effort Breakdown</h2>
  <div class="effort-box">
    <div class="effort-item">
      <div class="val ai">{ai_t}</div>
      <div class="lbl">🤖 AI Automated</div>
    </div>
    <div class="effort-item">
      <div class="val human">{hu_t}</div>
      <div class="lbl">👤 Human Reviews</div>
    </div>
    <div class="effort-item">
      <div class="val">{len(self._stages)}</div>
      <div class="lbl">DevFlow Stages</div>
    </div>
    <div class="effort-item">
      <div class="val">{len(self._files_changed)}</div>
      <div class="lbl">Files Changed</div>
    </div>
  </div>
</div>

<div class="card">
  <h2>🔄 Stage Timeline</h2>
  <table>
    <tr><th>Stage</th><th>Actor</th><th>Duration</th><th>Status</th></tr>
    {stage_rows}
  </table>
</div>

<div class="card">
  <h2>🧪 Validation</h2>
  <table>
    <tr><th>Check</th><th>Result</th></tr>
    <tr><td>Tests</td><td><span class="badge {'badge-pass' if tv=='passed' else 'badge-fail' if tv=='failed' else 'badge-skip'}">{test_icon} {tv}</span></td></tr>
    <tr><td>Build</td><td><span class="badge {'badge-pass' if bv=='passed' else 'badge-fail' if bv=='failed' else 'badge-skip'}">{build_icon} {bv}</span></td></tr>
    {"<tr><td>Test Command</td><td><code>" + str(self._test_result.get("command_used","")) + "</code></td></tr>" if self._test_result.get("command_used") else ""}
  </table>
</div>

<div class="card">
  <h2>📁 Files Changed ({len(self._files_changed)})</h2>
  <ul class="file-list">{file_items}</ul>
</div>

<div class="card">
  <h2>🔗 Merge Request</h2>
  <p>{mr_link}</p>
</div>

<div class="card meta">
  <small>Generated by DevFlow Automation — {self._repo_name}</small>
</div>
</body>
</html>"""

    # ── Generate both files ───────────────────────────────────────────────────

    def generate(self) -> dict[str, str]:
        md_path = self._report_dir / "automation-report.md"
        html_path = self._report_dir / "automation-report.html"
        json_path = self._report_dir / "automation-report.json"

        md_content = self.render_md()
        html_content = self.render_html()

        md_path.write_text(md_content, encoding="utf-8")
        html_path.write_text(html_content, encoding="utf-8")
        json_path.write_text(json.dumps({
            "story_id": self.story_id,
            "story": self._story,
            "feature": self._feature,
            "stages": self._stages,
            "files_changed": self._files_changed,
            "test_result": self._test_result,
            "build_result": self._build_result,
            "mr_url": self._mr_url,
            "generated_at": self._generated_at,
        }, indent=2, default=str), encoding="utf-8")

        return {
            "html": str(html_path),
            "md": str(md_path),
            "json": str(json_path),
            "report_dir": str(self._report_dir),
        }

    def mr_report_snippet(self) -> str:
        """Short markdown snippet to embed inside MR description."""
        tv = self._test_verdict()
        bv = self._build_verdict()
        ai_t = _fmt_duration(self._ai_total())
        hu_t = _fmt_duration(self._human_total())

        # Relative paths inside the repo
        story_report_rel  = f"Automation/reports/{self.story_id}/automation-report.html"
        analytics_rel     = "Automation/docs/devflow-analytics.html"

        # Build absolute GitLab links when we have a project URL + branch
        # e.g. https://gitlab.example.com/group/repo/-/blob/branch/path
        def _gitlab_file_link(rel_path: str, label: str) -> str:
            if self._mr_url:
                # Strip /merge_requests/NNN to get the project base URL
                import re as _re
                m = _re.match(r"(https?://[^/]+/[^/]+/[^/]+)/-/merge_requests/", self._mr_url)
                project_base = m.group(1) if m else ""
                branch = self._branch or "main"
                if project_base:
                    url = f"{project_base}/-/blob/{branch}/{rel_path}"
                    return f"[{label}]({url})"
            return f"`{rel_path}`"

        story_report_link   = _gitlab_file_link(story_report_rel,  "automation-report.html")
        analytics_link      = _gitlab_file_link(analytics_rel,      "devflow-analytics.html")

        return (
            f"\n---\n"
            f"### 🤖 DevFlow Automation Report — `{self.story_id}`\n"
            f"| | |\n|---|---|\n"
            f"| Tests       | {'✅ passed' if tv=='passed' else '❌ failed' if tv=='failed' else '— not run'} |\n"
            f"| Build       | {'✅ passed' if bv=='passed' else '❌ failed' if bv=='failed' else '— not run'} |\n"
            f"| AI effort   | {ai_t} |\n"
            f"| Human reviews | {hu_t} |\n"
            f"| Files changed | {len(self._files_changed)} |\n"
            f"| Story report  | {story_report_link} |\n"
            f"| Team dashboard | {analytics_link} |\n"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: report_generator.py <story_id>")
        sys.exit(1)
    rg = ReportGenerator(sys.argv[1])
    rg.set_story({"key": sys.argv[1], "summary": "Demo story", "status": "In Progress"})
    rg.record_stage("start", elapsed_s=3.2, actor="ai")
    rg.record_stage("apply_story_update", elapsed_s=5.1, actor="ai")
    rg.record_stage("jira_approval", elapsed_s=120.0, actor="human", status="approved")
    rg.record_stage("after_story_approval", elapsed_s=4.0, actor="ai")
    rg.record_stage("code_changes", elapsed_s=600.0, actor="ai")
    rg.record_stage("after_code_changes", elapsed_s=45.0, actor="ai")
    rg.record_stage("mr_approval", elapsed_s=180.0, actor="human", status="approved")
    rg.set_test_result({"ok": True, "verdict": "passed", "command_used": "mvn test"})
    rg.set_build_result({"ok": True})
    rg.set_files_changed(["src/main/java/Foo.java", "src/test/java/FooTest.java"])
    rg.set_mr("https://gitlab.example.com/mr/1", "feat(BDRSP-9999): demo")
    paths = rg.generate()
    print(json.dumps(paths, indent=2))

