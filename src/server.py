#!/usr/bin/env python3
"""
CacheForge - MCP Bi-Directionnel avec Cache Sémantique/Exact
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
PROJECT_ROOT = Path(r"D:\CacheForge")
CONFIG_FILE = PROJECT_ROOT / "config" / "cacheforge.json"
DB_FILE = Path(os.environ.get("CACHEFORGE_DB") or (PROJECT_ROOT / "db" / "cache.db"))
LOG_FILE = Path(os.environ.get("CACHEFORGE_LOG") or (PROJECT_ROOT / "logs" / "app.log"))

# Chargement de la configuration
with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)
if os.environ.get("CACHEFORGE_TARGET"):  # tests isoles: upstream factice
    CONFIG['proxy']['target_provider'] = os.environ["CACHEFORGE_TARGET"]

# Configuration du logging
logging.basicConfig(
    level=getattr(logging, CONFIG['logging']['level']),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("CacheForge")

# === MOTEUR DE NORMALISATION PAR PIPELINE (RIGOUREUX & RAPIDE) ===
class NormalizationPipeline:
    """Pipeline de normalisation multi-couches pour maximiser le cache hit rate"""
    
    def __init__(self, rules_path: Path):
        with open(rules_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        # Couche 1: Aho-Corasick pour remplacement multi-patterns
        try:
            import ahocorasick
            self.automaton = ahocorasick.Automaton()
            for rule in self.config['rules']:
                pattern = rule['pattern'].lower()
                replacement = rule['replace']
                self.automaton.add_word(pattern, (pattern, replacement))
            self.automaton.make_automaton()
            print(f"✓ Aho-Corasick chargé: {len(self.config['rules'])} patterns")
        except ImportError:
            print("⚠ ahocorasick non disponible, fallback regex")
            self.automaton = None
            self.regex_compiled = []
            for rule in self.config['rules']:
                pattern = re.compile(r'\b' + re.escape(rule['pattern'].lower()) + r'\b', re.IGNORECASE)
                self.regex_compiled.append((pattern, rule['replace']))
        
        # Couche 2: Stemmers Snowball
        try:
            from nltk.stem.snowball import SnowballStemmer
            self.stemmer_fr = SnowballStemmer('french')
            self.stemmer_en = SnowballStemmer('english')
            print("✓ Stemmers NLTK chargés")
        except ImportError:
            print("⚠ nltk non disponible, stemming désactivé")
            self.stemmer_fr = None
            self.stemmer_en = None
        
        # Couche 3: NFKD normalization
        import unicodedata
        self.unicodedata = unicodedata
        
        # Regex pour nettoyage final
        self.cleanup_regex = re.compile(r'[^\w\s]', re.UNICODE)
        self.space_regex = re.compile(r'\s+')
    
    def normalize(self, text: str) -> str:
        """Pipeline complet: NFKD → Lowercase → Aho-Corasick → Strip accents → Stemming → Cleanup"""
        
        # Étape 1: NFKD normalization (normalise les caractères Unicode)
        text = self.unicodedata.normalize('NFKD', text)
        
        # Étape 2: Lowercase
        text = text.lower()
        
        # Étape 3: Aho-Corasick ou Regex pour remplacement multi-patterns
        if self.automaton:
            # Aho-Corasick: remplacement optimal
            def _isw(c):
                return c.isalnum() or c == '_' or self.unicodedata.category(c) == 'Mn'
            cands = []
            for end_idx, (pattern, replacement) in self.automaton.iter(text):
                s, e = end_idx - len(pattern) + 1, end_idx + 1
                if s > 0 and _isw(pattern[0]) and _isw(text[s - 1]):
                    continue
                if e < len(text) and _isw(pattern[-1]) and _isw(text[e]):
                    continue
                cands.append((s, e, replacement))
            # leftmost-longest, sans chevauchement
            cands.sort(key=lambda t: (t[0], -(t[1] - t[0])))
            out, pos = [], 0
            for s, e, rep in cands:
                if s < pos:
                    continue
                out.append(text[pos:s])
                out.append(rep)
                pos = e
            out.append(text[pos:])
            text = ''.join(out)
        else:
            # Fallback regex
            for pattern, replacement in self.regex_compiled:
                text = pattern.sub(replacement, text)
        
        # Étape 4: Strip accents (garde seulement les caractères de base)
        text = ''.join(
            c for c in self.unicodedata.normalize('NFD', text)
            if self.unicodedata.category(c) != 'Mn'
        )
        
        # Étape 5: Stemming (optionnel, peut être trop agressif)
        # Désactivé pour l'instant car peut casser la sémantique
        # if self.stemmer_fr:
        #     words = text.split()
        #     # Détection simple de langue
        #     if any(word in ['le', 'la', 'les', 'un', 'une', 'des'] for word in words):
        #         text = ' '.join(self.stemmer_fr.stem(w) for w in words)
        #     else:
        #         text = ' '.join(self.stemmer_en.stem(w) for w in words)
        
        # Étape 6: Cleanup final (supprime ponctuation, normalise espaces)
        text = self.cleanup_regex.sub('', text)
        text = self.space_regex.sub(' ', text).strip()
        
        return text
    
    def get_hash(self, normalized_text: str) -> str:
        """Génère le hash SHA256 du texte normalisé"""
        return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()


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
            raise RuntimeError("Base de données non initialisée. Exécuter 2-FIX_CacheForge.ps1")
    
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
normalizer = NormalizationPipeline(PROJECT_ROOT / 'config' / 'normalization_rules.json')
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
    "CacheForge",
    host=CONFIG['mcp'].get('host', '127.0.0.1'),
    port=CONFIG['mcp'].get('port', 8081),
)

@mcp.tool()
def get_cache_stats() -> Dict[str, Any]:
    """Retourne les statistiques du cache"""
    return cache.get_stats()

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

# === PROXY HTTP ===
app = FastAPI(title="CacheForge Proxy", version="1.0.0")

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
    logger.info(f"Normalized prompt: {normalized[:100]}...")
    
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
                await client.aclose()
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
        client = httpx.AsyncClient(timeout=timeout)
        try:
            response = await client.send(
                client.build_request('POST', target_url, json=fwd, headers=headers), stream=True)
        except httpx.RequestError as e:
            await client.aclose()
            logger.exception(f"UPSTREAM UNREACHABLE (stream): {type(e).__name__}: {e}")
            return JSONResponse({"error": {"message": f"upstream unreachable: {type(e).__name__}: {e}",
                                           "type": "upstream_error"}}, status_code=502)
        if response.status_code >= 400:
            err_body = await response.aread()
            ctype = response.headers.get('content-type', 'application/json')
            status = response.status_code
            await response.aclose()
            await client.aclose()
            logger.error(f"UPSTREAM {status} (stream) body={err_body.decode('utf-8', 'replace')[:2000]}")
            return Response(content=err_body, status_code=status, media_type=ctype)
        return StreamingResponse(generate_and_cache(client, response, t_fwd), media_type="text/event-stream")
    else:
        # Non-streaming
        forward_start = time.perf_counter()
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await client.post(target_url, json=body, headers=headers)
            except httpx.RequestError as e:
                logger.exception(f"UPSTREAM UNREACHABLE (non-stream): {type(e).__name__}: {e}")
                return JSONResponse({"error": {"message": f"upstream unreachable: {type(e).__name__}: {e}",
                                               "type": "upstream_error"}}, status_code=502)
            if resp.status_code >= 400:
                logger.error(f"UPSTREAM {resp.status_code} (non-stream) body={resp.text[:2000]}")
                return Response(content=resp.content, status_code=resp.status_code,
                                media_type=resp.headers.get('content-type', 'application/json'))
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
            else:
                logger.info("CACHE SKIP: Not saving non-deterministic non-stream response")
            
            return JSONResponse(resp_json)

@app.get("/health")
async def health_check():
    """Endpoint de santé"""
    return {
        'status': 'healthy',
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
        return sorted(mcp._tool_manager._tools.keys())
    except Exception:
        return ["clear_cache", "get_cache_stats", "normalize_preview", "search_cached_prompts"]


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
    return {"stats": cache.get_stats(), "target_provider": CONFIG["proxy"]["target_provider"], "prompts": prompts}

@app.get("/api/metrics")
async def api_metrics():
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

@app.get("/api/metrics/series")
async def api_metrics_series(bucket: str = "hour", hours: int = 24):
    """Série temporelle (graphe dashboard) + agrégat par modèle sur la même fenêtre. Buckets en heure locale."""
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
    return {"schema": "cacheforge.metrics.export/v1",
            "generated_at_utc": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            "since": since or None, "count": len(rows),
            "summary": {"tokens_in_spent": sp, "tokens_out_spent": so, "tokens_in_saved": vp,
                        "tokens_out_saved": vo,
                        "pct_saved": round(100 * (vp + vo) / (vp + vo + sp + so), 1) if (vp + vo + sp + so) else 0},
            "events": rows}

@app.post("/api/cache-clear")
async def api_clear_cache(request: Request):
    """Vide le cache via API (exige l'en-tete X-CacheForge-Confirm: FORCE_DELETE)"""
    if request.headers.get('x-cacheforge-confirm') != 'FORCE_DELETE':
        raise HTTPException(status_code=403, detail="Missing header X-CacheForge-Confirm: FORCE_DELETE")
    cache.clear()
    return {"message": "Cache vidé avec succès", "success": True}

# === POINT D'ENTRÉE ===
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="CacheForge Server")
    parser.add_argument('--mode', choices=['proxy', 'mcp-stdio', 'mcp-sse', 'all'], 
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
    
    elif args.mode == 'all':
            logger.info(f"Starting CacheForge in full mode")
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
            try:
                while True:
                    time.sleep(10)
                    if not proxy_thread.is_alive():
                        logger.warning("Proxy thread died, will restart via wrapper")
                    if not mcp_thread.is_alive():
                        logger.warning("MCP SSE thread died, will restart via wrapper")
            except KeyboardInterrupt:
                logger.info("Shutdown requested")
                sys.exit(0)


