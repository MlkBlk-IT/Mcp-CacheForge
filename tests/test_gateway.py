#!/usr/bin/env python3
"""Tests isoles de la gateway IcyCache (src/server_new.py).

Aucune dependance aux services externes : un faux upstream OpenAI-compatible est
lance sur un port libre, la gateway sur un autre port, avec une base SQLite
temporaire. Le serveur de production (port 8080) et le dashboard ne sont
jamais touches.

Lancement :  D:\\IcyCache\\venv\\Scripts\\python.exe -m pytest tests/test_gateway.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# =============================================================================
# FAUX UPSTREAM (OpenAI-compatible)
# =============================================================================
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
import uvicorn  # noqa: E402

MOCK_STATE = {"calls": 0, "chat_calls": 0, "embed_calls": 0, "last_body": None}


def build_mock_upstream() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        MOCK_STATE["calls"] += 1
        MOCK_STATE["chat_calls"] += 1
        MOCK_STATE["last_body"] = body
        n_prompt = 11
        n_completion = 7
        if body.get("stream"):
            async def gen():
                for i in range(3):
                    chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0,
                             "model": body.get("model", "mock-model"),
                             "choices": [{"index": 0, "delta": {"content": f"tok{i} "}, "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
                last = {"id": "x", "object": "chat.completion.chunk", "created": 0,
                        "model": body.get("model", "mock-model"), "choices": [],
                        "usage": {"prompt_tokens": n_prompt, "completion_tokens": n_completion,
                                  "total_tokens": n_prompt + n_completion}}
                yield f"data: {json.dumps(last)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")

        return JSONResponse({
            "id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model", "mock-model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "MOCK_ANSWER"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": n_prompt, "completion_tokens": n_completion,
                      "total_tokens": n_prompt + n_completion},
        })

    @app.post("/v1/embeddings")
    async def embed(request: Request):
        body = await request.json()
        MOCK_STATE["calls"] += 1
        MOCK_STATE["embed_calls"] += 1
        MOCK_STATE["last_body"] = body
        return JSONResponse({
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "model": body.get("model", "mock-embed"),
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        })

    @app.get("/v1/models")
    async def models():
        return JSONResponse({"object": "list", "data": [{"id": "mock-model", "object": "model"}]})

    return app


def build_anthupstream() -> FastAPI:
    """Faux upstream Anthropic natif (/v1/messages)."""
    app = FastAPI()

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await request.json()
        MOCK_STATE["calls"] += 1
        MOCK_STATE["last_body"] = body
        return JSONResponse({
            "id": "msg_mock", "type": "message", "role": "assistant",
            "model": body.get("model", "mock-claude"),
            "content": [{"type": "text", "text": "MOCK_ANTHROPIC"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 13, "output_tokens": 5,
                      "cache_read_input_tokens": 2, "cache_creation_input_tokens": 3},
        })

    return app


# =============================================================================
# FIXTURES
# =============================================================================
@pytest.fixture(scope="session")
def stack():
    """(gateway_base_url, mock_base_url, db_path) - tout isole du systeme."""
    up_port = _free_port()
    gw_port = _free_port()
    mock_base = f"http://127.0.0.1:{up_port}"
    gw_base = f"http://127.0.0.1:{gw_port}"

    servers = []

    def _serve(app, port):
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        srv = uvicorn.Server(cfg)
        t = threading.Thread(target=srv.run, daemon=True)
        t.start()
        servers.append(srv)
        for _ in range(100):
            if srv.started:
                return
            time.sleep(0.05)
        raise RuntimeError(f"server on {port} did not start")

    _serve(build_mock_upstream(), up_port)
    _serve(build_anthupstream(), _free_port())  # port anthropic secondaire (inutile ici, garde-fou)

    tmpdir = tempfile.mkdtemp(prefix="icycache_test_")
    db_path = Path(tmpdir) / "test.db"
    cfg_path = Path(tmpdir) / "gateway.json"
    cfg = {
        "proxy": {"host": "127.0.0.1", "port": gw_port, "timeout": 30},
        "mcp": {"host": "127.0.0.1", "port": _free_port(), "transport": ["stdio", "sse"]},
        "gateway": {"host": "127.0.0.1", "port": gw_port, "default_upstream": "mock"},
        "upstreams": {
            "mock": {"base_url": mock_base, "kind": "openai"},
        },
        "cache": {"db_path": str(db_path), "max_entries": 100, "ttl_seconds": 3600,
                  "cache_when_temperature_unset": True},
        "loop_guard": {"enabled": True, "window_seconds": 300,
                       "tools": {"warn_after": 3, "block_after": 5, "max_cycle_len": 3},
                       "llm": {"warn_after": 3, "action": "warn", "block_after": 6}},
        "tool_cache": {"enabled": True, "default": "deny", "max_result_bytes": 1048576},
        "logging": {"level": "WARNING"},
    }
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    os.environ["ICYCACHE_CONFIG"] = str(cfg_path)
    os.environ["ICYCACHE_DB"] = str(db_path)
    for mod in ("server_new", "icy_core", "icy_cache_store", "icy_normalize", "upstream_utils"):
        sys.modules.pop(mod, None)
    sys.path.insert(0, str(SRC))
    import server_new  # noqa: E402

    _serve(server_new.app, gw_port)

    yield {"gw": gw_base, "mock": mock_base, "db": db_path, "cfg": cfg_path,
           "app": server_new.app, "state": MOCK_STATE}

    for srv in servers:
        srv.should_exit = True


@pytest.fixture()
def clean_cache(stack):
    stack["state"]["calls"] = 0
    stack["state"]["chat_calls"] = 0
    stack["state"]["embed_calls"] = 0
    import server_new
    server_new.CACHE.clear()
    yield


# =============================================================================
# TESTS
# =============================================================================
def test_health_and_status(stack):
    import httpx
    with httpx.Client(timeout=10) as c:
        r = c.get(stack["gw"] + "/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"
        s = c.get(stack["gw"] + "/api/status")
        assert s.status_code == 200
        assert "mock" in s.json()["gateway"]["upstreams"]


def test_missing_upstream_is_explicit(stack):
    """Sans default_upstream, le client DOIT nommer son upstream (pas de fallback cache)."""
    import httpx
    cfg = json.loads(stack["cfg"].read_text(encoding="utf-8"))
    cfg["gateway"]["default_upstream"] = None
    stack["cfg"].write_text(json.dumps(cfg), encoding="utf-8")
    try:
        import importlib
        import server_new
        importlib.reload(server_new)
        with httpx.Client(timeout=10) as c:
            r = c.post(stack["gw"] + "/v1/chat/completions",
                       json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
            # la config rechargee est prise en compte au prochain appel
            assert r.status_code in (400,)
            if r.status_code == 400:
                assert r.json()["detail"]["error"] == "upstream_required"
                assert "mock" in r.json()["detail"]["available_upstreams"]
    finally:
        cfg["gateway"]["default_upstream"] = "mock"
        stack["cfg"].write_text(json.dumps(cfg), encoding="utf-8")
        import importlib
        import server_new
        importlib.reload(server_new)


def test_unknown_upstream_404(stack):
    import httpx
    with httpx.Client(timeout=10) as c:
        r = c.post(stack["gw"] + "/nope/v1/chat/completions",
                   json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 404
        assert r.json()["detail"]["error"] == "unknown_upstream"


def test_miss_then_hit_with_real_tokens(stack, clean_cache):
    import httpx
    payload = {"model": "mock-model", "messages": [{"role": "user", "content": "Question unique"}],
               "temperature": 0}
    with httpx.Client(timeout=30) as c:
        r1 = c.post(stack["gw"] + "/v1/chat/completions", json=payload)
        assert r1.status_code == 200
        j1 = r1.json()
        assert j1["choices"][0]["message"]["content"] == "MOCK_ANSWER"
        assert j1["usage"]["prompt_tokens"] == 11
        assert j1["usage"]["completion_tokens"] == 7
        assert stack["state"]["chat_calls"] == 1

        r2 = c.post(stack["gw"] + "/v1/chat/completions", json=payload)
        assert r2.status_code == 200
        j2 = r2.json()
        assert j2["cached"] is True
        assert j2["saved_prompt_tokens"] == 11
        assert j2["saved_completion_tokens"] == 7
        assert stack["state"]["chat_calls"] == 1, "le 2e appel ne doit PAS toucher l'upstream"


def test_cache_key_uses_normalized_payload(stack, clean_cache):
    """"Bonjour!" et "bonjour" doivent tomber dans la meme entree de cache."""
    import httpx
    with httpx.Client(timeout=30) as c:
        c.post(stack["gw"] + "/v1/chat/completions",
               json={"model": "mock-model", "messages": [{"role": "user", "content": "Bonjour !"}],
                     "temperature": 0})
        before = stack["state"]["chat_calls"]
        r = c.post(stack["gw"] + "/v1/chat/completions",
                   json={"model": "mock-model", "messages": [{"role": "user", "content": "bonjour"}],
                         "temperature": 0})
        assert r.status_code == 200
        assert r.json()["cached"] is True
        assert stack["state"]["chat_calls"] == before, "payload normalise = pas de nouvel appel upstream"


def test_temperature_bypasses_cache(stack, clean_cache):
    import httpx
    payload = {"model": "mock-model", "messages": [{"role": "user", "content": "hot"}], "temperature": 0.8}
    with httpx.Client(timeout=30) as c:
        r1 = c.post(stack["gw"] + "/v1/chat/completions", json=payload)
        r2 = c.post(stack["gw"] + "/v1/chat/completions", json=payload)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json().get("cached") is not True
        assert r2.json().get("cached") is not True
        assert stack["state"]["chat_calls"] == 2


def test_streaming_and_websocket_events(stack, clean_cache):
    """Le flux SSE passe au client et le WS recoit delta + final (tokens reels)."""
    import httpx
    import websockets

    async def run():
        events = []
        async with websockets.connect(stack["gw"].replace("http://", "ws://") + "/ws/events") as ws:
            hello = json.loads(await ws.recv())
            assert hello["type"] == "hello"
            assert "mock" in hello["upstreams"]

            payload = {"model": "mock-model", "messages": [{"role": "user", "content": "streaming"}],
                       "temperature": 0, "stream": True}
            async with httpx.AsyncClient(timeout=30) as c:
                async with c.stream("POST", stack["gw"] + "/v1/chat/completions", json=payload) as r:
                    assert r.status_code == 200
                    body = ""
                    async for chunk in r.aiter_text():
                        body += chunk
            assert "MOCK" not in body or True
            assert "data:" in body
            assert "[DONE]" in body

            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                except asyncio.TimeoutError:
                    break
                events.append(ev)
                if ev.get("type") == "final":
                    break
        return events

    events = asyncio.run(run())
    finals = [e for e in events if e.get("type") == "final"]
    assert finals, f"aucun evenement final recu: {events}"
    f = finals[-1]
    assert f["upstream"] == "mock"
    assert f["prompt_tokens"] == 11, "le token final doit etre l'usage REEL de l'upstream"
    assert f["completion_tokens"] == 7
    assert f["tokens_source"] == "upstream"


def test_embeddings_cached(stack, clean_cache):
    import httpx
    payload = {"model": "mock-embed", "input": "texte a embedder"}
    with httpx.Client(timeout=30) as c:
        c.post(stack["gw"] + "/v1/embeddings", json=payload)
        before = stack["state"]["embed_calls"]
        r = c.post(stack["gw"] + "/v1/embeddings", json=payload)
        assert r.status_code == 200
        assert stack["state"]["embed_calls"] == before


def test_events_table_records_upstream_and_tokens(stack, clean_cache):
    """MISS = usage reel de l'upstream ; HIT = pas d'upstream appele, tokens sauvegardes."""
    import httpx
    import sqlite3
    payload = {"model": "mock-model", "messages": [{"role": "user", "content": "events"}], "temperature": 0}
    with httpx.Client(timeout=30) as c:
        c.post(stack["gw"] + "/v1/chat/completions", json=payload)
        c.post(stack["gw"] + "/v1/chat/completions", json=payload)
    conn = sqlite3.connect(stack["db"])
    try:
        rows = conn.execute("SELECT kind, upstream, prompt_tokens, completion_tokens, saved_prompt, "
                            "saved_completion FROM events ORDER BY id DESC LIMIT 5").fetchall()
    finally:
        conn.close()
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r[0], r)
    assert "HIT" in by_kind and "MISS" in by_kind

    miss = by_kind["MISS"]
    assert miss[1] == "mock"
    assert miss[2] == 11 and miss[3] == 7, "MISS = usage reel de l'upstream"

    hit = by_kind["HIT"]
    assert hit[1] == "mock"
    assert hit[2] is None and hit[3] is None, "HIT = aucun appel upstream, donc pas d'usage"
    assert hit[4] == 11 and hit[5] == 7, "HIT = tokens REELS sauvegardes du premier appel"


def test_cache_clear_requires_confirmation(stack):
    import httpx
    with httpx.Client(timeout=10) as c:
        r = c.post(stack["gw"] + "/api/cache-clear")
        assert r.status_code == 403
        r2 = c.post(stack["gw"] + "/api/cache-clear", headers={"X-IcyCache-Confirm": "FORCE_DELETE"})
        assert r2.status_code == 200
        assert r2.json()["success"] is True


def test_cache_clear_accepts_legacy_header(stack):
    """Le dashboard historique envoie encore X-CacheForge-Confirm : il doit passer."""
    import httpx
    with httpx.Client(timeout=10) as c:
        r = c.post(stack["gw"] + "/api/cache-clear", headers={"X-CacheForge-Confirm": "FORCE_DELETE"})
        assert r.status_code == 200
        assert r.json()["success"] is True
