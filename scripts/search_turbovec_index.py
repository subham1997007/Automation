#!/usr/bin/env python3
"""
search_turbovec_index.py
========================
Query the turbovec code index to find the most relevant files in the
Automation folder for any natural-language question.

Usage:
  python3 Automation/scripts/search_turbovec_index.py "jira story approval gate"
  python3 Automation/scripts/search_turbovec_index.py "MCP server guardrails" --top 10
  python3 Automation/scripts/search_turbovec_index.py "how does feature bootstrap work" --json

Options:
  --top N     Return top N results (default: 5)
  --json      Output raw JSON (for piping to AI tools or scripts)
"""

import sys
import json
import pathlib
import argparse
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
import turbovec

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT  = pathlib.Path(__file__).resolve().parents[2]
MEMORY_DIR = REPO_ROOT / "Automation" / ".memory"
INDEX_PATH = MEMORY_DIR / "code-index.tvim"
META_PATH  = MEMORY_DIR / "code-index-meta.json"


def load_meta() -> dict:
    if not META_PATH.exists():
        print("❌  Index not found. Run first:\n    python3 Automation/scripts/build_turbovec_index.py")
        sys.exit(1)
    return json.loads(META_PATH.read_text())


def embed_query(query: str, vocabulary: list[str], dim: int) -> np.ndarray:
    """Re-create the same TF-IDF vocabulary from metadata and embed the query."""
    vectorizer = TfidfVectorizer(
        vocabulary=vocabulary,
        sublinear_tf=True,
        strip_accents="unicode",
        analyzer="word",
        ngram_range=(1, 2),
    )
    # fit_transform needs a corpus — use vocab words as dummy corpus
    dummy = [" ".join(vocabulary[:50])]
    vectorizer.fit(dummy)
    vec = vectorizer.transform([query]).toarray().astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def search(query: str, top_k: int = 5, as_json: bool = False):
    meta  = load_meta()
    index = turbovec.IdMapIndex.load(str(INDEX_PATH))

    vocab = meta["vocabulary"]
    dim   = meta["vector_dim"]

    q_vec = embed_query(query, vocab, dim)
    scores, ids = index.search(q_vec, k=top_k)

    results = []
    for score, file_id in zip(scores[0], ids[0]):
        info = meta["files"].get(str(int(file_id)))
        if info:
            results.append({
                "score"  : float(score),
                "path"   : info["path"],
                "size_b" : info["size_b"],
                "summary": info["summary"],
            })

    if as_json:
        print(json.dumps(results, indent=2))
        return

    print(f"\n🔍  Query: \"{query}\"")
    print(f"    Top {len(results)} results from {meta['total_files']} indexed files\n")
    for i, r in enumerate(results, 1):
        bar = "█" * min(int(r["score"] * 30), 30)
        print(f"  {i}. [{bar:<30}] {r['score']:.3f}")
        print(f"     {r['path']}")
        print(f"     {r['summary'][:100]}...")
        print()


def main():
    parser = argparse.ArgumentParser(description="Search turbovec code index")
    parser.add_argument("query",      nargs="?",        help="Search query")
    parser.add_argument("--top",      type=int, default=5, help="Number of results")
    parser.add_argument("--json",     action="store_true", help="Output JSON")
    args = parser.parse_args()

    if not args.query:
        # Interactive mode
        print("turbovec code search  (type 'quit' to exit)")
        print("Index:", str(INDEX_PATH.relative_to(REPO_ROOT)))
        meta = load_meta()
        print(f"Files: {meta['total_files']}  |  Built: {meta['built_at']}\n")
        while True:
            try:
                q = input("🔍  Query: ").strip()
                if q.lower() in ("quit", "exit", "q"):
                    break
                if q:
                    search(q, top_k=args.top, as_json=args.json)
            except (KeyboardInterrupt, EOFError):
                break
    else:
        search(args.query, top_k=args.top, as_json=args.json)


if __name__ == "__main__":
    main()

