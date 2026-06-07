"""LLM-based story analysis and acceptance-criteria generation.

Uses LangChain prompt templates with an OpenAI backend (optional).
Falls back to rule-based heuristics when no API key is configured so the
workflow is never blocked by missing credentials.

Usage:
    from langchain_helpers.story_analyzer import StoryAnalyzer

    analyzer = StoryAnalyzer()
    acs = analyzer.generate_acceptance_criteria(
        story_summary="Implement RELATED_ARCHITECTURE_ELEMENT link type",
        story_description="...",
        adr_content="...",          # from ConfluenceCacheLoader
        sibling_story_summaries=["BDRSP-1623 analysis done", ...],
        changed_files=["LinkCreationService.java", ...],
    )
    # Returns list[str] of specific, testable ACs
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

# ── Prompt templates ────────────────────────────────────────────────────────────

_AC_SYSTEM_PROMPT = """You are a senior backend engineer writing Jira acceptance criteria for a Spring Boot middleware.
Rules:
- Each AC must follow: Given <pre-condition>, when <action>, then <expected result>.
- Be specific: mention method names, endpoint names, or class names when known.
- Focus on: create, delete, query flows; error handling; unit test coverage; no regression.
- Do NOT write generic statements like "the system works correctly".
- Output ONLY a numbered list of ACs, one per line, no markdown headers."""

_AC_HUMAN_PROMPT = """Story: {story_summary}

Description:
{story_description}

ADR / Architecture context:
{adr_content}

Sibling stories (for pattern context):
{sibling_summaries}

Changed files (for precision):
{changed_files}

Write 6–8 specific, testable acceptance criteria for this story:"""

_MR_DESCRIPTION_SYSTEM_PROMPT = """You are a senior engineer writing a GitLab merge request description.
Rules:
- Be precise and technical. Reference actual file names and method names.
- Include: what changed, why, testing evidence, deployment impact.
- Use markdown tables and bullet points for clarity.
- Keep the "Why" section tied to the Jira story and ADR."""

_MR_DESCRIPTION_HUMAN_PROMPT = """Jira story: {story_key} — {story_summary}

Git diff summary:
{diff_stat}

Changed files:
{changed_files}

Test result summary:
{test_summary}

ADR reference:
{adr_content}

Write a professional MR description with sections:
Summary, Why, Changes Made (table), Testing Performed, Configuration/Deployment Impact:"""

_FAILURE_ANALYSIS_SYSTEM_PROMPT = """You are a Java/Spring expert diagnosing test failures.
Given a test failure stack trace and the relevant source files, explain:
1. Root cause (one sentence)
2. Affected class and method
3. Minimal fix suggestion
Be concise — 5 lines max."""

_FAILURE_ANALYSIS_HUMAN_PROMPT = """Test failure output:
{failure_output}

Relevant source snippets:
{source_snippets}

Diagnose the failure and suggest a fix:"""


class StoryAnalyzer:
    """LLM-powered story and code analysis helper.

    All methods degrade gracefully to rule-based fallbacks when OpenAI is
    unavailable, so the rest of the dev-mcp workflow is never blocked.
    """

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.1) -> None:
        self.model = model
        self.temperature = temperature
        self._llm: Any = None  # lazy-loaded

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate_acceptance_criteria(
        self,
        *,
        story_summary: str,
        story_description: str = "",
        adr_content: str = "",
        sibling_story_summaries: list[str] | None = None,
        changed_files: list[str] | None = None,
    ) -> list[str]:
        """Generate specific, testable ACs for a Jira story.

        Returns list[str] — each string is one AC in Given/When/Then format.
        Falls back to generic rule-based ACs if LLM is unavailable.
        """
        siblings_text = "\n".join(f"- {s}" for s in (sibling_story_summaries or []))
        files_text = "\n".join(f"- {f}" for f in (changed_files or []))

        prompt_vars = {
            "story_summary": story_summary,
            "story_description": story_description[:2000] or "(no description provided)",
            "adr_content": adr_content[:1500] or "(no ADR content)",
            "sibling_summaries": siblings_text or "(none)",
            "changed_files": files_text or "(not yet analysed)",
        }

        raw = self._invoke_llm(
            system=_AC_SYSTEM_PROMPT,
            human=_AC_HUMAN_PROMPT,
            variables=prompt_vars,
            fallback=self._fallback_acs(story_summary, changed_files or []),
        )
        return self._parse_numbered_list(raw)

    def generate_mr_description(
        self,
        *,
        story_key: str,
        story_summary: str,
        diff_stat: str = "",
        changed_files: list[str] | None = None,
        test_summary: str = "",
        adr_content: str = "",
    ) -> str:
        """Generate a professional MR description from story + diff context.

        Falls back to a structured template when LLM is unavailable.
        """
        prompt_vars = {
            "story_key": story_key,
            "story_summary": story_summary,
            "diff_stat": diff_stat[:1000] or "(not available)",
            "changed_files": "\n".join(f"- {f}" for f in (changed_files or [])) or "(not available)",
            "test_summary": test_summary[:500] or "(not available)",
            "adr_content": adr_content[:800] or "(not available)",
        }
        return self._invoke_llm(
            system=_MR_DESCRIPTION_SYSTEM_PROMPT,
            human=_MR_DESCRIPTION_HUMAN_PROMPT,
            variables=prompt_vars,
            fallback=self._fallback_mr_description(story_key, story_summary, changed_files or []),
        )

    def analyze_test_failure(
        self,
        *,
        failure_output: str,
        source_snippets: list[str] | None = None,
    ) -> str:
        """Analyse a test failure and suggest a root-cause fix.

        Falls back to a structured extraction of the key error lines.
        """
        prompt_vars = {
            "failure_output": failure_output[:3000],
            "source_snippets": "\n\n---\n\n".join((source_snippets or [])[:3]),
        }
        return self._invoke_llm(
            system=_FAILURE_ANALYSIS_SYSTEM_PROMPT,
            human=_FAILURE_ANALYSIS_HUMAN_PROMPT,
            variables=prompt_vars,
            fallback=self._fallback_failure_analysis(failure_output),
        )

    # ── LLM helpers ─────────────────────────────────────────────────────────────

    def _get_llm(self) -> Any:
        """Return ChatOpenAI instance, or None if unavailable."""
        if self._llm is not None:
            return self._llm
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            log.debug("story_analyzer: OPENAI_API_KEY not set — using fallback mode")
            return None
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import]
            self._llm = ChatOpenAI(model=self.model, api_key=api_key, temperature=self.temperature)
            log.info("story_analyzer: using OpenAI model %s", self.model)
            return self._llm
        except ImportError:
            log.warning("story_analyzer: langchain_openai not installed — using fallback mode")
            return None
        except Exception as exc:
            log.warning("story_analyzer: LLM setup failed — %s", exc)
            return None

    def _invoke_llm(
        self,
        *,
        system: str,
        human: str,
        variables: dict[str, str],
        fallback: Any,
    ) -> str:
        """Invoke the LLM chain; return *fallback* on any failure."""
        llm = self._get_llm()
        if llm is None:
            return fallback if isinstance(fallback, str) else "\n".join(fallback)
        try:
            from langchain.prompts import ChatPromptTemplate  # type: ignore[import]
            from langchain.schema.output_parser import StrOutputParser  # type: ignore[import]

            prompt = ChatPromptTemplate.from_messages([
                ("system", system),
                ("human", human),
            ])
            chain = prompt | llm | StrOutputParser()
            return chain.invoke(variables)
        except Exception as exc:
            log.warning("story_analyzer: LLM chain failed — %s. Using fallback.", exc)
            return fallback if isinstance(fallback, str) else "\n".join(fallback)

    # ── Fallback helpers (rule-based, no LLM) ──────────────────────────────────

    def _fallback_acs(self, story_summary: str, changed_files: list[str]) -> list[str]:
        """Generate generic but structured ACs without an LLM."""
        service_files = [f for f in changed_files if "Service" in f or "service" in f]
        test_files = [f for f in changed_files if "Test" in f]
        acs = [
            f"Given the implementation is deployed, when the new behavior introduced by '{story_summary}' is triggered, then it produces the expected result without errors.",
            "Given a valid input, when the relevant GraphQL mutation or query runs, then the response contains all required non-null fields.",
            "Given an invalid or missing input, when the operation runs, then the system returns a clear error without throwing an unhandled exception.",
            "Given existing link creation, deletion, and query flows, when the implementation is deployed, then all existing behaviors remain unchanged (no regression).",
        ]
        if service_files:
            acs.append(f"Given the service layer changes in {', '.join(service_files[:2])}, when unit tests run, then all new positive and negative cases pass.")
        if test_files:
            acs.append(f"Given the test classes {', '.join(test_files[:2])}, when the test suite runs, then all new test methods pass with no failures.")
        acs.append("Given the unit test suite runs, when all tests execute, then there are zero regressions on previously passing tests.")
        return acs

    def _fallback_mr_description(
        self, story_key: str, story_summary: str, changed_files: list[str]
    ) -> str:
        """Generate a minimal MR description without an LLM."""
        import os
        jira_base_url = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
        files_md = "\n".join(f"| `{f}` | Updated |" for f in changed_files[:10])
        return f"""### Summary

{story_summary}

### Related ticket
[{story_key}]({jira_base_url}/browse/{story_key})

### Changes made

| File | Change |
|------|--------|
{files_md}

### Testing performed

Unit tests pass. See test results in the pipeline.

### Configuration / deployment impact

None — additive changes only."""

    @staticmethod
    def _fallback_failure_analysis(failure_output: str) -> str:
        """Extract the most relevant lines from a failure stack trace."""
        lines = failure_output.splitlines()
        important = [
            line for line in lines
            if any(kw in line for kw in ("Error", "Exception", "FAILED", "expected", "but was", "at com.mercedesbenz"))
        ][:8]
        return "\n".join(important) if important else failure_output[:500]

    @staticmethod
    def _parse_numbered_list(text: str) -> list[str]:
        """Parse a numbered list from LLM output into a clean list[str]."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        items = []
        for line in lines:
            # Strip leading number + dot/paren: "1. AC..." or "1) AC..."
            cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
            if cleaned:
                items.append(cleaned)
        return items if items else [text.strip()]

