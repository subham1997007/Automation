#!/usr/bin/env python3
"""Select the best Automation MCP profile for a user request."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys


PROFILE_RULES: list[tuple[str, list[str]]] = [
    (
        "dev",
        [
            r"\bimplement\b",
            r"\bdevelop\b",
            r"\bcode changes?\b",
            r"\bfix this story\b",
            r"\bend[- ]?to[- ]?end\b",
            r"\bcreate mr\b",
            r"\bmerge request\b",
        ],
    ),
    (
        "jira",
        [
            r"\bjira\b",
            r"\bstor(y|ies)\b",
            r"\brefine\b",
            r"\bupdate story\b",
            r"\bmodify story\b",
            r"\bcreate subtask\b",
            r"\bsubtask\b",
            r"\bfeature\b",
            r"\bepic\b",
            r"\bacceptance criteria\b",
        ],
    ),
    (
        "gitlab",
        [
            r"\bgitlab\b",
            r"\bbranch\b",
            r"\bpipeline\b",
            r"\bcommit\b",
            r"\bpush\b",
            r"\bmr\b",
            r"\bmerge request\b",
        ],
    ),
    (
        "test",
        [
            r"\btests?\b",
            r"\bunit\b",
            r"\bintegration\b",
            r"\bfailure\b",
            r"\bcoverage\b",
            r"\bmvn\b",
            r"\bgradle\b",
        ],
    ),
    (
        "review",
        [
            r"\breview\b",
            r"\bcode quality\b",
            r"\brisk\b",
            r"\bacceptance coverage\b",
            r"\bstatic analysis\b",
        ],
    ),
]

ALLOWED_PROFILES = {"dev", "jira", "gitlab", "test", "review"}


def _automation_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def _load_env_local(env_file: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def _boolish(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "auto"}


def _append_route_log(automation_dir: pathlib.Path, message: str) -> None:
    try:
        memory_dir = automation_dir / ".memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (memory_dir / "auto-profile.log").open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def _resolve_model_path(raw_path: str, automation_dir: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(raw_path)
    if p.is_absolute():
        return p
    return automation_dir / p


def _regex_scores(request: str) -> dict[str, int]:
    text = request.lower()
    if not text.strip():
        return {}

    scores: dict[str, int] = {}
    for profile, patterns in PROFILE_RULES:
        for pattern in patterns:
            if re.search(pattern, text):
                scores[profile] = scores.get(profile, 0) + 1

    if "implement" in text and re.search(r"\b[A-Z]+-\d+\b", request):
        scores["dev"] = scores.get("dev", 0) + 4

    return scores


def _regex_profile(request: str) -> str:
    scores = _regex_scores(request)
    if not scores:
        return "dev"

    if not scores:
        return "dev"

    priority = {"dev": 5, "jira": 4, "gitlab": 3, "test": 2, "review": 1}
    return sorted(scores, key=lambda p: (-scores[p], -priority[p]))[0]


def _llm_profile(request: str, env: dict[str, str], automation_dir: pathlib.Path) -> tuple[str | None, float, str]:
    if not _boolish(env.get("AUTOMATION_LOCAL_BRAIN"), default=False):
        return None, 0.0, "local-brain disabled"

    model_raw = env.get("AUTOMATION_LOCAL_BRAIN_MODEL", "models/qwen2.5-1.5b-instruct-q4_k_m.gguf")
    model_path = _resolve_model_path(model_raw, automation_dir)
    if not model_path.exists():
        return None, 0.0, f"model missing: {model_path}"

    py_bin = automation_dir / "mcp-servers" / "dev-mcp" / ".venv" / "bin" / "python"
    if not py_bin.exists():
        return None, 0.0, f"python missing: {py_bin}"

    ctx = env.get("AUTOMATION_LOCAL_BRAIN_CTX", "2048")
    gpu_layers = env.get("AUTOMATION_LOCAL_BRAIN_GPU_LAYERS", "-1")
    max_tokens = env.get("AUTOMATION_LOCAL_BRAIN_ROUTE_MAX_TOKENS", "48")

    prompt = (
        "Classify this request into exactly one profile: dev, jira, gitlab, test, review. "
        "Answer in one line using this format only: profile=<name>;confidence=<0-1>.\n"
        f"Request: {request}"
    )

    code = r'''
import sys
from llama_cpp import Llama

model_path, n_ctx, n_gpu_layers, max_tokens, prompt = sys.argv[1:6]
llm = Llama(model_path=model_path, n_ctx=int(n_ctx), n_gpu_layers=int(n_gpu_layers), verbose=False)
out = llm(
    prompt,
    max_tokens=max(8, int(max_tokens)),
    temperature=0.0,
    top_p=1.0,
)
print(out["choices"][0]["text"].strip())
'''

    try:
        proc = subprocess.run(
            [str(py_bin), "-c", code, str(model_path), str(ctx), str(gpu_layers), str(max_tokens), prompt],
            capture_output=True,
            text=True,
            timeout=int(env.get("AUTOMATION_LOCAL_BRAIN_ROUTE_TIMEOUT_SECONDS", "18")),
            check=False,
        )
    except Exception as exc:
        return None, 0.0, f"llm route exec failed: {exc}"

    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout or "unknown error").strip().splitlines()[:1]
        return None, 0.0, f"llm route failed: {reason[0] if reason else 'unknown'}"

    text = (proc.stdout or "").strip().lower()
    profile_match = re.search(r"\b(dev|jira|gitlab|test|review)\b", text)
    if not profile_match:
        return None, 0.0, "llm output missing valid profile"
    profile = profile_match.group(1)

    confidence_match = re.search(r"confidence\s*=\s*(0(?:\.\d+)?|1(?:\.0+)?)", text)
    if confidence_match:
        confidence = float(confidence_match.group(1))
    else:
        confidence = 0.60

    if profile not in ALLOWED_PROFILES:
        return None, confidence, f"invalid profile: {profile}"
    return profile, confidence, "ok"


def select_profile(request: str) -> str:
    automation_dir = _automation_dir()
    env_file = automation_dir / ".env.local"

    # Environment variables in process take precedence over .env.local.
    merged_env = _load_env_local(env_file)
    merged_env.update({k: v for k, v in os.environ.items() if k.startswith("AUTOMATION_")})

    min_conf = float(merged_env.get("AUTOMATION_LOCAL_BRAIN_ROUTE_MIN_CONFIDENCE", "0.75"))
    llm_profile, llm_conf, reason = _llm_profile(request, merged_env, automation_dir)
    regex_scores = _regex_scores(request)
    fallback = _regex_profile(request)

    if llm_profile is not None and llm_conf >= min_conf:
        fallback_score = regex_scores.get(fallback, 0)
        llm_anchor_score = regex_scores.get(llm_profile, 0)
        if llm_profile != fallback and fallback_score >= max(2, llm_anchor_score + 1):
            _append_route_log(
                automation_dir,
                (
                    f"router=regex profile={fallback} llm_profile={llm_profile} "
                    f"llm_conf={llm_conf:.2f} reason=strong-regex-signal"
                ),
            )
            return fallback
        _append_route_log(
            automation_dir,
            f"router=local profile={llm_profile} confidence={llm_conf:.2f} request={request[:160]}",
        )
        return llm_profile

    _append_route_log(
        automation_dir,
        f"router=regex profile={fallback} llm_profile={llm_profile or '-'} llm_conf={llm_conf:.2f} reason={reason}",
    )
    return fallback


if __name__ == "__main__":
    print(select_profile(" ".join(sys.argv[1:])))
