#!/usr/bin/env python3
"""
build_turbovec_index.py
=======================
Builds a turbovec semantic search index for the Automation folder.

What it does:
  1. Scans all source files (.py, .sh, .md, .json, .yaml, .yml, .ts, .js)
     in the Automation folder (skipping .venv, __pycache__, node_modules).
  2. Converts each file's text content into a TF-IDF embedding vector.
  3. Stores all vectors in a turbovec IdMapIndex (fast SIMD-accelerated search).
  4. Saves the index to  Automation/.memory/code-index.tvim
  5. Saves a metadata file  Automation/.memory/code-index-meta.json
     (maps numeric IDs back to file paths + short summaries).

Usage:
  python3 Automation/scripts/build_turbovec_index.py

Search usage (from another script or AI tool):
  python3 Automation/scripts/search_turbovec_index.py "jira story approval gate"

Output size: typically < 500 KB for the full Automation source tree.
"""

import os
import json
import time
import hashlib
import pathlib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
import turbovec

# ── Configuration ────────────────────────────────────────────────────────────
REPO_ROOT   = pathlib.Path(__file__).resolve().parents[2]
AUTO_DIR    = REPO_ROOT / "Automation"
MEMORY_DIR  = AUTO_DIR / ".memory"
INDEX_PATH  = MEMORY_DIR / "code-index.tvim"
META_PATH   = MEMORY_DIR / "code-index-meta.json"

EXTENSIONS = {".py", ".sh", ".md", ".json", ".yaml", ".yml", ".ts", ".js"}
SKIP_DIRS  = {".venv", "venv", "__pycache__", "node_modules", ".git", "runtime", "reports"}
MAX_FILE_BYTES = 128 * 1024   # skip files > 128 KB (binary/huge files)
VECTOR_DIM     = 512           # TF-IDF vector size (dimensionality)
QUANTIZE_BITS  = 4             # turbovec bit-width (4 = compact, 8 = more accurate)

# ── Helpers ──────────────────────────────────────────────────────────────────

def collect_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Walk root and return all source files matching EXTENSIONS."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune ignored directories in-place
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            fpath = pathlib.Path(dirpath) / fname
            if fpath.suffix.lower() in EXTENSIONS:
                result.append(fpath)
    return sorted(result)


def read_file_safe(path: pathlib.Path) -> str:
    """Read a file as text, return empty string on any error."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def short_summary(text: str, n: int = 120) -> str:
    """Return first n meaningful characters as a short summary."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    summary = " ".join(lines)[:n]
    return summary


def file_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:8]

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    start = time.time()

    print(f"📂  Scanning: {AUTO_DIR}")
    files = collect_files(AUTO_DIR)
    print(f"   Found {len(files)} source files")

    # Read all file contents
    contents = []
    valid_files = []
    for fp in files:
        text = read_file_safe(fp)
        if text.strip():
            contents.append(text)
            valid_files.append(fp)

    print(f"   {len(valid_files)} files have readable content")

    # ── Build TF-IDF embeddings ───────────────────────────────────────────
    print(f"\n🔢  Building TF-IDF embeddings (dim={VECTOR_DIM})...")
    vectorizer = TfidfVectorizer(
        max_features=VECTOR_DIM,
        sublinear_tf=True,
        strip_accents="unicode",
        analyzer="word",
        ngram_range=(1, 2),
        min_df=1,
    )
    tfidf_matrix = vectorizer.fit_transform(contents)   # sparse (N x VECTOR_DIM)
    dense_matrix = tfidf_matrix.toarray().astype(np.float32)   # (N x VECTOR_DIM)

    # Normalize rows to unit vectors for cosine similarity search
    norms = np.linalg.norm(dense_matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    dense_matrix /= norms

    print(f"   Matrix shape: {dense_matrix.shape}")

    # ── Build turbovec index ──────────────────────────────────────────────
    print(f"\n⚡  Building turbovec IdMapIndex (bit_width={QUANTIZE_BITS})...")
    index = turbovec.IdMapIndex(dim=VECTOR_DIM, bit_width=QUANTIZE_BITS)

    ids = np.arange(len(valid_files), dtype=np.uint64)
    index.add_with_ids(dense_matrix, ids)
    index.prepare()   # warm up SIMD caches

    index.write(str(INDEX_PATH))
    print(f"   ✅  Index saved → {INDEX_PATH.relative_to(REPO_ROOT)}")

    # ── Save metadata ─────────────────────────────────────────────────────
    print(f"\n📋  Writing metadata...")
    meta = {
        "built_at"    : time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_files" : len(valid_files),
        "vector_dim"  : VECTOR_DIM,
        "bit_width"   : QUANTIZE_BITS,
        "vocabulary"  : list(vectorizer.get_feature_names_out()),
        "files"       : {}
    }

    for idx, fp in enumerate(valid_files):
        rel = str(fp.relative_to(REPO_ROOT))
        text = contents[idx]
        meta["files"][str(idx)] = {
            "id"      : idx,
            "path"    : rel,
            "size_b"  : fp.stat().st_size,
            "hash"    : file_hash(text),
            "summary" : short_summary(text),
        }

    META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print(f"   ✅  Metadata saved → {META_PATH.relative_to(REPO_ROOT)}")

    # ── Report ────────────────────────────────────────────────────────────
    index_size = INDEX_PATH.stat().st_size
    meta_size  = META_PATH.stat().st_size
    elapsed    = time.time() - start

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✅  turbovec index built successfully
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Files indexed   : {len(valid_files)}
  Vector dim      : {VECTOR_DIM}
  Quantize bits   : {QUANTIZE_BITS}
  Index size      : {index_size / 1024:.1f} KB
  Metadata size   : {meta_size / 1024:.1f} KB
  Time taken      : {elapsed:.2f}s
  Index path      : {INDEX_PATH.relative_to(REPO_ROOT)}
  Meta path       : {META_PATH.relative_to(REPO_ROOT)}

  Run search:
    python3 Automation/scripts/search_turbovec_index.py "your query"
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    main()

