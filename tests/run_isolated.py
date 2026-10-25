r"""Tests isoles: DB temporaire + faux upstream (:8191) + proxy de test (:8190). Ne touche NI la DB live NI LM Studio.
Usage: venv\Scripts\python.exe tests\run_isolated.py"""
import os, sys, subprocess, tempfile, time, sqlite3, socket

# Racine derivee du fichier: le runner doit tester LE projet courant.
# (Il pointait sur D:\CacheForge, l'ancien projet renomme: la suite validait
#  du code obsolete et, sans les noms d'env historiques, la base de prod.)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable
SP, UP = 8190, 8191


def stub_main():
    import json, asyncio
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
    import uvicorn
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        b = await req.json()
        allt = json.dumps(b.get("messages", []))
        if "BAD400" in allt:
            return JSONResponse({"error": {"message": "stub bad request"}}, status_code=400)
        fin = "length" if "TRUNC" in allt else "stop"
        tool = "CALLTOOL" in allt
        if tool:
            fin = "tool_calls"
        pt, ct = max(1, len(allt) // 4), 5
        usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
        now = int(time.time())
        if not b.get("stream"):
            await asyncio.sleep(0.05)
            msg = {"role": "assistant", "content": "stub-ok"}
            if tool:
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{\"x\":1}"}}]}
            return JSONResponse({"id": "stub", "object": "chat.completion", "created": now, "model": "stub-real",
                                 "choices": [{"index": 0, "message": msg, "finish_reason": fin}], "usage": usage})
        slow = 1.5 if "SLOWSTREAM" in allt else 0.05
        inc = bool((b.get("stream_options") or {}).get("include_usage"))

        async def gen():
            base = {"id": "stub", "object": "chat.completion.chunk", "created": now, "model": "stub-real"}

            def ev(choices, **kw):
                return "data: " + json.dumps({**base, "choices": choices, **kw}) + "\n\n"
            yield ev([{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}])
            await asyncio.sleep(slow)
            if tool:
                yield ev([{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                           "function": {"name": "f", "arguments": "{\"x\""}}]}, "finish_reason": None}])
                await asyncio.sleep(slow)
                yield ev([{"index": 0, "delta": {"tool_calls": [{"index": 0,
                           "function": {"arguments": ":1}"}}]}, "finish_reason": None}])
            else:
                yield ev([{"index": 0, "delta": {"content": "stub-"}, "finish_reason": None}])
                await asyncio.sleep(slow)
                yield ev([{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}])
            await asyncio.sleep(slow)
            yield ev([{"index": 0, "delta": {}, "finish_reason": fin}])
            if inc:
                yield ev([], usage=usage)
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    uvicorn.run(app, host="127.0.0.1", port=UP, log_level="warning")


def port_up(p):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", p)) == 0


def wait_port(p, secs):
    t = time.time()
    while time.time() - t < secs:
        if port_up(p):
            return True
        time.sleep(0.3)
    return False


def kill_tree(pr):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pr.pid)], capture_output=True)


def main():
    if port_up(SP) or port_up(UP):
        sys.exit("ports %d/%d deja utilises" % (SP, UP))
    tmp = tempfile.mkdtemp(prefix="cf_test_")
    db, log = os.path.join(tmp, "cache.db"), os.path.join(tmp, "app.log")
    src = sqlite3.connect("file:" + os.path.join(ROOT, "db", "cache.db").replace("\\", "/") + "?mode=ro", uri=True)
    ddl = [r[0] for r in src.execute("select sql from sqlite_master where type='table' and name='cache'")]
    src.close()
    assert ddl, "schema cache introuvable dans la DB live"
    dst = sqlite3.connect(db)
    for s in ddl:
        dst.execute(s)
    dst.commit()
    dst.close()
    env = dict(os.environ, CACHEFORGE_DB=db, CACHEFORGE_LOG=log,
               CACHEFORGE_TARGET="http://127.0.0.1:%d/v1/chat/completions" % UP,
               # Noms courants en priorite: garantissent l'isolation meme si
               # la compatibilite CACHEFORGE_* disparait.
               ICYCACHE_DB=db, ICYCACHE_LOG=log,
               ICYCACHE_TARGET="http://127.0.0.1:%d/v1/chat/completions" % UP,
               CF_BASE="http://127.0.0.1:%d" % SP, CF_DB=db, CF_LOG=log,
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    procs, results = [], {}
    try:
        stub = subprocess.Popen([PY, os.path.abspath(__file__), "--stub"], env=env,
                                stdout=open(os.path.join(tmp, "stub.out"), "w"), stderr=subprocess.STDOUT)
        procs.append(stub)
        code = ("import sys; sys.path.insert(0, r'%s'); import mcp_server, uvicorn; "
                "uvicorn.run(mcp_server.app, host='127.0.0.1', port=%d, log_level='warning')"
                % (os.path.join(ROOT, "src"), SP))
        srv = subprocess.Popen([PY, "-c", code], env=env, cwd=ROOT,
                               stdout=open(os.path.join(tmp, "server.out"), "w"), stderr=subprocess.STDOUT)
        procs.append(srv)
        assert wait_port(UP, 30), "stub KO (voir %s\\stub.out)" % tmp
        assert wait_port(SP, 90), "proxy de test KO (voir %s\\server.out)" % tmp
        print("isole: stub :%d proxy :%d tmp=%s" % (UP, SP, tmp), flush=True)
        for name in ("test_cacheforge.py", "test_cacheforge_ext.py"):
            print("\n===== %s =====" % name, flush=True)
            r = subprocess.run([PY, os.path.join(ROOT, "tests", name)], env=env, cwd=ROOT)
            results[name] = r.returncode
    finally:
        for p in procs:
            kill_tree(p)
        time.sleep(1)
    print("\nPORTS LIBRES apres teardown: %s" % (not port_up(SP) and not port_up(UP)))
    for k, v in results.items():
        print("EXIT %s=%s" % (k, v))
    ok = len(results) == 2 and all(v == 0 for v in results.values())
    print("ISOLATED:", "ALL PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stub":
        stub_main()
    else:
        main()
