#!/usr/bin/env python3
"""IcyCache core: config, DB (events / agent_events / tool_cache), hub WebSocket, loop guard.

Module partage par server_new.py (gateway HTTP) et mcp_gateway.py (gateway MCP).
Aucune dependance a FastAPI. Les evenements "agent" (tools, boucles) vont dans une table
SEPARÉE (agent_events) pour ne pas fausser les compteurs de la table events (/api/metrics).
"""
import os
import json
import time
import sqlite3
import asyncio
import hashlib
import logging
import threading
from pathlib import Path
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger("IcyCache.core")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(os.environ.get("ICYCACHE_CONFIG") or (PROJECT_ROOT / "config" / "icycache.json"))
with open(CONFIG_FILE, "r", encoding="utf-8") as _f:
    CONFIG = json.load(_f)
# Priorite: variable d'env > chemin declare dans la config > chemin par defaut.
# (mcp_gateway/server_new lisaient "cache.db_path" ; on aligne icy_core sur la
#  meme regle pour que tous les processus pointent sur la meme base.)
DB_FILE = Path(os.environ.get("ICYCACHE_DB")
               or os.environ.get("CACHEFORGE_DB")
               or CONFIG.get("cache", {}).get("db_path")
               or (PROJECT_ROOT / "db" / "cache.db"))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)


def utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def short_hash(s, n=16):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _db():
    return sqlite3.connect(DB_FILE, timeout=10)


# === HUB WEBSOCKET (thread-safe publish, fan-out dans la boucle asyncio du proxy) ===
class EventHub:
    def __init__(self):
        self.loop = None
        self.subs = set()

    def bind(self, loop):
        self.loop = loop

    def subscribe(self):
        q = asyncio.Queue(maxsize=2000)
        self.subs.add(q)
        return q

    def unsubscribe(self, q):
        self.subs.discard(q)

    def publish(self, ev):
        loop = self.loop
        if loop is None or not self.subs:
            return

        def _fan():
            for q in list(self.subs):
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    pass

        try:
            loop.call_soon_threadsafe(_fan)
        except RuntimeError:
            pass


hub = EventHub()


# === DB ===
EVENT_NEW_COLS = {"upstream": "TEXT", "client": "TEXT", "cached_tokens": "INTEGER", "cache_write_tokens": "INTEGER"}


def init_db():
    conn = _db()
    conn.execute("""CREATE TABLE IF NOT EXISTS cache (
        prompt_hash TEXT PRIMARY KEY, normalized_prompt TEXT, original_prompt TEXT, response TEXT,
        is_stream INTEGER, provider TEXT, model TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_accessed TEXT DEFAULT CURRENT_TIMESTAMP,
        access_count INTEGER DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT CURRENT_TIMESTAMP,
        kind TEXT NOT NULL, hash TEXT, model_req TEXT, model_real TEXT, stream INTEGER,
        prompt_tokens INTEGER, completion_tokens INTEGER,
        saved_prompt INTEGER DEFAULT 0, saved_completion INTEGER DEFAULT 0,
        latency_ms INTEGER, ttft_ms INTEGER)""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    for c, t in EVENT_NEW_COLS.items():
        if c not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN %s %s" % (c, t))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_hash ON events(hash, kind)")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT CURRENT_TIMESTAMP,
        kind TEXT NOT NULL, server TEXT, tool TEXT, client TEXT, args_hash TEXT,
        bytes INTEGER, latency_ms INTEGER, saved_latency_ms INTEGER, detail TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tool_cache (
        key TEXT PRIMARY KEY, server TEXT, tool TEXT, result_json TEXT, bytes INTEGER,
        latency_ms INTEGER, ttl INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_accessed TEXT DEFAULT CURRENT_TIMESTAMP, hits INTEGER DEFAULT 0)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_cache_server ON tool_cache(server)")
    conn.commit()
    conn.close()


init_db()


def log_event(kind, h, model_req, model_real, stream, pt=None, ct=None, sp=0, sc=0,
              latency_ms=None, ttft_ms=None, upstream=None, client=None,
              cached_tokens=None, cache_write_tokens=None, req_id=None):
    """Ligne dans events (tokens = valeurs upstream reelles ou NULL, jamais estimees) + push WS."""
    try:
        conn = _db()
        conn.execute(
            "INSERT INTO events (kind,hash,model_req,model_real,stream,prompt_tokens,completion_tokens,"
            "saved_prompt,saved_completion,latency_ms,ttft_ms,upstream,client,cached_tokens,cache_write_tokens) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (kind, h, model_req, model_real, int(bool(stream)), pt, ct, sp, sc, latency_ms, ttft_ms,
             upstream, client, cached_tokens, cache_write_tokens))
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("events insert failed")
    hub.publish({"type": "final", "id": req_id, "ts": utc_iso(), "kind": kind, "upstream": upstream,
                 "client": client, "model_req": model_req, "model": model_real, "stream": bool(stream),
                 "prompt_tokens": pt, "completion_tokens": ct, "cached_tokens": cached_tokens,
                 "cache_write_tokens": cache_write_tokens, "saved_prompt": sp, "saved_completion": sc,
                 "latency_ms": latency_ms, "ttft_ms": ttft_ms, "hash": (h or "")[:8],
                 "tokens_source": "upstream" if (pt is not None or ct is not None) else "n/a"})


def log_agent_event(kind, server=None, tool=None, client=None, args_hash=None, nbytes=None,
                    latency_ms=None, saved_latency_ms=None, detail=None):
    try:
        conn = _db()
        conn.execute(
            "INSERT INTO agent_events (kind,server,tool,client,args_hash,bytes,latency_ms,saved_latency_ms,detail) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (kind, server, tool, client, args_hash, nbytes, latency_ms, saved_latency_ms,
             canonical(detail) if detail is not None else None))
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("agent_events insert failed")
    hub.publish({"type": "agent", "ts": utc_iso(), "kind": kind, "server": server, "tool": tool,
                 "client": client, "bytes": nbytes, "latency_ms": latency_ms,
                 "saved_latency_ms": saved_latency_ms, "detail": detail})


# === TOOL CACHE (stockage) ===
def tc_get(key):
    conn = _db()
    row = conn.execute(
        "SELECT result_json, latency_ms, bytes FROM tool_cache WHERE key=? "
        "AND datetime(created_at, '+' || ttl || ' seconds') >= datetime('now')", (key,)).fetchone()
    if row:
        conn.execute("UPDATE tool_cache SET hits=hits+1, last_accessed=CURRENT_TIMESTAMP WHERE key=?", (key,))
        conn.commit()
    conn.close()
    return row


def tc_set(key, server, tool, result_json, nbytes, latency_ms, ttl):
    conn = _db()
    conn.execute("INSERT OR REPLACE INTO tool_cache (key,server,tool,result_json,bytes,latency_ms,ttl,hits) "
                 "VALUES (?,?,?,?,?,?,?,0)", (key, server, tool, result_json, nbytes, latency_ms, int(ttl)))
    conn.execute("DELETE FROM tool_cache WHERE datetime(created_at, '+' || ttl || ' seconds') < datetime('now')")
    conn.commit()
    conn.close()


def tc_flush(server):
    conn = _db()
    n = conn.execute("DELETE FROM tool_cache WHERE server=?", (server,)).rowcount
    conn.commit()
    conn.close()
    return n


def tc_clear(server=None):
    conn = _db()
    if server:
        n = conn.execute("DELETE FROM tool_cache WHERE server=?", (server,)).rowcount
    else:
        n = conn.execute("DELETE FROM tool_cache").rowcount
    conn.commit()
    conn.close()
    return n


def tc_stats():
    conn = _db()
    entries = conn.execute("SELECT server, tool, COUNT(*), COALESCE(SUM(hits),0), COALESCE(SUM(bytes),0) "
                           "FROM tool_cache GROUP BY server, tool").fetchall()
    ev = conn.execute("SELECT kind, COUNT(*), COALESCE(SUM(saved_latency_ms),0), COALESCE(SUM(bytes),0) "
                      "FROM agent_events GROUP BY kind").fetchall()
    conn.close()
    return {"entries": [{"server": e[0], "tool": e[1], "entries": e[2], "hits": e[3], "bytes": e[4]} for e in entries],
            "events": {k: {"count": c, "saved_latency_ms": s, "bytes": b} for k, c, s, b in ev},
            "note": "tokens economises par tool-cache = n/a (non mesurables sans estimation); latence economisee mesuree."}


def agent_events_recent(limit=50, kinds=None):
    limit = max(1, min(int(limit), 1000))
    conn = _db()
    conn.row_factory = sqlite3.Row
    q = "SELECT id, ts, kind, server, tool, client, args_hash, bytes, latency_ms, saved_latency_ms, detail FROM agent_events"
    args = []
    if kinds:
        q += " WHERE kind IN (%s)" % ",".join("?" * len(kinds))
        args += list(kinds)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    conn.close()
    return rows


# === LOOP GUARD ===
def trailing_cycle(seq, max_cycle=3):
    """Motif repete en fin de sequence. Retourne (cycle_len, reps) avec reps>=2 maximal, sinon None."""
    n = len(seq)
    best = None
    for c in range(1, max_cycle + 1):
        if n < 2 * c:
            continue
        pat = seq[-c:]
        reps, i = 1, n - 2 * c
        while i >= 0 and seq[i:i + c] == pat:
            reps += 1
            i -= c
        if reps >= 2 and (best is None or reps > best[1]):
            best = (c, reps)
    return best


def _args_hash(a):
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            pass
    return short_hash(canonical(a))


def llm_tool_sigs(messages):
    """Signatures (nom, args, resultat) des tool calls deja faits dans l'historique d'une requete LLM."""
    results = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool" and m.get("tool_call_id"):
            results[m["tool_call_id"]] = short_hash(canonical(m.get("content")))
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = short_hash(canonical(b.get("content")))
    sigs = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for tc in (m.get("tool_calls") or []):
            fn = (tc or {}).get("function") or {}
            sigs.append((fn.get("name"), _args_hash(fn.get("arguments")), results.get((tc or {}).get("id"))))
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    sigs.append((b.get("name"), _args_hash(b.get("input")), results.get(b.get("id"))))
    return sigs


def llm_loop_check(messages, warn_after=3, max_cycle=3):
    """Boucle d'agent vue dans l'historique: meme(s) tool call(s) + memes resultats, repetes en fin."""
    if not isinstance(messages, list):
        return None
    sigs = llm_tool_sigs(messages)
    cyc = trailing_cycle(sigs, max_cycle)
    if cyc and cyc[1] >= warn_after:
        tail = sigs[-cyc[0]:]
        return {"cycle_len": cyc[0], "reps": cyc[1], "tools": [t[0] for t in tail]}
    return None


class ToolLoopGuard:
    """Detecte un agent qui rappelle les memes tools avec les memes args ET les memes resultats."""

    def __init__(self, cfg):
        c = (cfg or {})
        self.enabled = c.get("enabled", True)
        self.window = c.get("window_seconds", 300)
        t = c.get("tools", {})
        self.warn_after = t.get("warn_after", 3)
        self.block_after = t.get("block_after", 5)
        self.max_cycle = t.get("max_cycle_len", 3)
        self.sessions = {}
        self.lock = threading.Lock()

    def _s(self, key):
        s = self.sessions.get(key)
        if s is None:
            s = {"hist": deque(maxlen=128), "blocked": None}
            self.sessions[key] = s
        return s

    def check_before(self, key, tool, args_hash):
        """True si cet appel (meme tool + memes args) est bloque parce que la boucle persiste."""
        if not self.enabled:
            return False
        with self.lock:
            s = self._s(key)
            b = s["blocked"]
            if b is None:
                return False
            if b == (tool, args_hash):
                return True
            s["blocked"] = None  # l'agent a change d'approche
            return False

    def after(self, key, tool, args_hash, result_hash):
        """Enregistre l'appel. Retourne ('none'|'warn'|'block', cycle_len, reps)."""
        if not self.enabled:
            return ("none", 0, 0)
        now = time.time()
        with self.lock:
            s = self._s(key)
            hist = s["hist"]
            hist.append((now, (tool, args_hash, result_hash)))
            while hist and now - hist[0][0] > self.window:
                hist.popleft()
            seq = [h[1] for h in hist]
            cyc = trailing_cycle(seq, self.max_cycle)
            if not cyc:
                return ("none", 0, 0)
            c, reps = cyc
            if reps >= self.block_after:
                s["blocked"] = (tool, args_hash)
                return ("block", c, reps)
            if reps >= self.warn_after:
                return ("warn", c, reps)
            return ("none", c, reps)

    def record_blocked(self, key, tool, args_hash):
        """Un appel bloque reste dans l'historique pour que le blocage tienne tant que l'agent insiste."""
        with self.lock:
            s = self._s(key)
            s["hist"].append((time.time(), (tool, args_hash, "BLOCKED")))
