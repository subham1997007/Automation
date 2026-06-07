"""Local lightweight brain for Automation request routing.

This module uses llama-cpp-python with a small local GGUF model when configured.
If no model is configured, it falls back to deterministic rules so Automation
keeps working without a local LLM.

Context-memory growth is controlled via the companion context_manager module:
- Sliding window   : conversation turns are capped by AUTOMATION_CTX_TURNS_KEEP
- Summarisation    : old turns are compressed (AUTOMATION_CTX_REFINE_ENABLED=true)
- RAG snippets     : only keyword-relevant lines are injected into the prompt
- Tool trimming    : large tool-response fields are truncated before context injection
- Token budget     : AUTOMATION_CTX_MAX_TOKENS caps the total prompt size
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from langchain_helpers.context_manager import (
        ContextWindow,
        sliding_window_knowledge,
        rag_extract,
        estimate_tokens,
        token_budget_chars,
    )
    _CTX_MANAGER_AVAILABLE = True
except Exception:
    _CTX_MANAGER_AVAILABLE = False
    ContextWindow = None  # type: ignore[assignment,misc]

# Module-level conversation window — shared across calls within the same MCP server process
_conversation_window: "ContextWindow | None" = None


def _get_conversation_window() -> "ContextWindow | None":
    """Return (or lazily create) the module-level ContextWindow."""
    global _conversation_window
    if not _CTX_MANAGER_AVAILABLE:
        return None
    if _conversation_window is None:
        _conversation_window = ContextWindow()
    return _conversation_window


SMALL_TASK_PATTERNS = (
    r"\b(status|state|progress)\b",
    r"\b(read|show|tell me|summari[sz]e|explain)\b",
    r"\bsubtasks?\b",
    r"\bfeature|epic\b",
    r"\breport\b",
    r"\bwhich files?\b",
    r"\btest failure|logs?\b",
)

COMPLEX_TASK_PATTERNS = (
    r"\bimplement|fix|change|modify|update code|create mr|merge request\b",
    r"\brefactor|migration|architecture\b",
    r"\bwrite to jira|update jira|create subtask\b",
    r"\bpush|commit|branch\b",
    r"\bcreate (a |new )?(story|issue|ticket|task|epic|feature)\b",
    r"\bnew story|new issue|new ticket\b",
)

RISKY_ACTION_PATTERNS = (
    r"\bupdate jira|write to jira|create subtask|delete subtask\b",
    r"\bcommit|push|create mr|merge request\b",
    r"\bmodify|change|delete|remove\b",
)


def local_brain_enabled() -> bool:
    value = (os.getenv("AUTOMATION_LOCAL_BRAIN") or "auto").strip().lower()
    if value in {"0", "false", "no", "off", "disabled"}:
        return False
    if value in {"1", "true", "yes", "on", "enabled"}:
        return True
    return bool(model_path())


def model_path() -> str:
    configured = os.getenv("AUTOMATION_LOCAL_BRAIN_MODEL") or os.getenv("LLAMA_CPP_MODEL")
    if configured and Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser())
    return ""


def classify_request(
    request: str,
    *,
    story: dict[str, Any] | None = None,
    code_context: dict[str, Any] | None = None,
    knowledge_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a request before expensive AI/model work is used."""
    request = (request or "").strip()
    rule_decision = _rule_based_decision(request, story=story, code_context=code_context)

    if not local_brain_enabled():
        return {
            **rule_decision,
            "engine": "rules",
            "local_model_used": False,
            "model_path": "",
            "knowledge_packet_stats": _knowledge_packet_stats(knowledge_packet),
            "note": "Set AUTOMATION_LOCAL_BRAIN_MODEL to a GGUF file to enable llama-cpp-python routing.",
        }

    path = model_path()
    if not path:
        return {
            **rule_decision,
            "engine": "rules",
            "local_model_used": False,
            "model_path": "",
            "knowledge_packet_stats": _knowledge_packet_stats(knowledge_packet),
            "note": "Local brain enabled, but no GGUF model path was found.",
        }

    try:
        model_decision = _llama_decision(
            path,
            request,
            story=story,
            code_context=code_context,
            knowledge_packet=knowledge_packet,
        )
        return {
            **rule_decision,
            **model_decision,
            "engine": "llama-cpp-python",
            "local_model_used": True,
            "model_path": path,
            "knowledge_packet_stats": _knowledge_packet_stats(knowledge_packet),
            "fallback_decision": rule_decision,
        }
    except Exception as exc:
        return {
            **rule_decision,
            "engine": "rules",
            "local_model_used": False,
            "model_path": path,
            "knowledge_packet_stats": _knowledge_packet_stats(knowledge_packet),
            "local_model_error": str(exc),
        }


def _rule_based_decision(
    request: str,
    *,
    story: dict[str, Any] | None,
    code_context: dict[str, Any] | None,
) -> dict[str, Any]:
    text = request.lower()
    small = any(re.search(pattern, text) for pattern in SMALL_TASK_PATTERNS)
    complex_ = any(re.search(pattern, text) for pattern in COMPLEX_TASK_PATTERNS)
    risky = any(re.search(pattern, text) for pattern in RISKY_ACTION_PATTERNS)

    if complex_:
        task_size = "complex"
        route = "devflow_main_agent"
        can_handle_locally = False
    elif small:
        task_size = "small"
        route = "local_tools_or_local_brain"
        can_handle_locally = True
    else:
        task_size = "medium"
        route = "devflow_with_local_preflight"
        can_handle_locally = False

    confidence = "medium"
    if code_context and (code_context.get("confidence") == "high"):
        confidence = "high"
    if not request:
        confidence = "low"

    return {
        "task_size": task_size,
        "route": route,
        "can_handle_locally": can_handle_locally and not risky,
        "approval_required": risky,
        "confidence": confidence,
        "reason": _reason(task_size, risky),
        "recommended_next_step": _next_step(task_size, risky),
    }


def _llama_decision(
    path: str,
    request: str,
    *,
    story: dict[str, Any] | None,
    code_context: dict[str, Any] | None,
    knowledge_packet: dict[str, Any] | None,
) -> dict[str, Any]:
    from llama_cpp import Llama  # type: ignore[import]

    n_ctx = int(os.getenv("AUTOMATION_LOCAL_BRAIN_CTX", "2048"))
    max_tokens = int(os.getenv("AUTOMATION_LOCAL_BRAIN_MAX_TOKENS", "220"))
    llm = Llama(
        model_path=path,
        n_ctx=n_ctx,
        n_threads=int(os.getenv("AUTOMATION_LOCAL_BRAIN_THREADS", "4")),
        verbose=False,
    )

    # ── Build prompt within token budget ──────────────────────────────────────
    # Reserve max_tokens for the response; the rest is available for the prompt.
    prompt_budget_chars = (n_ctx - max_tokens) * 4  # rough chars available
    prompt = _prompt(
        request,
        story=story,
        code_context=code_context,
        knowledge_packet=knowledge_packet,
        prompt_budget_chars=prompt_budget_chars,
    )

    # ── Record user turn in the sliding-window conversation window ────────────
    window = _get_conversation_window()
    if window is not None:
        window.add_turn("user", request[:300])

    output = llm(
        prompt,
        max_tokens=max_tokens,
        temperature=0.0,
        stop=["</json>", "\n\nHuman:"],
    )
    text = (output.get("choices") or [{}])[0].get("text", "").strip()

    # Record assistant response in the window
    if window is not None:
        window.add_turn("assistant", text[:200])

    parsed = _parse_json_object(text)
    allowed = {
        "task_size",
        "route",
        "can_handle_locally",
        "approval_required",
        "confidence",
        "reason",
        "recommended_next_step",
        "knowledge_focus",
    }
    return {k: v for k, v in parsed.items() if k in allowed}


def _prompt(
    request: str,
    *,
    story: dict[str, Any] | None,
    code_context: dict[str, Any] | None,
    knowledge_packet: dict[str, Any] | None,
    prompt_budget_chars: int = 6400,
) -> str:
    story_summary = (story or {}).get("summary") or ""
    story_status = (story or {}).get("status") or ""
    code_confidence = (code_context or {}).get("confidence") or ""
    scan_method = (code_context or {}).get("scan_method") or ""

    # ── Fixed overhead (template text without variable content) ───────────────
    fixed_overhead = 420  # chars for the prompt scaffold

    # ── Sliding-window conversation history ───────────────────────────────────
    # Reserve budget for history: up to 15% of total prompt budget
    history_budget = min(800, prompt_budget_chars // 7)
    history_text = ""
    window = _get_conversation_window()
    if window is not None:
        history_text = window.get_history_for_prompt(reserved_tokens=history_budget // 4)
        history_text = history_text[:history_budget]

    # ── Knowledge packet — RAG-extracted, sliding-window trimmed ─────────────
    # Keywords from code context help focus RAG extraction
    keywords = list((code_context or {}).get("keywords") or [])[:6]
    knowledge_budget = prompt_budget_chars - fixed_overhead - len(request) - len(history_text)
    knowledge_budget = max(300, knowledge_budget)

    if _CTX_MANAGER_AVAILABLE and knowledge_packet:
        knowledge_text = sliding_window_knowledge(
            knowledge_packet,
            max_chars=knowledge_budget,
            keywords=keywords or None,
        )
    else:
        knowledge_text = _knowledge_for_prompt(knowledge_packet, max_chars=knowledge_budget)

    # ── Assemble prompt ───────────────────────────────────────────────────────
    history_section = f"\nPrevious context:\n{history_text}\n" if history_text else ""
    return (
        f"You are Automation Brain, a tiny local router for a Jira/GitLab development workflow.\n"
        f"Classify the request. Do not propose risky actions without approval.\n"
        f"Return only one compact JSON object.\n\n"
        f"Allowed task_size: small, medium, complex\n"
        f"Allowed route: local_tools_or_local_brain, devflow_with_local_preflight, devflow_main_agent\n"
        f"{history_section}\n"
        f"Request: {request}\n"
        f"Story summary: {story_summary}\n"
        f"Story status: {story_status}\n"
        f"Code confidence: {code_confidence}\n"
        f"Scan method: {scan_method}\n"
        f"Knowledge packet (memory + repo graph + confluence + code context):\n"
        f"{knowledge_text}\n\n"
        f"JSON:\n"
    )


def _knowledge_packet_stats(packet: dict[str, Any] | None) -> dict[str, Any]:
    packet = packet or {}
    confluence_pages = packet.get("confluence_pages") or []
    repo_hotspots = ((packet.get("repo_graph") or {}).get("hotspots") or [])
    return {
        "present": bool(packet),
        "sections": sorted(packet.keys()),
        "confluence_pages": len(confluence_pages),
        "repo_hotspots": len(repo_hotspots),
    }


def _knowledge_for_prompt(packet: dict[str, Any] | None, max_chars: int = 2200) -> str:
    """Serialise and trim rich knowledge input for local LLM context budget.

    When context_manager is available, delegates to sliding_window_knowledge()
    for RAG-based extraction.  Otherwise falls back to simple truncation.
    """
    packet = packet or {}
    if not packet:
        return "{}"
    if _CTX_MANAGER_AVAILABLE:
        try:
            return sliding_window_knowledge(packet, max_chars=max_chars)
        except Exception:
            pass
    # Fallback: simple JSON truncation
    try:
        text = json.dumps(packet, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return "{}"
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def _reason(task_size: str, risky: bool) -> str:
    if risky:
        return "The request may write to Jira/GitLab or change code, so approval gates must stay active."
    if task_size == "small":
        return "The request looks like read-only status, summary, report, or lookup work."
    if task_size == "complex":
        return "The request appears to require code, Jira, GitLab, or MR workflow orchestration."
    return "The request needs some context before deciding whether local handling is enough."


def _next_step(task_size: str, risky: bool) -> str:
    if risky:
        return "Use DevFlow approval gates before any write/change action."
    if task_size == "small":
        return "Try local memory, FAISS, SQLite metadata, or read-only MCP tools first."
    return "Run DevFlow preflight and escalate to the main AI agent only if needed."
