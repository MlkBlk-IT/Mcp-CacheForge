r"""Patch 2026-10-22 : items 0(test isolation) 2 3 4 6 7 8 10 11. Atomique: rien n'est ecrit si une assertion echoue.
Usage: venv\Scripts\python.exe patch_20261022.py"""
import os, sys, shutil

ROOT = r"D:\CacheForge"
def P(*a): return os.path.join(ROOT, *a)

def load(path):
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    txt = raw.decode("utf-8-sig")
    crlf = "\r\n" in txt
    if crlf and txt.count("\n") == txt.count("\r\n"):
        txt = txt.replace("\r\n", "\n")
    else:
        if crlf:
            print("note: fins de ligne mixtes conservees:", os.path.basename(path))
        crlf = False  # mixte: on garde les \r en bout de ligne tels quels
    return {"path": path, "lines": txt.split("\n"), "bom": bom, "crlf": crlf}

def dump(f):
    txt = "\n".join(f["lines"])
    if f["crlf"]:
        txt = txt.replace("\n", "\r\n")
    data = txt.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if f["bom"] else data

def idx_eq(f, s):
    hits = [i for i, l in enumerate(f["lines"]) if l.rstrip() == s.rstrip()]
    assert len(hits) == 1, "%s: %d match pour %r" % (os.path.basename(f["path"]), len(hits), s.strip()[:70])
    return hits[0]

def sub_line(f, old, new):
    i = idx_eq(f, old)
    f["lines"][i:i + 1] = new.split("\n")

def span(f, start, end, new):
    a = idx_eq(f, start)
    b = next((i for i in range(a, len(f["lines"])) if f["lines"][i].rstrip() == end.rstrip()), None)
    assert b is not None, "fin de span introuvable: %r" % end.strip()[:70]
    f["lines"][a:b + 1] = new.split("\n")

# ============================ src\server.py ============================
S = load(P("src", "server.py"))

sub_line(S, r'''import hashlib''', r'''import asyncio
import hashlib
import os''')
sub_line(S, r'''from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, FileResponse''',
         r'''from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, FileResponse, Response''')
sub_line(S, r'''DB_FILE = PROJECT_ROOT / "db" / "cache.db"''',
         r'''DB_FILE = Path(os.environ.get("CACHEFORGE_DB") or (PROJECT_ROOT / "db" / "cache.db"))''')
sub_line(S, r'''LOG_FILE = PROJECT_ROOT / "logs" / "app.log"''',
         r'''LOG_FILE = Path(os.environ.get("CACHEFORGE_LOG") or (PROJECT_ROOT / "logs" / "app.log"))''')
sub_line(S, r'''    CONFIG = json.load(f)''', r'''    CONFIG = json.load(f)
if os.environ.get("CACHEFORGE_TARGET"):  # tests isoles: upstream factice
    CONFIG['proxy']['target_provider'] = os.environ["CACHEFORGE_TARGET"]''')

# --- P3: remplacement avec bordures de mots, leftmost-longest, sans chevauchement ---
span(S, r'''            matches = list(self.automaton.iter(text))''',
     r'''                text = text[:start_idx] + replacement + text[end_idx + 1:]''',
     r'''            def _isw(c):
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
            text = ''.join(out)''')

# --- item 10: TTL applique a la lecture ---
span(S, r'''            SELECT normalized_prompt, original_prompt, response, is_stream, ''',
     r'''        """, (prompt_hash,))''',
     r'''            SELECT normalized_prompt, original_prompt, response, is_stream, 
                   provider, model, created_at, access_count
            FROM cache 
            WHERE prompt_hash = ? AND created_at >= datetime('now', '-' || ? || ' seconds')
        """, (prompt_hash, CONFIG.get('cache', {}).get('ttl_seconds', 86400)))''')
# --- item 10: rotation commitee ---
sub_line(S, r'''        logger.info(f"CACHE SAVE: {prompt_hash[:8]}... (provider={provider}, model={model})")''',
         r'''        conn.commit()
        logger.info(f"CACHE SAVE: {prompt_hash[:8]}... (provider={provider}, model={model})")''')

# --- item 11: fidelite des HIT (finish_reason, tool_calls) ---
sub_line(S, r'''        saved_usage, gen_time = {}, None''', r'''        saved_usage, gen_time, fin = {}, None, "stop"''')
sub_line(S, r'''                gen_time = _s.get("gen_time_ms")''', r'''                gen_time = _s.get("gen_time_ms")
                fin = _s.get("finish_reason") or "stop"''')
span(S, r'''            if cached_stream and not requested_stream:''', r'''                cached['is_stream'] = False''',
     r'''            if cached_stream and not requested_stream:
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
                cached['is_stream'] = False''')
span(S, r'''            elif not cached_stream and requested_stream:''', r'''                cached['is_stream'] = True''',
     r'''            elif not cached_stream and requested_stream:
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
                cached['is_stream'] = True''')
sub_line(S, r'''                    "finish_reason": "stop"''', r'''                    "finish_reason": fin''')
sub_line(S, r'''                {"_v": 2, "message": message, "usage": resp_json.get("usage") or {}, "gen_time_ms": gen_time},''',
         r'''                {"_v": 2, "message": message, "usage": resp_json.get("usage") or {}, "gen_time_ms": gen_time,
                 "finish_reason": (resp_json.get('choices') or [{}])[0].get('finish_reason')},''')

# --- item 6: framing SSE replay ---
sub_line(S, r'''                        yield f"{line}\n"''', r'''                        yield f"{line}\n\n"''')

# --- items 4/6/7: stream MISS (preflight status, framing, abort visible, erreurs visibles) ---
span(S, r'''    async def generate_and_cache():''',
     r'''        return StreamingResponse(generate_and_cache(), media_type="text/event-stream")''',
     r'''    wants_usage = bool((body.get('stream_options') or {}).get('include_usage'))

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
                    provider=target_url,
                    model=body.get('model', 'unknown')
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
        return StreamingResponse(generate_and_cache(client, response, t_fwd), media_type="text/event-stream")''')

# --- item 4: non-stream, erreurs relayees + loggees ---
span(S, r'''            resp = await client.post(target_url, json=body, headers=headers)''',
     r'''            resp.raise_for_status()''',
     r'''            try:
                resp = await client.post(target_url, json=body, headers=headers)
            except httpx.RequestError as e:
                logger.exception(f"UPSTREAM UNREACHABLE (non-stream): {type(e).__name__}: {e}")
                return JSONResponse({"error": {"message": f"upstream unreachable: {type(e).__name__}: {e}",
                                               "type": "upstream_error"}}, status_code=502)
            if resp.status_code >= 400:
                logger.error(f"UPSTREAM {resp.status_code} (non-stream) body={resp.text[:2000]}")
                return Response(content=resp.content, status_code=resp.status_code,
                                media_type=resp.headers.get('content-type', 'application/json'))''')

# --- dashboard no-store ---
sub_line(S, r'''    return HTMLResponse(html_path.read_text(encoding="utf-8"))''',
         r'''    return HTMLResponse(html_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})''')

# --- item 8: export brut ---
sub_line(S, r'''@app.post("/api/cache-clear")''', r'''@app.get("/api/metrics/export")
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

@app.post("/api/cache-clear")''')

# --- item 3: cache-clear protege ---
sub_line(S, r'''async def api_clear_cache():''', r'''async def api_clear_cache(request: Request):''')
sub_line(S, r'''    """Vide le cache via API"""''', r'''    """Vide le cache via API (exige l'en-tete X-CacheForge-Confirm: FORCE_DELETE)"""
    if request.headers.get('x-cacheforge-confirm') != 'FORCE_DELETE':
        raise HTTPException(status_code=403, detail="Missing header X-CacheForge-Confirm: FORCE_DELETE")''')

# ============================ dashboard.html ============================
D = load(P("static", "dashboard.html"))
sub_line(D, r'''                const r = await fetch('/api/cache-clear', { method: 'POST' });''',
         r'''                const r = await fetch('/api/cache-clear', { method: 'POST', headers: {'X-CacheForge-Confirm': 'FORCE_DELETE'} });''')
sub_line(D, r'''                alert(d.message);''', r'''                alert(d.message || d.detail || 'Erreur');''')

# ============================ tray.py (bind loopback) ============================
T = load(P("tray.py"))
ti = [i for i, l in enumerate(T["lines"]) if 'args=(proxy_app, "0.0.0.0", 8080, "PROXY")' in l]
assert len(ti) == 1, "tray.py: bind proxy introuvable"
T["lines"][ti[0]] = T["lines"][ti[0]].replace('"0.0.0.0"', '"127.0.0.1"')

# ============================ config\cacheforge.json (host proxy) ============================
C = load(P("config", "cacheforge.json"))
ci = [i for i, l in enumerate(C["lines"]) if l.strip() == '"host": "0.0.0.0",' and C["lines"][i - 1].strip() == '"proxy": {']
assert len(ci) == 1, "config: host proxy introuvable"
C["lines"][ci[0]] = C["lines"][ci[0]].replace("0.0.0.0", "127.0.0.1")

# ============================ tests\test_cacheforge.py (isolation) ============================
X = load(P("tests", "test_cacheforge.py"))
sub_line(X, r'''BASE = "http://127.0.0.1:8080"''',
         r'''import os
BASE = os.environ.get("CF_BASE") or sys.exit("Lancer via tests\\run_isolated.py (CF_BASE manquant)")''')
sub_line(X, r'''DB = r"D:\CacheForge\db\cache.db"''', r'''DB = os.environ["CF_DB"]''')

# ============================ ecriture (backups d'abord) ============================
def backup(path, n0):
    n = n0
    while os.path.exists("%s.bak%d" % (path, n)):
        n += 1
    dst = "%s.bak%d" % (path, n)
    shutil.copy2(path, dst)
    return os.path.basename(dst)

for f, n0 in ((S, 19), (D, 2), (T, 1), (C, 1), (X, 1)):
    print("backup:", backup(f["path"], n0))
    open(f["path"], "wb").write(dump(f))
    print("ecrit:", f["path"], len(f["lines"]), "lignes")

gi = P(".gitignore")
if not os.path.exists(gi):
    open(gi, "w", encoding="utf-8", newline="\n").write(
        "venv/\ndb/\nlogs/\n__pycache__/\n*.pyc\n*.bak*\nfix_*.py\n*.pre_purge*\n")
    print("ecrit:", gi)
print("PATCH OK")
