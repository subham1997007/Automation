"""Portable repository and Automation directory resolver.

Auto-detects the project root and Automation directory from any location inside
the Automation folder structure. Works across any repository — no hardcoded paths.

How detection works:
  1. Start from the current file's location.
  2. Walk UP the directory tree looking for the ``Automation`` folder that contains
     ``mcp-servers/``. That parent is the **repo root**.
  3. Fallback: look for VCS / build markers (pom.xml, .git, package.json, etc.).
  4. Cache the resolved paths in ``Automation/.memory/repo-config.json`` so every
     helper reads from a single source of truth.

Usage anywhere in the codebase:
    from langchain_helpers.repo_resolver import RepoResolver

    r = RepoResolver()
    r.repo_root        # /path/to/any-project
    r.automation_dir   # /path/to/any-project/Automation
    r.memory_dir       # /path/to/any-project/Automation/.memory
    r.env_file         # /path/to/any-project/Automation/.env.local (or None)
    r.src_dir          # /path/to/any-project/src  (or None if not found)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Markers that indicate a project/repo root
_ROOT_MARKERS = [
    "pom.xml",          # Maven / Spring Boot
    "build.gradle",     # Gradle
    "build.gradle.kts", # Gradle Kotlin
    "package.json",     # Node.js
    "setup.py",         # Python
    "pyproject.toml",   # Python
    "go.mod",           # Go
    "Cargo.toml",       # Rust
    ".git",             # Git repo root (directory or file)
    "settings.gradle",  # Gradle multi-project
]

# Config cache file name (written once, re-used on every call)
_CONFIG_FILE = "repo-config.json"


class RepoResolver:
    """Detects and exposes repo-relative paths for any project.

    Instantiate once per process — subsequent calls reuse memory cache.
    Thread-safe (reads only after __init__).

    Attributes:
        repo_root      Absolute path to the project root directory.
        automation_dir Absolute path to the ``Automation/`` directory.
        memory_dir     Absolute path to ``Automation/.memory/``.
        env_file       Path to ``.env.local`` (or None if not found).
        src_dir        Path to ``src/`` inside the project (or None).
    """

    # ── Module-level singleton cache so we only resolve once per process ───────
    _cache: dict[str, Any] = {}

    def __init__(self, start_path: str | Path | None = None) -> None:
        if RepoResolver._cache:
            self.__dict__.update(RepoResolver._cache)
            return

        start = Path(start_path).resolve() if start_path else Path(__file__).resolve()
        automation = self._find_automation_dir(start)
        repo = self._find_repo_root(automation)

        self.repo_root: Path = repo
        self.automation_dir: Path = automation
        self.memory_dir: Path = automation / ".memory"
        self.env_file: Path | None = self._find_env_file(automation, repo)
        self.src_dir: Path | None = (repo / "src") if (repo / "src").is_dir() else None

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._write_config()

        log.info(
            "repo_resolver: repo_root=%s automation=%s",
            self.repo_root,
            self.automation_dir,
        )

        # Populate singleton cache
        RepoResolver._cache = {k: v for k, v in self.__dict__.items()}

    # ── Path helpers ────────────────────────────────────────────────────────────

    def memory_path(self, *parts: str) -> Path:
        """Return a path under ``Automation/.memory/``, creating parents."""
        path = self.memory_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def env_value(self, key: str, default: str = "") -> str:
        """Read a key from ``.env.local`` or fall back to ``os.environ``."""
        env = self._load_env()
        return env.get(key) or os.getenv(key, default)

    def relative_to_repo(self, path: str | Path) -> str:
        """Return *path* as a string relative to repo_root."""
        try:
            return str(Path(path).resolve().relative_to(self.repo_root))
        except ValueError:
            return str(path)

    # ── Detection logic ─────────────────────────────────────────────────────────

    @staticmethod
    def _find_automation_dir(start: Path) -> Path:
        """Walk up from *start* to find the ``Automation/mcp-servers/`` parent."""
        candidate = start
        for _ in range(12):  # guard — max 12 levels up
            # Are we inside mcp-servers/dev-mcp/src/langchain_helpers?
            if (
                candidate.name == "langchain_helpers"
                or candidate.name == "src"
                or candidate.name == "dev-mcp"
                or candidate.name == "mcp-servers"
            ):
                # Walk to nearest "Automation" ancestor
                for parent in candidate.parents:
                    if parent.name == "Automation" and (parent / "mcp-servers").is_dir():
                        return parent
            if candidate.name == "Automation" and (candidate / "mcp-servers").is_dir():
                return candidate
            if candidate == candidate.parent:
                break
            candidate = candidate.parent

        # Fallback: look for any Automation/ with mcp-servers/ in ancestor tree
        candidate = start
        for parent in candidate.parents:
            a = parent / "Automation"
            if a.is_dir() and (a / "mcp-servers").is_dir():
                return a

        raise RuntimeError(
            f"Cannot find Automation/mcp-servers/ directory starting from {start}. "
            "Ensure the Automation folder is placed directly inside the project root."
        )

    @staticmethod
    def _find_repo_root(automation_dir: Path) -> Path:
        """Find the project root by looking for build/VCS markers above Automation/."""
        # The repo root is the PARENT of the Automation directory
        candidate = automation_dir.parent

        # Verify with markers — if the immediate parent has markers, use it
        for marker in _ROOT_MARKERS:
            if (candidate / marker).exists():
                return candidate

        # Walk up further (in case Automation is nested deeper)
        for parent in candidate.parents:
            for marker in _ROOT_MARKERS:
                if (parent / marker).exists():
                    return parent

        # Last resort: use the direct parent of Automation
        log.warning(
            "repo_resolver: no project root markers found above %s — using %s",
            automation_dir,
            candidate,
        )
        return candidate

    @staticmethod
    def _find_env_file(automation_dir: Path, repo_root: Path) -> Path | None:
        """Find .env.local in Automation/ or repo root."""
        candidates = [
            automation_dir / ".env.local",
            automation_dir / ".env",
            repo_root / ".env.local",
            repo_root / ".env",
        ]
        for c in candidates:
            if c.is_file():
                return c
        return None

    def _load_env(self) -> dict[str, str]:
        """Parse the .env.local file into a key/value dict."""
        env: dict[str, str] = {}
        if not self.env_file or not self.env_file.exists():
            return env
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
        return env

    def _write_config(self) -> None:
        """Write resolved paths to .memory/repo-config.json for transparency."""
        config_path = self.memory_dir / _CONFIG_FILE
        config = {
            "_meta": {
                "description": "Auto-detected repo and Automation paths. Re-generated on each process start.",
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "repo_root": str(self.repo_root),
            "automation_dir": str(self.automation_dir),
            "memory_dir": str(self.memory_dir),
            "env_file": str(self.env_file) if self.env_file else None,
            "src_dir": str(self.src_dir) if self.src_dir else None,
            "project_markers_found": [
                m for m in _ROOT_MARKERS if (self.repo_root / m).exists()
            ],
        }
        try:
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        except OSError:
            pass


# ── Module-level convenience singleton ─────────────────────────────────────────

def get_resolver() -> RepoResolver:
    """Return the shared RepoResolver instance (constructed once per process)."""
    return RepoResolver()

