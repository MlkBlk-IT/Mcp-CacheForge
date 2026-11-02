#!/usr/bin/env python3
"""
IcyCache - Gateway universel multi-upstream.

Point d'entree unique : http://127.0.0.1:8080
Tout client dont base_url pointe sur IcyCache passe par ici (Hermes, Claude Code,
scripts, IDE, SDK...). L'upstream de sortie est choisi EXPLICITEMENT :
prefixe d'URL, header X-IcyCache-Upstream, champ "upstream" du body, ou defaut de config.
Aucun defaut implicite vers OmniRoute : OmniRoute est UN upstream parmi d'autres.

Endpoints LLM (caches, tokens 100% upstream) :
  POST /v1/chat/completions     OpenAI-compatible
  POST /v1/messages             Anthropic natif
  POST /v1/embeddings           embeddings
  GET  /v1/models
  ANY  /{upstream}/v1/{rest}    routage explicite par prefixe

Observabilite :
  GET  /ws/events               WebSocket : deltas live + evenement final (tokens reels)
  GET  /health /api/status /api/metrics /api/metrics/series /api/metrics/export
  GET  /api/dashboard-stats /dashboard

Ce module NE touche PAS a static/dashboard.html ni a src/mcp_server.py : la gateway
tourne a cote, sur des ports isoles, jusqu'a unification explicite.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import socket
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from icy_paths import PROJECT_ROOT, resolve_data_path  # noqa: E402

# La config de la gateway doit etre lue par icy_core aussi (meme fichier).
_CONFIG_FILE = Path(os.environ.get("ICYCACHE_CONFIG") or (PROJECT_ROOT / "config" / "icycache.gateway.json"))
os.environ["ICYCACHE_CONFIG"] = str(_CONFIG_FILE)

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
import httpx  # noqa: E402
import uvicorn  # noqa: E402

import icy_core  # noqa: E402
from icy_core import hub, log_event, utc_iso  # noqa: E402
import upstream_utils  # noqa: E402
from upstream_utils import StreamMeter, mutate, usage_from  # noqa: E402
from icy_cache_store import CacheStore, decode_entry, encode_entry  # noqa: E402
import icy_normalize  # noqa: E402
from icy_normalize import cache_key, normalize_payload  # noqa: E402
import logging  # noqa: E402

_log = logging.getLogger("IcyCache.gateway")

CONFIG: Dict[str, Any] = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
DB_FILE = resolve_data_path(
    os.environ.get("ICYCACHE_DB") or CONFIG.get("cache", {}).get("db_path"),
    "db/cache.db",
)
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

UPSTREAMS: Dict[str, Dict[str, Any]] = CONFIG.get("upstreams", {})
GW = CONFIG.get("gateway", {})
DEFAULT_UPSTREAM: Optional[str] = GW.get("default_upstream")
PROXY_CFG = CONFIG.get("proxy", {})
TIMEOUT = float(PROXY_CFG.get("timeout", 120))
CACHE_CFG = CONFIG.get("cache", {})
CACHE_UNSET_TEMPERATURE = bool(CACHE_CFG.get("cache_when_temperature_unset", True))
WS_DELTA_INTERVAL = float(GW.get("ws_delta_interval_s", 0.25))

CACHE = CacheStore(
    DB_FILE,
    ttl_seconds=int(CACHE_CFG.get("ttl_seconds", 604800)),  # 7 jours defaut (etait 86400)
    max_entries=int(CACHE_CFG.get("max_entries", 10000)),
)

CLIENT_HEADER = "x-icycache-client"


# =============================================================================
# ROUTAGE
# =============================================================================
def _upstream_names() -> list:
    return sorted(UPSTREAMS.keys())


def resolve_upstream(request: Request, body: Optional[Dict[str, Any]] = None,
                     prefix: Optional[str] = None) -> Tuple[str, str]:
    """(nom_upstream, source) - jamais de defaut implicite si default_upstream absent."""
    if prefix:
        return _validate(prefix, "path")
    hdr = request.headers.get("x-icycache-upstream")
    if hdr:
        return _validate(hdr.strip().lower(), "header")
    if isinstance(body, dict):
        val = body.get("upstream")
        if isinstance(val, str) and val.strip():
            return _validate(val.strip().lower(), "body")
    if DEFAULT_UPSTREAM:
        return _validate(DEFAULT_UPSTREAM, "default")
    raise HTTPException(
        status_code=400,
        detail={
            "error": "upstream_required",
            "message": ("Aucun upstream indique. Precisez l'upstream par prefixe d'URL "
                        "(/omniroute/v1/chat/completions), par le header X-IcyCache-Upstream, "
                        "par le champ \"upstream\" du body, ou par gateway.default_upstream dans la config."),
            "available_upstreams": _upstream_names(),
        },
    )


def _validate(name: str, source: str) -> Tuple[str, str]:
    key = (name or "").strip().lower()
    if key not in UPSTREAMS:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_upstream", "upstream": key, "source": source,
                    "available_upstreams": _upstream_names()},
        )
    return key, source


def target_url(upstream: str, path: str) -> str:
    """Assemble base_url + chemin sans dupliquer /v1."""
    base = str(UPSTREAMS[upstream].get("base_url", "")).rstrip("/")
    if not base:
        raise HTTPException(status_code=500, detail={"error": "upstream_misconfigured", "upstream": upstream})
    if (base.endswith("/v1") or UPSTREAMS[upstream].get("strip_v1")) and path.startswith("/v1"):
        path = path[3:] or "/"
    return base + path


def upstream_kind(upstream: str) -> str:
    return str(UPSTREAMS[upstream].get("kind", "openai")).lower()


def forward_headers(request: Request, upstream: str, kind: str) -> Dict[str, str]:
    """Passe les credentials du client tels quels. Aucune cle API stockee cote IcyCache."""
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    for name in upstream_utils.FWD_HEADERS:
        val = request.headers.get(name)
        if val:
            headers[name] = val
    if "authorization" not in {k.lower() for k in headers} and "x-api-key" not in {k.lower() for k in headers}:
        env_name = UPSTREAMS[upstream].get("api_key_env")
        if env_name:
            token = os.environ.get(env_name)
            if token:
                headers["Authorization"] = f"Bearer {token}"
    if kind == "anthropic" and "anthropic-version" not in {k.lower() for k in headers}:
        headers["anthropic-version"] = request.headers.get("anthropic-version", "2023-06-01")
    for k, v in (UPSTREAMS[upstream].get("headers") or {}).items():
        headers[k] = v
    headers.pop("host", None)
    return headers


def client_label(request: Request) -> str:
    lab = request.headers.get(CLIENT_HEADER)
    if lab:
        return lab[:64]
    ua = request.headers.get("user-agent")
    if ua:
        return ua[:64]
    return "unknown"


# =============================================================================
# CACHE : CLE SUR PAYLOAD NORMALISE
# =============================================================================
def cacheable_reason(body: Dict[str, Any]) -> Tuple[bool, str]:
    """(cacheable, raison). Refus explicite plutot qu'un silence."""
    if not isinstance(body, dict):
        return False, "body invalide"
    temp = body.get("temperature", None)
    if temp is None:
        if CACHE_UNSET_TEMPERATURE:
            return True, "temperature absent (cacheable par config)"
        return False, "temperature absent (non cacheable par config)"
    try:
        if float(temp) == 0.0:
            return True, "temperature=0"
    except (TypeError, ValueError):
        return False, "temperature illisible"
    return False, f"temperature={temp} (non deterministe)"


def build_cache_key(body: Dict[str, Any], upstream: str, path: str) -> Tuple[str, str]:
    scope = {"upstream": upstream, "path": path}
    key = cache_key(body, scope)
    raw = hashlib.sha256(icy_normalize.canonical(
        {k: v for k, v in (body or {}).items() if k not in icy_normalize.CACHE_KEY_IGNORED}
    ).encode("utf-8")).hexdigest()
    return key, raw


def display_prompt(body: Dict[str, Any]) -> str:
    msgs = body.get("messages") or []
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if isinstance(body.get("system"), str):
        return body["system"]
    return ""


# =============================================================================
# CONVERSIONS SSE <-> JSON
# =============================================================================
def sse_to_message(sse_text: str) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    """(message, model, finish_reason) a partir d'un flux SSE OpenAI-compatible."""
    content_parts: list = []
    tool_calls: Dict[int, dict] = {}
    model = None
    finish = None
    for raw_line in sse_text.split("\n"):
        line = raw_line.strip()
        if not line.startswith("data:") or line.startswith("data: [DONE]"):
            continue
        try:
            chunk = json.loads(line[5:].strip())
        except Exception:
            continue
        model = chunk.get("model") or model
        for ch in (chunk.get("choices") or []):
            delta = ch.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
            for tc in (delta.get("tool_calls") or []):
                slot = tool_calls.setdefault(tc.get("index", 0), {"id": None, "type": "function",
                                                                 "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
    msg: Dict[str, Any] = {"role": "assistant", "content": "".join(content_parts) if content_parts else None}
    if tool_calls:
        msg["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return msg, model, finish


def message_to_sse(message: dict, model: Optional[str], finish: str = "stop") -> str:
    delta: Dict[str, Any] = {"role": "assistant"}
    if message.get("content"):
        delta["content"] = message["content"]
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **tc} for i, tc in enumerate(message["tool_calls"])]
    chunk = {"id": "chatcmpl-cache-" + uuid.uuid4().hex[:8], "object": "chat.completion.chunk",
             "created": int(time.time()), "model": model or "unknown",
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"


def openai_usage(u: Dict[str, Any]) -> Dict[str, Any]:
    pt = u.get("prompt_tokens")
    ct = u.get("completion_tokens")
    out = {"prompt_tokens": pt, "completion_tokens": ct}
    if pt is not None or ct is not None:
        out["total_tokens"] = (pt or 0) + (ct or 0)
    return out


def anthropic_usage(u: Dict[str, Any]) -> Dict[str, Any]:
    """Usage au format Anthropic natif (a partir de l'usage brut de l'upstream)."""
    return {
        "input_tokens": u.get("input_tokens"),
        "output_tokens": u.get("output_tokens"),
        "cache_read_input_tokens": u.get("cache_read_input_tokens") or 0,
        "cache_creation_input_tokens": u.get("cache_creation_input_tokens") or 0,
    }


def anthropic_usage_from_norm(norm: Dict[str, Any]) -> Dict[str, Any]:
    """Usage Anthropic reconstruit depuis la forme normalisee (cache hit)."""
    return {
        "input_tokens": norm.get("prompt_tokens"),
        "output_tokens": norm.get("completion_tokens"),
        "cache_read_input_tokens": norm.get("cached_tokens") or 0,
        "cache_creation_input_tokens": norm.get("cache_write_tokens") or 0,
    }


def build_openai_response(message: Optional[dict], model: Optional[str], finish: str,
                          usage: Dict[str, Any], cached: bool = False, extra: Optional[dict] = None) -> dict:
    out = {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model or "unknown",
        "choices": [{"index": 0, "message": message or {"role": "assistant", "content": ""},
                     "finish_reason": finish}],
        "usage": usage,
    }
    if extra:
        out.update(extra)
    return out


def build_anthropic_response(message: Optional[dict], model: Optional[str], stop: str,
                              usage: Dict[str, Any], extra: Optional[dict] = None) -> dict:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not content:
        content = []
    out = {
        "id": "msg_" + uuid.uuid4().hex[:16],
        "type": "message",
        "role": "assistant",
        "model": model or "unknown",
        "content": content,
        "stop_reason": stop or "end_turn",
        "stop_sequence": None,
        "usage": usage,
    }
    if extra:
        out.update(extra)
    return out


# =============================================================================
# APPLICATION
# =============================================================================
@asynccontextmanager
async def lifespan(_app: FastAPI):
    hub.bind(asyncio.get_running_loop())
    icy_core.init_db()
    yield


app = FastAPI(title="IcyCache Gateway", version="2.0.0", lifespan=lifespan)


def _cache_age(created_at: Optional[str]) -> Optional[int]:
    if not created_at:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(str(created_at).split(".")[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return int((datetime.now(timezone.utc) - dt).total_seconds())
        except Exception:
            continue
    return None


async def _read_body(request: Request) -> Optional[Dict[str, Any]]:
    raw = await request.body()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


@app.post("/v1/chat/completions")
@app.post("/v1/messages")
@app.post("/v1/embeddings")
async def llm_endpoint(request: Request, api: str = None):
    """Point d'entree LLM unifie. Le dispatch se fait sur le chemin reel."""
    path = request.url.path
    return await handle_llm(request, path)


@app.api_route("/{upstream}/v1/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def prefixed_endpoint(upstream: str, rest: str, request: Request):
    """Routage explicite par prefixe : /omniroute/v1/chat/completions."""
    return await handle_llm(request, "/v1/" + rest, prefix=upstream)


async def handle_llm(request: Request, path: str, prefix: Optional[str] = None):
    body = await _read_body(request)
    if body is None and request.method != "GET":
        raise HTTPException(status_code=400, detail={"error": "invalid_json",
                                                     "message": "Body JSON invalide ou absent."})
    body = body or {}
    upstream, source = resolve_upstream(request, body, prefix)
    kind = upstream_kind(upstream)

    if path.startswith("/v1/messages"):
        want_kind = "anthropic"
    elif path.startswith("/v1/embeddings") or path.startswith("/v1/models"):
        want_kind = "openai"
    else:
        want_kind = "openai"
    if kind != want_kind and not UPSTREAMS[upstream].get("allow_cross_kind"):
        raise HTTPException(
            status_code=400,
            detail={"error": "protocol_mismatch",
                    "message": (f"L'upstream '{upstream}' parle le protocole '{kind}' mais ce chemin "
                                f"attend '{want_kind}'. Utilisez le chemin correspondant ou un autre upstream."),
                    "available_upstreams": _upstream_names()},
        )

    if path.startswith("/v1/models") and request.method == "GET":
        return await proxy_models(request, upstream)

    return await run_llm(request, body, path, upstream, kind, source)


async def proxy_models(request: Request, upstream: str):
    """Liste des modeles de l'upstream (cache 60s)."""
    url = target_url(upstream, "/v1/models")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=forward_headers(request, upstream, upstream_kind(upstream)))
        if resp.status_code >= 400:
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type=resp.headers.get("content-type", "application/json"))
        return Response(content=resp.content, media_type="application/json",
                        headers={"X-IcyCache-Upstream": upstream})
    except httpx.RequestError as exc:
        return JSONResponse({"error": {"message": f"upstream unreachable: {exc}", "type": "upstream_error"}},
                            status_code=502)


async def run_llm(request: Request, body: Dict[str, Any], path: str, upstream: str, kind: str, source: str):
    t0 = time.perf_counter()
    req_id = uuid.uuid4().hex[:12]
    lab = client_label(request)
    is_stream = bool(body.get("stream"))

    # --- loop guard LLM (historique de tool calls identiques + resultats identiques) ---
    _lg = CONFIG.get("loop_guard") or {}
    if _lg.get("enabled", True) and isinstance(body.get("messages"), list):
        _llm = _lg.get("llm") or {}
        _loop = icy_core.llm_loop_check(body["messages"], warn_after=int(_llm.get("warn_after", 3)))
        if _loop:
            icy_core.log_agent_event("llm_loop", client=lab, detail=_loop)
            if _llm.get("action") == "block" and _loop["reps"] >= int(_llm.get("block_after", 6)):
                return JSONResponse({"error": {"type": "loop_detected", "loop": _loop,
                                               "message": "Boucle d'agent detectee (memes tool calls + memes resultats)."}},
                                    status_code=429)

    # --- D2 : injection cache_control / prompt_cache_key selon l'upstream ---
    out_body = json.loads(json.dumps(body))  # copie profonde (le body original n'est pas touche)
    out_body.pop("upstream", None)
    injected = mutate(kind, upstream, out_body)

    cacheable, cache_reason = cacheable_reason(out_body)
    ckey, raw_hash = build_cache_key(out_body, upstream, path)
    is_chat = path.startswith("/v1/chat/completions") or path.startswith("/v1/messages")
    is_embed = path.startswith("/v1/embeddings")

    # --- HIT ---
    if cacheable and (is_chat or is_embed):
        hit = CACHE.get(ckey)
        if hit:
            return serve_hit(hit, body, path, upstream, kind, ckey, raw_hash, t0, lab, req_id)

    # --- MISS ---
    url = target_url(upstream, path)
    headers = forward_headers(request, upstream, kind)
    if is_stream and kind == "openai":
        out_body["stream_options"] = {**(out_body.get("stream_options") or {}), "include_usage": True}

    if is_stream:
        return await forward_stream(req_id, url, headers, out_body, upstream, kind, ckey, raw_hash,
                                    cacheable, cache_reason, t0, lab, path, is_chat, is_embed, injected)
    return await forward_json(req_id, url, headers, out_body, upstream, kind, ckey, raw_hash,
                              cacheable, cache_reason, t0, lab, path, is_chat, is_embed, injected)


def serve_hit(hit: Dict[str, Any], body: Dict[str, Any], path: str, upstream: str, kind: str,
              ckey: str, raw_hash: str, t0: float, lab: str, req_id: str) -> Response:
    """Reponse depuis le cache : tokens sauvegardes = usage reel du premier appel."""
    entry = decode_entry(hit["response"])
    usage = entry.get("usage") or {}
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    model = entry.get("model") or hit.get("model")
    finish = entry.get("finish_reason") or "stop"
    latency_ms = int((time.perf_counter() - t0) * 1000)

    if kind == "anthropic":
        sp = usage.get("prompt_tokens")
        sc = usage.get("completion_tokens")
        log_event("HIT", ckey, body.get("model"), model, bool(body.get("stream")),
                  sp=sp, sc=sc, latency_ms=latency_ms, upstream=upstream, client=lab,
                  cached_tokens=usage.get("cached_tokens"), req_id=req_id)
        if body.get("stream"):
            return _replay_anthropic_stream(entry, model, finish, usage)
        return JSONResponse(build_anthropic_response(
            entry.get("message"), model, finish, anthropic_usage_from_norm(usage),
            extra={"cached": True, "cache_hash": ckey[:8],
                   "saved_prompt_tokens": sp, "saved_completion_tokens": sc,
                   "cache_age_seconds": _cache_age(hit.get("created_at")),
                   "hit_count": hit.get("access_count", 1),
                   "original_usage": anthropic_usage_from_norm(usage)}))

    sp = pt or 0
    sc = ct or 0
    log_event("HIT", ckey, body.get("model"), model, bool(body.get("stream")),
              sp=sp, sc=sc, latency_ms=latency_ms, upstream=upstream, client=lab,
              cached_tokens=usage.get("cached_tokens"), req_id=req_id)

    if body.get("stream"):
        sse = entry.get("sse")
        if not sse:
            sse = message_to_sse(entry.get("message") or {}, model, finish)
        return StreamingResponse(_sse_iter(sse), media_type="text/event-stream",
                                 headers={"X-IcyCache-Upstream": upstream, "X-IcyCache-Hit": "1"})

    return JSONResponse(build_openai_response(
        entry.get("message"), model, finish,
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        extra={"cached": True, "cache_hash": ckey[:8],
               "saved_prompt_tokens": sp, "saved_completion_tokens": sc,
               "saved_total_tokens": sp + sc,
               "original_gen_time_ms": entry.get("gen_time_ms"),
               "cache_age_seconds": _cache_age(hit.get("created_at")),
               "hit_count": hit.get("access_count", 1),
               "original_usage": usage}),
        headers={"X-IcyCache-Upstream": upstream, "X-IcyCache-Hit": "1"})


async def _sse_iter(text: str):
    """Rejoue un flux SSE stocke tel quel (separateurs \\n\\n preserves)."""
    if not text:
        return
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if block:
            yield block + "\n\n"
    if "[DONE]" not in text:
        yield "data: [DONE]\n\n"


def _replay_anthropic_stream(entry: Dict[str, Any], model: Optional[str], finish: str,
                              usage: Dict[str, Any]) -> Response:
    sse = entry.get("sse")
    if not sse:
        msg = entry.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else ""
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        sse = (
            "event: message_start\n"
            "data: " + json.dumps({"type": "message_start", "message": {
                "id": "msg_cache", "type": "message", "role": "assistant", "model": model,
                "content": [], "stop_reason": None,
                "usage": {"input_tokens": usage.get("prompt_tokens") or 0,
                          "output_tokens": 0}}}, ensure_ascii=False) + "\n\n"
            "event: content_block_delta\n"
            "data: " + json.dumps({"type": "content_block_delta", "index": 0,
                                   "delta": {"type": "text_delta", "text": ""}}, ensure_ascii=False) + "\n\n"
            "event: message_delta\n"
            "data: " + json.dumps({"type": "message_delta",
                                   "usage": {"output_tokens": usage.get("completion_tokens") or 0}},
                                  ensure_ascii=False) + "\n\n"
            "event: message_stop\n"
            "data: " + json.dumps({"type": "message_stop"}, ensure_ascii=False) + "\n\n"
        )
        _ = blocks
    return StreamingResponse(_sse_iter(sse), media_type="text/event-stream",
                             headers={"X-IcyCache-Upstream": "cache", "X-IcyCache-Hit": "1"})


async def forward_json(req_id: str, url: str, headers: Dict[str, str], body: Dict[str, Any],
                       upstream: str, kind: str, ckey: str, raw_hash: str, cacheable: bool,
                       cache_reason: str, t0: float, lab: str, path: str, is_chat: bool,
                       is_embed: bool, injected: Optional[str]):
    t_fwd = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(url, json=body, headers=headers)
    except httpx.RequestError as exc:
        return JSONResponse({"error": {"message": f"upstream unreachable: {exc}", "type": "upstream_error",
                                       "upstream": upstream}}, status_code=502)

    if resp.status_code >= 400:
        return Response(content=resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"),
                        headers={"X-IcyCache-Upstream": upstream})

    gen_time = int((time.perf_counter() - t_fwd) * 1000)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    try:
        data = resp.json()
    except Exception:
        data = {}

    raw_usage = data.get("usage") or {}
    norm = usage_from(kind, raw_usage)
    pt = norm.get("prompt_tokens")
    ct = norm.get("completion_tokens")
    model = data.get("model") or body.get("model")

    message = None
    finish = "stop"
    if kind == "anthropic":
        content = data.get("content")
        message = {"role": "assistant", "content": content if content is not None else ""}
        finish = data.get("stop_reason") or "end_turn"
    else:
        choices = data.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            finish = choices[0].get("finish_reason") or "stop"

    log_event("MISS" if cacheable else "BYPASS", ckey, body.get("model"), model, False, pt, ct,
              latency_ms=latency_ms, upstream=upstream, client=lab,
              cached_tokens=norm.get("cached_tokens"),
              cache_write_tokens=norm.get("cache_write_tokens"), req_id=req_id)

    if cacheable and (is_chat or is_embed):
        stored = encode_entry(kind, message, norm, model, finish, "", gen_time)
        try:
            CACHE.set(ckey, icy_normalize.get_default_normalizer().normalize(display_prompt(body)),
                      display_prompt(body), stored, False, upstream, model, raw_hash)
        except Exception:
            _log.exception("CACHE.set (json) a echoue")

    if kind == "anthropic":
        payload = build_anthropic_response(message, model, finish, anthropic_usage(raw_usage))
    else:
        payload = data if isinstance(data, dict) and data else build_openai_response(message, model, finish,
                                                                                    openai_usage(norm))
    return JSONResponse(payload, headers={"X-IcyCache-Upstream": upstream,
                                          "X-IcyCache-Tokens-Source": "upstream" if pt is not None else "n/a"})


async def forward_stream(req_id: str, url: str, headers: Dict[str, str], body: Dict[str, Any],
                         upstream: str, kind: str, ckey: str, raw_hash: str, cacheable: bool,
                         cache_reason: str, t0: float, lab: str, path: str, is_chat: bool,
                         is_embed: bool, injected: Optional[str]):
    t_fwd = time.perf_counter()
    client = httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await client.send(client.build_request("POST", url, json=body, headers=headers), stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        return JSONResponse({"error": {"message": f"upstream unreachable: {exc}", "type": "upstream_error",
                                       "upstream": upstream}}, status_code=502)

    if response.status_code >= 400:
        err = await response.aread()
        ctype = response.headers.get("content-type", "application/json")
        status = response.status_code
        await response.aclose()
        await client.aclose()
        return Response(content=err, status_code=status, media_type=ctype,
                        headers={"X-IcyCache-Upstream": upstream})

    meter = StreamMeter(kind)
    state = {"chunks": 0, "ttft_ms": None, "sse": [], "last_push": 0.0, "ok": False}

    async def gen():
        """Rejoue les bytes upstream tels quels (fidelite SSE), tout en mesurant.

        - les octets recus sont forwarding intacts (le client voit exactement
          ce que l'upstream a produit) ;
        - les lignes completes sont en parallele passees au StreamMeter pour
          extraire l'usage REEL et le modele ;
        - le compteur de chunks et le TTFT alimentent le WebSocket.
        """
        buffer = b""
        try:
            async for raw in response.aiter_bytes():
                buffer += raw
                while b"\n" in buffer:
                    line_bytes, buffer = buffer.split(b"\n", 1)
                    meter.feed(line_bytes + b"\n")
                    line = line_bytes.decode("utf-8", "replace").strip()
                    if state["ttft_ms"] is None and line.startswith("data:"):
                        try:
                            ev = json.loads(line[5:].strip())
                            delta = (ev.get("choices") or [{}])[0].get("delta") or {}
                            if delta.get("content") or ev.get("type") == "content_block_delta":
                                state["ttft_ms"] = int((time.perf_counter() - t_fwd) * 1000)
                        except Exception:
                            pass
                state["chunks"] += 1
                state["sse"].append(raw.decode("utf-8", "replace"))
                now = time.perf_counter()
                if now - state["last_push"] >= WS_DELTA_INTERVAL:
                    state["last_push"] = now
                    hub.publish({"type": "delta", "id": req_id, "ts": utc_iso(), "upstream": upstream,
                                 "client": lab, "model": meter.model or body.get("model"),
                                 "chunks": state["chunks"], "ttft_ms": state["ttft_ms"]})
                yield raw
            if buffer:
                meter.feed(buffer)
            state["ok"] = True
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception:
            pass
        finally:
            try:
                await response.aclose()
                await client.aclose()
            except Exception:
                pass

        norm = usage_from(kind, meter.usage)
        pt = norm.get("prompt_tokens")
        ct = norm.get("completion_tokens")
        latency_ms = int((time.perf_counter() - t_fwd) * 1000)
        model = meter.model or body.get("model")
        log_event("MISS" if cacheable else "BYPASS", ckey, body.get("model"), model, True, pt, ct,
                  latency_ms=latency_ms, ttft_ms=state["ttft_ms"], upstream=upstream, client=lab,
                  cached_tokens=norm.get("cached_tokens"),
                  cache_write_tokens=norm.get("cache_write_tokens"), req_id=req_id)

        _sse_all = "".join(state["sse"])
        _complete = state["ok"] and (("[DONE]" in _sse_all) if kind == "openai"
                                     else ("message_stop" in _sse_all))
        if cacheable and (is_chat or is_embed) and not _complete:
            _log.warning("flux incomplet: NON cache (req %s, upstream %s)", req_id, upstream)
        if cacheable and (is_chat or is_embed) and _complete:
            sse_text = _sse_all
            if kind == "openai":
                msg, m2, finish = sse_to_message(sse_text)
                stored = encode_entry("openai", msg, norm, m2 or model, finish or "stop", sse_text, latency_ms)
            else:
                stored = encode_entry("anthropic", None, norm, model, "end_turn", sse_text, latency_ms)
            try:
                CACHE.set(ckey, icy_normalize.get_default_normalizer().normalize(display_prompt(body)),
                          display_prompt(body), stored, True, upstream, model, raw_hash)
            except Exception:
                _log.exception("CACHE.set (stream) a echoue")

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-IcyCache-Upstream": upstream,
                                      "X-IcyCache-Cache-Reason": cache_reason,
                                      "X-IcyCache-Injected": injected or "none"})


# =============================================================================
# WEBSOCKET /ws/events
# =============================================================================
@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """Flux temps reel : 'delta' pendant le streaming, 'final' a la fin (tokens reels)."""
    origin = websocket.headers.get("origin")
    if origin and not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", origin):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = hub.subscribe()
    subs = {"upstreams": None}

    async def pump():
        try:
            while True:
                ev = await queue.get()
                if subs["upstreams"] and ev.get("upstream") not in subs["upstreams"]:
                    continue
                await websocket.send_json(ev)
        except Exception:
            return

    pump_task = asyncio.create_task(pump())
    try:
        await websocket.send_json({
            "type": "hello",
            "ts": utc_iso(),
            "protocol": "icycache.ws/v1",
            "upstreams": _upstream_names(),
            "default_upstream": DEFAULT_UPSTREAM,
            "note": "Tokens = usage reel de l'upstream. NULL = absent chez l'upstream (jamais estime).",
        })
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            if mtype == "ping":
                await websocket.send_json({"type": "pong", "ts": utc_iso()})
            elif mtype == "subscribe":
                subs["upstreams"] = set(msg.get("upstreams") or []) or None
                await websocket.send_json({"type": "subscribed", "ts": utc_iso(),
                                           "upstreams": sorted(subs["upstreams"]) if subs["upstreams"] else None})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        pump_task.cancel()
        hub.unsubscribe(queue)


# =============================================================================
# OBSERVABILITE (formes compatibles avec le dashboard existant)
# =============================================================================
def _tcp_alive(host: str, port: int, timeout: float = 0.7) -> Tuple[bool, Optional[float]]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, round((time.perf_counter() - t0) * 1000, 1)
    except OSError:
        return False, None


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "IcyCache", "project_root": str(PROJECT_ROOT),
            "cache_stats": CACHE.stats(),
            "upstreams": _upstream_names(), "default_upstream": DEFAULT_UPSTREAM}


@app.get("/api/status")
async def api_status():
    mcp_host = CONFIG.get("mcp", {}).get("host", "127.0.0.1")
    mcp_port = int(CONFIG.get("mcp", {}).get("port", 8081))
    mcp_alive, mcp_ms = _tcp_alive(mcp_host, mcp_port)
    ups = {}
    for name, cfg in UPSTREAMS.items():
        m = re.search(r"://([^:/]+)(?::(\d+))?", str(cfg.get("base_url", "")))
        if m:
            host = m.group(1)
            port = int(m.group(2) or (443 if str(cfg.get("base_url", "")).startswith("https") else 80))
            alive, ms = _tcp_alive(host, port)
            ups[name] = {"alive": alive, "latency_ms": ms, "base_url": cfg.get("base_url"),
                         "kind": cfg.get("kind", "openai")}
    return {
        "proxy": {"alive": True, "host": PROXY_CFG.get("host", "127.0.0.1"),
                  "port": PROXY_CFG.get("port", 8080)},
        "mcp": {"alive": mcp_alive, "latency_ms": mcp_ms, "host": mcp_host, "port": mcp_port,
                "transports": CONFIG.get("mcp", {}).get("transport", []), "tools": list(_MCP_TOOL_NAMES)},
        "upstream": ups,
        "gateway": {"default_upstream": DEFAULT_UPSTREAM, "upstreams": _upstream_names(),
                    "websocket": "/ws/events"},
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/dashboard-stats")
async def dashboard_stats():
    import sqlite3
    conn = sqlite3.connect(DB_FILE)
    try:
        rows = conn.execute("SELECT prompt_hash, model, normalized_prompt, access_count, "
                            "datetime(created_at, 'localtime') FROM cache ORDER BY last_accessed DESC LIMIT 50").fetchall()
    finally:
        conn.close()
    prompts = [{"hash": r[0][:8] + "...", "model": r[1],
                "normalized_prompt": (r[2] or "")[:80] + ("..." if len(r[2] or "") > 80 else ""),
                "access_count": r[3], "created_at": r[4]} for r in rows]
    # no-store: sans cet en-tete le navigateur ressert un payload perime et le
    # dashboard affiche des compteurs figes (0) alors que la base a des donnees.
    return JSONResponse({"stats": CACHE.stats(), "target_provider": DEFAULT_UPSTREAM, "prompts": prompts,
                         "upstreams": _upstream_names()},
                        headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/metrics")
async def api_metrics():
    import sqlite3
    conn = sqlite3.connect(DB_FILE)
    try:
        ok = "kind!='HIT' AND completion_tokens>0 AND latency_ms>0"
        # os = one-shot (non-agentic): NOT (stream=1 AND prompt_tokens > 10000)
        os_cond = "NOT (stream=1 AND COALESCE(prompt_tokens,0) > 10000)"
        r = conn.execute(f"""SELECT COUNT(*), COALESCE(SUM(kind='HIT'),0), COALESCE(SUM(kind='MISS'),0),
            COALESCE(SUM(kind='BYPASS'),0),
            COALESCE(SUM(CASE WHEN kind!='HIT' THEN prompt_tokens END),0),
            COALESCE(SUM(CASE WHEN kind!='HIT' THEN completion_tokens END),0),
            COALESCE(SUM(saved_prompt),0), COALESCE(SUM(saved_completion),0),
            COALESCE(SUM(CASE WHEN {ok} THEN completion_tokens END),0),
            COALESCE(SUM(CASE WHEN {ok} THEN latency_ms END),0),
            COALESCE(SUM(CASE WHEN kind!='HIT' AND prompt_tokens IS NULL THEN 1 END),0),
            COALESCE(SUM(CASE WHEN kind!='HIT' AND completion_tokens>0 AND ttft_ms>0 AND latency_ms>ttft_ms+500 THEN completion_tokens END),0),
            COALESCE(SUM(CASE WHEN kind!='HIT' AND completion_tokens>0 AND ttft_ms>0 AND latency_ms>ttft_ms+500 THEN latency_ms-ttft_ms END),0),
            COALESCE(SUM(CASE WHEN {os_cond} THEN 1 END),0),
            COALESCE(SUM(CASE WHEN kind='HIT' AND {os_cond} THEN 1 END),0),
            COALESCE(SUM(CASE WHEN kind='MISS' AND {os_cond} THEN 1 END),0),
            COALESCE(SUM(CASE WHEN kind='BYPASS' AND {os_cond} THEN 1 END),0),
            COALESCE(SUM(CASE WHEN kind='HIT' AND {os_cond} AND latency_ms>0 THEN latency_ms END),0),
            COALESCE(SUM(CASE WHEN kind='HIT' AND {os_cond} AND latency_ms>0 THEN 1 END),0),
            COALESCE(SUM(CASE WHEN kind='MISS' AND {os_cond} AND latency_ms>0 THEN latency_ms END),0),
            COALESCE(SUM(CASE WHEN kind='MISS' AND {os_cond} AND latency_ms>0 THEN 1 END),0)
            FROM events""").fetchone()
        rows = conn.execute("""SELECT datetime(ts,'localtime'), kind, model_real, prompt_tokens,
            completion_tokens, latency_ms, ttft_ms, saved_prompt+saved_completion
            FROM events ORDER BY id DESC LIMIT 50""").fetchall()
    finally:
        conn.close()
    n, hits, miss, byp, tin, tout, sin, sout, tk_c, tk_ms, no_usage, d_c, d_ms, \
        os_n, os_hits, os_miss, os_byp, os_hit_lat_sum, os_hit_lat_cnt, os_miss_lat_sum, os_miss_lat_cnt = r
    spent, saved = tin + tout, sin + sout
    recent = [{"ts": x[0], "kind": x[1], "model": x[2], "in": x[3], "out": x[4],
               "tks": round(x[4] * 1000 / x[5], 1) if x[1] != "HIT" and x[4] and x[5] else None,
               "tks_decode": round(x[4] * 1000 / (x[5] - x[6]), 1) if x[1] != "HIT" and x[4] and x[5] and x[6] and x[5] - x[6] >= 500 else None,
               "latency_ms": x[5], "ttft_ms": x[6], "saved": x[7]} for x in rows]
    os_hr = round(100 * os_hits / os_n, 1) if os_n else 0
    os_hit_lat = round(os_hit_lat_sum / os_hit_lat_cnt, 1) if os_hit_lat_cnt else None
    os_miss_lat = round(os_miss_lat_sum / os_miss_lat_cnt, 1) if os_miss_lat_cnt else None
    ag_n = n - os_n
    ag_hits = hits - os_hits
    ag_hr = round(100 * ag_hits / ag_n, 1) if ag_n else 0
    speedup = round(os_miss_lat / os_hit_lat, 1) if os_hit_lat and os_hit_lat > 0 and os_miss_lat else None
    return {"requests": n, "hits": hits, "misses": miss, "bypass": byp,
            "hit_rate_pct": round(100 * hits / n, 1) if n else 0,
            "tokens_in_spent": tin, "tokens_out_spent": tout,
            "tokens_in_saved": sin, "tokens_out_saved": sout,
            "pct_saved": round(100 * saved / (saved + spent), 1) if (saved + spent) else 0,
            "tks_avg": round(tk_c * 1000 / tk_ms, 1) if tk_ms else None,
            "tks_decode_avg": round(d_c * 1000 / d_ms, 1) if d_ms else None,
            "tks_decode_last": next((x["tks_decode"] for x in recent if x["tks_decode"]), None),
            "tks_last": next((x["tks"] for x in recent if x["tks"]), None),
            "events_without_usage": no_usage,
            "oneshot_requests": os_n, "oneshot_hits": os_hits, "oneshot_misses": os_miss,
            "oneshot_bypass": os_byp, "oneshot_hit_rate_pct": os_hr,
            "oneshot_hit_latency_ms": os_hit_lat, "oneshot_miss_latency_ms": os_miss_lat,
            "agentic_requests": ag_n, "agentic_hits": ag_hits, "agentic_hit_rate_pct": ag_hr,
            "speedup_factor": speedup, "recent": recent}


@app.get("/api/metrics/series")
async def api_metrics_series(bucket: str = "hour", hours: int = 24):
    import sqlite3
    hours = max(1, min(int(hours), 720))
    fmt = "%Y-%m-%d %H:%M" if bucket == "min" else "%Y-%m-%d %H:00"
    bucket = "min" if bucket == "min" else "hour"
    conn = sqlite3.connect(DB_FILE)
    try:
        pts = conn.execute(f"""SELECT strftime('{fmt}', ts, 'localtime') AS b,
            COALESCE(SUM(kind='HIT'),0), COALESCE(SUM(kind='MISS'),0), COALESCE(SUM(kind='BYPASS'),0),
            COALESCE(SUM(saved_prompt+saved_completion),0),
            COALESCE(SUM(CASE WHEN kind!='HIT' THEN COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0) END),0)
            FROM events WHERE ts >= datetime('now', '-' || ? || ' hours') GROUP BY b ORDER BY b""",
            (hours,)).fetchall()
        mdl = conn.execute("""SELECT COALESCE(model_real, model_req, '?'), COUNT(*),
            COALESCE(SUM(kind='HIT'),0), COALESCE(SUM(saved_prompt+saved_completion),0),
            COALESCE(SUM(CASE WHEN kind!='HIT' THEN COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0) END),0)
            FROM events WHERE ts >= datetime('now', '-' || ? || ' hours') GROUP BY 1 ORDER BY 2 DESC LIMIT 10""",
            (hours,)).fetchall()
    finally:
        conn.close()
    return {"bucket": bucket, "hours": hours,
            "points": [{"t": p[0], "hits": p[1], "misses": p[2], "bypass": p[3], "saved": p[4], "spent": p[5]} for p in pts],
            "models": [{"model": m[0], "requests": m[1], "hits": m[2], "saved": m[3], "spent": m[4]} for m in mdl]}


@app.get("/api/metrics/export")
async def api_metrics_export(since: str = "", limit: int = 10000):
    import sqlite3
    limit = max(1, min(int(limit), 100000))
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        q = ("SELECT id, ts, kind, hash, model_req, model_real, stream, prompt_tokens, completion_tokens, "
             "saved_prompt, saved_completion, latency_ms, ttft_ms, upstream, client, cached_tokens FROM events")
        args = []
        if since:
            q += " WHERE ts >= ?"
            args.append(since)
        q += " ORDER BY id ASC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    finally:
        conn.close()
    sp = sum((r["prompt_tokens"] or 0) for r in rows if r["kind"] != "HIT")
    so = sum((r["completion_tokens"] or 0) for r in rows if r["kind"] != "HIT")
    vp = sum(r["saved_prompt"] or 0 for r in rows)
    vo = sum(r["saved_completion"] or 0 for r in rows)
    return {"schema": "icycache.metrics.export/v1",
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "since": since or None, "count": len(rows),
            "summary": {"tokens_in_spent": sp, "tokens_out_spent": so, "tokens_in_saved": vp,
                        "tokens_out_saved": vo,
                        "pct_saved": round(100 * (vp + vo) / (vp + vo + sp + so), 1) if (vp + vo + sp + so) else 0},
            "events": rows}


@app.post("/api/cache-clear")
async def api_clear_cache(request: Request):
    # X-IcyCache-Confirm = naming unifie. X-CacheForge-Confirm reste accepte
    # (le dashboard n'a pas encore ete renomme).
    confirm = (request.headers.get("x-icycache-confirm")
               or request.headers.get("x-cacheforge-confirm"))
    if confirm != "FORCE_DELETE":
        raise HTTPException(status_code=403, detail="Missing header X-IcyCache-Confirm: FORCE_DELETE")
    n = CACHE.clear()
    return {"message": f"Cache vidé ({n} entrées)", "success": True, "deleted": n}


# Outils exposes par la couche MCP (liste de mcp_server.py ; sert au compteur du dashboard).
_MCP_TOOL_NAMES = ("check_tool_loop", "clear_cache", "clear_tool_cache", "get_cache_stats",
                   "get_tool_cache_stats", "list_proxy_servers", "list_proxy_tools",
                   "normalize_preview", "proxy_tool", "proxy_tools", "search_cached_prompts")


@app.get("/api/tools")
async def api_list_tools():
    """Serveurs MCP routables via le proxy d'outils (config/icycache.json -> tool_proxy)."""
    try:
        import tool_proxy as tp
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"tool_proxy indisponible: {exc}"}, status_code=503)
    names = tp.server_names()
    return {"ok": True, "servers": names, "count": len(names)}


@app.get("/api/tools/stats")
async def api_tool_cache_stats():
    """Stats du cache d'outils (declaree avant /api/tools/{server})."""
    from fastapi.responses import JSONResponse
    try:
        import tool_proxy as tp
        return tp.stats()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.get("/api/tools/{server}")
async def api_list_server_tools(server: str):
    """Outils exposes par un serveur MCP cible."""
    try:
        import tool_proxy as tp
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"tool_proxy indisponible: {exc}"}, status_code=503)
    return await tp.list_server_tools(server)


@app.get("/api/live")
async def api_live(request: Request, bucket: str = "hour", hours: int = 24):
    """Flux SSE temps reel : pousse {metrics, series} a chaque nouvel evenement (<1 s)."""
    import sqlite3

    def _sig():
        conn = sqlite3.connect(DB_FILE)
        try:
            return conn.execute("SELECT COALESCE(MAX(id),0), COUNT(*) FROM events").fetchone()
        finally:
            conn.close()

    async def gen():
        last_sig, last_ping = None, time.time()
        while True:
            if await request.is_disconnected():
                return
            try:
                sig = _sig()
                now = time.time()
                if sig != last_sig:
                    last_sig, last_ping = sig, now
                    payload = {"metrics": await api_metrics(),
                               "series": await api_metrics_series(bucket, hours)}
                    yield f"data: {json.dumps(payload)}\n\n"
                elif now - last_ping > 12:
                    last_ping = now
                    yield ": keepalive\n\n"
            except Exception as exc:  # noqa: BLE001
                _log.exception("api/live")
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
                await asyncio.sleep(2)
            await asyncio.sleep(0.7)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/api/agent-events")
async def api_agent_events(limit: int = 50, kind: str = ""):
    kinds = [k for k in (kind or "").split(",") if k] or None
    return {"events": icy_core.agent_events_recent(limit=limit, kinds=kinds)}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Sert le dashboard existant (lecture seule, aucune modification)."""
    html = PROJECT_ROOT / "static" / "dashboard.html"
    if not html.exists():
        raise HTTPException(status_code=404, detail="dashboard.html absent")
    return HTMLResponse(html.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


# ==== Anti-cache API =========================================================
# Les endpoints d'observabilite renvoyaient un corps JSON sans Cache-Control.
# Chrome pouvait donc resservir un payload perime -> dashboard fige a 0 alors
# que la base contenait des donnees. On force no-store sur toute l'API.
@app.middleware("http")
async def _no_store_api(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith(("/api/", "/health")):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# ==== Assets du dashboard ====================================================
# dashboard.html reference three.min.js / icywebgl.js / logo.svg a la RACINE.
# Liste blanche stricte (pas de directory traversal) + montage /static.
_ROOT_ASSETS = {"three.min.js": "application/javascript",
                "icywebgl.js": "application/javascript",
                "logo.svg": "image/svg+xml"}


@app.get("/v1/models")
async def models_endpoint(request: Request):
    """Liste des modeles de l'upstream (meme traitement que /<upstream>/v1/models)."""
    return await handle_llm(request, "/v1/models")


@app.get("/{asset_name}")
async def root_asset(asset_name: str):
    media = _ROOT_ASSETS.get(asset_name)
    path = PROJECT_ROOT / "static" / asset_name
    if media is None or not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})


from fastapi.staticfiles import StaticFiles  # noqa: E402
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")


# =============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IcyCache Gateway (multi-upstream)")
    parser.add_argument("--port", type=int, default=int(GW.get("port", PROXY_CFG.get("port", 8080))))
    parser.add_argument("--host", default=str(GW.get("host", PROXY_CFG.get("host", "127.0.0.1"))))
    parser.add_argument("--db", default=str(DB_FILE))
    args = parser.parse_args()

    logging_level = CONFIG.get("logging", {}).get("level", "INFO")
    import logging
    logging.basicConfig(level=getattr(logging, logging_level, logging.INFO),
                        format="%(asctime)s - IcyCache.gateway - %(levelname)s - %(message)s")
    print(f"IcyCache gateway  ->  http://{args.host}:{args.port}")
    print(f"  upstreams       :  {', '.join(_upstream_names())}")
    print(f"  default         :  {DEFAULT_UPSTREAM or '(aucun - explicite obligatoire)'}")
    print(f"  websocket       :  ws://{args.host}:{args.port}/ws/events")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
