#!/usr/bin/env python3
"""
IcyCache - facade MCP de la gateway.

Le MCP est une FACADE : il observe et pilote, il n'est PAS dans le chemin des
requetes LLM (un client MCP appelle volontairement ses outils, on ne peut pas
intercepter un flux OpenAI/Anthropic avec lui). Le chemin des requetes passe
par la gateway HTTP (server_new.py).

Outils exposes :
  get_cache_stats          etat du cache LLM
  clear_cache              purge (confirmation explicite requise)
  normalize_preview        apercu de la normalisation (et de la cle de cache)
  search_cached_prompts    recherche dans le cache
  get_realtime_tokens      DERNIERS tokens REELS par upstream (jamais estimes)
  list_upstreams           upstreams disponibles dans la gateway
  tool_cache_stats         etat du cache d'outils

Surfaces :
  MCP   : stdio (--mode mcp-stdio) ou SSE (--mode mcp-sse)
  REST  : /api/tools, /api/tools/{nom}, /api/agent/events, /api/loop/check,
          /api/toolcache/{stats,flush,clear}
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_FILE = Path(os.environ.get("ICYCACHE_CONFIG") or (PROJECT_ROOT / "config" / "icycache.gateway.json"))
os.environ["ICYCACHE_CONFIG"] = str(_CONFIG_FILE)
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
import uvicorn  # noqa: E402

import icy_core  # noqa: E402
from icy_core import log_agent_event, utc_iso  # noqa: E402
from icy_cache_store import CacheStore  # noqa: E402
import icy_normalize  # noqa: E402

# Proxy d'outils: disponible si le module s'importe. IcyCache sert alors aussi
# de relais vers les serveurs MCP cibles (tools calling).
try:
    import tool_proxy as tp
    TOOL_PROXY_OK = True
except Exception:  # pragma: no cover
    tp = None
    TOOL_PROXY_OK = False

CONFIG: Dict[str, Any] = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
DB_FILE = Path(os.environ.get("ICYCACHE_DB") or CONFIG.get("cache", {}).get("db_path")
               or (PROJECT_ROOT / "db" / "cache.db"))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

UPSTREAMS: Dict[str, Any] = CONFIG.get("upstreams", {})
MCP_CFG = CONFIG.get("mcp", {})
MCP_GW_CFG = CONFIG.get("mcp_gateway", {"host": "127.0.0.1", "port": 8083})
GW_CFG = CONFIG.get("gateway", {})
LOOP_CFG = CONFIG.get("loop_guard", {})
TOOL_CACHE_CFG = CONFIG.get("tool_cache", {})

CACHE = CacheStore(DB_FILE,
                   ttl_seconds=int(CONFIG.get("cache", {}).get("ttl_seconds", 86400)),
                   max_entries=int(CONFIG.get("cache", {}).get("max_entries", 10000)))
LOOP_GUARD = icy_core.ToolLoopGuard(LOOP_CFG)


# =============================================================================
# OUTILS MCP
# =============================================================================
mcp = FastMCP("IcyCache Gateway",
               host=str(MCP_GW_CFG.get("host", "127.0.0.1")),
               port=int(MCP_GW_CFG.get("port", 8083)))


@mcp.tool()
def get_cache_stats() -> Dict[str, Any]:
    """Etat du cache LLM (entrees, hits, repartition par upstream/modele)."""
    return CACHE.stats()


@mcp.tool()
def clear_cache(confirm: str) -> str:
    """Vide le cache. Necessite confirm='FORCE_DELETE'."""
    if confirm != "FORCE_DELETE":
        return "ECHEC: utilisez confirm='FORCE_DELETE' pour valider la suppression destructive."
    n = CACHE.clear()
    return f"SUCCES: cache vide ({n} entrees supprimees)."


@mcp.tool()
def normalize_preview(text: str) -> Dict[str, str]:
    """Apercu de la normalisation d'un prompt et de sa cle de cache."""
    norm = icy_normalize.get_default_normalizer()
    normalized = norm.normalize(text)
    return {"original": text, "normalized": normalized, "hash": norm.get_hash(normalized)}


@mcp.tool()
def search_cached_prompts(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Recherche dans les prompts normalises du cache."""
    return CACHE.search(query, limit)


@mcp.tool()
def get_realtime_tokens(limit: int = 20, upstream: str = "") -> Dict[str, Any]:
    """Derniers tokens REELS constatees en amont (jamais estimes).

    Retourne pour chaque evenement : upstream, modele, prompt/completion/cached tokens,
    latence, ttft et la source du token ('upstream' ou 'n/a' si l'upstream n'envoie pas d'usage).
    """
    limit = max(1, min(int(limit), 200))
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        q = ("SELECT id, ts, kind, upstream, client, model_req, model_real, stream, prompt_tokens, "
             "completion_tokens, cached_tokens, cache_write_tokens, latency_ms, ttft_ms, saved_prompt, "
             "saved_completion FROM events")
        args: List[Any] = []
        if upstream:
            q += " WHERE upstream = ?"
            args.append(upstream)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    finally:
        conn.close()
    events = []
    for r in rows:
        events.append({
            "id": r["id"], "ts": r["ts"], "kind": r["kind"], "upstream": r["upstream"],
            "client": r["client"], "model": r["model_real"] or r["model_req"], "stream": bool(r["stream"]),
            "prompt_tokens": r["prompt_tokens"], "completion_tokens": r["completion_tokens"],
            "cached_tokens": r["cached_tokens"], "cache_write_tokens": r["cache_write_tokens"],
            "saved_tokens": (r["saved_prompt"] or 0) + (r["saved_completion"] or 0),
            "latency_ms": r["latency_ms"], "ttft_ms": r["ttft_ms"],
            "tokens_source": "upstream" if (r["prompt_tokens"] is not None or r["completion_tokens"] is not None) else "n/a",
        })
    return {
        "events": events,
        "note": ("prompt_tokens / completion_tokens proviennent du champ usage de la reponse upstream. "
                 "Valeur NULL = l'upstream n'a pas envoye d'usage : aucune estimation n'est faite."),
        "websocket": f"ws://{GW_CFG.get('host', '127.0.0.1')}:{GW_CFG.get('port', 8082)}/ws/events",
        "generated_at": utc_iso(),
    }


@mcp.tool()
def list_upstreams() -> Dict[str, Any]:
    """Upstreams configures dans la gateway (base_url, protocole, defaut)."""
    return {
        "upstreams": {k: {"base_url": v.get("base_url"), "kind": v.get("kind", "openai")}
                      for k, v in UPSTREAMS.items()},
        "default_upstream": GW_CFG.get("default_upstream"),
        "note": "Aucun defaut implicite : si default_upstream est absent, le client doit nommer son upstream.",
    }


@mcp.tool()
def tool_cache_stats() -> Dict[str, Any]:
    """Etat du cache d'outils (entrees, hits, octets) et evenements agent recents."""
    return {"tool_cache": icy_core.tc_stats(), "recent_agent_events": icy_core.agent_events_recent(limit=20)}


# =============================================================================
# OUTILS DE RELAY MCP (tool calling vers les serveurs cibles)
# =============================================================================
@mcp.tool()
async def proxy_tool(server: str, tool: str, args: Optional[Dict[str, Any]] = None,
                    client: str = "gateway") -> Dict[str, Any]:
    """Appelle un outil d'un serveur MCP cible via IcyCache (cache + garde anti-boucle).

    Args:
        server: nom du serveur cible (voir list_proxy_servers)
        tool: nom de l'outil a executer
        args: arguments JSON de l'outil, optionnel
        client: identifiant de l'appelant (session agent), optionnel

    Returns:
        {"ok": true, "cached": false, "latency_ms": 12, "result": ...}
        {"ok": false, "error": "..."} en cas d'erreur (jamais de faux succes)
    """
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible"}
    return await tp.call_tool(server, tool, args, client)


@mcp.tool()
async def proxy_tools(calls: List[Dict[str, Any]], client: str = "gateway") -> List[Dict[str, Any]]:
    """Appel batch d'outils MCP cibles.

    Args:
        calls: liste de [{"server": "..", "tool": "..", "args": {...}}, ...]
        client: identifiant de l'appelant, optionnel

    Returns:
        liste des resultats dans le meme ordre (format identique a proxy_tool)
    """
    if not TOOL_PROXY_OK:
        return [{"ok": False, "error": "tool_proxy indisponible"}]
    return await tp.batch_call(calls or [], client)


@mcp.tool()
def list_proxy_servers() -> Dict[str, Any]:
    """Liste des serveurs MCP routables via IcyCache."""
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible"}
    names = tp.server_names()
    return {"ok": True, "servers": names, "count": len(names)}


@mcp.tool()
async def list_proxy_tools(server: str) -> Dict[str, Any]:
    """Liste les outils exposes par un serveur MCP cible (ex: 'github', 'fetch')."""
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible"}
    return await tp.list_server_tools(server)


# =============================================================================
# API REST (miroir des outils + introspection)
# =============================================================================
app = FastAPI(title="IcyCache Gateway MCP", version="2.0.0")

TOOL_IMPLS = {
    "get_cache_stats": get_cache_stats,
    "clear_cache": clear_cache,
    "normalize_preview": normalize_preview,
    "search_cached_prompts": search_cached_prompts,
    "get_realtime_tokens": get_realtime_tokens,
    "list_upstreams": list_upstreams,
    "tool_cache_stats": tool_cache_stats,
    "proxy_tool": proxy_tool,
    "proxy_tools": proxy_tools,
    "list_proxy_servers": list_proxy_servers,
    "list_proxy_tools": list_proxy_tools,
}


@app.get("/health")
async def health():
    return {"status": "healthy", "tools": sorted(TOOL_IMPLS), "upstreams": sorted(UPSTREAMS)}


@app.get("/api/tools")
async def list_tools():
    return {"tools": sorted(TOOL_IMPLS), "protocol": "icycache.mcp/v1"}


@app.post("/api/tools/{name}")
async def call_tool(name: str, request: Request):
    if name not in TOOL_IMPLS:
        raise HTTPException(status_code=404, detail={"error": "unknown_tool", "tools": sorted(TOOL_IMPLS)})
    try:
        args = await request.json()
    except Exception:
        args = {}
    if not isinstance(args, dict):
        args = {}
    func = TOOL_IMPLS[name]
    t0 = time.perf_counter()
    try:
        if asyncio.iscoroutinefunction(func):
            result = await func(**args)
        else:
            result = await asyncio.to_thread(func, **args)
    except TypeError as exc:
        raise HTTPException(status_code=400, detail={"error": "bad_args", "message": str(exc)})
    latency = int((time.perf_counter() - t0) * 1000)
    log_agent_event("tool_call", server="gateway", tool=name, client="rest",
                    args_hash=icy_core.short_hash(json.dumps(args, sort_keys=True, default=str)),
                    latency_ms=latency, detail={"args": sorted(args.keys())})
    return {"tool": name, "result": result, "latency_ms": latency}


@app.get("/api/agent/events")
async def agent_events(limit: int = 50, kind: str = ""):
    kinds = [k for k in (kind or "").split(",") if k] or None
    return {"events": icy_core.agent_events_recent(limit=limit, kinds=kinds)}


@app.post("/api/loop/check")
async def loop_check(request: Request):
    """Detecte une boucle d'agent dans l'historique d'une requete LLM.

    Body : {"messages": [...], "client": "hermes"}
    """
    body = await request.json()
    messages = body.get("messages") or []
    client = body.get("client") or "unknown"
    warn_after = int(LOOP_CFG.get("llm", {}).get("warn_after", 3))
    verdict = icy_core.llm_loop_check(messages, warn_after=warn_after)
    if verdict:
        log_agent_event("loop_detected", server="gateway", client=client, detail=verdict)
    return {"loop": verdict, "action": LOOP_CFG.get("llm", {}).get("action", "warn"),
            "checked_at": utc_iso()}


@app.post("/api/toolcache/flush")
async def toolcache_flush(request: Request):
    body = await request.json()
    server = body.get("server")
    n = icy_core.tc_flush(server) if server else icy_core.tc_clear()
    return {"flushed": n, "server": server}


@app.get("/api/toolcache/stats")
async def toolcache_stats():
    return icy_core.tc_stats()


@app.get("/api/realtime")
async def realtime(limit: int = 20, upstream: str = ""):
    return get_realtime_tokens(limit=limit, upstream=upstream)


# =============================================================================
if __name__ == "__main__":
    import argparse
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - IcyCache.mcp - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="IcyCache Gateway MCP")
    parser.add_argument("--mode", choices=["api", "mcp-stdio", "mcp-sse", "mcp-http", "all"],
                        default="api")
    parser.add_argument("--port", type=int, default=int(GW_CFG.get("port", 8082)))
    parser.add_argument("--host", default=str(GW_CFG.get("host", "127.0.0.1")))
    args = parser.parse_args()

    if args.mode == "mcp-stdio":
        mcp.run(transport="stdio")
    elif args.mode == "mcp-sse":
        mcp.run(transport="sse")
    elif args.mode == "mcp-http":
        # Streamable HTTP: transport standard des clients MCP recents.
        mcp.run(transport="streamable-http")
    elif args.mode == "all":
        threading = __import__("threading")
        t = threading.Thread(target=lambda: uvicorn.run(app, host=args.host, port=args.port,
                                                        log_level="warning"), daemon=True)
        t.start()
        mcp.run(transport="sse")
    else:
        print(f"IcyCache MCP API  ->  http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
