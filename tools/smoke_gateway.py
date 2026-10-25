#!/usr/bin/env python3
"""IcyCache - test de fumee REEL de la gateway multi-upstream.

Lance la gateway sur un port isole (8090 par defaut) avec une base de test
separee, puis frappe les vrais upstreams (LM Studio local, OmniRoute local) :
  1. routage par prefixe d'URL
  2. miss puis hit (cache)
  3. tokens reels en base
  4. flux streaming + evenements WebSocket

Ne touche NI au serveur de production (8080) NI au dashboard.
Usage :  python tools/smoke_gateway.py [--port 8090] [--upstreams lmstudio,omniroute]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC))

import httpx  # noqa: E402
import websockets  # noqa: E402

OK, KO = "  OK ", " FAIL"
results = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    results.append((label, cond, detail))
    print(f"[{OK if cond else KO}] {label}" + (f"  -> {detail}" if detail else ""))
    return cond


def wait_up(url: str, timeout: float = 40) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--upstreams", default="lmstudio,omniroute")
    ap.add_argument("--model-lmstudio", default="")
    ap.add_argument("--model-omniroute", default="auto/best-coding")
    ap.add_argument("--keep-db", action="store_true")
    args = ap.parse_args()

    wanted = [u.strip() for u in args.upstreams.split(",") if u.strip()]
    base_cfg = json.loads((PROJECT_ROOT / "config" / "icycache.gateway.json").read_text(encoding="utf-8"))
    upstreams = {k: v for k, v in base_cfg["upstreams"].items() if k in wanted}
    if not upstreams:
        print("Aucun upstream connu. Dispo :", ", ".join(base_cfg["upstreams"]))
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="icycache_smoke_"))
    db_path = tmp / "smoke.db"
    cfg_path = tmp / "gateway.json"
    cfg = dict(base_cfg)
    cfg["proxy"] = {**base_cfg["proxy"], "port": args.port}
    cfg["gateway"] = {**base_cfg["gateway"], "port": args.port}
    cfg["upstreams"] = upstreams
    cfg["cache"] = {**base_cfg["cache"], "db_path": str(db_path)}
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    env = dict(os.environ)
    env["ICYCACHE_CONFIG"] = str(cfg_path)
    env["ICYCACHE_DB"] = str(db_path)
    env["PYTHONPATH"] = str(SRC)

    print(f"Gateway de test -> port {args.port} | base {db_path}")
    proc = subprocess.Popen([sys.executable, str(SRC / "server.py"), "--port", str(args.port)],
                            env=env, cwd=str(PROJECT_ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{args.port}"
    try:
        if not wait_up(base + "/health"):
            print("La gateway n'a pas demarre. Sortie :")
            proc.terminate()
            try:
                print(proc.stdout.read()[-4000:])
            except Exception:
                pass
            return 1
        check("gateway demarre et repond sur /health", True, base)

        with httpx.Client(timeout=180) as c:
            # --- 1. routage multi-upstream ---
            for name, cfg_u in upstreams.items():
                models_url = base + "/" + name + "/v1/models"
                r = c.get(models_url, timeout=20)
                check(f"routage prefixe /{name}/v1/models", r.status_code == 200,
                      f"HTTP {r.status_code}")
                if r.status_code == 200:
                    try:
                        ids = [m["id"] for m in r.json().get("data", [])][:3]
                        check(f"  modeles recus de {name}", bool(ids), ", ".join(map(str, ids)))
                    except Exception as exc:
                        check(f"  modeles recus de {name}", False, str(exc))

            # --- 2. miss + hit sur un upstream local ---
            target = "lmstudio" if "lmstudio" in upstreams else list(upstreams)[0]
            model = args.model_lmstudio
            candidates = []
            if not model:
                r = c.get(base + "/" + target + "/v1/models", timeout=20)
                ids = [m["id"] for m in r.json().get("data", [])]
                candidates = ids[:6]
            else:
                candidates = [model]
            payload = {"model": candidates[0] if candidates else "local-model",
                       "messages": [{"role": "user", "content": "Reponds: SMOKE_OK"}],
                       "temperature": 0}
            r1 = None
            tried = []
            for cand in candidates or [payload["model"]]:
                payload["model"] = cand
                resp = c.post(base + "/" + target + "/v1/chat/completions", json=payload)
                tried.append(f"{cand}:HTTP{resp.status_code}")
                if resp.status_code == 200:
                    r1 = resp
                    model = cand
                    break
                # 400 = model refuse par l'upstream (ex: modele corrompu) -> essai le suivant
            if r1 is None:
                check(f"chat completion vers {target}", False,
                      "aucun modele repond. essais: " + ", ".join(tried))
                r1 = c.post(base + "/" + target + "/v1/chat/completions", json=payload)
            dt1 = 0
            if r1.status_code == 200:
                check(f"chat completion vers {target} ({model})", True, f"HTTP 200")
                j = r1.json()
                usage = j.get("usage") or {}
                check("  tokens REELS renvoyes par l'upstream",
                      usage.get("prompt_tokens") is not None,
                      f"prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')}")
                check("  pas de faux hit", not j.get("cached"))
                t0 = time.perf_counter()
                r2 = c.post(base + "/" + target + "/v1/chat/completions", json=payload)
                dt2 = int((time.perf_counter() - t0) * 1000)
                j2 = r2.json() if r2.status_code == 200 else {}
                check("  2e appel = CACHE HIT (upstream non rappele)", bool(j2.get("cached")),
                      f"{dt2}ms (1er appel: {j.get('model')})")
                check("  tokens sauvegardes = usage reel du 1er appel",
                      j2.get("saved_prompt_tokens") == usage.get("prompt_tokens"),
                      f"saved={j2.get('saved_prompt_tokens')}")
            else:
                check(f"chat completion vers {target}", False,
                      f"HTTP {r1.status_code} body={r1.text[:200]}")

            # --- 3. upstream inconnu ---
            r = c.post(base + "/inexistant/v1/chat/completions", json=payload)
            check("upstream inconnu -> 404 explicite", r.status_code == 404, f"HTTP {r.status_code}")

        # --- 4. streaming + WebSocket ---
        events = asyncio.run(ws_probe(base, target, model))
        finals = [e for e in events if e.get("type") == "final"]
        check("WebSocket: evenement final recu", bool(finals), f"{len(events)} evenements")
        if finals:
            f = finals[-1]
            check("  tokens finaux = usage REEL", f.get("prompt_tokens") is not None,
                  f"prompt={f.get('prompt_tokens')} completion={f.get('completion_tokens')} "
                  f"source={f.get('tokens_source')} upstream={f.get('upstream')}")

        # --- 5. base : attribution upstream + tokens ---
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT kind, upstream, prompt_tokens, completion_tokens, client "
                                "FROM events ORDER BY id DESC LIMIT 10").fetchall()
        finally:
            conn.close()
        check("events: chaque requete est attribuee a un upstream",
              all(r[1] for r in rows), f"{len(rows)} lignes")
        miss_rows = [r for r in rows if r[0] == "MISS"]
        check("events: MISS = tokens reels (jamais NULL sur un appel upstream)",
              bool(miss_rows) and all(r[2] is not None for r in miss_rows),
              str(miss_rows[:2]))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 62)
    print(f"RESULTAT : {len(results) - len(failed)}/{len(results)} controles OK")
    if failed:
        for label, _, detail in failed:
            print(f"  ECHEC : {label} {detail}")
    if not args.keep_db:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 62)
    return 1 if failed else 0


async def ws_probe(base: str, upstream: str, model: str) -> list:
    events = []
    url = base.replace("http://", "ws://") + "/ws/events"
    payload = {"model": model, "messages": [{"role": "user", "content": "Compte jusqu'a 3"}],
               "temperature": 0, "stream": True}
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            hello = json.loads(await ws.recv())
            events.append(hello)
            async with httpx.AsyncClient(timeout=120) as c:
                async with c.stream("POST", f"{base}/{upstream}/v1/chat/completions", json=payload) as r:
                    async for _ in r.aiter_text():
                        pass
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                except (asyncio.TimeoutError, Exception):
                    break
                events.append(ev)
                if ev.get("type") == "final":
                    break
    except Exception as exc:
        print(f"  (WS: {exc})")
    return events


if __name__ == "__main__":
    sys.exit(main())
