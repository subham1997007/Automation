"""Testing helpers for test-mcp."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


class TestMcpError(RuntimeError):
    """Raised when a test operation cannot be completed."""


REPORT_GLOBS = [
    "target/surefire-reports/*.xml",
    "target/failsafe-reports/*.xml",
    "build/test-results/**/*.xml",
    "coverage/**/*.xml",
    "test-results/**/*.xml",
    "reports/**/*.xml",
    "pytest*.xml",
    "junit*.xml",
]


def resolve_executable(name: str) -> str | None:
    """Resolve tool executable from the active PATH."""
    return shutil.which(name)


def maven_cmd(root: Path) -> str:
    if (root / "mvnw").exists():
        return "./mvnw"
    return resolve_executable("mvn") or "mvn"


def gradle_cmd(root: Path) -> str:
    if (root / "gradlew").exists():
        return "./gradlew"
    return resolve_executable("gradle") or "gradle"


def json_response(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def run_command(args: list[str], *, cwd: str | None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
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
    cwd = working_dir or os.getenv("TEST_WORKING_DIR") or os.getenv("GIT_WORKING_DIR") or os.getenv("WORKSPACE_DIR") or os.getcwd()
    path = Path(cwd).resolve()
    if not path.exists():
        raise TestMcpError(f"Project directory does not exist: {path}")

    git_root = run_command(["git", "rev-parse", "--show-toplevel"], cwd=str(path), timeout=20)
    if git_root.returncode == 0 and git_root.stdout.strip():
        return git_root.stdout.strip()
    return str(path)


def read_text(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def detect_project_type(*, working_dir: str | None = None) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    root = Path(repo)
    signals: list[str] = []
    project_types: list[str] = []

    checks = [
        ("maven", "pom.xml"),
        ("gradle", "build.gradle"),
        ("gradle", "build.gradle.kts"),
        ("node", "package.json"),
        ("python", "pyproject.toml"),
        ("python", "pytest.ini"),
        ("python", "tox.ini"),
        ("python", "requirements.txt"),
        ("go", "go.mod"),
        ("dotnet", "*.csproj"),
    ]

    for project_type, pattern in checks:
        matches = list(root.glob(pattern))
        if matches:
            signals.extend(str(path.relative_to(root)) for path in matches[:5])
            if project_type not in project_types:
                project_types.append(project_type)

    return {
        "ok": True,
        "repository": repo,
        "project_types": project_types or ["unknown"],
        "signals": signals,
        "primary_type": project_types[0] if project_types else "unknown",
    }


def discover_test_commands(*, working_dir: str | None = None) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    root = Path(repo)
    detected = detect_project_type(working_dir=repo)
    commands: list[dict[str, Any]] = []

    if (root / "pom.xml").exists():
        _mvn = maven_cmd(root)
        commands.extend(
            [
                {"kind": "unit", "command": f"{_mvn} --no-transfer-progress test", "confidence": "high"},
                {"kind": "integration", "command": f"{_mvn} --no-transfer-progress verify", "confidence": "medium"},
                {"kind": "specific", "command": f"{_mvn} --no-transfer-progress -Dtest=<TestClass> test", "confidence": "high"},
            ]
        )
    if (root / "gradlew").exists() or (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        _gradle = gradle_cmd(root)
        commands.extend(
            [
                {"kind": "unit", "command": f"{_gradle} test", "confidence": "high"},
                {"kind": "integration", "command": f"{_gradle} integrationTest", "confidence": "medium"},
                {"kind": "specific", "command": f"{_gradle} test --tests <TestClassOrMethod>", "confidence": "high"},
            ]
        )
    if (root / "package.json").exists():
        package_json = read_text(root / "package.json")
        scripts = re.findall(r'"([^"]+)":\s*"([^"]*test[^"]*)"', package_json, flags=re.IGNORECASE)
        for script_name, script_command in scripts:
            kind = "integration" if "integration" in script_name.lower() or "e2e" in script_name.lower() else "unit"
            commands.append(
                {
                    "kind": kind,
                    "command": f"npm run {script_name}",
                    "confidence": "high",
                    "script_command": script_command,
                }
            )
        if not scripts:
            commands.append({"kind": "unit", "command": "npm test", "confidence": "medium"})
    if any((root / name).exists() for name in ("pytest.ini", "pyproject.toml", "tox.ini", "requirements.txt")):
        commands.extend(
            [
                {"kind": "unit", "command": "python3 -m pytest", "confidence": "medium"},
                {"kind": "specific", "command": "python3 -m pytest <path_or_test_name>", "confidence": "medium"},
            ]
        )
    if (root / "go.mod").exists():
        commands.append({"kind": "unit", "command": "go test ./...", "confidence": "high"})

    return {
        "ok": True,
        "repository": repo,
        "detected": detected,
        "commands": commands,
        "recommended_unit_command": first_command(commands, "unit"),
        "recommended_integration_command": first_command(commands, "integration"),
    }


def first_command(commands: list[dict[str, Any]], kind: str) -> str | None:
    for command in commands:
        if command.get("kind") == kind:
            return command["command"]
    return None


def safe_split_command(command: str) -> list[str]:
    parts = shlex.split(command)
    if not parts:
        raise TestMcpError("Command is empty.")
    return parts


def truncate(text: str, limit: int = 16000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def execute_test_command(command: str, *, working_dir: str | None = None, timeout_seconds: int = 600) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    args = safe_split_command(command)
    result = run_command(args, cwd=repo, timeout=timeout_seconds)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return {
        "ok": result.returncode == 0,
        "repository": repo,
        "command": command,
        "exit_code": result.returncode,
        "stdout": truncate(result.stdout),
        "stderr": truncate(result.stderr),
        "analysis": analyze_failure_text(output) if result.returncode != 0 else {"status": "passed", "summary": "Command completed successfully."},
    }


def run_unit_tests(*, command: str | None = None, working_dir: str | None = None, timeout_seconds: int = 600) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    discovered = discover_test_commands(working_dir=repo)
    selected = command or discovered.get("recommended_unit_command")
    if not selected:
        raise TestMcpError("No unit test command found. Pass command='...' explicitly.")
    result = execute_test_command(selected, working_dir=repo, timeout_seconds=timeout_seconds)
    result["discovered_commands"] = discovered["commands"]
    return result


def run_integration_tests(*, command: str | None = None, working_dir: str | None = None, timeout_seconds: int = 900) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    discovered = discover_test_commands(working_dir=repo)
    selected = command or discovered.get("recommended_integration_command")
    if not selected:
        raise TestMcpError("No integration test command found. Pass command='...' explicitly.")
    result = execute_test_command(selected, working_dir=repo, timeout_seconds=timeout_seconds)
    result["discovered_commands"] = discovered["commands"]
    return result


def run_specific_test(
    *,
    test_target: str,
    command_template: str | None = None,
    working_dir: str | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    discovered = discover_test_commands(working_dir=repo)
    selected_template = command_template
    if not selected_template:
        specific = [command for command in discovered["commands"] if command.get("kind") == "specific"]
        selected_template = specific[0]["command"] if specific else None
    if not selected_template:
        raise TestMcpError("No specific test command found. Pass command_template='...' explicitly.")

    command = selected_template.replace("<TestClass>", test_target).replace("<TestClassOrMethod>", test_target).replace("<path_or_test_name>", test_target)
    return execute_test_command(command, working_dir=repo, timeout_seconds=timeout_seconds)


def analyze_failure_text(output: str) -> dict[str, Any]:
    lowered = output.lower()
    likely_causes = []
    if "compilation failure" in lowered or "compilation error" in lowered or "cannot find symbol" in lowered:
        likely_causes.append("Compilation failure or missing symbol.")
    if "assertion" in lowered or "expected" in lowered and "actual" in lowered:
        likely_causes.append("Assertion mismatch in test expectations.")
    if "connection refused" in lowered or "timeout" in lowered:
        likely_causes.append("External service, network, or timing issue.")
    if "permission denied" in lowered:
        likely_causes.append("Permission issue while running tests.")
    if "no tests" in lowered or "no test" in lowered:
        likely_causes.append("No matching tests were found for the selected command.")
    if not likely_causes:
        likely_causes.append("Review the first error stack trace and failing test name.")

    failing_lines = []
    patterns = ("failed", "failure", "error", "exception", "expected", "actual", "cannot find symbol")
    for line in output.splitlines():
        if any(pattern in line.lower() for pattern in patterns):
            failing_lines.append(line.strip())
        if len(failing_lines) >= 20:
            break

    return {
        "status": "failed",
        "likely_causes": likely_causes,
        "important_lines": failing_lines,
        "next_actions": [
            "Find the first failing test or first compile error.",
            "Fix the root cause before rerunning the full suite.",
            "Run a specific test command after the first fix for faster feedback.",
        ],
    }


def analyze_failures(*, test_output: str = "", working_dir: str | None = None) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    output = test_output
    if not output:
        reports = collect_test_reports(working_dir=repo, max_files=10)
        output = "\n".join(report.get("snippet", "") for report in reports["reports"])
    return {
        "ok": True,
        "repository": repo,
        "analysis": analyze_failure_text(output),
    }


def collect_test_reports(*, working_dir: str | None = None, max_files: int = 30) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    root = Path(repo)
    reports = []
    seen: set[Path] = set()
    for pattern in REPORT_GLOBS:
        for path in root.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            reports.append(
                {
                    "path": str(path.relative_to(root)),
                    "size_bytes": path.stat().st_size,
                    "snippet": read_text(path, limit=2000),
                }
            )
            if len(reports) >= max_files:
                break
        if len(reports) >= max_files:
            break
    return {
        "ok": True,
        "repository": repo,
        "report_count": len(reports),
        "reports": reports,
    }


def generate_test_summary(*, working_dir: str | None = None, latest_output: str = "") -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    detected = detect_project_type(working_dir=repo)
    commands = discover_test_commands(working_dir=repo)
    reports = collect_test_reports(working_dir=repo, max_files=20)
    analysis = analyze_failure_text(latest_output) if latest_output else None
    return {
        "ok": True,
        "repository": repo,
        "detected": detected,
        "available_commands": commands["commands"],
        "recommended_unit_command": commands["recommended_unit_command"],
        "recommended_integration_command": commands["recommended_integration_command"],
        "test_reports": {
            "count": reports["report_count"],
            "paths": [report["path"] for report in reports["reports"]],
        },
        "latest_failure_analysis": analysis,
        "next_steps": [
            "Run the recommended unit command first.",
            "Run integration tests only when the story touches API, database, external services, or configuration.",
            "Use test_run_specific_test for fast reruns of a failing test.",
        ],
    }


def git_output(args: list[str], *, cwd: str) -> str:
    result = run_command(args, cwd=cwd, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else ""


def compare_ref(repo: str, base_branch: str) -> str | None:
    for candidate in (f"origin/{base_branch}", base_branch):
        if run_command(["git", "rev-parse", "--verify", candidate], cwd=repo, timeout=20).returncode == 0:
            return candidate
    return None


def parse_changed_files(text: str) -> list[dict[str, str]]:
    files = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            status, path = line.split("\t", 1)
        else:
            status, path = line[:2].strip() or "modified", line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append({"status": status.strip(), "path": path.strip()})
    return files


def current_changed_files(repo: str, base_branch: str = "main") -> dict[str, Any]:
    status_text = git_output(["git", "status", "--short"], cwd=repo)
    status_files = parse_changed_files(status_text)
    ref = compare_ref(repo, base_branch)
    committed_files: list[dict[str, str]] = []
    diff_stat = ""
    if ref:
        committed_files = parse_changed_files(git_output(["git", "diff", "--name-status", f"{ref}...HEAD"], cwd=repo))
        diff_stat = git_output(["git", "diff", "--stat", f"{ref}...HEAD"], cwd=repo)
    merged = {item["path"]: item for item in committed_files + status_files}
    return {
        "compare_ref": ref,
        "changed_files": list(merged.values()),
        "working_tree_status": status_text,
        "diff_stat": diff_stat,
    }


def test_category_for_path(path: str) -> str:
    lowered = path.lower()
    if "test" in lowered or "spec" in lowered:
        return "test_changed"
    if any(token in lowered for token in ("controller", "resource", "api", "endpoint")):
        return "api"
    if any(token in lowered for token in ("repository", "dao", "database", "migration", ".sql")):
        return "database"
    if any(token in lowered for token in ("config", ".yml", ".yaml", ".properties", ".gitlab-ci")):
        return "configuration"
    if any(token in lowered for token in ("frontend", "component", ".tsx", ".jsx", ".vue", ".html", ".css")):
        return "frontend"
    return "unit_scope"


def recommended_test_scope(categories: dict[str, int]) -> list[str]:
    scope = ["unit"]
    if any(category in categories for category in ("api", "database", "configuration")):
        scope.append("integration")
    if categories.get("frontend"):
        scope.append("frontend/ui")
    if categories.get("test_changed"):
        scope.append("rerun_changed_tests")
    return scope


def current_change_test_report(
    *,
    base_branch: str = "main",
    latest_output: str = "",
    working_dir: str | None = None,
) -> dict[str, Any]:
    repo = resolve_project_dir(working_dir)
    changes = current_changed_files(repo, base_branch)
    categories = Counter(test_category_for_path(item["path"]) for item in changes["changed_files"])
    detected = detect_project_type(working_dir=repo)
    commands = discover_test_commands(working_dir=repo)
    reports = collect_test_reports(working_dir=repo, max_files=20)
    failure_analysis = analyze_failure_text(latest_output) if latest_output else None
    scope = recommended_test_scope(dict(categories))

    command_plan = []
    if commands["recommended_unit_command"]:
        command_plan.append({"scope": "unit", "command": commands["recommended_unit_command"], "required": True})
    if "integration" in scope and commands["recommended_integration_command"]:
        command_plan.append({"scope": "integration", "command": commands["recommended_integration_command"], "required": True})

    if not changes["changed_files"]:
        verdict = "no_code_changes_found"
    elif reports["report_count"] == 0:
        verdict = "tests_not_evidenced_yet"
    elif failure_analysis and failure_analysis.get("status") == "failed":
        verdict = "test_failures_need_attention"
    else:
        verdict = "test_report_ready"

    return {
        "ok": True,
        "tool": "test_current_change_report",
        "repository": repo,
        "verdict": verdict,
        "changed_files": changes["changed_files"],
        "change_categories": dict(categories),
        "recommended_test_scope": scope,
        "detected_project": detected,
        "available_test_commands": commands["commands"],
        "recommended_command_plan": command_plan,
        "test_reports": {
            "count": reports["report_count"],
            "paths": [report["path"] for report in reports["reports"]],
        },
        "latest_failure_analysis": failure_analysis,
        "diff_stat": changes["diff_stat"],
        "testing_guidance": [
            "Run unit tests first for fast feedback.",
            "Run integration tests when API, DB, configuration, or external behavior changed.",
            "Run focused tests while fixing failures, then rerun the broader suite.",
            "Attach test command results to the MR summary.",
        ],
        "next_actions": [
            "Run the recommended command plan.",
            "Use test_analyze_failures if any command fails.",
            "Use review_full_current_changes before MR creation if not already done.",
        ],
    }


def run_full_validation_pipeline(
    *,
    working_dir: str | None = None,
    test_command: str | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Run a full validation pipeline: tests → build → code quality.

    Steps (auto-detected per project type):
      1. Tests      — mvn test / npm test / pytest etc.
      2. Build      — mvn package -DskipTests / npm run build etc.
      3. Quality    — checkstyle (if checkstyle.xml present) for Maven

    Returns a combined verdict and per-step results.
    """
    repo = resolve_project_dir(working_dir)
    root = Path(repo)
    detected = detect_project_type(working_dir=repo)
    primary = detected.get("primary_type", "unknown")

    steps: list[dict[str, Any]] = []
    overall_ok = True

    # ── Step 1: Tests ─────────────────────────────────────────────────────────
    if test_command:
        test_cmd = test_command
    elif primary == "maven":
        test_cmd = f"{maven_cmd(root)} --no-transfer-progress test -DfailIfNoTests=false"
    elif primary == "gradle":
        test_cmd = f"{gradle_cmd(root)} test"
    elif primary == "node":
        test_cmd = "npm test --if-present"
    elif primary == "python":
        test_cmd = "python3 -m pytest -x -q"
    else:
        test_cmd = None

    test_result: dict[str, Any] = {}
    if test_cmd:
        r = execute_test_command(test_cmd, working_dir=repo, timeout_seconds=timeout_seconds)
        test_result = r
        step = {"step": "tests", "command": test_cmd, "ok": r.get("ok", False),
                "exit_code": r.get("exit_code", -1), "verdict": "passed" if r.get("ok") else "failed"}
        steps.append(step)
        if not r.get("ok"):
            overall_ok = False

    # ── Step 2: Build (skip if tests already failed to save time) ────────────
    build_result: dict[str, Any] = {}
    if overall_ok:
        if primary == "maven":
            build_cmd = f"{maven_cmd(root)} --no-transfer-progress package -DskipTests"
        elif primary == "gradle":
            build_cmd = f"{gradle_cmd(root)} build -x test"
        elif primary == "node":
            build_cmd = "npm run build --if-present"
        else:
            build_cmd = None

        if build_cmd:
            br = execute_test_command(build_cmd, working_dir=repo, timeout_seconds=300)
            build_result = br
            step = {"step": "build", "command": build_cmd, "ok": br.get("ok", False),
                    "exit_code": br.get("exit_code", -1), "verdict": "passed" if br.get("ok") else "failed"}
            steps.append(step)
            if not br.get("ok"):
                overall_ok = False

    # ── Step 3: Code quality gate (Maven validate => Checkstyle + PMD) ───────
    quality_result: dict[str, Any] = {}
    quality_skipped = True
    if primary == "maven":
        quality_cmd = f"{maven_cmd(root)} --no-transfer-progress validate"
        qr = execute_test_command(quality_cmd, working_dir=repo, timeout_seconds=120)
        quality_result = qr
        quality_skipped = False
        step = {"step": "quality_validate", "command": quality_cmd, "ok": qr.get("ok", False),
                "exit_code": qr.get("exit_code", -1), "verdict": "passed" if qr.get("ok") else "failed"}
        steps.append(step)
        if not qr.get("ok"):
            overall_ok = False

    passed = sum(1 for s in steps if s.get("ok"))
    failed = sum(1 for s in steps if not s.get("ok"))

    return {
        "ok": overall_ok,
        "verdict": "passed" if overall_ok else "failed",
        "repository": repo,
        "project_type": primary,
        "steps": steps,
        "steps_passed": passed,
        "steps_failed": failed,
        "test_result": test_result,
        "build_result": build_result,
        "quality_result": quality_result,
        "quality_skipped": quality_skipped,
        "summary": (
            f"All {passed} validation steps passed ✅"
            if overall_ok
            else f"{failed} of {len(steps)} validation steps failed ❌"
        ),
    }
