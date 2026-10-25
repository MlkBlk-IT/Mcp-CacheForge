#!/usr/bin/env python3
"""Patch idempotent 2026-10-25 : chaque remplacement exige exactement 1 occurrence."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors = []


def patch(path, pairs):
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    for tag, old, new in pairs:
        if new in s and old not in s:
            print(f"[SKIP deja applique] {path} {tag}")
            continue
        n = s.count(old)
        if n != 1:
            errors.append(f"{path} {tag}: {n} occurrence(s) de l'ancre (attendu 1)")
            continue
        s = s.replace(old, new)
        print(f"[OK] {path} {tag}")
    p.write_text(s, encoding="utf-8", newline="")


# ----------------------------------------------------------------------------- server.py
patch("src/server.py", [
    ("logger", 'from icy_normalize import cache_key, normalize_payload  # noqa: E402\n',
     'from icy_normalize import cache_key, normalize_payload  # noqa: E402\n'
     'import logging  # noqa: E402\n\n_log = logging.getLogger("IcyCache.gateway")\n'),

    ("cache.set json log",
     "                      display_prompt(body), stored, False, upstream, model, raw_hash)\n"
     "        except Exception:\n            pass\n",
     "                      display_prompt(body), stored, False, upstream, model, raw_hash)\n"
     "        except Exception:\n            _log.exception(\"CACHE.set (json) a echoue\")\n"),

    ("cache.set stream log",
     "                          display_prompt(body), stored, True, upstream, model, raw_hash)\n"
     "            except Exception:\n                pass\n",
     "                          display_prompt(body), stored, True, upstream, model, raw_hash)\n"
     "            except Exception:\n                _log.exception(\"CACHE.set (stream) a echoue\")\n"),

    ("stream state ok",
     'state = {"chunks": 0, "ttft_ms": None, "sse": [], "last_push": 0.0}',
     'state = {"chunks": 0, "ttft_ms": None, "sse": [], "last_push": 0.0, "ok": False}'),

    ("stream ok flag",
     "            if buffer:\n                meter.feed(buffer)\n        except (asyncio.CancelledError, GeneratorExit):",
     "            if buffer:\n                meter.feed(buffer)\n            state[\"ok\"] = True\n"
     "        except (asyncio.CancelledError, GeneratorExit):"),

    ("stream cache only if complete",
     '        if cacheable and (is_chat or is_embed):\n            sse_text = "".join(state["sse"])\n',
     '        _sse_all = "".join(state["sse"])\n'
     '        _complete = state["ok"] and (("[DONE]" in _sse_all) if kind == "openai"\n'
     '                                     else ("message_stop" in _sse_all))\n'
     '        if cacheable and (is_chat or is_embed) and not _complete:\n'
     '            _log.warning("flux incomplet: NON cache (req %s, upstream %s)", req_id, upstream)\n'
     '        if cacheable and (is_chat or is_embed) and _complete:\n'
     '            sse_text = _sse_all\n'),

    ("llm loop guard",
     '    is_stream = bool(body.get("stream"))\n\n    # --- D2',
     '    is_stream = bool(body.get("stream"))\n\n'
     '    # --- loop guard LLM (historique de tool calls identiques + resultats identiques) ---\n'
     '    _lg = CONFIG.get("loop_guard") or {}\n'
     '    if _lg.get("enabled", True) and isinstance(body.get("messages"), list):\n'
     '        _llm = _lg.get("llm") or {}\n'
     '        _loop = icy_core.llm_loop_check(body["messages"], warn_after=int(_llm.get("warn_after", 3)))\n'
     '        if _loop:\n'
     '            icy_core.log_agent_event("llm_loop", client=lab, detail=_loop)\n'
     '            if _llm.get("action") == "block" and _loop["reps"] >= int(_llm.get("block_after", 6)):\n'
     '                return JSONResponse({"error": {"type": "loop_detected", "loop": _loop,\n'
     '                                               "message": "Boucle d\'agent detectee (memes tool calls + memes resultats)."}},\n'
     '                                    status_code=429)\n\n'
     '    # --- D2'),

    ("ws origin",
     '    await websocket.accept()\n    queue = hub.subscribe()',
     '    origin = websocket.headers.get("origin")\n'
     '    if origin and not re.match(r"^https?://(127\\.0\\.0\\.1|localhost|\\[::1\\])(:\\d+)?$", origin):\n'
     '        await websocket.close(code=1008)\n'
     '        return\n'
     '    await websocket.accept()\n    queue = hub.subscribe()'),

    ("static routes",
     '# =============================================================================\nif __name__ == "__main__":',
     '# ==== Assets du dashboard ====================================================\n'
     '# dashboard.html reference three.min.js / icywebgl.js / logo.svg a la RACINE.\n'
     '# Liste blanche stricte (pas de directory traversal) + montage /static.\n'
     '_ROOT_ASSETS = {"three.min.js": "application/javascript",\n'
     '                "icywebgl.js": "application/javascript",\n'
     '                "logo.svg": "image/svg+xml"}\n\n\n'
     '@app.get("/{asset_name}")\n'
     'async def root_asset(asset_name: str):\n'
     '    media = _ROOT_ASSETS.get(asset_name)\n'
     '    path = PROJECT_ROOT / "static" / asset_name\n'
     '    if media is None or not path.is_file():\n'
     '        raise HTTPException(status_code=404, detail="not found")\n'
     '    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-cache"})\n\n\n'
     'from fastapi.staticfiles import StaticFiles  # noqa: E402\n'
     'app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")\n\n\n'
     '# =============================================================================\nif __name__ == "__main__":'),
])

# ----------------------------------------------------------------------------- icy_normalize.py
patch("src/icy_normalize.py", [
    ("cleanup regex garde les symboles porteurs de sens",
     'self.cleanup_regex = re.compile(r"[^\\w\\s]", re.UNICODE)',
     '# Ne supprime que la ponctuation de phrase. +-*/=<>%^&|()[]{} etc. sont conserves :\n'
     '        # "12+2" et "1+22" ne doivent PAS partager la meme cle de cache.\n'
     '        self.cleanup_regex = re.compile(r"[!?.,;:\'\\"`\\u00b4\\u201c\\u201d\\u2018\\u2019\\u00ab\\u00bb\\u00a1\\u00bf\\u2026]", re.UNICODE)'),
    ("volatile tail const",
     'CACHE_KEY_IGNORED = {"stream", "stream_options", "user", "metadata"}\n',
     'CACHE_KEY_IGNORED = {"stream", "stream_options", "user", "metadata"}\n\n'
     '# Bloc volatil injecte par Hermes en fin de message ("Retrieved skills (top-K relevant ...)").\n'
     '# Il change a chaque requete et empeche tout HIT : retire de la CLE uniquement.\n'
     '_VOLATILE_TAIL = re.compile(r"retrieved skills\\W*top\\W?k relevant to your query.*$", re.DOTALL)\n'),
    ("volatile tail apply",
     '        return self.space_regex.sub(" ", text).strip()\n\n    def _replace_rules',
     '        text = self.space_regex.sub(" ", text).strip()\n'
     '        return _VOLATILE_TAIL.sub("", text).strip()\n\n    def _replace_rules'),
])

# ----------------------------------------------------------------------------- icy_core.py
patch("src/icy_core.py", [
    ("wal + index",
     '    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_hash ON events(hash, kind)")\n',
     '    try:\n'
     '        conn.execute("PRAGMA journal_mode=WAL")\n'
     '    except sqlite3.OperationalError:\n'
     '        logger.warning("WAL non active (base verrouillee par un autre process)")\n'
     '    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_created ON cache(created_at)")\n'
     '    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_accessed ON cache(last_accessed)")\n'
     '    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")\n'
     '    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_hash ON events(hash, kind)")\n'),
])

# ----------------------------------------------------------------------------- config : serveur "time" recursif
cfg = ROOT / "config" / "icycache.json"
txt = cfg.read_text(encoding="utf-8")
new_txt, n = re.subn(r',\s*"time":\s*\{.*?\n        \}', "", txt, count=1, flags=re.DOTALL)
if n == 1:
    json.loads(new_txt)  # valide
    cfg.write_text(new_txt, encoding="utf-8", newline="")
    print("[OK] config/icycache.json: serveur 'time' (recursif) retire")
elif '"time"' in txt:
    errors.append("config: bloc 'time' present mais regex non appliquee")
else:
    print("[SKIP] config: pas de serveur 'time'")

if errors:
    print("\nERREURS:")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("\nTOUS LES PATCHS OK")
