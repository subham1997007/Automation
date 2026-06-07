"""Universal project type detector.

Detects frontend / backend / fullstack project types and returns the correct:
- Source file glob patterns for FAISS indexing
- Test commands
- Build commands
- Package manager
- Framework name

Supports:
  Backend  : Spring Boot / Maven, Gradle, Django, FastAPI, Express, Go, Rust, .NET
  Frontend : React, Vue, Angular, Next.js, Nuxt, Svelte, plain HTML/JS
  Fullstack: Next.js, Nuxt, Django+React, Spring Boot + frontend module

Usage:
    from langchain_helpers.project_detector import ProjectDetector

    d = ProjectDetector("/path/to/any/repo")
    print(d.project_type)       # "spring_boot" | "react" | "nextjs" | ...
    print(d.source_globs)       # ["src/**/*.java"] | ["src/**/*.tsx", ...] | ...
    print(d.test_command)       # "mvn test" | "npm test" | "pytest" | ...
    print(d.build_command)      # "mvn package" | "npm run build" | ...
    print(d.language)           # "java" | "typescript" | "python" | "go" | ...
    print(d.summary())          # human-readable project summary dict
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ── Project profiles ────────────────────────────────────────────────────────────
# Each profile has: detect_markers, source_globs, test_command, build_command,
#                   language, category (frontend/backend/fullstack)

_PROFILES: list[dict[str, Any]] = [

    # ── Backend: Java / Spring Boot (Maven) ─────────────────────────────────────
    {
        "name": "spring_boot_maven",
        "label": "Spring Boot (Maven)",
        "category": "backend",
        "language": "java",
        "markers": ["pom.xml", "src/main/java"],
        "extra_markers": ["src/main/resources/application.properties",
                          "src/main/resources/application.yaml",
                          "src/main/resources/application.yml"],
        "source_globs": [
            "src/main/java/**/*.java",
            "src/test/java/**/*.java",
            "src/main/resources/**/*.graphqls",
            "src/main/resources/**/*.yaml",
            "src/main/resources/**/*.yml",
        ],
        "skip_dirs": ["target", ".git", ".mvn"],
        "test_command": "./mvnw --no-transfer-progress test -DfailIfNoTests=false",
        "test_command_fallback": "mvn --no-transfer-progress test -DfailIfNoTests=false",
        "targeted_test_template": "./mvnw --no-transfer-progress test -Dtest=\"{CLASSES}\" -DfailIfNoTests=false",
        "build_command": "./mvnw --no-transfer-progress package -DskipTests",
        "package_manager": "maven",
        "entry_point_hints": ["src/main/java/**/*Application.java"],
        "test_class_suffix": "Test",
        "test_dir": "src/test/java",
    },

    # ── Backend: Java / Gradle ────────────────────────────────────────────────────
    {
        "name": "java_gradle",
        "label": "Java (Gradle)",
        "category": "backend",
        "language": "java",
        "markers": ["build.gradle", "src/main/java"],
        "extra_markers": ["build.gradle.kts", "settings.gradle"],
        "source_globs": [
            "src/main/java/**/*.java",
            "src/test/java/**/*.java",
            "src/main/resources/**/*.yaml",
            "src/main/resources/**/*.yml",
        ],
        "skip_dirs": ["build", ".gradle", ".git"],
        "test_command": "./gradlew test",
        "test_command_fallback": "gradle test",
        "targeted_test_template": "./gradlew test --tests \"{CLASSES}\"",
        "build_command": "./gradlew build -x test",
        "package_manager": "gradle",
        "entry_point_hints": ["src/main/java/**/*Application.java"],
        "test_class_suffix": "Test",
        "test_dir": "src/test/java",
    },

    # ── Backend: Python / Django ────────────────────────────────────────────────
    {
        "name": "django",
        "label": "Django (Python)",
        "category": "backend",
        "language": "python",
        "markers": ["manage.py", "requirements.txt"],
        "extra_markers": ["manage.py"],
        "source_globs": [
            "**/*.py",
            "templates/**/*.html",
        ],
        "skip_dirs": [".venv", "venv", "__pycache__", ".git", "node_modules", "migrations"],
        "test_command": "python manage.py test",
        "test_command_fallback": "pytest",
        "targeted_test_template": "python manage.py test {CLASSES}",
        "build_command": "pip install -r requirements.txt",
        "package_manager": "pip",
        "entry_point_hints": ["manage.py", "wsgi.py", "asgi.py"],
        "test_class_suffix": "Test",
        "test_dir": "",
    },

    # ── Backend: Python / FastAPI ─────────────────────────────────────────────────
    {
        "name": "fastapi",
        "label": "FastAPI (Python)",
        "category": "backend",
        "language": "python",
        "markers": ["pyproject.toml", "main.py"],
        "extra_markers": ["fastapi", "uvicorn"],
        "source_globs": ["**/*.py"],
        "skip_dirs": [".venv", "venv", "__pycache__", ".git", "node_modules"],
        "test_command": "pytest",
        "test_command_fallback": "python -m pytest",
        "targeted_test_template": "pytest {CLASSES} -v",
        "build_command": "pip install -e .",
        "package_manager": "pip",
        "entry_point_hints": ["main.py", "app.py", "app/main.py"],
        "test_class_suffix": "_test",
        "test_dir": "tests",
    },

    # ── Backend: Go ──────────────────────────────────────────────────────────────
    {
        "name": "golang",
        "label": "Go",
        "category": "backend",
        "language": "go",
        "markers": ["go.mod"],
        "extra_markers": ["go.sum"],
        "source_globs": ["**/*.go"],
        "skip_dirs": ["vendor", ".git", "node_modules"],
        "test_command": "go test ./...",
        "test_command_fallback": "go test ./...",
        "targeted_test_template": "go test -run {CLASSES} ./...",
        "build_command": "go build ./...",
        "package_manager": "go_modules",
        "entry_point_hints": ["main.go", "cmd/main.go"],
        "test_class_suffix": "_test",
        "test_dir": "",
    },

    # ── Backend: Rust ─────────────────────────────────────────────────────────────
    {
        "name": "rust",
        "label": "Rust",
        "category": "backend",
        "language": "rust",
        "markers": ["Cargo.toml"],
        "extra_markers": ["Cargo.lock"],
        "source_globs": ["src/**/*.rs", "tests/**/*.rs"],
        "skip_dirs": ["target", ".git"],
        "test_command": "cargo test",
        "targeted_test_template": "cargo test {CLASSES}",
        "build_command": "cargo build",
        "package_manager": "cargo",
        "entry_point_hints": ["src/main.rs", "src/lib.rs"],
        "test_class_suffix": "_test",
        "test_dir": "tests",
    },

    # ── Backend: .NET / C# ────────────────────────────────────────────────────────
    {
        "name": "dotnet",
        "label": ".NET / C#",
        "category": "backend",
        "language": "csharp",
        "markers": ["*.csproj", "*.sln"],
        "extra_markers": [],
        "source_globs": ["**/*.cs", "**/*.csproj"],
        "skip_dirs": ["bin", "obj", ".git"],
        "test_command": "dotnet test",
        "targeted_test_template": "dotnet test --filter \"{CLASSES}\"",
        "build_command": "dotnet build",
        "package_manager": "nuget",
        "entry_point_hints": ["Program.cs", "Startup.cs"],
        "test_class_suffix": "Tests",
        "test_dir": "tests",
    },

    # ── Frontend: Next.js ────────────────────────────────────────────────────────
    {
        "name": "nextjs",
        "label": "Next.js (React + SSR)",
        "category": "fullstack",
        "language": "typescript",
        "markers": ["next.config.js", "next.config.ts", "next.config.mjs"],
        "extra_markers": ["pages", "app"],
        "source_globs": [
            "app/**/*.tsx", "app/**/*.ts",
            "pages/**/*.tsx", "pages/**/*.ts",
            "components/**/*.tsx", "components/**/*.ts",
            "lib/**/*.ts", "lib/**/*.tsx",
            "hooks/**/*.ts",
            "styles/**/*.css",
        ],
        "skip_dirs": [".next", "node_modules", ".git", "dist", "out"],
        "test_command": "npm test",
        "test_command_fallback": "npx jest",
        "targeted_test_template": "npx jest {CLASSES}",
        "build_command": "npm run build",
        "package_manager": "npm",
        "entry_point_hints": ["app/layout.tsx", "pages/_app.tsx", "pages/index.tsx"],
        "test_class_suffix": ".test",
        "test_dir": "__tests__",
    },

    # ── Frontend: React (CRA / Vite) ────────────────────────────────────────────
    {
        "name": "react",
        "label": "React",
        "category": "frontend",
        "language": "typescript",
        "markers": ["package.json", "src/App.tsx", "src/App.jsx"],
        "extra_markers": ["vite.config.ts", "vite.config.js", "react-scripts"],
        "source_globs": [
            "src/**/*.tsx", "src/**/*.ts",
            "src/**/*.jsx", "src/**/*.js",
            "src/**/*.css", "src/**/*.scss",
            "public/**/*.html",
        ],
        "skip_dirs": ["node_modules", ".git", "dist", "build"],
        "test_command": "npm test -- --watchAll=false",
        "test_command_fallback": "npx jest --watchAll=false",
        "targeted_test_template": "npm test -- --testPathPattern={CLASSES} --watchAll=false",
        "build_command": "npm run build",
        "package_manager": "npm",
        "entry_point_hints": ["src/index.tsx", "src/main.tsx", "src/App.tsx"],
        "test_class_suffix": ".test",
        "test_dir": "src/__tests__",
    },

    # ── Frontend: Vue.js ─────────────────────────────────────────────────────────
    {
        "name": "vue",
        "label": "Vue.js",
        "category": "frontend",
        "language": "typescript",
        "markers": ["vue.config.js", "vite.config.ts"],
        "extra_markers": ["src/App.vue", "src/main.ts", "src/main.js"],
        "source_globs": [
            "src/**/*.vue", "src/**/*.ts",
            "src/**/*.js", "src/**/*.css",
        ],
        "skip_dirs": ["node_modules", ".git", "dist"],
        "test_command": "npm test",
        "test_command_fallback": "npx vitest",
        "targeted_test_template": "npx vitest run {CLASSES}",
        "build_command": "npm run build",
        "package_manager": "npm",
        "entry_point_hints": ["src/App.vue", "src/main.ts"],
        "test_class_suffix": ".spec",
        "test_dir": "tests",
    },

    # ── Frontend: Angular ────────────────────────────────────────────────────────
    {
        "name": "angular",
        "label": "Angular",
        "category": "frontend",
        "language": "typescript",
        "markers": ["angular.json"],
        "extra_markers": ["src/app/app.module.ts", "src/app/app.component.ts"],
        "source_globs": [
            "src/app/**/*.ts", "src/app/**/*.html",
            "src/app/**/*.scss", "src/app/**/*.css",
            "src/environments/**/*.ts",
        ],
        "skip_dirs": ["node_modules", ".git", "dist", ".angular"],
        "test_command": "ng test --watch=false --browsers=ChromeHeadless",
        "test_command_fallback": "npx ng test --watch=false",
        "targeted_test_template": "ng test --include={CLASSES} --watch=false",
        "build_command": "ng build",
        "package_manager": "npm",
        "entry_point_hints": ["src/main.ts", "src/app/app.module.ts"],
        "test_class_suffix": ".spec",
        "test_dir": "src/app",
    },

    # ── Frontend: Nuxt.js ────────────────────────────────────────────────────────
    {
        "name": "nuxt",
        "label": "Nuxt.js (Vue + SSR)",
        "category": "fullstack",
        "language": "typescript",
        "markers": ["nuxt.config.ts", "nuxt.config.js"],
        "extra_markers": [],
        "source_globs": [
            "pages/**/*.vue", "components/**/*.vue",
            "composables/**/*.ts", "server/**/*.ts",
            "layouts/**/*.vue",
        ],
        "skip_dirs": [".nuxt", "node_modules", ".git", "dist"],
        "test_command": "npm test",
        "test_command_fallback": "npx vitest",
        "targeted_test_template": "npx vitest run {CLASSES}",
        "build_command": "npm run build",
        "package_manager": "npm",
        "entry_point_hints": ["app.vue", "pages/index.vue"],
        "test_class_suffix": ".spec",
        "test_dir": "tests",
    },

    # ── Backend: Node.js / Express ─────────────────────────────────────────────
    {
        "name": "nodejs_express",
        "label": "Node.js / Express",
        "category": "backend",
        "language": "javascript",
        "markers": ["package.json", "server.js", "app.js", "index.js"],
        "extra_markers": ["express"],
        "source_globs": [
            "src/**/*.js", "src/**/*.ts",
            "routes/**/*.js", "routes/**/*.ts",
            "middleware/**/*.js", "controllers/**/*.js",
            "models/**/*.js",
        ],
        "skip_dirs": ["node_modules", ".git", "dist"],
        "test_command": "npm test",
        "test_command_fallback": "npx jest",
        "targeted_test_template": "npx jest {CLASSES}",
        "build_command": "npm run build",
        "package_manager": "npm",
        "entry_point_hints": ["server.js", "app.js", "src/index.js"],
        "test_class_suffix": ".test",
        "test_dir": "tests",
    },
]

# Fallback generic profile
_GENERIC_PROFILE: dict[str, Any] = {
    "name": "generic",
    "label": "Generic Project",
    "category": "unknown",
    "language": "unknown",
    "markers": [],
    "source_globs": [
        "src/**/*.java", "src/**/*.py",
        "src/**/*.ts", "src/**/*.tsx",
        "src/**/*.js", "src/**/*.jsx",
        "src/**/*.go", "src/**/*.rs", "src/**/*.cs",
    ],
    "skip_dirs": ["node_modules", ".git", "target", "dist", "build", "__pycache__", ".venv"],
    "test_command": "echo 'No test command detected'",
    "targeted_test_template": "{CLASSES}",
    "build_command": "echo 'No build command detected'",
    "package_manager": "unknown",
    "entry_point_hints": [],
    "test_class_suffix": "Test",
    "test_dir": "tests",
}


class ProjectDetector:
    """Auto-detects project type and returns profile-specific dev configuration.

    Instantiate with the repo root path. Detection is deterministic and
    stateless — safe to call multiple times.

    Example:
        d = ProjectDetector("/path/to/my-react-app")
        d.project_type       # "react"
        d.category           # "frontend"
        d.language           # "typescript"
        d.source_globs       # ["src/**/*.tsx", ...]
        d.test_command       # "npm test -- --watchAll=false"
        d.build_command      # "npm run build"
    """

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_root = Path(repo_path).resolve()
        self._profile = self._detect()
        self._copy_profile()

    # ── Public attributes (set from profile) ────────────────────────────────────

    @property
    def project_type(self) -> str:
        return self._profile["name"]

    @property
    def label(self) -> str:
        return self._profile["label"]

    @property
    def category(self) -> str:
        return self._profile["category"]

    @property
    def language(self) -> str:
        return self._profile["language"]

    @property
    def source_globs(self) -> list[str]:
        return self._profile["source_globs"]

    @property
    def skip_dirs(self) -> list[str]:
        return self._profile.get("skip_dirs", [])

    @property
    def test_command(self) -> str:
        # Prefer wrapper script if available
        cmd = self._profile.get("test_command", "")
        if "./mvnw" in cmd and not (self.repo_root / "mvnw").exists():
            return self._profile.get("test_command_fallback", cmd)
        if "./gradlew" in cmd and not (self.repo_root / "gradlew").exists():
            return self._profile.get("test_command_fallback", cmd)
        return cmd

    @property
    def targeted_test_template(self) -> str:
        return self._profile.get("targeted_test_template", "{CLASSES}")

    @property
    def build_command(self) -> str:
        return self._profile.get("build_command", "")

    @property
    def package_manager(self) -> str:
        return self._profile.get("package_manager", "unknown")

    @property
    def test_dir(self) -> str:
        return self._profile.get("test_dir", "")

    @property
    def test_class_suffix(self) -> str:
        return self._profile.get("test_class_suffix", "Test")

    def targeted_test(self, *class_names: str) -> str:
        """Build a targeted test command for specific class/file names."""
        joined = ",".join(class_names)
        return self.targeted_test_template.replace("{CLASSES}", joined)

    def summary(self) -> dict[str, Any]:
        """Return a JSON-serializable project summary dict."""
        return {
            "project_type": self.project_type,
            "label": self.label,
            "category": self.category,
            "language": self.language,
            "package_manager": self.package_manager,
            "test_command": self.test_command,
            "build_command": self.build_command,
            "source_globs": self.source_globs,
            "repo_root": str(self.repo_root),
        }

    # ── Detection logic ─────────────────────────────────────────────────────────

    def _detect(self) -> dict[str, Any]:
        """Score each profile against the repo and return the best match."""
        scores: list[tuple[int, dict[str, Any]]] = []
        root = self.repo_root

        for profile in _PROFILES:
            score = 0
            # Required markers
            for marker in profile.get("markers", []):
                if self._exists(root, marker):
                    score += 10
            # Extra markers (bonus points)
            for marker in profile.get("extra_markers", []):
                if self._exists(root, marker):
                    score += 3
            if score > 0:
                scores.append((score, profile))

        if not scores:
            log.info("project_detector: no markers matched — using generic profile")
            return _GENERIC_PROFILE

        best_score, best_profile = max(scores, key=lambda x: x[0])
        log.info(
            "project_detector: detected %s (score=%d) for %s",
            best_profile["label"],
            best_score,
            root,
        )
        return best_profile

    @staticmethod
    def _exists(root: Path, marker: str) -> bool:
        """Check if a marker file or directory exists (supports glob wildcards)."""
        if "*" in marker:
            return bool(list(root.glob(marker))[:1])
        return (root / marker).exists()

    def _copy_profile(self) -> None:
        """Deep-copy profile into instance attributes for easy mutation."""
        import copy
        self._profile = copy.deepcopy(self._profile)


# ── Convenience singleton (per-process, lazy) ──────────────────────────────────
_detector_cache: dict[str, ProjectDetector] = {}


def detect_project(repo_path: str | Path | None = None) -> ProjectDetector:
    """Return a cached ProjectDetector for *repo_path* (or auto-detected root)."""
    if repo_path is None:
        try:
            from langchain_helpers.repo_resolver import get_resolver
            repo_path = get_resolver().repo_root
        except Exception:
            repo_path = Path.cwd()

    key = str(Path(repo_path).resolve())
    if key not in _detector_cache:
        _detector_cache[key] = ProjectDetector(key)
    return _detector_cache[key]

