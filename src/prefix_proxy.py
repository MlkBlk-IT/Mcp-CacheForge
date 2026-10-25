#!/usr/bin/env python3
"""CacheForge PrefixProxy v1 - proxy ADDITIF (port 8090) pour le cache de PREFIXE des fournisseurs cloud.
Ne modifie ni mcp_server.py ni cache.db. Ne stocke AUCUNE cle API (transmises telles quelles au fournisseur).

  Anthropic : ajoute `cache_control` au niveau BLOC (system / dernier content), jamais a la racine.
  OpenAI    : ajoute un `prompt_cache_key` stable (hash du prompt systeme) si absent.
  Mesure    : tokens lus / ecrits dans le cache du fournisseur  ->  GET /api/prefix-stats

Base URL cote client:
  Anthropic SDK : http://127.0.0.1:8090/anthropic     (POST /anthropic/v1/messages)
  OpenAI SDK    : http://127.0.0.1:8090/openai/v1     (POST /openai/v1/chat/completions)
Autres fournisseurs compatibles OpenAI (URLs NON TESTEES): /openrouter /groq /mistral /deepseek
Surcharge d'une URL amont: variable d'environnement PREFIX_URL_<NOM> (ex: PREFIX_URL_OPENAI).
"""
import os, json, time, sqlite3, hashlib, logging
from pathlib import Path
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

ROOT = Path(__file__).resolve().parent.parent
DB_FILE = Path(os.environ.get("PREFIX_DB", str(ROOT / "db" / "prefix.db")))
(ROOT / "logs").mkdir(parents=True, exist_ok=True)
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    handlers=[logging.FileHandler(ROOT / "logs" / "prefix_proxy.log", encoding="utf-8")])
log = logging.getLogger("PrefixProxy")


def _flag(name, default="1"):
    return os.environ.get(name, default) == "1"


INJECT_ANTHROPIC = _flag("PREFIX_INJECT_ANTHROPIC")        # ajoute cache_control racine
INJECT_OPENAI_KEY = _flag("PREFIX_INJECT_OPENAI_KEY")      # ajoute prompt_cache_key
METER_OPENAI_STREAM = _flag("PREFIX_METER_OPENAI_STREAM")  # ajoute stream_options.include_usage (OpenAI, stream)

UPSTREAMS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",   # non teste
    "groq": "https://api.groq.com/openai/v1/chat/completions",       # non teste
    "mistral": "https://api.mistral.ai/v1/chat/completions",         # non teste
    "deepseek": "https://api.deepseek.com/chat/completions",         # non teste
}
for _k in list(UPSTREAMS):
    UPSTREAMS[_k] = os.environ.get("PREFIX_URL_" + _k.upper(), UPSTREAMS[_k])

# Multiplicateurs de prix pour l'equivalent en tokens economises (pas des $).
# Anthropic lecture = 0.1x (verifie dans la doc officielle). Ecriture 5 min = 1.25x: NON VERIFIE (surchargeable).
# OpenAI: aucun multiplicateur configure -> pas d'equivalent calcule (tokens lus bruts seulement).
READ_MULT = {"anthropic": 0.1}
WRITE_MULT = {"anthropic": float(os.environ.get("PREFIX_ANTHROPIC_WRITE_MULT", "1.25"))}

FWD = {"authorization", "x-api-key", "anthropic-version", "anthropic-beta", "content-type",
       "openai-organization", "openai-project", "http-referer", "x-title", "user-agent"}

client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
app = FastAPI(title="CacheForge PrefixProxy")


def init_db():
    c = sqlite3.connect(DB_FILE)
    c.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT DEFAULT CURRENT_TIMESTAMP,
        provider TEXT, model TEXT, stream INTEGER, status INTEGER, injected TEXT,
        total_in INTEGER, cache_read INTEGER, cache_write INTEGER, out_tokens INTEGER,
        latency_ms INTEGER, measured INTEGER)""")
    c.commit()
    c.close()


init_db()


def _log_event(provider, model, stream, status, injected, usage, latency_ms):
    u = usage or {}
    try:
        c = sqlite3.connect(DB_FILE)
        c.execute("INSERT INTO events (provider,model,stream,status,injected,total_in,cache_read,cache_write,"
                  "out_tokens,latency_ms,measured) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (provider, model, int(bool(stream)), status, injected, u.get("total_in", 0), u.get("read", 0),
                   u.get("write", 0), u.get("out", 0), latency_ms, 1 if usage else 0))
        c.commit()
        c.close()
    except Exception:
        log.exception("event log failed")
    if usage:
        log.info("%s %s status=%s in=%s cache_read=%s cache_write=%s out=%s injected=%s %sms", provider, model,
                 status, u.get("total_in"), u.get("read"), u.get("write"), u.get("out"), injected, latency_ms)
    else:
        log.info("%s %s status=%s (usage non mesure) injected=%s %sms", provider, model, status, injected, latency_ms)


def usage_from(kind, u):
    u = u or {}
    if kind == "anthropic":
        i = u.get("input_tokens") or 0
        r = u.get("cache_read_input_tokens") or 0
        w = u.get("cache_creation_input_tokens") or 0
        return {"total_in": i + r + w, "read": r, "write": w, "out": u.get("output_tokens") or 0}
    d = u.get("prompt_tokens_details") or {}
    return {"total_in": u.get("prompt_tokens") or 0, "read": d.get("cached_tokens") or 0,
            "write": d.get("cache_write_tokens") or 0, "out": u.get("completion_tokens") or 0}


def _inject_anthropic_cache_control(body):
    """Pose `cache_control` sur les blocs stables UNIQUEMENT (system + tools), jamais sur le dernier content.

    Raison : le dernier message user peut etre compacte/reecrit par l'agent, ce qui casserait le
    cache Anthropic (qui invalide TOUT ce qui suit un changement de prefixe). Le system et les tools
    sont les prefixes les plus stables. On ne pose jamais cache_control sur un message user/assistant."""
    targets = []
    sysv = body.get("system")
    if isinstance(sysv, list):
        targets = sysv
    elif isinstance(sysv, str) and sysv.strip():
        blocks = [{"type": "text", "text": sysv}]
        body["system"] = blocks
        targets = blocks
    if targets and isinstance(targets[-1], dict) and "cache_control" not in targets[-1]:
        targets[-1]["cache_control"] = {"type": "ephemeral"}
    # Tools : posent aussi un breakpoint (prefixe stable, max 4 breakpoints Anthropic)
    for t in body.get("tools") or []:
        if isinstance(t, dict) and "cache_control" not in t:
            t["cache_control"] = {"type": "ephemeral"}
    return bool(targets)


def _extract_text(msg):
    """Extrait le texte d'un message : content str ou liste de blocs (multimodal)."""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and "text" in b)
    return ""


def mutate(kind, provider, body):
    """Active le cache de prefixe cote fournisseur. Retourne le nom du champ injecte (ou None).
    Anthropic : cache_control au niveau bloc (system/content). Tous les autres (famille
    OpenAI-compatible : OpenAI, Mistral, DeepSeek, Groq, OpenRouter, omniroute, locaux) :
    prompt_cache_key stable par session = hash(system + 1er message user + modele). Le 1er user
    reste stable sur tous les tours d'une conversation, ce qui colle les tours au meme prefix
    cache cote fournisseur (tokens caches factures ~10% au lieu de 100%)."""
    if kind == "anthropic":
        if INJECT_ANTHROPIC and _inject_anthropic_cache_control(body):
            return "cache_control"
    else:
        if INJECT_OPENAI_KEY and "prompt_cache_key" not in body:
            msgs = body.get("messages") or []
            sys_txt = next((_extract_text(m) for m in msgs if m.get("role") in ("system", "developer")), "")
            first_user = next((_extract_text(m) for m in msgs if m.get("role") == "user"), "")
            if sys_txt or first_user:
                model = body.get("model") or ""
                src = json.dumps({"s": sys_txt, "u": first_user, "m": model},
                                 sort_keys=True, ensure_ascii=False)
                digest = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
                body["prompt_cache_key"] = "ic-" + digest
                # session_id forward : si le client envoie un session_id (sticky routing OpenRouter/omniroute),
                # on le laisse passer tel quel. Les providers qui ne le connaissent pas l'ignorent (sans danger).
                return "prompt_cache_key"
    return None


class StreamMeter:
    """Lit les evenements SSE au passage (sans les modifier) pour extraire l'usage."""

    def __init__(self, kind):
        self.kind, self.buf, self.usage, self.model = kind, b"", {}, None

    def feed(self, chunk):
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if raw == b"[DONE]":
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if self.kind == "anthropic":
                t = ev.get("type")
                if t == "message_start":
                    m = ev.get("message") or {}
                    self.model = m.get("model") or self.model
                    self.usage.update({k: v for k, v in (m.get("usage") or {}).items() if v is not None})
                elif t == "message_delta":
                    self.usage.update({k: v for k, v in (ev.get("usage") or {}).items() if v is not None})
            else:
                self.model = ev.get("model") or self.model
                if ev.get("usage"):
                    self.usage = ev["usage"]


async def _send(url, headers, body):
    req = client.build_request("POST", url, json=body, headers=headers)
    return await client.send(req, stream=True)


@app.post("/{provider}/v1/{endpoint:path}")
async def proxy(provider: str, endpoint: str, request: Request):
    url = UPSTREAMS.get(provider)
    kind = "anthropic" if provider == "anthropic" else "openai"
    expected = "messages" if kind == "anthropic" else "chat/completions"
    if not url or endpoint != expected:
        return JSONResponse({"error": {"message": "route inconnue: /%s/v1/%s" % (provider, endpoint),
                                       "type": "not_found"}}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "corps JSON invalide", "type": "invalid_request"}}, status_code=400)

    is_stream = bool(body.get("stream"))
    injected = mutate(kind, provider, body)
    if provider == "openai" and is_stream and METER_OPENAI_STREAM:
        body.setdefault("stream_options", {}).setdefault("include_usage", True)
    headers = {k: v for k, v in request.headers.items() if k.lower() in FWD}
    headers["accept-encoding"] = "identity"
    model = body.get("model")
    t0 = time.time()

    def ms():
        return int((time.time() - t0) * 1000)

    try:
        resp = await _send(url, headers, body)
        if resp.status_code == 400 and injected:
            err = await resp.aread()
            ctype = resp.headers.get("content-type", "application/json")
            await resp.aclose()
            if injected.encode() in err:
                log.warning("amont refuse '%s' -> retry sans injection", injected)
                body.pop(injected, None)
                injected = None
                resp = await _send(url, headers, body)
            else:
                _log_event(provider, model, is_stream, 400, injected, None, ms())
                return Response(content=err, status_code=400, headers={"content-type": ctype})
    except httpx.HTTPError as e:
        log.error("UPSTREAM UNREACHABLE %s: %s: %s", provider, type(e).__name__, e)
        return JSONResponse({"error": {"message": "upstream unreachable: %s: %s" % (type(e).__name__, e),
                                       "type": "upstream_error"}}, status_code=502)

    ctype = resp.headers.get("content-type", "application/json")

    if is_stream and resp.status_code == 200:
        async def gen():
            meter = StreamMeter(kind)
            try:
                async for chunk in resp.aiter_raw():
                    meter.feed(chunk)
                    yield chunk
            finally:
                await resp.aclose()
                _log_event(provider, meter.model or model, True, 200, injected,
                           usage_from(kind, meter.usage) if meter.usage else None, ms())
        return StreamingResponse(gen(), status_code=200, media_type="text/event-stream")

    content = await resp.aread()
    await resp.aclose()
    usage = None
    if resp.status_code == 200:
        try:
            j = json.loads(content)
            model = j.get("model") or model
            if j.get("usage"):
                usage = usage_from(kind, j["usage"])
        except Exception:
            pass
    _log_event(provider, model, False, resp.status_code, injected, usage, ms())
    return Response(content=content, status_code=resp.status_code, headers={"content-type": ctype})


@app.get("/health")
async def health():
    return {"status": "ok", "providers": sorted(UPSTREAMS), "inject_anthropic": INJECT_ANTHROPIC,
            "inject_openai_key": INJECT_OPENAI_KEY, "db": str(DB_FILE)}


@app.get("/api/prefix-stats")
async def prefix_stats():
    c = sqlite3.connect(DB_FILE)
    rows = c.execute("""SELECT provider, COUNT(*), COALESCE(SUM(measured),0), COALESCE(SUM(cache_read>0),0),
        COALESCE(SUM(total_in),0), COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0),
        COALESCE(SUM(out_tokens),0), COALESCE(SUM(injected IS NOT NULL),0)
        FROM events WHERE status=200 GROUP BY provider""").fetchall()
    c.close()
    out = {}
    for prov, n, meas, hits, tin, rd, wr, outt, inj in rows:
        rm = READ_MULT.get(prov if prov == "anthropic" else "openai")
        wm = WRITE_MULT.get(prov if prov == "anthropic" else "openai", 1.0)
        equiv = None if rm is None else round(rd * (1 - rm) - wr * (wm - 1), 1)
        out[prov] = {"requests": n, "measured": meas, "hits": hits, "input_tokens_total": tin,
                     "cache_read_tokens": rd, "cache_write_tokens": wr, "output_tokens": outt,
                     "injected_requests": inj, "prefix_hit_pct": round(100 * rd / tin, 1) if tin else 0,
                     "equiv_saved_input_tokens": equiv}
    return {"providers": out,
            "notes": ["equiv_saved_input_tokens = lecture*(1-mult_lecture) - ecriture*(mult_ecriture-1); "
                      "negatif = le cache a coute plus qu'il n'a economise.",
                      "Anthropic: lecture 0.1x verifie; ecriture 1.25x non verifie. OpenAI: pas de multiplicateur configure.",
                      "Ce sont des TOKENS equivalents, pas des $."]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PREFIX_PORT", "8090")), log_level="warning")
