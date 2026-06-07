"""FAISS-backed codebase vector store for semantic code search.

Indexes Java source files from src/ using local sentence-transformer embeddings
(no API key required). The index is persisted under .memory/code-vectorstore/
and rebuilt automatically when source files change.

Usage:
    from langchain_helpers.code_vectorstore import CodeVectorStore

    vs = CodeVectorStore(repo_path="/path/to/project")
    results = vs.search("which files handle CM to AM link creation?", k=5)
    for r in results:
        print(r["file"], r["snippet"])

    # Or get a direct answer using an LLM (optional — requires OPENAI_API_KEY)
    answer = vs.ask("Where is RELATED_ARCHITECTURE_ELEMENT handled?")
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Embeddings model — runs locally, no API key needed
_DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Source file patterns to index
_INDEX_GLOBS = [
    "src/main/java/**/*.java",
    "src/test/java/**/*.java",
    "src/main/resources/graphql/*.graphqls",
]

# Files/dirs to skip
_SKIP_DIRS = {"target", ".git", "node_modules", "__pycache__", ".venv"}


def _repo_hash(repo_path: str, max_files: int = 200) -> str:
    """Compute a quick fingerprint from Java file mtimes to detect changes."""
    paths = sorted(
        p for pattern in _INDEX_GLOBS
        for p in Path(repo_path).glob(pattern)
        if not any(skip in p.parts for skip in _SKIP_DIRS)
    )[:max_files]
    digest = hashlib.md5(
        b"".join(f"{p.stat().st_mtime_ns}".encode() for p in paths if p.exists())
    ).hexdigest()
    return digest


class CodeVectorStore:
    """Semantic search over project source files using FAISS + local embeddings.

    Falls back gracefully to an empty result list when langchain/faiss are not
    installed so the rest of the dev-mcp workflow is unaffected.
    """

    def __init__(
        self,
        repo_path: str,
        index_dir: str | Path | None = None,
        embedding_model: str = _DEFAULT_EMBEDDING_MODEL,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
    ) -> None:
        self.repo_path = str(Path(repo_path).resolve())
        self.embedding_model = embedding_model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        if index_dir is None:
            try:
                from langchain_helpers.repo_resolver import get_resolver
                index_dir = get_resolver().memory_path("code-vectorstore")
            except Exception:
                index_dir = Path(repo_path).resolve() / "Automation" / ".memory" / "code-vectorstore"
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._meta_file = self.index_dir / "meta.json"
        self._sqlite_file = self.index_dir / "metadata.sqlite"
        self._vectorstore: Any = None  # lazy-loaded

    # ── Public API ─────────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 6) -> list[dict[str, str]]:
        """Return top-k code snippets most relevant to *query*.

        Each result has: file, snippet, score.
        Returns empty list if FAISS/sentence-transformers are not available.
        """
        vs = self._get_or_build_store()
        if vs is None:
            log.debug("code_vectorstore: store unavailable — returning empty results")
            return []
        try:
            docs = vs.similarity_search_with_score(query, k=k)
            results = []
            for doc, score in docs:
                results.append({
                    "file": doc.metadata.get("source", "unknown"),
                    "snippet": doc.page_content[:400],
                    "score": round(float(score), 4),
                })
            return results
        except Exception as exc:
            log.warning("code_vectorstore: search failed — %s", exc)
            return []

    def ask(self, question: str, k: int = 5) -> str:
        """Answer *question* using retrieved code context + an LLM.

        Requires OPENAI_API_KEY to be set. Falls back to raw snippet list
        if no LLM is configured or langchain_openai is not installed.
        """
        snippets = self.search(question, k=k)
        if not snippets:
            return "No relevant code found for the question."

        context = "\n\n---\n\n".join(
            f"File: {s['file']}\n{s['snippet']}" for s in snippets
        )
        try:
            from langchain_openai import ChatOpenAI  # type: ignore[import]
            from langchain.prompts import ChatPromptTemplate  # type: ignore[import]
            from langchain.schema.output_parser import StrOutputParser  # type: ignore[import]

            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                raise EnvironmentError("OPENAI_API_KEY not set")

            llm = ChatOpenAI(model="gpt-4o-mini", api_key=api_key, temperature=0)
            prompt = ChatPromptTemplate.from_messages([
                ("system", (
                    "You are a senior Java developer familiar with this Spring Boot codebase. "
                    "Answer the question using only the provided code snippets. "
                    "Be precise, reference file names and method names."
                )),
                ("human", "Code snippets:\n{context}\n\nQuestion: {question}"),
            ])
            chain = prompt | llm | StrOutputParser()
            return chain.invoke({"context": context, "question": question})
        except Exception as exc:
            log.info("code_vectorstore.ask: LLM unavailable (%s) — returning snippet paths", exc)
            return "\n".join(f"[{s['file']}]\n{s['snippet'][:200]}" for s in snippets)

    def rebuild(self) -> bool:
        """Force-rebuild the FAISS index from current source files."""
        self._vectorstore = None
        if self._meta_file.exists():
            self._meta_file.unlink()
        store = self._build_store()
        return store is not None

    def is_index_fresh(self) -> bool:
        """Return True if the persisted index matches the current repo state."""
        if not self._meta_file.exists():
            return False
        if not self._sqlite_file.exists():
            return False
        try:
            meta = json.loads(self._meta_file.read_text(encoding="utf-8"))
            return meta.get("repo_hash") == _repo_hash(self.repo_path)
        except Exception:
            return False

    def metadata_summary(self) -> dict[str, Any]:
        """Return JSON + SQLite metadata status for the current FAISS index."""
        meta: dict[str, Any] = {}
        if self._meta_file.exists():
            try:
                meta = json.loads(self._meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        sqlite_summary: dict[str, Any] = {
            "available": self._sqlite_file.exists(),
            "path": str(self._sqlite_file),
        }
        if self._sqlite_file.exists():
            try:
                with sqlite3.connect(self._sqlite_file) as con:
                    file_count = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                    chunk_count = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                    sqlite_summary.update({"file_count": file_count, "chunk_count": chunk_count})
            except Exception as exc:
                sqlite_summary.update({"available": False, "error": str(exc)})

        return {
            "engine": "faiss",
            "index_dir": str(self.index_dir),
            "json_metadata": meta,
            "sqlite_metadata": sqlite_summary,
            "fresh": self.is_index_fresh(),
        }

    # ── Internal helpers ────────────────────────────────────────────────────────

    def _get_or_build_store(self) -> Any:
        """Return the FAISS vector store, loading or building it as needed."""
        if self._vectorstore is not None:
            return self._vectorstore
        if self.is_index_fresh():
            self._vectorstore = self._load_store()
        if self._vectorstore is None:
            self._vectorstore = self._build_store()
        return self._vectorstore

    def _embeddings(self) -> Any:
        """Return a HuggingFaceEmbeddings instance (local, no API key)."""
        from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore[import]
        return HuggingFaceEmbeddings(
            model_name=self.embedding_model,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"batch_size": 32, "normalize_embeddings": True},
        )

    def _build_store(self) -> Any:
        """Build FAISS index from source files and persist to disk."""
        try:
            from langchain_community.vectorstores import FAISS  # type: ignore[import]
            try:
                from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore[import]
            except ImportError:
                from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore[import]
            from langchain_community.document_loaders import DirectoryLoader, TextLoader  # type: ignore[import]
        except ImportError as exc:
            log.warning("code_vectorstore: langchain/faiss not installed — %s", exc)
            return None

        log.info("code_vectorstore: building index for %s …", self.repo_path)
        start = time.time()

        # Collect source files
        all_docs = []
        for pattern in _INDEX_GLOBS:
            try:
                loader = DirectoryLoader(
                    self.repo_path,
                    glob=pattern,
                    loader_cls=TextLoader,
                    loader_kwargs={"encoding": "utf-8", "autodetect_encoding": True},
                    silent_errors=True,
                )
                all_docs.extend(loader.load())
            except Exception as exc:
                log.debug("code_vectorstore: loader error for %s — %s", pattern, exc)

        if not all_docs:
            log.warning("code_vectorstore: no source files found in %s", self.repo_path)
            return None

        # Split into chunks
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", " ", ""],
        )
        chunks = splitter.split_documents(all_docs)
        log.info("code_vectorstore: %d chunks from %d files", len(chunks), len(all_docs))

        # Build FAISS index
        try:
            embeddings = self._embeddings()
            vs = FAISS.from_documents(chunks, embeddings)
            vs.save_local(str(self.index_dir))
        except Exception as exc:
            log.warning("code_vectorstore: FAISS build failed — %s", exc)
            return None

        # Persist metadata
        try:
            from langchain_helpers.project_detector import detect_project
            proj = detect_project(self.repo_path)
            proj_type = proj.project_type
            proj_lang = proj.language
        except Exception:
            proj_type = "unknown"
            proj_lang = "unknown"

        meta = {
            "repo_hash": _repo_hash(self.repo_path),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "file_count": len(all_docs),
            "chunk_count": len(chunks),
            "embedding_model": self.embedding_model,
            "elapsed_seconds": round(time.time() - start, 1),
            "project_type": proj_type,
            "language": proj_lang,
            "globs_used": _INDEX_GLOBS,
            "metadata_store": {
                "json": str(self._meta_file),
                "sqlite": str(self._sqlite_file),
            },
        }
        self._meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self._persist_sqlite_metadata(all_docs, chunks, meta)
        log.info(
            "code_vectorstore: index built in %.1f s (%d chunks)",
            meta["elapsed_seconds"],
            len(chunks),
        )
        return vs

    def _persist_sqlite_metadata(self, docs: list[Any], chunks: list[Any], meta: dict[str, Any]) -> None:
        """Persist searchable file/chunk metadata next to the FAISS index."""
        try:
            with sqlite3.connect(self._sqlite_file) as con:
                con.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS index_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        repo_hash TEXT NOT NULL,
                        built_at TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        file_count INTEGER NOT NULL,
                        chunk_count INTEGER NOT NULL,
                        project_type TEXT,
                        language TEXT,
                        elapsed_seconds REAL
                    );
                    CREATE TABLE IF NOT EXISTS files (
                        path TEXT PRIMARY KEY,
                        extension TEXT,
                        size_bytes INTEGER,
                        mtime_ns INTEGER,
                        line_count INTEGER,
                        chunk_count INTEGER DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS chunks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        file_path TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        char_count INTEGER NOT NULL,
                        preview TEXT,
                        FOREIGN KEY(file_path) REFERENCES files(path)
                    );
                    """
                )
                con.execute("DELETE FROM files")
                con.execute("DELETE FROM chunks")
                con.execute(
                    """
                    INSERT INTO index_runs (
                        repo_hash, built_at, embedding_model, file_count, chunk_count,
                        project_type, language, elapsed_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meta.get("repo_hash"),
                        meta.get("built_at"),
                        meta.get("embedding_model"),
                        meta.get("file_count"),
                        meta.get("chunk_count"),
                        meta.get("project_type"),
                        meta.get("language"),
                        meta.get("elapsed_seconds"),
                    ),
                )

                chunk_counts: dict[str, int] = {}
                for chunk in chunks:
                    source = str(chunk.metadata.get("source") or "unknown")
                    rel = self._relative_source(source)
                    chunk_counts[rel] = chunk_counts.get(rel, 0) + 1

                for doc in docs:
                    source = str(doc.metadata.get("source") or "unknown")
                    rel = self._relative_source(source)
                    path = Path(source)
                    try:
                        text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else doc.page_content
                        stat = path.stat() if path.exists() else None
                        size = stat.st_size if stat else len(text.encode("utf-8", errors="ignore"))
                        mtime = stat.st_mtime_ns if stat else 0
                        line_count = text.count("\n") + 1 if text else 0
                    except Exception:
                        size, mtime, line_count = 0, 0, 0
                    con.execute(
                        """
                        INSERT OR REPLACE INTO files (
                            path, extension, size_bytes, mtime_ns, line_count, chunk_count
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (rel, path.suffix, size, mtime, line_count, chunk_counts.get(rel, 0)),
                    )

                per_file_index: dict[str, int] = {}
                for chunk in chunks:
                    source = str(chunk.metadata.get("source") or "unknown")
                    rel = self._relative_source(source)
                    index = per_file_index.get(rel, 0)
                    per_file_index[rel] = index + 1
                    con.execute(
                        """
                        INSERT INTO chunks (file_path, chunk_index, char_count, preview)
                        VALUES (?, ?, ?, ?)
                        """,
                        (rel, index, len(chunk.page_content), chunk.page_content[:240]),
                    )
        except Exception as exc:
            log.warning("code_vectorstore: SQLite metadata write failed — %s", exc)

    def _relative_source(self, source: str) -> str:
        try:
            return str(Path(source).resolve().relative_to(Path(self.repo_path)))
        except Exception:
            return source

    def _load_store(self) -> Any:
        """Load a persisted FAISS index from disk."""
        try:
            from langchain_community.vectorstores import FAISS  # type: ignore[import]
            embeddings = self._embeddings()
            vs = FAISS.load_local(
                str(self.index_dir), embeddings, allow_dangerous_deserialization=True
            )
            log.debug("code_vectorstore: loaded persisted index from %s", self.index_dir)
            return vs
        except Exception as exc:
            log.debug("code_vectorstore: could not load index — %s", exc)
            return None
