#!/usr/bin/env python3
"""Ajoute un flux SSE temps reel (/api/live) a IcyCache.

Jusqu'ici le dashboard|POLLAIT /api/metrics et /api/metrics/series toutes
les 5 s : entre deux refresh l'affichage etait fige. Ici on pousse chaque
changement d'etat (< 1 s) dans un flux SSE, et le dashboard s'y abonne.

Le code est refactore en deux helpers partages (_metrics_snapshot /
_series_snapshot) pour que /api/metrics, /api/metrics/series et /api/live
ne puissent pas diverger.
"""
import shutil
from pathlib import Path

SERVER = Path(r"D:\IcyCache\src\server.py")
BACKUP = SERVER.with_suffix(".py.bak-pre-live-sse")

src = SERVER.read_text(encoding="utf-8")
if not BACKUP.exists():
    shutil.copy2(SERVER, BACKUP)
    src = BACKUP.read_text(encoding="utf-8")

# --- 1. remplacer le bloc /api/metrics -------------------------------
start = src.index('@app.get("/api/metrics")')
end = src.index('@app.get("/api/metrics/series")')

NEW_METRICS = '''def _metrics_snapshot():
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
    return _metrics_snapshot()


'''

src = src[:start] + NEW_METRICS + src[end:]

# --- 2. remplacer le bloc /api/metrics/series ------------------------
start = src.index('@app.get("/api/metrics/series")')
end = src.index('@app.get("/api/metrics/export")')

NEW_SERIES = '''def _series_snapshot(bucket: str = "hour", hours: int = 24):
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
                        yield f"data: {json.dumps(payload)}\\n\\n"
                    elif now - last_ping > 12:
                        last_ping = now
                        yield ": keepalive\\n\\n"
                except Exception as exc:
                    yield f"event: error\\ndata: {json.dumps({'error': str(exc)})}\\n\\n"
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


'''

src = src[:start] + NEW_SERIES + src[end:]

SERVER.write_text(src, encoding="utf-8")
print("OK : /api/live ajoute, helpers partages extraits")
print(f"taille : {SERVER.stat().st_size} octets")
