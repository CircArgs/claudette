"""Local semantic index over repo issues and PRs.

Uses model2vec (potion-base-8M, ~8MB) for embeddings and sqlite for metadata.
Embeddings are built incrementally — only new/changed documents get embedded.
Search is brute-force cosine similarity — sub-millisecond for a few thousand documents.
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


class MemoryIndex:
    """Semantic index over issues and PRs."""

    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = memory_dir / "index.db"
        self.embeddings_path = memory_dir / "embeddings.npy"
        self.keys_path = memory_dir / "keys.json"
        self._model = None
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
    def model(self):
        if self._model is None:
            from model2vec import StaticModel

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

        # Only re-embed if something changed
        if dirty_keys:
            self._update_embeddings(dirty_keys)

        total = self._count()
        return {"added": added, "updated": updated, "total": total}

    def _update_embeddings(self, dirty_keys: list[str]) -> None:
        """Incrementally update embeddings for changed documents only."""
        with sqlite3.connect(self.db_path) as conn:
            all_rows = conn.execute("SELECT key, title, snippet FROM nodes ORDER BY key").fetchall()

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

    # ── Search ────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10, state: str | None = None) -> list[dict]:
        """Semantic search. Returns list of dicts with key, title, url, score."""
        if not self.embeddings_path.exists() or not self.keys_path.exists():
            return []

        embeddings = np.load(self.embeddings_path)
        keys = json.loads(self.keys_path.read_text())

        if embeddings.shape[0] == 0:
            return []

        # Get metadata
        with sqlite3.connect(self.db_path) as conn:
            rows_by_key = {}
            for row in conn.execute(
                "SELECT key, title, url, labels, state, is_pr FROM nodes"
            ).fetchall():
                rows_by_key[row[0]] = row

        # Filter by state if requested
        if state:
            indices = [
                i for i, k in enumerate(keys) if k in rows_by_key and rows_by_key[k][4] == state
            ]
            if not indices:
                return []
            embeddings = embeddings[indices]
            filtered_keys = [keys[i] for i in indices]
        else:
            filtered_keys = keys

        query_emb = self.model.encode([query])
        query_emb = query_emb / np.linalg.norm(query_emb, axis=1, keepdims=True)
        scores = (embeddings @ query_emb.T).squeeze()

        if scores.ndim == 0:
            scores = np.array([float(scores)])

        top_indices = np.argsort(scores)[::-1][:limit]

        results = []
        for idx in top_indices:
            key = filtered_keys[idx]
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
                    "score": float(scores[idx]),
                }
            )
        return results

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            open_count = conn.execute("SELECT COUNT(*) FROM nodes WHERE state='open'").fetchone()[0]
            pr_count = conn.execute("SELECT COUNT(*) FROM nodes WHERE is_pr=1").fetchone()[0]
            last_sync = conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()

        emb_size = self.embeddings_path.stat().st_size if self.embeddings_path.exists() else 0

        return {
            "total": total,
            "open": open_count,
            "prs": pr_count,
            "last_sync": last_sync[0] if last_sync else "never",
            "db_path": str(self.db_path),
            "embeddings_size_kb": round(emb_size / 1024, 1),
        }

    def _count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    # ── Clear ─────────────────────────────────────────────────────────────

    def clear(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM nodes")
            conn.execute("DELETE FROM meta")
        for p in [self.embeddings_path, self.keys_path]:
            if p.exists():
                p.unlink()
