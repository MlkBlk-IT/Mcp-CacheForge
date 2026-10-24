#!/usr/bin/env python3
"""IcyCache - store de cache LLM (compatible avec la table `cache` de server.py).

La table `cache` existe deja en base (creee par server.py). Ce module y accede
sans la recreer ni l'alterer, a part une colonne additive `raw_hash` qui permet
de savoir si un hit vient d'un payload identique ou d'un payload normalise
equivalent (transparence pour le dashboard).

Format de stockage de `response` (v3) :
    {"_v": 3, "kind": "openai", "message": {...}, "usage": {...}, "model": "...",
     "finish_reason": "stop", "sse": "data: ...\\n\\n", "gen_time_ms": 123}

Les entrees v2 (server.py actuel) restent lisibles.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["CacheStore", "decode_entry", "encode_entry"]


def encode_entry(kind: str, message: Any, usage: Dict[str, Any], model: str,
                 finish_reason: str, sse: str, gen_time_ms: int) -> str:
    return json.dumps(
        {
            "_v": 3,
            "kind": kind,
            "message": message,
            "usage": usage or {},
            "model": model,
            "finish_reason": finish_reason or "stop",
            "sse": sse,
            "gen_time_ms": gen_time_ms,
        },
        ensure_ascii=False,
    )


def decode_entry(raw: str) -> Dict[str, Any]:
    """Decode une entree cache (v3 ou v2 ou texte brut) en structure exploitable."""
    out: Dict[str, Any] = {"message": None, "usage": {}, "model": None, "finish_reason": "stop",
                           "sse": None, "gen_time_ms": None, "version": 0}
    if not raw:
        return out
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        out["message"] = {"role": "assistant", "content": raw}
        return out
    if not isinstance(data, dict):
        out["message"] = data
        return out
    v = data.get("_v")
    if v == 3:
        out.update({
            "message": data.get("message"),
            "usage": data.get("usage") or {},
            "model": data.get("model"),
            "finish_reason": data.get("finish_reason") or "stop",
            "sse": data.get("sse"),
            "gen_time_ms": data.get("gen_time_ms"),
            "version": 3,
        })
    elif v == 2:
        out.update({
            "message": data.get("message"),
            "usage": data.get("usage") or {},
            "model": data.get("model"),
            "finish_reason": data.get("finish_reason") or "stop",
            "sse": None,
            "gen_time_ms": data.get("gen_time_ms"),
            "version": 2,
        })
    else:
        out["message"] = data
        out["version"] = 1
    return out


class CacheStore:
    """Acces thread-safe (un lock par operation) au cache LLM."""

    def __init__(self, db_path: Path, ttl_seconds: int = 86400, max_entries: int = 10000):
        self.db_path = Path(db_path)
        self.ttl_seconds = int(ttl_seconds)
        self.max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _migrate(self) -> None:
        """Colonnes additives uniquement (jamais de DROP / recreation)."""
        with self._lock:
            conn = self._connect()
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(cache)")}
                if "raw_hash" not in cols:
                    conn.execute("ALTER TABLE cache ADD COLUMN raw_hash TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass
            finally:
                conn.close()

    def get(self, prompt_hash: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT normalized_prompt, original_prompt, response, is_stream, provider, model, "
                    "created_at, access_count, raw_hash FROM cache "
                    "WHERE prompt_hash = ? AND created_at >= datetime('now', '-' || ? || ' seconds')",
                    (prompt_hash, self.ttl_seconds),
                ).fetchone()
                if not row:
                    return None
                conn.execute(
                    "UPDATE cache SET access_count = access_count + 1, last_accessed = CURRENT_TIMESTAMP "
                    "WHERE prompt_hash = ?", (prompt_hash,))
                conn.commit()
                return {
                    "prompt_hash": prompt_hash,
                    "normalized_prompt": row[0],
                    "original_prompt": row[1],
                    "response": row[2],
                    "is_stream": bool(row[3]),
                    "provider": row[4],
                    "model": row[5],
                    "created_at": row[6],
                    "access_count": (row[7] or 1) + 1,
                    "raw_hash": row[8],
                }
            finally:
                conn.close()

    def set(self, prompt_hash: str, normalized: str, original: str, response: str,
            is_stream: bool, provider: str = None, model: str = None, raw_hash: str = None) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO cache "
                    "(prompt_hash, normalized_prompt, original_prompt, response, is_stream, provider, model, "
                    " created_at, last_accessed, access_count, raw_hash) "
                    "VALUES (?,?,?,?,?,?,?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, ?)",
                    (prompt_hash, normalized, original, response, int(bool(is_stream)), provider, model, raw_hash),
                )
                conn.execute("DELETE FROM cache WHERE created_at < datetime('now', '-' || ? || ' seconds')",
                             (self.ttl_seconds,))
                count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
                if count >= self.max_entries:
                    excess = count - self.max_entries + 1
                    conn.execute(
                        "DELETE FROM cache WHERE prompt_hash IN "
                        "(SELECT prompt_hash FROM cache ORDER BY last_accessed ASC LIMIT ?)", (excess,))
                conn.commit()
            finally:
                conn.close()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
                hits = conn.execute("SELECT COALESCE(SUM(access_count - 1), 0) FROM cache").fetchone()[0] or 0
                by_provider = dict(conn.execute(
                    "SELECT provider, COUNT(*) FROM cache GROUP BY provider").fetchall())
                by_model = dict(conn.execute(
                    "SELECT model, COUNT(*) FROM cache GROUP BY model ORDER BY 2 DESC LIMIT 20").fetchall())
                return {
                    "total_entries": total,
                    "total_hits": hits,
                    "by_provider": by_provider,
                    "by_model": by_model,
                    "db_path": str(self.db_path),
                }
            finally:
                conn.close()

    def clear(self, prompt_hash: Optional[str] = None) -> int:
        with self._lock:
            conn = self._connect()
            try:
                if prompt_hash:
                    n = conn.execute("DELETE FROM cache WHERE prompt_hash = ?", (prompt_hash,)).rowcount
                else:
                    n = conn.execute("DELETE FROM cache").rowcount
                conn.commit()
                return n
            finally:
                conn.close()

    def search(self, query: str, limit: int = 10) -> list:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT prompt_hash, normalized_prompt, provider, model, created_at, access_count "
                    "FROM cache WHERE normalized_prompt LIKE ? ORDER BY access_count DESC LIMIT ?",
                    ("%" + query + "%", limit)).fetchall()
                return [
                    {"hash": r[0][:8] + "...", "normalized": r[1], "provider": r[2],
                     "model": r[3], "created_at": r[4], "access_count": r[5]}
                    for r in rows
                ]
            finally:
                conn.close()
