r"""Test OFFLINE du PrefixProxy avec un faux fournisseur: aucune cle API, aucun appel reseau externe.
Lancer: D:\CacheForge\venv\Scripts\python.exe D:\CacheForge\tests\test_prefix_proxy.py"""
import os, sys, json, time, tempfile, threading
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="pp_test_")
MOCK_PORT, PROXY_PORT = 8199, 8191
os.environ["PREFIX_DB"] = os.path.join(TMP, "prefix_test.db")
os.environ["PREFIX_URL_ANTHROPIC"] = "http://127.0.0.1:%d/v1/messages" % MOCK_PORT
os.environ["PREFIX_URL_OPENAI"] = "http://127.0.0.1:%d/v1/chat/completions" % MOCK_PORT
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx, uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import prefix_proxy

mock = FastAPI()
STATE = {"last": None, "headers": None, "reject": False}


@mock.post("/v1/messages")
async def m_messages(req: Request):
    body = await req.json()
    STATE["last"], STATE["headers"] = body, dict(req.headers)
    if STATE["reject"] and "cache_control" in json.dumps(body):
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error",
                             "message": "cache_control: Extra inputs are not permitted"}}, status_code=400)
    if body.get("stream"):
        async def gen():
            yield "event: message_start\ndata: " + json.dumps({"type": "message_start", "message": {
                "model": "mock-claude", "usage": {"input_tokens": 21, "cache_creation_input_tokens": 0,
                                                  "cache_read_input_tokens": 1000, "output_tokens": 1}}}) + "\n\n"
            yield "event: message_delta\ndata: " + json.dumps({"type": "message_delta",
                  "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 15}}) + "\n\n"
            yield 'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        return StreamingResponse(gen(), media_type="text/event-stream")
    return JSONResponse({"id": "msg_1", "type": "message", "model": "mock-claude",
                         "content": [{"type": "text", "text": "ok"}],
                         "usage": {"input_tokens": 21, "cache_creation_input_tokens": 2000,
                                   "cache_read_input_tokens": 0, "output_tokens": 10}})


@mock.post("/v1/chat/completions")
async def m_chat(req: Request):
    body = await req.json()
    STATE["last"], STATE["headers"] = body, dict(req.headers)
    return JSONResponse({"id": "c1", "model": "mock-gpt", "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                         "usage": {"prompt_tokens": 1500, "completion_tokens": 5,
                                   "prompt_tokens_details": {"cached_tokens": 1024}}})


def run(app, port):
    s = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=s.run, daemon=True).start()


run(mock, MOCK_PORT)
run(prefix_proxy.app, PROXY_PORT)
base = "http://127.0.0.1:%d" % PROXY_PORT
for _ in range(60):
    try:
        httpx.get(base + "/health", timeout=1)
        httpx.get("http://127.0.0.1:%d/docs" % MOCK_PORT, timeout=1)
        break
    except Exception:
        time.sleep(0.2)

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS  " if cond else "FAIL  ") + name)


c = httpx.Client(timeout=20)
HA = {"x-api-key": "sk-test-123", "anthropic-version": "2023-06-01", "content-type": "application/json"}
HO = {"authorization": "Bearer sk-openai-test", "content-type": "application/json"}
ANT = base + "/anthropic/v1/messages"
OAI = base + "/openai/v1/chat/completions"
base_msg = {"model": "claude-x", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}

# 1. Anthropic non-stream: injection + transmission des en-tetes
r = c.post(ANT, headers=HA, json=dict(base_msg, system="sys"))
check("1 anthropic: 200 + reponse transmise", r.status_code == 200 and r.json()["content"][0]["text"] == "ok")
check("1 anthropic: cache_control racine injecte", STATE["last"].get("cache_control") == {"type": "ephemeral"})
check("1 anthropic: x-api-key transmis tel quel", STATE["headers"].get("x-api-key") == "sk-test-123")
check("1 anthropic: anthropic-version transmis", STATE["headers"].get("anthropic-version") == "2023-06-01")

# 2. cache_control explicite deja present -> pas d'injection racine
r = c.post(ANT, headers=HA, json=dict(base_msg, system=[{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]))
check("2 anthropic: pas d'injection racine si cache_control explicite", "cache_control" not in STATE["last"])

# 3. Anthropic stream: SSE transmis intact + usage lu au passage
with c.stream("POST", ANT, headers=HA, json=dict(base_msg, stream=True)) as r:
    txt = "".join(r.iter_text())
check("3 stream: SSE transmis intact", "message_start" in txt and "message_delta" in txt and "message_stop" in txt)

# 4. OpenAI: prompt_cache_key stable + Authorization transmis
r = c.post(OAI, headers=HO, json={"model": "gpt-x", "messages": [{"role": "system", "content": "long system"}, {"role": "user", "content": "hi"}]})
k1 = STATE["last"].get("prompt_cache_key", "")
check("4 openai: 200", r.status_code == 200)
check("4 openai: prompt_cache_key injecte (cf-...)", str(k1).startswith("cf-"))
check("4 openai: Authorization transmis tel quel", STATE["headers"].get("authorization") == "Bearer sk-openai-test")
r = c.post(OAI, headers=HO, json={"model": "gpt-x", "messages": [{"role": "system", "content": "long system"}, {"role": "user", "content": "autre question"}]})
check("4 openai: meme prompt systeme -> meme cle", STATE["last"].get("prompt_cache_key") == k1)
r = c.post(OAI, headers=HO, json={"model": "gpt-x", "messages": [{"role": "user", "content": "hi"}]})
check("4 openai: pas de cle sans prompt systeme", "prompt_cache_key" not in STATE["last"])

# 5. Fallback: l'amont refuse cache_control -> retry sans injection
STATE["reject"] = True
r = c.post(ANT, headers=HA, json=dict(base_msg, system="sys"))
check("5 fallback: 200 apres retry sans cache_control", r.status_code == 200 and "cache_control" not in STATE["last"])
STATE["reject"] = False

# 6. Route inconnue
r = c.post(base + "/inconnu/v1/chat/completions", headers=HO, json={})
check("6 route inconnue: 404", r.status_code == 404)

# 7. Stats
s = c.get(base + "/api/prefix-stats").json()
print(json.dumps(s["providers"], indent=2, ensure_ascii=False))
a, o = s["providers"]["anthropic"], s["providers"]["openai"]
check("7 stats anthropic: requests=4, lu=1000, ecrit=6000, total_in=7084, out=45",
      (a["requests"], a["cache_read_tokens"], a["cache_write_tokens"], a["input_tokens_total"], a["output_tokens"]) == (4, 1000, 6000, 7084, 45))
check("7 stats anthropic: equiv = 1000*0.9 - 6000*0.25 = -600", a["equiv_saved_input_tokens"] == -600.0)
check("7 stats openai: requests=3, lu=3072, total_in=4500, equiv=None",
      (o["requests"], o["cache_read_tokens"], o["input_tokens_total"], o["equiv_saved_input_tokens"]) == (3, 3072, 4500, None))

print("\nRESULTAT: %d/%d PASS" % (sum(results), len(results)))
os._exit(0 if all(results) else 1)
