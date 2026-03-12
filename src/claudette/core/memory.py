"""Local semantic index over repo issues and PRs.

Supports three backends (configured via memory.backend in config.yaml):
  - dense:  model2vec (potion-base-8M, ~8MB) embeddings + cosine similarity
  - bm25:   BM25 keyword search via bm25s (no model download needed)
  - hybrid: both combined via reciprocal rank fusion (RRF)

Embeddings are built incrementally — only new/changed documents get embedded.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

MODEL_NAME = "minishlab/potion-base-8M"
EMBEDDING_DIM = 256
RRF_K = 60  # Reciprocal rank fusion constant


def _has_model2vec() -> bool:
    try:
        import model2vec  # noqa: F401

        return True
    except ImportError:
        return False


def _has_bm25s() -> bool:
    try:
        import bm25s  # noqa: F401

        return True
    except ImportError:
        return False


def available_backends() -> list[str]:
    """Return list of backends that have their dependencies installed."""
    backends = []
    if _has_model2vec():
        backends.append("dense")
    if _has_bm25s():
        backends.append("bm25")
    if _has_model2vec() and _has_bm25s():
        backends.append("hybrid")
    return backends


class MemoryIndex:
    """Semantic index over issues and PRs."""

    def __init__(self, memory_dir: Path, backend: str = "dense") -> None:
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.db_path = memory_dir / "index.db"
        self.embeddings_path = memory_dir / "embeddings.npy"
        self.keys_path = memory_dir / "keys.json"
        self.bm25_dir = memory_dir / "bm25_index"
        self._model = None
        self._bm25 = None
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    key TEXT PRIMARY KEY,
                    repo TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    labels TEXT NOT NULL DEFAULT '[]',
                    state TEXT NOT NULL DEFAULT 'open',
                    is_pr INTEGER NOT NULL DEFAULT 0,
                    snippet TEXT NOT NULL DEFAULT '',
                    updated_at TEXT,
                    indexed_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

    @property
    def _uses_dense(self) -> bool:
        return self.backend in ("dense", "hybrid")

    @property
    def _uses_bm25(self) -> bool:
        return self.backend in ("bm25", "hybrid")

    @property
    def model(self):
        if self._model is None:
            if not _has_model2vec():
                raise ImportError(
                    "model2vec is required for dense/hybrid search. "
                    "Install it: pip install claudette[dense]"
                )
            import logging
            import os
            import warnings

            from model2vec import StaticModel

            # Suppress "unauthenticated requests" noise from huggingface_hub
            warnings.filterwarnings("ignore", message=".*unauthenticated.*")
            logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

            # Propagate corporate CA bundles so huggingface_hub/requests can find them
            for env_var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
                ca_path = os.environ.get(env_var)
                if ca_path and os.path.exists(ca_path):
                    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_path)
                    os.environ.setdefault("CURL_CA_BUNDLE", ca_path)
                    break

            self._model = StaticModel.from_pretrained(MODEL_NAME)
        return self._model

    # ── Sync ──────────────────────────────────────────────────────────────

    def sync(self, issues: list) -> dict:
        """Index a list of Issue objects. Only embeds new/changed docs. Returns stats."""
        if not issues:
            return {"added": 0, "updated": 0, "total": 0}

        added = 0
        updated = 0
        dirty_keys: list[str] = []

        with sqlite3.connect(self.db_path) as conn:
            existing = {
                row[0]: row[1]
                for row in conn.execute("SELECT key, updated_at FROM nodes").fetchall()
            }

            for issue in issues:
                key = f"{issue.repo}#{issue.number}"
                kind = "pull" if issue.is_pull_request else "issues"
                url = f"https://github.com/{issue.repo}/{kind}/{issue.number}"
                labels_json = json.dumps(issue.labels)
                snippet = (issue.body or "")[:500]
                updated_at = issue.updated_at.isoformat() if issue.updated_at else ""
                now = datetime.now(UTC).isoformat()

                if key in existing:
                    if existing[key] == updated_at:
                        continue  # No change
                    conn.execute(
                        """UPDATE nodes SET title=?, url=?, labels=?, state=?,
                           is_pr=?, snippet=?, updated_at=?, indexed_at=?
                           WHERE key=?""",
                        (
                            issue.title,
                            url,
                            labels_json,
                            issue.state,
                            int(issue.is_pull_request),
                            snippet,
                            updated_at,
                            now,
                            key,
                        ),
                    )
                    updated += 1
                    dirty_keys.append(key)
                else:
                    conn.execute(
                        """INSERT INTO nodes (key, repo, number, title, url, labels,
                           state, is_pr, snippet, updated_at, indexed_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            key,
                            issue.repo,
                            issue.number,
                            issue.title,
                            url,
                            labels_json,
                            issue.state,
                            int(issue.is_pull_request),
                            snippet,
                            updated_at,
                            now,
                        ),
                    )
                    added += 1
                    dirty_keys.append(key)

            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync', ?)",
                (datetime.now(UTC).isoformat(),),
            )

        # Only re-index if something changed
        if dirty_keys:
            if self._uses_dense:
                self._update_embeddings(dirty_keys)
            if self._uses_bm25:
                self._update_bm25()

        total = self._count()
        return {"added": added, "updated": updated, "total": total}

    def _update_embeddings(self, dirty_keys: list[str]) -> None:
        """Incrementally update embeddings for changed documents only."""
        with sqlite3.connect(self.db_path) as conn:
            all_rows = conn.execute(
                "SELECT key, title, snippet FROM nodes ORDER BY key"
            ).fetchall()

        if not all_rows:
            for p in [self.embeddings_path, self.keys_path]:
                if p.exists():
                    p.unlink()
            return

        all_keys = [row[0] for row in all_rows]
        key_to_row = {row[0]: row for row in all_rows}
        dirty_set = set(dirty_keys)

        # Load existing embeddings if available
        old_keys: list[str] = []
        old_embeddings: np.ndarray | None = None
        if self.keys_path.exists() and self.embeddings_path.exists():
            old_keys = json.loads(self.keys_path.read_text())
            old_embeddings = np.load(self.embeddings_path)

        # Build old key->index map
        old_key_to_idx = {k: i for i, k in enumerate(old_keys)}

        # Figure out which keys need fresh embeddings
        keys_to_embed: list[str] = []
        for key in all_keys:
            if key in dirty_set or key not in old_key_to_idx:
                keys_to_embed.append(key)

        # Embed only the new/changed ones
        new_embeddings: dict[str, np.ndarray] = {}
        if keys_to_embed:
            texts = [f"{key_to_row[k][1]}\n{key_to_row[k][2]}" for k in keys_to_embed]
            raw = self.model.encode(texts)
            norms = np.linalg.norm(raw, axis=1, keepdims=True)
            norms[norms == 0] = 1
            normalized = raw / norms
            for i, key in enumerate(keys_to_embed):
                new_embeddings[key] = normalized[i]

        # Assemble the full matrix in key-sorted order
        dim = EMBEDDING_DIM
        if old_embeddings is not None and old_embeddings.shape[1] != dim:
            dim = old_embeddings.shape[1]

        result = np.empty((len(all_keys), dim), dtype=np.float32)
        for i, key in enumerate(all_keys):
            if key in new_embeddings:
                result[i] = new_embeddings[key]
            elif key in old_key_to_idx and old_embeddings is not None:
                result[i] = old_embeddings[old_key_to_idx[key]]
            else:
                # Shouldn't happen, but fallback to embedding
                text = f"{key_to_row[key][1]}\n{key_to_row[key][2]}"
                emb = self.model.encode([text])
                norm = np.linalg.norm(emb)
                result[i] = emb[0] / norm if norm > 0 else emb[0]

        np.save(self.embeddings_path, result)
        self.keys_path.write_text(json.dumps(all_keys))

    def _update_bm25(self) -> None:
        """Rebuild the BM25 index from all documents."""
        import bm25s

        with sqlite3.connect(self.db_path) as conn:
            all_rows = conn.execute(
                "SELECT key, title, snippet FROM nodes ORDER BY key"
            ).fetchall()

        if not all_rows:
            return

        keys = [row[0] for row in all_rows]
        texts = [f"{row[1]} {row[2]}" for row in all_rows]

        tokenized = bm25s.tokenize(texts, stopwords="en")
        retriever = bm25s.BM25()
        retriever.index(tokenized)

        self.bm25_dir.mkdir(parents=True, exist_ok=True)
        retriever.save(str(self.bm25_dir))
        (self.bm25_dir / "keys.json").write_text(json.dumps(keys))

    # ── Search ────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10, state: str | None = None) -> list[dict]:
        """Search across indexed issues/PRs. Uses configured backend."""
        # Get metadata for all nodes
        with sqlite3.connect(self.db_path) as conn:
            rows_by_key = {}
            for row in conn.execute(
                "SELECT key, title, url, labels, state, is_pr FROM nodes"
            ).fetchall():
                rows_by_key[row[0]] = row

        if not rows_by_key:
            return []

        # State filter
        allowed_keys = None
        if state:
            allowed_keys = {k for k, row in rows_by_key.items() if row[4] == state}
            if not allowed_keys:
                return []

        if self.backend == "dense":
            ranked = self._search_dense(query, allowed_keys)
        elif self.backend == "bm25":
            ranked = self._search_bm25(query, allowed_keys)
        else:  # hybrid
            dense_ranked = self._search_dense(query, allowed_keys)
            bm25_ranked = self._search_bm25(query, allowed_keys)
            ranked = _rrf_merge(dense_ranked, bm25_ranked)

        results = []
        for key, score in ranked[:limit]:
            row = rows_by_key.get(key)
            if not row:
                continue
            results.append(
                {
                    "key": row[0],
                    "title": row[1],
                    "url": row[2],
                    "labels": json.loads(row[3]),
                    "state": row[4],
                    "is_pr": bool(row[5]),
                    "score": score,
                }
            )
        return results

    def _search_dense(
        self, query: str, allowed_keys: set[str] | None = None
    ) -> list[tuple[str, float]]:
        """Dense cosine similarity search. Returns [(key, score), ...]."""
        if not self.embeddings_path.exists() or not self.keys_path.exists():
            return []

        embeddings = np.load(self.embeddings_path)
        keys = json.loads(self.keys_path.read_text())

        if embeddings.shape[0] == 0:
            return []

        if allowed_keys is not None:
            indices = [i for i, k in enumerate(keys) if k in allowed_keys]
            if not indices:
                return []
            embeddings = embeddings[indices]
            keys = [keys[i] for i in indices]

        query_emb = self.model.encode([query])
        query_emb = query_emb / np.linalg.norm(query_emb, axis=1, keepdims=True)
        scores = (embeddings @ query_emb.T).squeeze()

        if scores.ndim == 0:
            scores = np.array([float(scores)])

        order = np.argsort(scores)[::-1]
        return [(keys[i], float(scores[i])) for i in order]

    def _search_bm25(
        self, query: str, allowed_keys: set[str] | None = None
    ) -> list[tuple[str, float]]:
        """BM25 keyword search. Returns [(key, score), ...]."""
        if not self.bm25_dir.exists():
            return []

        import bm25s

        retriever = bm25s.BM25.load(str(self.bm25_dir))
        keys = json.loads((self.bm25_dir / "keys.json").read_text())

        tokenized = bm25s.tokenize([query], stopwords="en")
        results, scores = retriever.retrieve(tokenized, k=len(keys))

        ranked = []
        for i in range(results.shape[1]):
            idx = int(results[0, i])
            if idx < 0 or idx >= len(keys):
                continue
            key = keys[idx]
            score = float(scores[0, i])
            if score <= 0:
                continue
            if allowed_keys is not None and key not in allowed_keys:
                continue
            ranked.append((key, score))

        return ranked

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            open_count = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE state='open'"
            ).fetchone()[0]
            pr_count = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE is_pr=1"
            ).fetchone()[0]
            last_sync = conn.execute(
                "SELECT value FROM meta WHERE key='last_sync'"
            ).fetchone()

        emb_size = self.embeddings_path.stat().st_size if self.embeddings_path.exists() else 0

        return {
            "total": total,
            "open": open_count,
            "prs": pr_count,
            "backend": self.backend,
            "last_sync": last_sync[0] if last_sync else "never",
            "db_path": str(self.db_path),
            "embeddings_size_kb": round(emb_size / 1024, 1),
        }

    def _count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    # ── Clear ─────────────────────────────────────────────────────────────

    def clear(self) -> None:
        import shutil

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM nodes")
            conn.execute("DELETE FROM meta")
        for p in [self.embeddings_path, self.keys_path]:
            if p.exists():
                p.unlink()
        if self.bm25_dir.exists():
            shutil.rmtree(self.bm25_dir)


def _rrf_merge(
    *ranked_lists: list[tuple[str, float]], k: int = RRF_K
) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: combine multiple ranked lists into one."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (key, _) in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
