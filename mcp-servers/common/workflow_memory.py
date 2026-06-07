"""Shared LangGraph workflow and memory helpers for Automation MCP servers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

try:
    from langgraph.graph import END, StateGraph
except ImportError:
    END = "__end__"
    StateGraph = None


def automation_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_env_local() -> dict[str, str]:
    env_path = automation_dir() / ".env.local"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    except OSError:
        return values
    return values


def _boolish(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "auto"}


def _resolve_model_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return automation_dir() / candidate


def _interactive_session_block(tool_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    lowered = tool_name.lower()
    mutating_tokens = (
        "create",
        "delete",
        "update",
        "apply",
        "push",
        "run",
        "manage",
        "implement",
        "refine",
    )
    is_mutating = any(token in lowered for token in mutating_tokens)
    questions = [
        {
            "id": "output_preference",
            "question": "Do you want a brief summary or full detailed output for this step?",
            "options": ["brief", "detailed"],
        },
        {
            "id": "scope_confirmation",
            "question": "Should I continue with the same scope, or adjust the scope before the next step?",
            "options": ["continue", "adjust scope"],
        },
    ]
    if is_mutating:
        questions.append(
            {
                "id": "approval",
                "question": "This step can change data/state. Do you approve proceeding to the next action?",
                "options": ["yes", "no"],
            }
        )

    return {
        "required": True,
        "mode": "always",
        "tool": tool_name,
        "is_mutating_action": is_mutating,
        "input_preview": {k: str(v)[:120] for k, v in list(inputs.items())[:6]},
        "questions": questions,
        "note": "Ask the user these questions and wait for explicit response before continuing.",
    }


def _local_brain_refine(
    *,
    tool_name: str,
    inputs: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    env = _load_env_local()
    env.update({k: v for k, v in os.environ.items() if k.startswith("AUTOMATION_")})
    if not _boolish(env.get("AUTOMATION_LOCAL_BRAIN"), default=False):
        return None
    if not _boolish(env.get("AUTOMATION_LOCAL_BRAIN_REFINE_TOOL_IO"), default=True):
        return None

    dev_python = automation_dir() / "mcp-servers" / "dev-mcp" / ".venv" / "bin" / "python"
    model_path = _resolve_model_path(env.get("AUTOMATION_LOCAL_BRAIN_MODEL"))
    if not dev_python.exists() or not model_path or not model_path.exists():
        return None

    payload = {
        "tool": tool_name,
        "inputs": inputs,
        "result": result,
    }
    try:
        raw_json = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        raw_json = str(payload)
    max_chars = int(env.get("AUTOMATION_LOCAL_BRAIN_REFINE_MAX_CHARS", "12000"))
    payload_slice = raw_json[:max_chars]

    n_ctx = env.get("AUTOMATION_LOCAL_BRAIN_CTX", "2048")
    n_gpu_layers = env.get("AUTOMATION_LOCAL_BRAIN_GPU_LAYERS", "-1")
    max_tokens = env.get("AUTOMATION_LOCAL_BRAIN_REFINE_MAX_TOKENS", "180")
    timeout_seconds = int(env.get("AUTOMATION_LOCAL_BRAIN_REFINE_TIMEOUT_SECONDS", "20"))

    code = r'''
import sys
from llama_cpp import Llama

model_path, n_ctx, n_gpu_layers, max_tokens, payload_json = sys.argv[1:6]
prompt = (
    "You are a preprocessing assistant. Summarize payload for a stronger external model. "
    "Return four short lines with prefixes exactly: intent:, facts:, risks:, next:."
    "\nPayload:\n" + payload_json
)

llm = Llama(model_path=model_path, n_ctx=int(n_ctx), n_gpu_layers=int(n_gpu_layers), verbose=False)
out = llm(prompt, max_tokens=max(64, int(max_tokens)), temperature=0.0, top_p=1.0)
text = out["choices"][0]["text"].strip()
print(text)
'''

    try:
        proc = subprocess.run(
            [
                str(dev_python),
                "-c",
                code,
                str(model_path),
                str(n_ctx),
                str(n_gpu_layers),
                str(max_tokens),
                payload_slice,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception:
        return None

    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None

    refined: dict[str, Any] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            key = k.strip().lower()
            if key in {"intent", "facts", "risks", "next"}:
                refined[key] = v.strip()

    if not refined:
        refined = {"intent": raw[:280]}

    return {
        "source": "local-brain",
        "payload_chars": min(len(raw_json), max_chars),
        "truncated": len(raw_json) > max_chars,
        "summary": refined,
    }


def memory_path(server_name: str, memory_key: str) -> Path:
    safe_key = "".join(ch for ch in memory_key if ch.isalnum() or ch in {"-", "_", "."}) or "default"
    path = automation_dir() / ".memory" / server_name
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{safe_key}.json"


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_memory(server_name: str, memory_key: str) -> dict[str, Any]:
    path = memory_path(server_name, memory_key)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_memory(server_name: str, memory_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = memory_path(server_name, memory_key)
    current = load_memory(server_name, memory_key)
    current.update(payload)
    current["updated_at_epoch"] = int(time.time())
    path.write_text(json.dumps(current, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {"path": str(path), "updated_at_epoch": current["updated_at_epoch"]}


def load_repo_cognition_preflight() -> dict[str, Any]:
    """Load the repo cognition graph summary before a tool runs.

    This is intentionally compact so MCP responses stay fast and readable. The
    full graph lives in Automation/docs/repo-graph.html and the AI-facing memory
    lives in Automation/.memory/codebase-index.json.
    """
    index_path = automation_dir() / ".memory" / "codebase-index.json"
    if not index_path.exists():
        return {
            "available": False,
            "path": str(index_path),
            "status": "missing",
            "message": "Repo cognition index is missing. Run Automation/graph.sh once to generate it.",
        }
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "path": str(index_path),
            "status": "unreadable",
            "message": str(exc),
        }

    meta = index.get("_meta", {})
    summary = index.get("graph_summary", {})
    layers = index.get("graph_layers", {})
    hotspots = index.get("graph_hotspots", [])
    ttl_days = int(meta.get("ttl_days") or 7)
    generated = summary.get("generated_at") or meta.get("graph_last_updated")
    stale = False
    if generated:
        try:
            # generated_at is local ISO format; compare with epoch through fromisoformat.
            from datetime import datetime

            generated_epoch = datetime.fromisoformat(generated).timestamp()
            stale = (time.time() - generated_epoch) > ttl_days * 86400
        except Exception:
            stale = False

    return {
        "available": True,
        "path": str(index_path),
        "status": "stale" if stale else "ready",
        "generated_at": generated,
        "rule": meta.get("graph_rule")
        or "Check repo cognition before editing files or writing story impact.",
        "summary": {
            "build_system": meta.get("build_system"),
            "total_code_files": summary.get("total_code_files"),
            "total_dependencies": summary.get("total_dependencies"),
            "high_coupling_files": summary.get("high_coupling_files"),
            "health": summary.get("health"),
        },
        "layer_index": {
            name: {
                "description": data.get("description"),
                "file_count": data.get("file_count"),
                "sample_files": (data.get("files") or [])[:5],
            }
            for name, data in list(layers.items())[:10]
        },
        "hotspots": hotspots[:8],
    }


def _build_knowledge_packet(
    server_name: str,
    tool_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Build a compact knowledge packet that every profile response must carry.

    Lightweight by design — loads only from disk caches (no live Jira/git calls).
    Uses .setdefault() semantics so the caller should always prefer this value
    only when the richer dev-mcp version hasn't already been set.
    """
    sections_loaded: list[str] = []

    # ── 1. Repo cognition graph ─────────────────────────────────────────────
    repo_graph: dict[str, Any] = {"available": False}
    index_path = automation_dir() / ".memory" / "codebase-index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            meta = index.get("_meta", {})
            summary = index.get("graph_summary", {})
            repo_graph = {
                "available": True,
                "health": summary.get("health"),
                "total_code_files": summary.get("total_code_files"),
                "high_coupling_files": summary.get("high_coupling_files"),
                "build_system": meta.get("build_system"),
                "hotspots": (index.get("graph_hotspots") or [])[:5],
            }
            sections_loaded.append("repo_graph")
        except Exception:
            pass

    # ── 2. Story memory (keyed from jira_id in inputs if present) ───────────
    story_memory: dict[str, Any] = {}
    jira_id: str = str(inputs.get("jira_id") or inputs.get("story_id") or "").upper()
    if jira_id:
        safe_key = "".join(ch for ch in jira_id if ch.isalnum() or ch in {"-", "_"})
        story_mem_path = automation_dir() / ".memory" / "devflow" / "stories" / f"{safe_key}.json"
        if story_mem_path.exists():
            try:
                raw = json.loads(story_mem_path.read_text(encoding="utf-8"))
                story_memory = {
                    "jira_id": jira_id,
                    "stage": raw.get("stage"),
                    "next_gate": raw.get("next_gate"),
                    "readiness_blockers": raw.get("readiness_blockers") or [],
                    "scan_method": raw.get("scan_method"),
                    "updated_at_epoch": raw.get("updated_at_epoch"),
                }
                sections_loaded.append("story_memory")
            except Exception:
                pass

    # ── 3. Confluence cache index (page count + recent titles) ──────────────
    confluence_summary: dict[str, Any] = {}
    conf_index_path = automation_dir() / ".memory" / "confluence-cache" / "index.json"
    if conf_index_path.exists():
        try:
            conf_index = json.loads(conf_index_path.read_text(encoding="utf-8"))
            pages = conf_index.get("cached_pages") or []
            confluence_summary = {
                "cached_pages_count": len(pages),
                "recent_titles": [p.get("title") or p.get("url", "")[:60] for p in pages[:3]],
            }
            sections_loaded.append("confluence_cache")
        except Exception:
            pass

    # ── 4. Local brain availability check ───────────────────────────────────
    env = _load_env_local()
    env.update({k: v for k, v in os.environ.items() if k.startswith("AUTOMATION_")})
    local_brain_enabled = _boolish(env.get("AUTOMATION_LOCAL_BRAIN"), default=False)
    model_path = _resolve_model_path(env.get("AUTOMATION_LOCAL_BRAIN_MODEL"))
    model_available = bool(model_path and model_path.exists())

    local_brain: dict[str, Any] = {
        "engine": "local-brain" if (local_brain_enabled and model_available) else "unavailable",
        "enabled": local_brain_enabled,
        "model_available": model_available,
        "server": server_name,
        "tool": tool_name,
        "knowledge_packet_stats": {
            "present": True,
            "sections_loaded": sections_loaded,
            "jira_id_detected": jira_id or None,
        },
        "repo_graph": repo_graph,
        "story_memory": story_memory,
        "confluence_summary": confluence_summary,
    }

    return {
        "runtime_build_marker": "knowledge-packet-v1",
        "knowledge_packet_sections": sections_loaded,
        "local_brain": local_brain,
    }


def enrich_response(
    result: dict[str, Any],
    server_name: str,
    tool_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Inject the knowledge packet into any MCP response using .setdefault().

    Safe to call on every profile — if dev-mcp has already set these fields with
    a richer version, .setdefault() leaves them untouched.
    """
    kp = _build_knowledge_packet(server_name, tool_name, inputs)
    result.setdefault("runtime_build_marker", kp["runtime_build_marker"])
    result.setdefault("knowledge_packet_sections", kp["knowledge_packet_sections"])
    result.setdefault("local_brain", kp["local_brain"])
    return result


def execute_workflow(
    *,
    server_name: str,
    tool_name: str,
    memory_key: str,
    inputs: dict[str, Any],
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Execute a tool through a tiny deterministic LangGraph workflow with memory."""

    def load_memory_node(state: dict[str, Any]) -> dict[str, Any]:
        state["previous_memory"] = load_memory(server_name, memory_key)
        return state

    def load_repo_cognition_node(state: dict[str, Any]) -> dict[str, Any]:
        state["repo_cognition_preflight"] = load_repo_cognition_preflight()
        return state

    def run_tool_node(state: dict[str, Any]) -> dict[str, Any]:
        state["result"] = operation()
        env = _load_env_local()
        env.update({k: v for k, v in os.environ.items() if k.startswith("AUTOMATION_")})

        if _boolish(env.get("AUTOMATION_INTERACTIVE_SESSION_REQUIRED"), default=True):
            state["result"]["interactive_session"] = _interactive_session_block(tool_name, inputs)

        refined = _local_brain_refine(
            tool_name=tool_name,
            inputs=inputs,
            result=state["result"],
        )
        if refined:
            state["result"]["local_brain_refinement"] = refined
        return state

    def save_memory_node(state: dict[str, Any]) -> dict[str, Any]:
        result = state["result"]
        input_hash = stable_hash(inputs)
        output_hash = stable_hash(result)
        previous = state.get("previous_memory") or {}
        state["memory_write"] = save_memory(
            server_name,
            memory_key,
            {
                "server": server_name,
                "tool": tool_name,
                "memory_key": memory_key,
                "last_inputs": inputs,
                "last_input_hash": input_hash,
                "last_output_hash": output_hash,
                "previous_output_hash": previous.get("last_output_hash"),
                "stable_output": previous.get("last_output_hash") == output_hash,
            },
        )
        result["workflow"] = {
            "engine": "langgraph" if StateGraph else "sequential_fallback",
            "server": server_name,
            "tool": tool_name,
            "memory": state["memory_write"],
            "repo_cognition_preflight": state.get("repo_cognition_preflight"),
            "input_hash": input_hash,
            "output_hash": output_hash,
            "stable_output": previous.get("last_output_hash") == output_hash,
            "loaded_memory": bool(previous),
        }
        # ── Knowledge packet — permanent on every profile response ────────────
        enrich_response(result, server_name, tool_name, inputs)
        return state

    state: dict[str, Any] = {
        "server": server_name,
        "tool": tool_name,
        "memory_key": memory_key,
        "inputs": inputs,
    }
    if StateGraph:
        graph = StateGraph(dict)
        graph.add_node("load_memory", load_memory_node)
        graph.add_node("load_repo_cognition", load_repo_cognition_node)
        graph.add_node("run_tool", run_tool_node)
        graph.add_node("save_memory", save_memory_node)
        graph.set_entry_point("load_memory")
        graph.add_edge("load_memory", "load_repo_cognition")
        graph.add_edge("load_repo_cognition", "run_tool")
        graph.add_edge("run_tool", "save_memory")
        graph.add_edge("save_memory", END)
        state = graph.compile().invoke(state)
    else:
        for node in (load_memory_node, load_repo_cognition_node, run_tool_node, save_memory_node):
            state = node(state)
    return state["result"]
