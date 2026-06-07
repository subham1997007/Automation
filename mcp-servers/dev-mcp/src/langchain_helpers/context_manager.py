"""Context memory manager for local LLM interactions.

Implements all six strategies to prevent context memory bloat:

1. Sliding window   — drop oldest turns when approaching the token limit
2. Summarization    — compress old turns into a one-sentence summary using the local LLM
3. RAG snippets     — extract only keyword-relevant lines from large documents
4. Cache respect    — honour TTL gates before re-injecting Confluence / Jira content
5. Tool trimming    — truncate large tool-response fields before they enter context
6. Token budget     — enforce a hard token-count ceiling on every prompt section

Environment variables (all optional):
    AUTOMATION_CTX_MAX_TOKENS        — hard token ceiling for local-brain prompt (default 1800)
    AUTOMATION_CTX_TURNS_KEEP        — sliding window: max conversation turns to keep (default 6)
    AUTOMATION_CTX_SUMMARY_TOKENS    — token budget for the compressed old-turn summary (default 120)
    AUTOMATION_CTX_TOOL_MAX_CHARS    — max chars for any single tool-response field (default 800)
    AUTOMATION_CTX_RAG_MAX_CHARS     — max chars returned from rag_extract() (default 600)
    AUTOMATION_CTX_REFINE_ENABLED    — set 'true' to enable LLM summarisation of old turns (default false)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

# ── Tuneable defaults (overridable via env) ───────────────────────────────────
_MAX_TOKENS: int = int(os.getenv("AUTOMATION_CTX_MAX_TOKENS", "1800"))
_TURNS_KEEP: int = int(os.getenv("AUTOMATION_CTX_TURNS_KEEP", "6"))
_SUMMARY_TOKENS: int = int(os.getenv("AUTOMATION_CTX_SUMMARY_TOKENS", "120"))
_TOOL_MAX_CHARS: int = int(os.getenv("AUTOMATION_CTX_TOOL_MAX_CHARS", "800"))
_RAG_MAX_CHARS: int = int(os.getenv("AUTOMATION_CTX_RAG_MAX_CHARS", "600"))
_REFINE_ENABLED: bool = os.getenv("AUTOMATION_CTX_REFINE_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on",
}

# Rough token estimator — 1 token ≈ 4 characters (works for code + English prose)
_CHARS_PER_TOKEN: int = 4


# ── Token helpers ─────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Return a rough token count estimate (chars / 4)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def token_budget_chars(max_tokens: int) -> int:
    """Convert a token ceiling to a character ceiling."""
    return max_tokens * _CHARS_PER_TOKEN


# ── 1. Sliding window ─────────────────────────────────────────────────────────

@dataclass
class ConversationTurn:
    role: str          # 'user' | 'assistant' | 'tool'
    content: str
    token_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.token_count = estimate_tokens(self.content)


class ContextWindow:
    """Sliding-window conversation history with optional LLM summarisation.

    Usage::

        window = ContextWindow()
        window.add_turn("user", "Implement BDRSP-1234")
        window.add_turn("assistant", "Starting codebase scan ...")
        history_str = window.get_history_for_prompt()
    """

    def __init__(
        self,
        max_turns: int = _TURNS_KEEP,
        max_tokens: int = _MAX_TOKENS,
        summary_tokens: int = _SUMMARY_TOKENS,
    ) -> None:
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.summary_tokens = summary_tokens
        self._turns: list[ConversationTurn] = []
        self._summary: str = ""          # compressed representation of dropped turns

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_turn(self, role: str, content: str) -> None:
        """Append a new turn, dropping old ones when the window is full."""
        turn = ConversationTurn(role=role, content=content)
        self._turns.append(turn)
        self._enforce_window()

    def get_history_for_prompt(self, reserved_tokens: int = 0) -> str:
        """Return formatted history string that fits within the token budget."""
        budget = self.max_tokens - reserved_tokens
        lines: list[str] = []

        # Always prepend the running summary of dropped turns
        if self._summary:
            lines.append(f"[Earlier context summary]: {self._summary}")
            budget -= estimate_tokens(self._summary)

        # Walk newest → oldest, add while tokens remain
        included: list[ConversationTurn] = []
        for turn in reversed(self._turns):
            if budget - turn.token_count < 0:
                break
            included.append(turn)
            budget -= turn.token_count

        for turn in reversed(included):
            lines.append(f"{turn.role.upper()}: {turn.content}")

        return "\n".join(lines)

    def summarize_old_turns(self, llm_fn: Callable[[str], str] | None = None) -> str:
        """Compress the oldest half of the turns into a short summary.

        *llm_fn* receives a plain-text prompt and should return a plain-text
        summary string.  When omitted or None, a heuristic extractive summary
        is used instead.
        """
        if not self._turns:
            return self._summary
        half = max(1, len(self._turns) // 2)
        old_turns = self._turns[:half]
        self._turns = self._turns[half:]

        combined = "\n".join(f"{t.role}: {t.content[:200]}" for t in old_turns)
        if llm_fn is not None and _REFINE_ENABLED:
            prompt = (
                f"Summarise the following conversation in one sentence (max 80 words):\n\n{combined}\n\nSummary:"
            )
            try:
                self._summary = (llm_fn(prompt) or "").strip()[:token_budget_chars(_SUMMARY_TOKENS)]
            except Exception:
                self._summary = _extractive_summary(combined)
        else:
            self._summary = _extractive_summary(combined)
        return self._summary

    # ── Internal ───────────────────────────────────────────────────────────────

    def _enforce_window(self) -> None:
        """Drop oldest turns if we exceed max_turns or max_tokens."""
        # Hard turn limit
        while len(self._turns) > self.max_turns:
            dropped = self._turns.pop(0)
            self._summary = _merge_summary(self._summary, dropped.content)

        # Soft token limit — drop oldest if total exceeds budget
        total = sum(t.token_count for t in self._turns)
        while total > self.max_tokens and len(self._turns) > 1:
            dropped = self._turns.pop(0)
            total -= dropped.token_count
            self._summary = _merge_summary(self._summary, dropped.content)


def _extractive_summary(text: str, max_chars: int = 300) -> str:
    """Simple extractive summary: first sentence of each turn."""
    sentences: list[str] = []
    for line in text.splitlines():
        match = re.search(r"[^.!?]*[.!?]", line)
        if match:
            sentences.append(match.group(0).strip())
        if sum(len(s) for s in sentences) >= max_chars:
            break
    return " ".join(sentences)[:max_chars]


def _merge_summary(existing: str, new_content: str) -> str:
    """Merge a dropped-turn snippet into the running summary."""
    snippet = new_content[:80].replace("\n", " ").strip()
    combined = f"{existing} | {snippet}" if existing else snippet
    return combined[:400]


# ── 2. Summarisation helper (standalone) ─────────────────────────────────────

def summarize_text(text: str, llm_fn: Callable[[str], str] | None = None, max_chars: int = 300) -> str:
    """Summarise *text* using *llm_fn* or fall back to extractive summary."""
    if llm_fn is not None and _REFINE_ENABLED:
        prompt = f"Summarise in one short sentence:\n\n{text[:1200]}\n\nSummary:"
        try:
            return (llm_fn(prompt) or "").strip()[:max_chars]
        except Exception:
            pass
    return _extractive_summary(text, max_chars)


# ── 3. RAG snippet extraction ─────────────────────────────────────────────────

def rag_extract(
    text: str,
    keywords: list[str],
    max_chars: int = _RAG_MAX_CHARS,
    context_lines: int = 2,
) -> str:
    """Extract keyword-relevant lines from *text* instead of injecting the whole thing.

    Algorithm:
    1. Score each line by number of keyword hits.
    2. Include top-scored lines plus *context_lines* neighbours.
    3. Trim to *max_chars*.

    Example::

        snippet = rag_extract(full_confluence_page, ["link type", "mutation"], max_chars=600)
    """
    if not text or not keywords:
        return text[:max_chars]

    lower_kws = [k.lower() for k in keywords]
    lines = text.splitlines()
    scores: list[int] = []
    for line in lines:
        lower_line = line.lower()
        scores.append(sum(1 for kw in lower_kws if kw in lower_line))

    # Collect line indices worth including (score > 0 + neighbours)
    selected: set[int] = set()
    for i, score in enumerate(scores):
        if score > 0:
            for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
                selected.add(j)

    if not selected:
        # No keyword hits — return the first max_chars chars
        return text[:max_chars]

    result_lines = [lines[i] for i in sorted(selected)]
    result = "\n".join(result_lines)
    return result[:max_chars]


# ── 4. Cache gate ─────────────────────────────────────────────────────────────

def is_cache_fresh(cached_at_epoch: int | float, ttl_seconds: int) -> bool:
    """Return True if *cached_at_epoch* is within *ttl_seconds*.

    Used as a guard before re-fetching Confluence / Jira content.
    Centralises the TTL logic so callers don't reimplement it.
    """
    import time
    return (time.time() - cached_at_epoch) <= ttl_seconds


# ── 5. Tool output trimmer ────────────────────────────────────────────────────

# Fields that are safe to truncate aggressively when they appear in tool responses.
_LARGE_FIELD_KEYS = frozenset({
    "description",
    "body",
    "content",
    "stdout",
    "stderr",
    "diff",
    "full_diff",
    "changes",
    "raw_output",
    "test_output",
    "confluence_content",
    "confluence_context",
    "full_pipeline",
    "confluence_pages",
    "readme",
    "file_content",
    "source_snippet",
})

# Fields to drop entirely from MCP responses (they bloat context but add no value to the LLM)
_DROP_FIELD_KEYS = frozenset({
    "target_id",
    "cached_at",
    "_cached_at_epoch",
    "_jira_id",
    "_ttl_seconds",
    "token",
    "auth_header",
})


def trim_tool_response(
    payload: Any,
    max_chars_per_field: int = _TOOL_MAX_CHARS,
    *,
    depth: int = 0,
    max_depth: int = 6,
) -> Any:
    """Recursively trim large strings inside a tool-response payload.

    - Strings longer than *max_chars_per_field* are truncated with ``…``.
    - Known large fields are trimmed more aggressively (½ budget).
    - Known metadata-only fields are dropped.
    - Lists longer than 20 items are capped.
    - Recursion is limited to *max_depth* to avoid stack overflows.
    """
    if depth > max_depth:
        return payload

    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if key in _DROP_FIELD_KEYS:
                continue
            if key in _LARGE_FIELD_KEYS and isinstance(value, str) and len(value) > max_chars_per_field // 2:
                result[key] = value[: max_chars_per_field // 2] + "…"
            else:
                result[key] = trim_tool_response(value, max_chars_per_field, depth=depth + 1, max_depth=max_depth)
        return result

    if isinstance(payload, list):
        trimmed = [
            trim_tool_response(item, max_chars_per_field, depth=depth + 1, max_depth=max_depth)
            for item in payload[:20]
        ]
        if len(payload) > 20:
            trimmed.append(f"… ({len(payload) - 20} more items truncated)")
        return trimmed

    if isinstance(payload, str) and len(payload) > max_chars_per_field:
        return payload[:max_chars_per_field] + "…"

    return payload


def compact_mcp_response(payload: dict[str, Any], *, stage: str = "") -> dict[str, Any]:
    """Produce a context-budget-aware version of an MCP stage response.

    Applies *trim_tool_response* and additionally collapses fields that are
    only needed internally (e.g. ``feature_context``, ``story_context``) into
    compact summaries so the IDE doesn't store the full Jira payload in every
    subsequent prompt turn.
    """
    compacted = trim_tool_response(payload, _TOOL_MAX_CHARS)

    # Collapse verbose nested contexts into compact summaries
    for ctx_key in ("story_context", "feature_context", "refreshed_story_context"):
        ctx = compacted.get(ctx_key)
        if isinstance(ctx, dict):
            story = ctx.get("story") or {}
            feature = (ctx.get("feature_context") or {}).get("feature") or {}
            compacted[ctx_key] = {
                "_compacted": True,
                "story_key": story.get("key"),
                "story_summary": story.get("summary"),
                "story_status": story.get("status"),
                "feature_key": feature.get("key"),
                "feature_name": feature.get("name"),
            }

    # Trim large codebase_analysis fields
    cb = compacted.get("codebase_analysis")
    if isinstance(cb, dict):
        cb.pop("vectorstore_metadata", None)
        cb.pop("repo_cognition_graph", None)
        cb.pop("confluence_context", None)

    return compacted


# ── 6. Knowledge-packet sliding window ───────────────────────────────────────

def sliding_window_knowledge(
    packet: dict[str, Any],
    max_chars: int | None = None,
    keywords: list[str] | None = None,
) -> str:
    """Serialise *packet* using RAG + sliding window to stay within token budget.

    Steps:
    1. Drop empty / None values.
    2. Apply RAG extraction to string-valued leaves using *keywords*.
    3. Trim each section to its proportional share of *max_chars*.
    4. Return a JSON string within budget.
    """
    if max_chars is None:
        max_chars = token_budget_chars(_MAX_TOKENS) // 3   # knowledge gets ≤ ⅓ of total budget

    if not packet:
        return "{}"

    # Per-section budget = total / number of sections
    sections = {k: v for k, v in packet.items() if v is not None}
    if not sections:
        return "{}"

    per_section = max(120, max_chars // max(1, len(sections)))
    result: dict[str, Any] = {}

    for key, value in sections.items():
        if isinstance(value, str):
            if keywords:
                value = rag_extract(value, keywords, max_chars=per_section)
            elif len(value) > per_section:
                value = value[:per_section] + "…"
            result[key] = value
        elif isinstance(value, (dict, list)):
            try:
                serialised = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            except Exception:
                serialised = str(value)
            if keywords:
                serialised = rag_extract(serialised, keywords, max_chars=per_section)
            elif len(serialised) > per_section:
                serialised = serialised[:per_section] + "…"
            result[key] = serialised
        else:
            result[key] = value

    try:
        out = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        out = "{}"

    # Final hard clamp
    if len(out) > max_chars:
        out = out[:max_chars] + "…}"
    return out

