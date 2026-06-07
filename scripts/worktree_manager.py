#!/usr/bin/env python3
"""Worktree Manager — Isolated Git Worktree Runtime for DevFlow Agent.

Each Jira story gets its own git worktree so the main working tree stays
clean and agent can safely code, test, fail, and retry in isolation.

Flow:
    User: implement BDRSP-1413
        ↓  dev_client calls WorktreeManager.create(...)
    git worktree add Automation/runtime/worktrees/BDRSP-1413 <branch>
        ↓
    Agent works inside runtime/worktrees/BDRSP-1413/
        ↓
    Tests run inside that path
        ↓
    User approves → push → cleanup

Directory layout:
    Automation/runtime/
        worktrees/BDRSP-1413/    ← full git working tree on the story branch
        jobs/BDRSP-1413.json     ← state: branch, path, status, timestamps
        logs/BDRSP-1413.log      ← stdout/stderr from test runs

Usage:
    wm = WorktreeManager()
    ws = wm.create("BDRSP-1413", branch_name="feature/BDRSP-1413-foo")
    print(ws.path)          # Automation/runtime/worktrees/BDRSP-1413
    wm.remove("BDRSP-1413")
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(args: list[str], *, cwd: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(args, returncode=127, stdout="", stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args, returncode=124, stdout=exc.stdout or "", stderr="Command timed out"
        )


def _automation_dir() -> Path:
    """Return Automation/ directory — always works regardless of where script is run."""
    try:
        from langchain_helpers.repo_resolver import get_resolver  # type: ignore[import]
        return get_resolver().automation_dir
    except Exception:
        return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WorktreeInfo:
    story_id:   str
    branch:     str
    path:       str           # absolute path to the worktree
    base_branch: str = "main"
    status:     str = "active"   # active | removed | error
    created_at: int = field(default_factory=lambda: int(time.time()))
    removed_at: int = 0
    error:      str = ""

    def path_obj(self) -> Path:
        return Path(self.path)

    def exists(self) -> bool:
        p = self.path_obj()
        return p.exists() and (p / ".git").exists()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# WorktreeManager
# ---------------------------------------------------------------------------

class WorktreeManager:
    """Create, query, and remove per-story git worktrees."""

    def __init__(self, repo_root: Path | None = None, automation_dir: Path | None = None) -> None:
        self._auto_dir = automation_dir or _automation_dir()
        self._repo_root = repo_root or self._auto_dir.parent
        self._runtime_dir = self._auto_dir / "runtime"
        self._worktrees_dir = self._runtime_dir / "worktrees"
        self._jobs_dir = self._runtime_dir / "jobs"
        self._logs_dir = self._runtime_dir / "logs"
        self._ensure_dirs()

    # ── directory setup ───────────────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        for d in (self._worktrees_dir, self._jobs_dir, self._logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── job persistence ───────────────────────────────────────────────────────

    def _job_path(self, story_id: str) -> Path:
        safe = "".join(c for c in story_id.upper() if c.isalnum() or c in {"-", "_"})
        return self._jobs_dir / f"{safe}.json"

    def save_job(self, info: WorktreeInfo) -> None:
        self._job_path(info.story_id).write_text(
            json.dumps(info.to_dict(), indent=2) + "\n", encoding="utf-8"
        )

    def load_job(self, story_id: str) -> WorktreeInfo | None:
        path = self._job_path(story_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return WorktreeInfo(**data)
        except Exception:
            return None

    # ── log helpers ───────────────────────────────────────────────────────────

    def log_path(self, story_id: str) -> Path:
        safe = "".join(c for c in story_id.upper() if c.isalnum() or c in {"-", "_"})
        return self._logs_dir / f"{safe}.log"

    def append_log(self, story_id: str, content: str) -> None:
        with self.log_path(story_id).open("a", encoding="utf-8") as f:
            f.write(f"\n--- {time.strftime('%Y-%m-%dT%H:%M:%S')} ---\n")
            f.write(content)
            f.write("\n")

    def read_log(self, story_id: str) -> str:
        p = self.log_path(story_id)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    # ── worktree path ─────────────────────────────────────────────────────────

    def _worktree_path(self, story_id: str) -> Path:
        safe = "".join(c for c in story_id.upper() if c.isalnum() or c in {"-", "_"})
        return self._worktrees_dir / safe

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, story_id: str) -> WorktreeInfo | None:
        """Return job info if the worktree is active, otherwise None."""
        info = self.load_job(story_id)
        if info and info.status == "active":
            return info
        return None

    def create(
        self,
        story_id: str,
        branch_name: str,
        base_branch: str = "main",
    ) -> WorktreeInfo:
        """Create an isolated git worktree for a story branch.

        If a worktree already exists for this story and its branch matches,
        return the existing info (idempotent). If the branch differs, remove
        the old one first.
        """
        existing = self.load_job(story_id)
        if existing and existing.status == "active":
            if existing.branch == branch_name and existing.exists():
                return existing          # already good — return as-is
            self._do_remove(existing)    # branch changed, clean up

        wt_path = self._worktree_path(story_id)
        # Remove stale directory if present
        if wt_path.exists():
            import shutil
            shutil.rmtree(wt_path, ignore_errors=True)

        repo = str(self._repo_root)

        # Check if branch exists locally or needs to be created
        branch_exists = _run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=repo, timeout=20
        ).returncode == 0

        if branch_exists:
            # Worktree on existing branch (branch was already created in main tree)
            result = _run(
                ["git", "worktree", "add", str(wt_path), branch_name],
                cwd=repo, timeout=60,
            )
        else:
            # Try to create worktree + branch from remote base
            remote_base = f"origin/{base_branch}"
            remote_exists = _run(
                ["git", "rev-parse", "--verify", remote_base],
                cwd=repo, timeout=20
            ).returncode == 0

            if remote_exists:
                result = _run(
                    ["git", "worktree", "add", "-b", branch_name, str(wt_path), remote_base],
                    cwd=repo, timeout=60,
                )
            else:
                result = _run(
                    ["git", "worktree", "add", "-b", branch_name, str(wt_path), base_branch],
                    cwd=repo, timeout=60,
                )

        info = WorktreeInfo(
            story_id=story_id,
            branch=branch_name,
            path=str(wt_path),
            base_branch=base_branch,
        )

        if result.returncode != 0:
            info.status = "error"
            info.error = result.stderr.strip() or result.stdout.strip()
            self.save_job(info)
            return info

        info.status = "active"
        self.save_job(info)
        return info

    def remove(self, story_id: str) -> dict[str, Any]:
        """Remove the worktree for the given story. Safe to call multiple times."""
        info = self.load_job(story_id)
        if not info:
            return {"ok": True, "message": f"No worktree found for {story_id}"}
        return self._do_remove(info)

    def _do_remove(self, info: WorktreeInfo) -> dict[str, Any]:
        repo = str(self._repo_root)
        wt_path = info.path_obj()

        result = _run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=repo, timeout=60,
        )

        # If git worktree remove fails, force-delete the directory anyway
        if result.returncode != 0 and wt_path.exists():
            import shutil
            shutil.rmtree(wt_path, ignore_errors=True)
            # Prune stale worktree refs
            _run(["git", "worktree", "prune"], cwd=repo, timeout=30)

        info.status = "removed"
        info.removed_at = int(time.time())
        self.save_job(info)
        return {
            "ok": True,
            "story_id": info.story_id,
            "branch": info.branch,
            "path": info.path,
            "removed_at": info.removed_at,
        }

    def list_active(self) -> list[WorktreeInfo]:
        """Return all active worktrees (exist on disk)."""
        results = []
        for job_file in self._jobs_dir.glob("*.json"):
            try:
                data = json.loads(job_file.read_text(encoding="utf-8"))
                info = WorktreeInfo(**data)
                if info.status == "active":
                    results.append(info)
            except Exception:
                continue
        return results

    def prune_stale(self) -> list[str]:
        """Remove worktrees whose directories no longer exist. Returns pruned story IDs."""
        pruned = []
        for info in self.list_active():
            if not info.exists():
                info.status = "removed"
                info.removed_at = int(time.time())
                self.save_job(info)
                pruned.append(info.story_id)
        # Also run git worktree prune to clear stale refs
        _run(["git", "worktree", "prune"], cwd=str(self._repo_root), timeout=30)
        return pruned

    def status(self, story_id: str) -> dict[str, Any]:
        """Return a status summary for a story worktree."""
        info = self.load_job(story_id)
        if not info:
            return {"story_id": story_id, "status": "not_found"}
        return {
            "story_id": info.story_id,
            "status": info.status,
            "branch": info.branch,
            "path": info.path,
            "exists_on_disk": info.exists(),
            "base_branch": info.base_branch,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(info.created_at)),
            "error": info.error or None,
            "log_path": str(self.log_path(story_id)),
        }


# ---------------------------------------------------------------------------
# CLI — python3 Automation/scripts/worktree_manager.py status BDRSP-1413
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    def _usage():
        print("Usage: worktree_manager.py <cmd> [args]")
        print("  create  <story_id> <branch_name> [base_branch]")
        print("  remove  <story_id>")
        print("  status  <story_id>")
        print("  list")
        print("  prune")
        sys.exit(1)

    args = sys.argv[1:]
    if not args:
        _usage()

    wm = WorktreeManager()
    cmd = args[0]

    if cmd == "create" and len(args) >= 3:
        base = args[3] if len(args) > 3 else "main"
        info = wm.create(args[1], args[2], base)
        print(json.dumps(info.to_dict(), indent=2))
    elif cmd == "remove" and len(args) == 2:
        print(json.dumps(wm.remove(args[1]), indent=2))
    elif cmd == "status" and len(args) == 2:
        print(json.dumps(wm.status(args[1]), indent=2))
    elif cmd == "list":
        for w in wm.list_active():
            print(json.dumps(w.to_dict(), indent=2))
    elif cmd == "prune":
        pruned = wm.prune_stale()
        print(f"Pruned: {pruned}")
    else:
        _usage()

