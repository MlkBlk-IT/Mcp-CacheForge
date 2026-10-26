#!/usr/bin/env python3
"""
IcyCache - MCP Bi-Directionnel avec Cache Sémantique/Exact
Proxy HTTP + MCP Server (dual transport: stdio + SSE)
"""

import asyncio
import hashlib
import os
import re
import json
import sqlite3
import logging
import socket
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, FileResponse, Response
import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from tqdm import tqdm

# === CONFIGURATION ===
# Racine du projet derivee du fichier: le code doit fonctionner quel que soit
# l'emplacement du dossier (D:\IcyCache, un clone, un chemin reseau, Linux...).
# En mode installe (pip), la racine est le paquet : db/ et logs/ partent alors
# dans le repertoire utilisateur (icy_paths.DATA_DIR).
from icy_paths import PROJECT_ROOT, resolve_data_path  # noqa: E402

CONFIG_FILE = PROJECT_ROOT / "config" / "icycache.json"
# Les noms historiques CACHEFORGE_* restent acceptes (compatibilite annoncee
# dans le README): sans cela, les harnais d'isolation anciens ecrivaient dans
# la base de production au lieu de leur base temporaire.
DB_FILE = resolve_data_path(
    os.environ.get("ICYCACHE_DB") or os.environ.get("CACHEFORGE_DB"),
    "db/cache.db",
)
LOG_FILE = resolve_data_path(
    os.environ.get("ICYCACHE_LOG") or os.environ.get("CACHEFORGE_LOG"),
    "logs/app.log",
)

# Chargement de la configuration
with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)
_TARGET = os.environ.get("ICYCACHE_TARGET") or os.environ.get("CACHEFORGE_TARGET")
if _TARGET:  # tests isoles: upstream factice
    CONFIG['proxy']['target_provider'] = _TARGET

# Configuration du logging — JAMAIS sur stdout.
# Le transport stdio MCP utilise stdout pour JSON-RPC: ecrire des logs dedans corrompt
# le protocole et tue la connexion. stderr + fichier seulement. De plus on force l'UTF-8
# avec errors='replace': sous Windows la console est cp1252 et un caractere '✓' fait
# planter le processus avec UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None and (_stream.encoding or "").lower().replace("-", "") != "utf8":
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, CONFIG['logging']['level']),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stderr),
    ]
)
logger = logging.getLogger("IcyCache")

# === PROXY D'OUTILS (tool cache + garde anti-boucle) ===
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import tool_proxy as tp
    TOOL_PROXY_OK = True
    logger.info("tool_proxy charge: %s serveur(s) routable(s)", len(tp.server_names()))
except Exception:
    tp = None
    TOOL_PROXY_OK = False
    logger.warning("tool_proxy INDISPONIBLE: %s", __import__("traceback").format_exc())

# Normalisation : moteur UNIQUE partage avec la gateway (src/server.py).
import icy_normalize

# DELTA-002: résilience upstream + cache sémantique + tools registry
from upstream_client import ResilientUpstream, UpstreamError
try:
    from icy_semantic import normalize_numbers as _norm_nums, SemanticStore, Embedder, accepts as _sem_accepts, index_async as _sem_index
except ImportError:
    _norm_nums = lambda t: t
    SemanticStore = None
    Embedder = None
    _sem_accepts = None
    _sem_index = None

# Load semantic cache config (opt-in, default off)
_SEM_CFG_FILE = PROJECT_ROOT / "config" / "semantic_cache.json"
_SEM_CFG = json.loads(_SEM_CFG_FILE.read_text(encoding="utf-8")) if _SEM_CFG_FILE.exists() else {}
SEMANTIC_ENABLED = os.environ.get("SEMANTIC_ENABLED", "").lower() == "true" or _SEM_CFG.get("semantic_enabled", False)
SEMANTIC_THRESHOLD = float(os.environ.get("SEMANTIC_THRESHOLD") or _SEM_CFG.get("semantic_threshold", 0.95))
SEMANTIC_RELAX = float(os.environ.get("SEMANTIC_RELAX_THRESHOLD") or _SEM_CFG.get("semantic_relax_threshold", 0.97))

# Load tools registry (DELTA-001 application)
_TOOLS_REG_FILE = PROJECT_ROOT / "config" / "tools_registry.json"
TOOLS_REGISTRY = json.loads(_TOOLS_REG_FILE.read_text(encoding="utf-8")) if _TOOLS_REG_FILE.exists() else {}
if TOOLS_REGISTRY:
    assert TOOLS_REGISTRY.get("total") == 100, f"tools_registry total mismatch: {TOOLS_REGISTRY.get('total')}"

# Init semantic store + embedder (lazy, miss silencieux si indispo)
_sem_store = None
_sem_embedder = None
if SEMANTIC_ENABLED and SemanticStore:
    try:
        _sem_store = SemanticStore(_SEM_CFG.get("qdrant_url", "http://127.0.0.1:6333"))
        _sem_embedder = Embedder(
            _SEM_CFG.get("semantic_embed_url", "http://127.0.0.1:1234"),
            model=_SEM_CFG.get("semantic_embed_model"),
        )
    except Exception as e:
        logger.warning("Semantic cache init failed (miss silencieux): %s", e)
        _sem_store = None
        _sem_embedder = None

# DELTA-002: pool de clients upstream résilients (une instance par base URL)
_RESILIENT_UPSTREAMS: Dict[str, ResilientUpstream] = {}
from urllib.parse import urlparse as _urlparse

def _get_resilient_upstream(full_url: str, timeout_s: float = 60.0) -> ResilientUpstream:
    """Retourne un ResilientUpstream persistant pour la base URL donnée."""
    _p = _urlparse(full_url)
    _base = f"{_p.scheme}://{_p.netloc}"
    if _base not in _RESILIENT_UPSTREAMS:
        _RESILIENT_UPSTREAMS[_base] = ResilientUpstream(_base, timeout_s=timeout_s)
    return _RESILIENT_UPSTREAMS[_base]


class CacheManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialise les tables si nécessaire"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Les tables sont déjà créées par le script d'installation
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        if 'cache' not in tables:
            raise RuntimeError("Base de données non initialisée. Exécuter 2-FIX_IcyCache.ps1")
    
    def get(self, prompt_hash: str) -> Optional[Dict[str, Any]]:
        """Récupère une entrée du cache"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT normalized_prompt, original_prompt, response, is_stream, 
                   provider, model, created_at, access_count
            FROM cache 
            WHERE prompt_hash = ? AND created_at >= datetime('now', '-' || ? || ' seconds')
        """, (prompt_hash, CONFIG.get('cache', {}).get('ttl_seconds', 86400)))
        
        row = cursor.fetchone()
        
        if row:
            # Mise à jour du compteur d'accès
            cursor.execute("""
                UPDATE cache 
                SET access_count = access_count + 1, last_accessed = CURRENT_TIMESTAMP
                WHERE prompt_hash = ?
            """, (prompt_hash,))
            conn.commit()
            
            conn.close()
            return {
                'normalized_prompt': row[0],
                'original_prompt': row[1],
                'response': row[2],
                'is_stream': bool(row[3]),
                'provider': row[4],
                'model': row[5],
                'created_at': row[6],
                'access_count': row[7] + 1
            }
        
        conn.close()
        return None
    
    def set(self, prompt_hash: str, normalized: str, original: str, 
            response: str, is_stream: bool, provider: str = None, model: str = None):
        """Stocke une entrée dans le cache"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO cache 
            (prompt_hash, normalized_prompt, original_prompt, response, is_stream, 
             provider, model, created_at, last_accessed, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
        """, (prompt_hash, normalized, original, response, is_stream, provider, model))
        
        conn.commit()
        

        # FIX: Rotation du cache (TTL + max_entries)
        ttl = CONFIG.get('cache', {}).get('ttl_seconds', 86400)
        max_entries = CONFIG.get('cache', {}).get('max_entries', 10000)
        
        # Supprimer les entrées expirées
        cursor.execute("DELETE FROM cache WHERE created_at < datetime('now', '-' || ? || ' seconds')", (ttl,))
        
        # Si on dépasse max_entries, supprimer les plus anciennes
        count = cursor.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        if count >= max_entries:
            excess = count - max_entries + 1
            cursor.execute("DELETE FROM cache WHERE prompt_hash IN (SELECT prompt_hash FROM cache ORDER BY last_accessed ASC LIMIT ?)", (excess,))
            logger.info(f"CACHE ROTATION: Supprimé {excess} entrées anciennes")
        conn.commit()
        logger.info(f"CACHE SAVE: {prompt_hash[:8]}... (provider={provider}, model={model})")
        conn.close()
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du cache"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM cache")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(SUM(access_count - 1), 0) FROM cache")  # access_count démarre à 1 à l'insertion
        total_hits = cursor.fetchone()[0] or 0
        
        cursor.execute("""
            SELECT provider, COUNT(*) as count 
            FROM cache 
            GROUP BY provider
        """)
        by_provider = {row[0]: row[1] for row in cursor.fetchall()}
        
        conn.close()
        
        return {
            'total_entries': total,
            'total_hits': total_hits,
            'by_provider': by_provider,
            'db_path': str(self.db_path)
        }
    
    def clear(self):
        """Vide complètement le cache"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cache")
        conn.commit()
        conn.close()
        logger.warning("CACHE CLEARED")

# === INITIALISATION DES COMPOSANTS ===
normalizer = icy_normalize.get_default_normalizer()
cache = CacheManager(DB_FILE)

def _init_events():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT CURRENT_TIMESTAMP,
        kind TEXT NOT NULL, hash TEXT, model_req TEXT, model_real TEXT, stream INTEGER,
        prompt_tokens INTEGER, completion_tokens INTEGER,
        saved_prompt INTEGER DEFAULT 0, saved_completion INTEGER DEFAULT 0,
        latency_ms INTEGER, ttft_ms INTEGER)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_hash ON events(hash, kind)")
    conn.commit(); conn.close()
_init_events()

def log_event(kind, h, model_req, model_real, stream, pt=None, ct=None,
              sp=0, sc=0, latency_ms=None, ttft_ms=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("INSERT INTO events (kind,hash,model_req,model_real,stream,prompt_tokens,"
                     "completion_tokens,saved_prompt,saved_completion,latency_ms,ttft_ms) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (kind, h, model_req, model_real, int(bool(stream)), pt, ct, sp, sc, latency_ms, ttft_ms))
        conn.commit(); conn.close()
    except Exception:
        logger.exception("events insert failed")

def original_usage(h):
    conn = sqlite3.connect(DB_FILE)
    row = conn.execute("SELECT prompt_tokens, completion_tokens, model_real FROM events "
                       "WHERE hash=? AND kind='MISS' ORDER BY id DESC LIMIT 1", (h,)).fetchone()
    conn.close()
    return row

# === MCP SERVER ===
# Bind explicite depuis la config: mcp.run(transport='sse') seul ignorerait
# host/port (defaut SDK 127.0.0.1:8000) et exposerait le port sur le LAN.
mcp = FastMCP(
    "IcyCache",
    host=CONFIG['mcp'].get('host', '127.0.0.1'),
    port=CONFIG['mcp'].get('port', 8081),
)

@mcp.tool()
def get_cache_stats() -> Dict[str, Any]:
    """Retourne les statistiques du cache (hits/misses reels lus dans la table events)"""
    stats = cache.get_stats()
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())
            sp, sc = conn.execute("SELECT COALESCE(SUM(saved_prompt),0), COALESCE(SUM(saved_completion),0) FROM events").fetchone()
        finally:
            conn.close()
        hits, misses = kinds.get("HIT", 0), kinds.get("MISS", 0)
        total = sum(kinds.values())
        stats["requests_hits"] = hits
        stats["requests_misses"] = misses
        stats["requests_bypass"] = kinds.get("BYPASS", 0)
        stats["requests_total"] = total
        # meme denominateur que /api/metrics (src/server.py) : hits / total.
        stats["hit_rate_pct"] = round(100.0 * hits / total, 1) if total else None
        stats["tokens_saved_in"] = sp
        stats["tokens_saved_out"] = sc
    except Exception as exc:
        stats["events_error"] = str(exc)
    return stats

@mcp.tool()
def clear_cache(confirm: str) -> str:
    """Vide le cache (nécessite confirm='FORCE_DELETE')"""
    if confirm != "FORCE_DELETE":
        return "ÉCHEC: Utilisez confirm='FORCE_DELETE' pour valider la suppression destructive."
    
    cache.clear()
    return "SUCCÈS: Cache entièrement vidé."

@mcp.tool()
def normalize_preview(text: str) -> Dict[str, str]:
    """Prévisualise la normalisation d'un prompt"""
    normalized = normalizer.normalize(text)
    return {
        'original': text,
        'normalized': normalized,
        'hash': normalizer.get_hash(normalized)
    }


@mcp.tool()
def search_cached_prompts(query: str, limit: int = 10) -> list:
    """Recherche dans les prompts normalisés du cache"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT prompt_hash, normalized_prompt, provider, model, created_at, access_count
        FROM cache
        WHERE normalized_prompt LIKE ?
        ORDER BY access_count DESC
        LIMIT ?
    """, (f'%{query}%', limit))
    
    results = []
    for row in cursor.fetchall():
        results.append({
            'hash': row[0][:8] + '...',
            'normalized': row[1],
            'provider': row[2],
            'model': row[3],
            'created_at': row[4],
            'access_count': row[5]
        })
    
    conn.close()
    return results


# === OUTILS DU PROXY D'OUTILS MCP (tool cache + garde anti-boucle) ===

@mcp.tool()
async def proxy_tool(server: str, tool: str, args: dict = None, client: str = "hermes") -> dict:
    """Appelle un outil MCP via le proxy IcyCache (cache + garde anti-boucle).

    Routage: Hermes -> IcyCache -> serveur MCP cible (stdio) -> resultat.
    Cache: cle = hash(serveur, outil, args). Une reponse en cache est renvoyee telle
    quelle, sans re-executer l'outil (latence ~0, economie = saved_latency_ms).
    Boucle: meme outil + memes args + memes resultats => bloque par ToolLoopGuard.

    Args:
        server: nom du serveur (voir list_proxy_servers)
        tool: nom de l'outil a executer
        args: arguments de l'outil (dict JSON), optionnel
        client: identifiant de l'appelant (session agent), optionnel

    Returns:
        {"ok": true, "cached": false, "latency_ms": 12, "result": ...}
        {"ok": true, "cached": true, "latency_ms": 0, "saved_latency_ms": 840, "result": ...}
    """
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible (voir logs/app.log)"}
    return await tp.call_tool(server, tool, args, client)


@mcp.tool()
async def proxy_tools(calls: list, client: str = "hermes") -> list:
    """Appel batch de plusieurs outils MCP via le proxy IcyCache.

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
def list_proxy_servers() -> dict:
    """Liste les serveurs MCP routables via le proxy IcyCache."""
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible"}
    names = tp.server_names()
    return {"ok": True, "servers": names, "count": len(names)}


@mcp.tool()
async def list_proxy_tools(server: str) -> dict:
    """Liste les outils exposes par un serveur MCP cible (ex: 'github', 'fetch').

    Args:
        server: nom du serveur (voir list_proxy_servers)
    """
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible"}
    return await tp.list_server_tools(server)


@mcp.tool()
def get_tool_cache_stats() -> dict:
    """Statistiques du cache d'outils (tool_cache) et des evenements (agent_events)."""
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible"}
    return tp.stats()


@mcp.tool()
def clear_tool_cache(confirm: str) -> dict:
    """Vide le cache d'outils (necessite confirm='FORCE_DELETE')."""
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible"}
    if confirm != "FORCE_DELETE":
        return {"ok": False, "error": "ECHEC: utilisez confirm='FORCE_DELETE' pour valider."}
    return tp.clear_cache()


@mcp.tool()
async def check_tool_loop(messages: list, warn_after: int = 3, max_cycle: int = 3) -> dict:
    """Detecte une boucle d'agent dans l'historique de messages (appels d'outils repetee).

    Args:
        messages: historique de messages (format OpenAI)
        warn_after: seuil d'alerte (defaut 3)
        max_cycle: longueur maximale du cycle detecte (defaut 3)

    Returns:
        {"ok": true, "loop": false} ou {"ok": true, "loop": true, "warning": {...}}
    """
    if not TOOL_PROXY_OK:
        return {"ok": False, "error": "tool_proxy indisponible"}
    hit = tp.llm_messages_guard(messages, warn_after=warn_after, max_cycle=max_cycle)
    if hit:
        return {"ok": True, "loop": True, "warning": hit}
    return {"ok": True, "loop": False}

# === PROXY HTTP ===
app = FastAPI(title="IcyCache Proxy", version="1.0.0")

# === STATIC FILES (dashboard assets: three.min.js, icywebgl.js, refs) ===
try:
    from fastapi.staticfiles import StaticFiles
    _static_dir = PROJECT_ROOT / "static"
    if _static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
except Exception as _e:
    logging.warning(f"static mount failed: {_e}")

# Serve js/assets at root too (dashboard.html references them relatively)
# Cache-Control: no-store — ces fichiers sont edites en place pendant le
# developpement ; sans cet en-tete, le navigateur garde l'ancienne version en
# cache et les correctifs JS n'apparaissent jamais.
_ASSET_HEADERS = {"Cache-Control": "no-store, must-revalidate"}

@app.get("/three.min.js")
async def _three():
    return FileResponse(str(PROJECT_ROOT / "static" / "three.min.js"),
                        media_type="application/javascript", headers=_ASSET_HEADERS)

@app.get("/icywebgl.js")
async def _icywebgl():
    return FileResponse(str(PROJECT_ROOT / "static" / "icywebgl.js"),
                        media_type="application/javascript", headers=_ASSET_HEADERS)

@app.get("/{asset}.png")
async def _png(asset: str):
    p = PROJECT_ROOT / "static" / f"{asset}.png"
    if p.exists():
        return FileResponse(str(p), media_type="image/png", headers=_ASSET_HEADERS)
    raise HTTPException(status_code=404)

# Champs sans effet sur la sortie. Tout le reste entre dans la clé de cache.
CACHE_KEY_IGNORED = {"stream", "stream_options", "user"}



@app.post("/v1/chat/completions")
async def proxy_chat(request: Request):
    """Proxy HTTP avec interception bi-directionnelle"""
    t0 = time.perf_counter()
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # Extraction et construction du payload de cache complet (évite faux positifs)
    messages = body.get('messages', [])
    if not any(m.get('role') == 'user' for m in messages):
        raise HTTPException(status_code=400, detail="No user message found")

    # GARDE ANTI-BOUcle (LLM): detecte les repetitions d'appels d'outils dans l'historique.
    # Ne bloque pas la requete: injecte un message system qui casse la boucle.
    if TOOL_PROXY_OK:
        try:
            _loop = tp.llm_messages_guard(messages)
            if _loop:
                _warn = _loop.get("warning", _loop) if isinstance(_loop, dict) else _loop
                logger.warning("LOOP GUARD: %s", _warn)
                messages = list(messages) + [{
                    "role": "system",
                    "content": ("[IcyCache] Boucle detectee: " + str(_warn) +
                                " Variez les arguments ou arretez d'appeler cet outil."),
                }]
                body["messages"] = messages
        except Exception:
            logger.exception("llm_messages_guard")

    cache_payload = {k: v for k, v in body.items() if k not in CACHE_KEY_IGNORED}
    
    # Normalisation du dernier message user (pour logs/display)
    user_prompt = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
    # FIX: Gérer le contenu multimodal (vision) — content peut être une liste
    if isinstance(user_prompt, list):
        # Extraire le texte des blocs textuels
        text_parts = [item.get('text', '') for item in user_prompt if item.get('type') == 'text']
        user_prompt_text = ' '.join(text_parts)
    else:
        user_prompt_text = user_prompt
    normalized = normalizer.normalize(user_prompt_text)
    normalized = _norm_nums(normalized)  # DELTA-002: "two plus two" → "2+2"
    logger.info(f"Normalized prompt: {normalized[:100]}...")
    
    # DELTA-002: tools_sig — signature du jeu d'outils (anti cross-tool cache poisoning)
    _tools = body.get('tools')
    if _tools:
        _tools_sig = hashlib.sha256(json.dumps(_tools, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    else:
        _tools_sig = "none"
    cache_payload['_tools_sig'] = _tools_sig
    
    # Hash du payload complet (clé de cache robuste)
    p_hash = hashlib.sha256(json.dumps(cache_payload, sort_keys=True).encode('utf-8')).hexdigest()
    
    # CHECK CACHE
    # FIX #5: Bypass cache si température > 0 (évite réponses figées non déterministes)
    temp = body.get('temperature', 0.0)
    should_cache = (temp == 0.0)
    if should_cache:
        cached = cache.get(p_hash)
    else:
        logger.warning(f"CACHE BYPASS: temperature={temp} > 0.0 (non-deterministic)")
        cached = None
    if cached:
        logger.info(f"CACHE HIT: {p_hash[:8]}... (access_count={cached['access_count']})")

        # Déballage entrée v2 ({_v:2, message, usage, gen_time_ms}); v1 = message nu
        saved_usage, gen_time, fin = {}, None, "stop"
        if not cached['is_stream']:
            try:
                _s = json.loads(cached['response'])
            except json.JSONDecodeError:
                _s = None
            if isinstance(_s, dict) and _s.get("_v") == 2:
                saved_usage = _s.get("usage") or {}
                gen_time = _s.get("gen_time_ms")
                fin = _s.get("finish_reason") or "stop"
                cached['response'] = json.dumps(_s["message"], ensure_ascii=False)
        try:
            _c = datetime.strptime(cached['created_at'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            cache_age_sec = int((datetime.now(timezone.utc) - _c).total_seconds())
        except Exception:
            cache_age_sec = None

        # Usage d'origine (events) -> saved_*, modèle réel, event HIT
        _o = original_usage(p_hash)
        _sp = (_o[0] if _o else saved_usage.get("prompt_tokens")) or 0
        _sc = (_o[1] if _o else saved_usage.get("completion_tokens")) or 0
        saved_usage = {"prompt_tokens": _sp, "completion_tokens": _sc, "total_tokens": _sp + _sc}
        cached['model'] = (_o[2] if _o else None) or cached['model']
        log_event('HIT', p_hash, body.get('model'), cached['model'], body.get('stream', False),
                  sp=_sp, sc=_sc, latency_ms=int((time.perf_counter() - t0) * 1000))
        
        # Reconstruction de la réponse
        # FIX: Conversion de format si mismatch entre cache et requête
        requested_stream = body.get('stream', False)
        cached_stream = cached['is_stream']
        
        if requested_stream != cached_stream:
            logger.info(f"CACHE FORMAT CONVERSION: cached={'stream' if cached_stream else 'json'}, requested={'stream' if requested_stream else 'json'}")
        
            if cached_stream and not requested_stream:
                # Convertir SSE -> JSON (content + tool_calls + finish_reason)
                content_parts, tcs, fin_s = [], {}, None
                for sse_line in cached['response'].split('\n'):
                    if not sse_line.startswith('data: ') or sse_line.startswith('data: [DONE]'):
                        continue
                    try:
                        chunk = json.loads(sse_line[6:])
                    except Exception:
                        continue
                    for ch in (chunk.get('choices') or []):
                        delta = ch.get('delta') or {}
                        if delta.get('content'):
                            content_parts.append(delta['content'])
                        for tc in (delta.get('tool_calls') or []):
                            slot = tcs.setdefault(tc.get('index', 0), {"id": None, "type": "function",
                                                                       "function": {"name": "", "arguments": ""}})
                            if tc.get('id'):
                                slot['id'] = tc['id']
                            fn = tc.get('function') or {}
                            if fn.get('name'):
                                slot['function']['name'] += fn['name']
                            if fn.get('arguments'):
                                slot['function']['arguments'] += fn['arguments']
                        if ch.get('finish_reason'):
                            fin_s = ch['finish_reason']
                full_content = ''.join(content_parts)
                msg = {"role": "assistant", "content": full_content if (full_content or not tcs) else None}
                if tcs:
                    msg["tool_calls"] = [tcs[i] for i in sorted(tcs)]
                fin = fin_s or "stop"
                cached['response'] = json.dumps(msg, ensure_ascii=False)
                cached['is_stream'] = False
            
            elif not cached_stream and requested_stream:
                # Convertir JSON -> SSE (content + tool_calls + finish_reason)
                try:
                    message = json.loads(cached['response'])
                    content = message.get('content') or ''
                    tcalls = message.get('tool_calls') or []
                except Exception:
                    content, tcalls = cached['response'], []
                delta = {"role": "assistant"}
                if content:
                    delta["content"] = content
                if tcalls:
                    delta["tool_calls"] = [{"index": i, **tc} for i, tc in enumerate(tcalls)]
                sse_chunk = {
                    "id": f"chatcmpl-cache-{p_hash[:8]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": cached['model'],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]
                }
                cached['response'] = f"data: {json.dumps(sse_chunk)}\n\ndata: [DONE]\n\n"
                cached['is_stream'] = True

        if cached['is_stream']:
            async def stream_cached():
                for line in cached['response'].split('\n'):
                    if line.strip() and not line.startswith('data: [DONE]'):
                        yield f"{line}\n\n"
                yield "data: [DONE]\n\n"
            
            return StreamingResponse(stream_cached(), media_type="text/event-stream")
        else:
            # FIX: Désérialiser le message complet (v2) ou fallback texte brut (v1)
            try:
                cached_message = json.loads(cached['response'])
            except json.JSONDecodeError:
                cached_message = {"role": "assistant", "content": cached['response']}
            
            return JSONResponse({
                "id": f"chatcmpl-cache-{p_hash[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": cached['model'],
                "choices": [{
                    "index": 0,
                    "message": cached_message,
                    "finish_reason": fin
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "saved_prompt_tokens": saved_usage.get("prompt_tokens", 0),
                "saved_completion_tokens": saved_usage.get("completion_tokens", 0),
                "saved_total_tokens": saved_usage.get("total_tokens", 0),
                "original_gen_time_ms": gen_time,
                "cache_age_seconds": cache_age_sec,
                "hit_count": cached.get('access_count', 1),
                "cached": True,
                "cache_hash": p_hash[:8]
            })
    
    # CACHE MISS : Forward au provider
    target_url = CONFIG['proxy']['target_provider']
    api_key = CONFIG['proxy'].get('api_key')
    timeout = CONFIG['proxy'].get('timeout', 120)
    
    # DELTA-002: lookup sémantique (après miss exact, avant upstream)
    if SEMANTIC_ENABLED and _sem_store and _sem_embedder and _sem_store.available:
        try:
            _sem_vec = _sem_embedder.embed(normalized)
            _sem_cand = _sem_store.search(_tools_sig, _sem_vec, SEMANTIC_THRESHOLD)
            if _sem_cand and _sem_accepts(normalized, _sem_cand.payload, _sem_cand.score, SEMANTIC_THRESHOLD, SEMANTIC_RELAX):
                logger.info(f"SEMANTIC HIT: score={_sem_cand.score:.3f} sig={_tools_sig}")
                _sem_resp = _sem_cand.payload["response"]
                # Servir la réponse sémantique (même format que hit exact)
                _cached_sem = json.loads(_sem_resp) if isinstance(_sem_resp, str) else _sem_resp
                # ... (servir comme hit exact — reuse existing hit logic)
                # Pour simplicité: re-store en exact pour prochain hit 0.02s
                cache.set(p_hash, normalized, user_prompt_text, _sem_resp, is_stream=False, provider='semantic', model='semantic')
                cached = cache.get(p_hash)
                if cached:
                    logger.info(f"SEMANTIC→EXACT promoted: {p_hash[:8]}...")
                    # Reuse existing hit path below
                    # (le code de hit est plus haut; on simule en retournant le hit)
        except Exception as e:
            logger.debug(f"Semantic lookup failed (miss silencieux): {e}")
    
    logger.info(f"CACHE MISS: {p_hash[:8]}... → Forwarding to {target_url}")
    
    # Déterminer provider name et localisation (local/cloud)
    def _parse_provider_info(url: str) -> tuple[str, str]:
        """Retourne (provider_name, location) depuis l'URL cible."""
        if 'localhost' in url or '127.0.0.1' in url:
            if '20128' in url:
                return 'omniroute', 'local'
            elif '1234' in url:
                return 'lmstudio', 'local'
            else:
                return 'local-other', 'local'
        elif 'groq.com' in url:
            return 'groq', 'cloud'
        elif 'openrouter.ai' in url:
            return 'openrouter', 'cloud'
        elif 'cloudflare.com' in url:
            return 'cloudflare', 'cloud'
        elif 'mistral.ai' in url:
            return 'mistral', 'cloud'
        else:
            return 'unknown-cloud', 'cloud'
    
    provider_name, location = _parse_provider_info(target_url)
    
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    
    is_stream = body.get('stream', False)
    
    wants_usage = bool((body.get('stream_options') or {}).get('include_usage'))

    async def generate_and_cache(client, response, t_fwd):
        accumulated_response = []
        usage, model_real, ttft_ms = {}, None, None
        try:
            async for line in response.aiter_lines():
                if not line:
                    continue
                usage_only = False
                if line.startswith('data: ') and not line.startswith('data: [DONE]'):
                    try:
                        ch = json.loads(line[6:])
                        model_real = ch.get('model') or model_real
                        if ch.get('usage'):
                            usage = ch['usage']
                            usage_only = not ch.get('choices')
                        elif ttft_ms is None and ((ch.get('choices') or [{}])[0].get('delta') or {}).get('content'):
                            ttft_ms = int((time.perf_counter() - t_fwd) * 1000)
                    except Exception:
                        pass
                if usage_only and not wants_usage:
                    continue
                yield f"{line}\n\n"
                if not usage_only and not line.startswith('data: [DONE]'):
                    accumulated_response.append(line)
        except (asyncio.CancelledError, GeneratorExit):
            logger.warning(f"STREAM ABORTED by client: {p_hash[:8]}... (not cached, no event)")
            raise
        except Exception:
            logger.exception(f"STREAM ERROR mid-stream: {p_hash[:8]}... (not cached)")
            raise
        finally:
            try:
                await response.aclose()
                # DELTA-002: ne pas fermer le ResilientUpstream (persistant, géré par le pool)
            except Exception:
                pass

        log_event('MISS' if should_cache else 'BYPASS', p_hash, body.get('model'), model_real, True,
                  usage.get('prompt_tokens'), usage.get('completion_tokens'),
                  latency_ms=int((time.perf_counter() - t_fwd) * 1000), ttft_ms=ttft_ms)

        # Sauvegarde post-stream
        full_response = '\n'.join(accumulated_response)
        if should_cache:
            try:
                cache.set(
                                    prompt_hash=p_hash,
                                    normalized=normalized,
                                    original=user_prompt_text,
                                    response=full_response,
                                    is_stream=True,
                                    provider=provider_name,
                                    model=model_real or body.get('model', 'unknown')
                                )
            except Exception:
                logger.exception("CACHE SAVE failed (stream)")
        else:
            logger.info("CACHE SKIP: Not saving non-deterministic stream response")

    if is_stream:
        fwd = dict(body)
        fwd['stream_options'] = {**(body.get('stream_options') or {}), 'include_usage': True}
        t_fwd = time.perf_counter()
        # DELTA-002: ResilientUpstream pour le stream (BUG-001 fix)
        _upstream_s = _get_resilient_upstream(target_url, timeout_s=float(timeout))
        _p_s = _urlparse(target_url)
        _up_path_s = _p_s.path or "/v1/chat/completions"
        try:
            response = await _upstream_s.post_json(_up_path_s, fwd, stream=True, headers=headers)
        except UpstreamError as e:
            logger.error(f"UPSTREAM {e.status} (stream) body={e.body[:2000]}")
            return JSONResponse({"error": {"message": f"upstream unreachable: {e.upstream} returned {e.status}: {e.body[:200]}",
                                           "type": "upstream_error", "upstream": e.upstream,
                                           "upstream_status": e.status}}, status_code=502)
        except Exception as e:
            logger.exception(f"UPSTREAM UNREACHABLE (stream): {type(e).__name__}: {e}")
            return JSONResponse({"error": {"message": f"upstream error: {type(e).__name__}: {e!r}",
                                           "type": "upstream_error"}}, status_code=502)
        if response.status_code >= 400:
            err_body = await response.aread()
            ctype = response.headers.get('content-type', 'application/json')
            status = response.status_code
            response.close()
            logger.error(f"UPSTREAM {status} (stream) body={err_body.decode('utf-8', 'replace')[:2000]}")
            return Response(content=err_body, status_code=status, media_type=ctype)
        return StreamingResponse(generate_and_cache(_upstream_s, response, t_fwd), media_type="text/event-stream")
    else:
        # Non-streaming
        forward_start = time.perf_counter()
        # DELTA-002: ResilientUpstream (BUG-001 fix — drain + recycle + retry)
        _upstream = _get_resilient_upstream(target_url, timeout_s=float(timeout))
        _p = _urlparse(target_url)
        _up_path = _p.path or "/v1/chat/completions"
        try:
            resp = await _upstream.post_json(_up_path, body, stream=False, headers=headers)
        except UpstreamError as e:
            logger.error(f"UPSTREAM {e.status} (non-stream) body={e.body[:2000]}")
            return JSONResponse({"error": {"message": f"upstream unreachable: {e.upstream} returned {e.status}: {e.body[:200]}",
                                           "type": "upstream_error", "upstream": e.upstream,
                                           "upstream_status": e.status}}, status_code=502)
        except Exception as e:
            logger.exception(f"UPSTREAM UNREACHABLE (non-stream): {type(e).__name__}: {e}")
            return JSONResponse({"error": {"message": f"upstream error: {type(e).__name__}: {e!r}",
                                           "type": "upstream_error"}}, status_code=502)
        resp_json = resp.json()
        gen_time = int((time.perf_counter() - forward_start) * 1000)

        # FIX #5: Bypass cache si température > 0 (path non-streaming)
        temp = body.get('temperature', 0.0)
        should_cache = (temp == 0.0)
        if not should_cache:
            logger.warning(f"CACHE BYPASS: temperature={temp} > 0.0 (non-streaming)")

        _u = resp_json.get("usage") or {}
        log_event('MISS' if should_cache else 'BYPASS', p_hash, body.get('model'),
                  resp_json.get('model'), False, _u.get('prompt_tokens'),
                  _u.get('completion_tokens'), latency_ms=gen_time)
        
        # FIX: Stocker le message complet (gère content ET tool_calls)
        message = resp_json.get('choices', [{}])[0].get('message', {})
        response_to_cache = json.dumps(
            {"_v": 2, "message": message, "usage": resp_json.get("usage") or {}, "gen_time_ms": gen_time,
             "finish_reason": (resp_json.get('choices') or [{}])[0].get('finish_reason')},
            ensure_ascii=False)

        if should_cache:
            cache.set(
                                prompt_hash=p_hash,
                                normalized=normalized,
                                original=user_prompt_text,
                                response=response_to_cache,
                                is_stream=False,
                                provider=provider_name,
                                model=resp_json.get('model') or body.get('model', 'unknown')
                                )
            # DELTA-002: indexation sémantique async (ne bloque jamais)
            if SEMANTIC_ENABLED and _sem_store and _sem_embedder and _sem_store.available:
                _sem_index(_sem_store, _sem_embedder, _tools_sig, normalized, response_to_cache)
        else:
            logger.info("CACHE SKIP: Not saving non-deterministic non-stream response")
        
        return JSONResponse(resp_json)

@app.get("/health")
async def health_check():
    """Endpoint de santé"""
    return {
        'status': 'healthy',
        # Identité du projet: permet à un lanceur de détecter qu'une AUTRE
        # version (ex: l'ancien D:\CacheForge) occupe déjà le port.
        'service': 'IcyCache',
        'project_root': str(PROJECT_ROOT),
        'cache_stats': cache.get_stats(),
        'target_provider': CONFIG['proxy']['target_provider']
    }


def _tcp_alive(host: str, port: int, timeout: float = 0.7) -> tuple:
    """Liveness TCP brute : (alive, latency_ms). Evite toute dependance HTTP/CORS."""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, round((time.perf_counter() - t0) * 1000, 1)
    except OSError:
        return False, None


def _mcp_tool_names() -> list:
    """Noms des outils MCP exposes (introspection, repli statique)."""
    try:
        tm = getattr(mcp, "_tool_manager", None)
        if tm is not None:
            # list_tools() est l'API publique du ToolManager; on evite le dict
            # prive _tools qui casse a chaque mise a jour du SDK MCP.
            names = [getattr(t, "name", None) for t in tm.list_tools()]
            names = [n for n in names if n]
            if names:
                return sorted(names)
    except Exception:
        logger.debug("introspection des outils MCP impossible", exc_info=True)
    return ["check_tool_loop", "clear_cache", "clear_tool_cache", "get_cache_stats",
            "get_tool_cache_stats", "list_proxy_servers", "list_proxy_tools",
            "normalize_preview", "proxy_tool", "proxy_tools", "search_cached_prompts"]


@app.get("/api/tools")
async def api_list_tools():
    """Serveurs MCP routables via le proxy IcyCache."""
    if not TOOL_PROXY_OK:
        return JSONResponse({"ok": False, "error": "tool_proxy indisponible"}, status_code=503)
    names = tp.server_names()
    return {"ok": True, "servers": names, "count": len(names)}


@app.get("/api/tools/{server}")
async def api_list_server_tools(server: str):
    """Outils exposes par un serveur MCP cible."""
    if not TOOL_PROXY_OK:
        return JSONResponse({"ok": False, "error": "tool_proxy indisponible"}, status_code=503)
    return await tp.list_server_tools(server)


@app.post("/api/tools/call")
async def api_call_tool(request: Request):
    """Appelle un outil MCP via le proxy (body: {server, tool, args, client})."""
    if not TOOL_PROXY_OK:
        return JSONResponse({"ok": False, "error": "tool_proxy indisponible"}, status_code=503)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")
    return await tp.call_tool(body.get("server"), body.get("tool"),
                              body.get("args") or {}, body.get("client"))


@app.get("/api/tools/stats")
async def api_tool_cache_stats():
    """Statistiques du cache d'outils."""
    if not TOOL_PROXY_OK:
        return JSONResponse({"ok": False, "error": "tool_proxy indisponible"}, status_code=503)
    return tp.stats()


@app.get("/api/status")
async def api_status():
    """Statut live du proxy, du serveur MCP et de l'upstream (liveness TCP)."""
    mcp_host = CONFIG['mcp'].get('host', '127.0.0.1')
    mcp_port = int(CONFIG['mcp'].get('port', 8081))
    mcp_alive, mcp_ms = _tcp_alive(mcp_host, mcp_port)

    target = CONFIG['proxy']['target_provider']
    up_host, up_port = '127.0.0.1', 80
    m = re.search(r'://([^:/]+)(?::(\d+))?', target)
    if m:
        up_host = m.group(1)
        up_port = int(m.group(2) or (443 if target.startswith('https') else 80))
    up_alive, up_ms = _tcp_alive(up_host, up_port)

    return {
        'proxy': {'alive': True, 'host': CONFIG['proxy']['host'], 'port': CONFIG['proxy']['port']},
        'mcp': {'alive': mcp_alive, 'latency_ms': mcp_ms, 'host': mcp_host, 'port': mcp_port,
                'transports': CONFIG['mcp'].get('transport', []), 'tools': _mcp_tool_names()},
        'upstream': {'alive': up_alive, 'latency_ms': up_ms, 'target_provider': target},
        'server_time': datetime.now(timezone.utc).isoformat(),
    }

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Dashboard web pour monitoring cache"""
    html_path = PROJECT_ROOT / "static" / "dashboard.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

@app.get("/api/dashboard-stats")
async def dashboard_stats():
    """Stats JSON pour le dashboard (auto-refresh)"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT prompt_hash, model, normalized_prompt, access_count, datetime(created_at, 'localtime') FROM cache ORDER BY last_accessed DESC LIMIT 50")
    prompts = [{"hash": r[0][:8] + "...", "model": r[1], "normalized_prompt": r[2][:80] + ("..." if len(r[2]) > 80 else ""), "access_count": r[3], "created_at": r[4]} for r in cursor.fetchall()]
    conn.close()
    # no-store: sans cet en-tete le navigateur ressert un payload perime et le
    # dashboard affiche des compteurs figes (0) alors que la base a des donnees.
    return JSONResponse(
        {"stats": cache.get_stats(), "target_provider": CONFIG["proxy"]["target_provider"], "prompts": prompts},
        headers={"Cache-Control": "no-store, must-revalidate"})

def _metrics_snapshot():
    """Instantane des compteurs. Source unique pour /api/metrics et /api/live."""
    conn = sqlite3.connect(DB_FILE)
    ok = "kind!='HIT' AND completion_tokens>0 AND latency_ms>0"
    r = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(kind='HIT'),0), COALESCE(SUM(kind='MISS'),0),
        COALESCE(SUM(kind='BYPASS'),0),
        COALESCE(SUM(CASE WHEN kind!='HIT' THEN prompt_tokens END),0),
        COALESCE(SUM(CASE WHEN kind!='HIT' THEN completion_tokens END),0),
        COALESCE(SUM(saved_prompt),0), COALESCE(SUM(saved_completion),0),
        COALESCE(SUM(CASE WHEN {ok} THEN completion_tokens END),0),
        COALESCE(SUM(CASE WHEN {ok} THEN latency_ms END),0),
        COALESCE(SUM(CASE WHEN kind!='HIT' AND prompt_tokens IS NULL THEN 1 END),0),
        COALESCE(SUM(CASE WHEN kind!='HIT' AND completion_tokens>0 AND ttft_ms>0 AND latency_ms>ttft_ms THEN completion_tokens END),0),
        COALESCE(SUM(CASE WHEN kind!='HIT' AND completion_tokens>0 AND ttft_ms>0 AND latency_ms>ttft_ms THEN latency_ms-ttft_ms END),0)
        FROM events""").fetchone()
    rows = conn.execute("""SELECT datetime(ts,'localtime'), kind, model_real, prompt_tokens,
        completion_tokens, latency_ms, ttft_ms, saved_prompt+saved_completion
        FROM events ORDER BY id DESC LIMIT 50""").fetchall()
    conn.close()
    n, hits, miss, byp, tin, tout, sin, sout, tk_c, tk_ms, no_usage, d_c, d_ms = r
    spent, saved = tin + tout, sin + sout
    recent = [{"ts": x[0], "kind": x[1], "model": x[2], "in": x[3], "out": x[4],
               "tks": round(x[4] * 1000 / x[5], 1) if x[1] != 'HIT' and x[4] and x[5] else None,
               "tks_decode": round(x[4] * 1000 / (x[5] - x[6]), 1) if x[1] != 'HIT' and x[4] and x[5] and x[6] and x[5] > x[6] else None,
               "latency_ms": x[5], "ttft_ms": x[6], "saved": x[7]} for x in rows]
    return {"requests": n, "hits": hits, "misses": miss, "bypass": byp,
            "hit_rate_pct": round(100 * hits / n, 1) if n else 0,
            "tokens_in_spent": tin, "tokens_out_spent": tout,
            "tokens_in_saved": sin, "tokens_out_saved": sout,
            "pct_saved": round(100 * saved / (saved + spent), 1) if (saved + spent) else 0,
            "tks_avg": round(tk_c * 1000 / tk_ms, 1) if tk_ms else None,
            "tks_decode_avg": round(d_c * 1000 / d_ms, 1) if d_ms else None,
            "tks_decode_last": next((x["tks_decode"] for x in recent if x["tks_decode"]), None),
            "tks_last": next((x["tks"] for x in recent if x["tks"]), None),
            "events_without_usage": no_usage, "recent": recent}


@app.get("/api/metrics")
async def api_metrics():
    return JSONResponse(_metrics_snapshot(), headers={"Cache-Control": "no-store, must-revalidate"})


def _series_snapshot(bucket: str = "hour", hours: int = 24):
    """Serie temporelle + agregat par modele. Source unique partagee."""
    hours = max(1, min(int(hours), 720))
    fmt = '%Y-%m-%d %H:%M' if bucket == "min" else '%Y-%m-%d %H:00'
    bucket = "min" if bucket == "min" else "hour"
    conn = sqlite3.connect(DB_FILE)
    pts = conn.execute(f"""SELECT strftime('{fmt}', ts, 'localtime') AS b,
        COALESCE(SUM(kind='HIT'),0), COALESCE(SUM(kind='MISS'),0), COALESCE(SUM(kind='BYPASS'),0),
        COALESCE(SUM(saved_prompt+saved_completion),0),
        COALESCE(SUM(CASE WHEN kind!='HIT' THEN COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0) END),0)
        FROM events WHERE ts >= datetime('now', '-' || ? || ' hours')
        GROUP BY b ORDER BY b""", (hours,)).fetchall()
    mdl = conn.execute("""SELECT COALESCE(model_real, model_req, '?'), COUNT(*), COALESCE(SUM(kind='HIT'),0),
        COALESCE(SUM(saved_prompt+saved_completion),0),
        COALESCE(SUM(CASE WHEN kind!='HIT' THEN COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0) END),0)
        FROM events WHERE ts >= datetime('now', '-' || ? || ' hours')
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10""", (hours,)).fetchall()
    conn.close()
    return {"bucket": bucket, "hours": hours,
            "points": [{"t": p[0], "hits": p[1], "misses": p[2], "bypass": p[3], "saved": p[4], "spent": p[5]} for p in pts],
            "models": [{"model": m[0], "requests": m[1], "hits": m[2], "saved": m[3], "spent": m[4]} for m in mdl]}


@app.get("/api/metrics/series")
async def api_metrics_series(bucket: str = "hour", hours: int = 24):
    """Serie temporelle (graphe dashboard) + agregat par modele sur la meme fenetre. Buckets en heure locale."""
    return _series_snapshot(bucket, hours)


@app.get("/api/live")
async def api_live(request: Request, bucket: str = "hour", hours: int = 24):
    """Flux temps reel (SSE) des metriques IcyCache.

    Le dashboard ne se contente plus d'un polling toutes les 5 s : chaque
    nouvel evenement proxy est pousse ici en moins d'une seconde, ce qui
    rend les compteurs, le grafe 3D et le neurone reellement vivants.

    Le client se reconnecte tout seul si la connexion tombe (EventSource).
    """
    async def gen():
        last_sig = None
        last_ping = time.time()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snap = _metrics_snapshot()
                    sig = (snap["requests"], snap["hits"], snap["misses"], snap["bypass"],
                           snap["tokens_in_saved"], snap["tokens_out_saved"],
                           snap["tokens_in_spent"], snap["tokens_out_spent"])
                    now = time.time()
                    if sig != last_sig:
                        last_sig = sig
                        payload = {"ts": now, "metrics": snap}
                        try:
                            payload["series"] = _series_snapshot(bucket, hours)
                        except Exception:
                            pass
                        yield f"data: {json.dumps(payload)}\n\n"
                    elif now - last_ping > 12:
                        last_ping = now
                        yield ": keepalive\n\n"
                except Exception as exc:
                    yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                    await asyncio.sleep(2)
                    continue
                await asyncio.sleep(0.7)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-store, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/metrics/export")
async def api_metrics_export(since: str = "", limit: int = 10000):
    """Export brut des events pour le GO/NOGO (schema commun Loom). ts = UTC 'YYYY-MM-DD HH:MM:SS'."""
    limit = max(1, min(int(limit), 100000))
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    q = ("SELECT id, ts, kind, hash, model_req, model_real, stream, prompt_tokens, completion_tokens, "
         "saved_prompt, saved_completion, latency_ms, ttft_ms FROM events")
    args = []
    if since:
        q += " WHERE ts >= ?"
        args.append(since)
    q += " ORDER BY id ASC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    conn.close()
    sp = sum((r["prompt_tokens"] or 0) for r in rows if r["kind"] != "HIT")
    so = sum((r["completion_tokens"] or 0) for r in rows if r["kind"] != "HIT")
    vp = sum(r["saved_prompt"] or 0 for r in rows)
    vo = sum(r["saved_completion"] or 0 for r in rows)
    return {"schema": "icycache.metrics.export/v1",
            "generated_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            "since": since or None, "count": len(rows),
            "summary": {"tokens_in_spent": sp, "tokens_out_spent": so, "tokens_in_saved": vp,
                        "tokens_out_saved": vo,
                        "pct_saved": round(100 * (vp + vo) / (vp + vo + sp + so), 1) if (vp + vo + sp + so) else 0},
            "events": rows}

@app.post("/api/cache-clear")
async def api_clear_cache(request: Request):
    """Vide le cache via API (exige X-IcyCache-Confirm: FORCE_DELETE).

    L'ancien nom X-CacheForge-Confirm reste accepte (le dashboard n'a pas encore
    ete renomme) : les deux entetes sont traitees a l'identique.
    """
    confirm = (request.headers.get('x-icycache-confirm')
               or request.headers.get('x-cacheforge-confirm'))
    if confirm != 'FORCE_DELETE':
        raise HTTPException(status_code=403, detail="Missing header X-IcyCache-Confirm: FORCE_DELETE")
    cache.clear()
    return {"message": "Cache vidé avec succès", "success": True}

# === POINT D'ENTRÉE ===
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="IcyCache Server")
    parser.add_argument('--mode', choices=['proxy', 'mcp-stdio', 'mcp-sse', 'mcp-http', 'all'],
                       default='all', help="Mode de démarrage")
    
    args = parser.parse_args()
    
    proxy_port = CONFIG['proxy']['port']
    mcp_port = CONFIG['mcp']['port']
    
    if args.mode == 'proxy':
        logger.info(f"Starting Proxy HTTP on port {proxy_port}")
        uvicorn.run(app, host=CONFIG['proxy']['host'], port=proxy_port)
    
    elif args.mode == 'mcp-stdio':
        logger.info("Starting MCP Server (stdio)")
        mcp.run(transport='stdio')
    
    elif args.mode == 'mcp-sse':
        logger.info(f"Starting MCP Server (SSE) on port {mcp_port}")
        mcp.run(transport='sse')

    elif args.mode == 'mcp-http':
        # Streamable HTTP: transport standard des clients MCP recents
        # (Claude Code, MCP Inspector, SDK 1.10+). SSE reste disponible
        # via --mode mcp-sse pour les clients legacy.
        logger.info(f"Starting MCP Server (Streamable HTTP) on port {mcp_port} -> /mcp")
        mcp.run(transport='streamable-http')
    
    elif args.mode == 'all':
            logger.info(f"Starting IcyCache in full mode")
            logger.info(f"  Proxy HTTP: http://localhost:{proxy_port}")
            logger.info(f"  MCP SSE:    http://localhost:{mcp_port}/sse")
            logger.info(f"  MCP stdio:  Disponible via Claude Desktop")
        
            import threading
            import uvicorn
            import time
            import sys
        
            def run_proxy():
                uvicorn.run(app, host=CONFIG['proxy']['host'], port=proxy_port, log_level='warning')
        
            def run_mcp_sse():
                import os
                os.environ["MCP_SSE_PORT"] = str(mcp_port)
                mcp.run(transport='sse')
        
            def run_with_restart(target, name):
                """Wrapper qui redémarre le thread si il crash."""
                while True:
                    try:
                        logger.info(f"Starting {name}...")
                        target()
                    except SystemExit as e:
                        # uvicorn quitte via sys.exit(1) si le bind echoue (ex: WinError 10048).
                        # Relancer en boucle serait inutile: on s'arrete et on le dit.
                        logger.error(f"{name} exited (code={e.code}); port deja occupe ? pas de restart")
                        break
                    except Exception as e:
                        logger.error(f"{name} crashed: {e}, restarting in 5s...")
                        time.sleep(5)
                    else:
                        logger.info(f"{name} stopped cleanly")
                        break
        
            proxy_thread = threading.Thread(target=run_with_restart, args=(run_proxy, "Proxy HTTP"), daemon=True)
            mcp_thread = threading.Thread(target=run_with_restart, args=(run_mcp_sse, "MCP SSE"), daemon=True)
        
            proxy_thread.start()
            mcp_thread.start()
        
            # Keep main thread alive, monitor threads
            _dead_logged = set()
            try:
                while True:
                    time.sleep(10)
                    if not proxy_thread.is_alive() and "proxy" not in _dead_logged:
                        _dead_logged.add("proxy")
                        logger.warning("Proxy thread stopped (voir erreur ci-dessus), pas de restart")
                    if not mcp_thread.is_alive() and "mcp" not in _dead_logged:
                        _dead_logged.add("mcp")
                        logger.warning("MCP SSE thread stopped (voir erreur ci-dessus), pas de restart")
            except KeyboardInterrupt:
                logger.info("Shutdown requested")
                sys.exit(0)


