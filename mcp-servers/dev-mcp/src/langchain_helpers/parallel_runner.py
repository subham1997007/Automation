"""Threading-based parallel execution for independent LangGraph nodes.

LangGraph runs nodes sequentially by default. This module adds true
concurrent execution for nodes that have no data dependency on each other.

Usage (in dev_client.py):

    from langchain_helpers.parallel_runner import run_parallel

    results = run_parallel(
        ("jira",    lambda: story_payload(jira_id)),
        ("memory",  lambda: load_story_memory(jira_id)),
        ("git",     lambda: git_snapshot(working_dir, base_branch)),
    )
    story_context = results["jira"]
    memory        = results["memory"]
    snapshot      = results["git"]
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

log = logging.getLogger(__name__)


def run_parallel(
    *tasks: tuple[str, Callable[[], Any]],
    timeout: float = 30.0,
    max_workers: int = 6,
) -> dict[str, Any]:
    """Run multiple callables concurrently and return a {name: result} dict.

    Args:
        tasks: Sequence of (name, callable) pairs. Each callable is called
               with no arguments in its own thread.
        timeout: Maximum seconds to wait for all tasks.
        max_workers: Thread pool size.

    Returns:
        dict mapping each task name to its return value.
        On error the value is {"_error": str(exc)}.

    Example:
        results = run_parallel(
            ("jira",   lambda: fetch_jira(id)),
            ("memory", lambda: load_memory(id)),
        )
        story = results["jira"]
        mem   = results["memory"]
    """
    start = time.time()
    results: dict[str, Any] = {}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(tasks))) as pool:
        future_to_name = {pool.submit(fn): name for name, fn in tasks}
        for future in as_completed(future_to_name, timeout=timeout):
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                log.warning("parallel_runner: task '%s' failed — %s", name, exc)
                results[name] = {"_error": str(exc), "_task": name}

    elapsed = round((time.time() - start) * 1000)
    log.debug("parallel_runner: %d tasks done in %dms", len(tasks), elapsed)
    return results

