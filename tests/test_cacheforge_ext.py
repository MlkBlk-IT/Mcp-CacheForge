r"""Tests ext (items 2,3,4,6,7,8,10,11). Lancer via tests\run_isolated.py (DB temp + faux upstream)."""
import os, sys, re, time, json, hashlib, sqlite3, unicodedata, httpx

BASE = os.environ.get("CF_BASE") or sys.exit("Lancer via tests\\run_isolated.py (CF_BASE manquant)")
DB, LOG = os.environ["CF_DB"], os.environ["CF_LOG"]
URL = BASE + "/v1/chat/completions"
RUN = str(time.time())
IGNORED = {"stream", "stream_options", "user"}
bad = 0


def check(name, ok, detail=""):
    global bad
    bad += (not ok)
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


def body(tag, **kw):
    b = {"model": "test", "temperature": 0, "max_tokens": 16,
         "messages": [{"role": "user", "content": f"ext {tag} {RUN}"}]}
    b.update(kw)
    return b


def key(b):
    return hashlib.sha256(json.dumps({k: v for k, v in b.items() if k not in IGNORED},
                                     sort_keys=True).encode("utf-8")).hexdigest()


def last_kind():
    return httpx.get(BASE + "/api/metrics", timeout=30).json()["recent"][0]["kind"]


def post(b):
    return httpx.post(URL, json=b, timeout=60)


def stream(b):
    with httpx.stream("POST", URL, json={**b, "stream": True}, timeout=60) as r:
        return r.status_code, "".join(r.iter_text())


def sse_chunks(txt):
    out = []
    for blk in txt.split("\n\n"):
        blk = blk.strip()
        if blk.startswith("data: ") and blk != "data: [DONE]":
            out.append(json.loads(blk[6:]))
    return out


def log_text():
    return open(LOG, encoding="utf-8", errors="replace").read()


def db_count(sql, *a):
    c = sqlite3.connect(DB)
    n = c.execute(sql, a).fetchone()[0]
    c.close()
    return n


def inject(b, created_at, resp):
    c = sqlite3.connect(DB)
    c.execute("INSERT OR REPLACE INTO cache (prompt_hash, normalized_prompt, original_prompt, response, is_stream, "
              "provider, model, created_at, last_accessed, access_count) VALUES (?,?,?,?,?,?,?,?,?,1)",
              (key(b), "ext", "ext", resp, 0, "test", "test", created_at, created_at))
    c.commit()
    c.close()


# --- item 4: erreurs upstream relayees + visibles ---
r = post(body("BAD400-ns"))
check("E1 non-stream: 400 upstream relaye (status+body)", r.status_code == 400 and "stub bad request" in r.text,
      f"status={r.status_code}")
st, t = stream(body("BAD400-st"))
check("E2 stream: 400 upstream relaye (status+body)", st == 400 and "stub bad request" in t, f"status={st}")
check("E3 app.log: 'UPSTREAM 400' x2 (non-stream + stream)", log_text().count("UPSTREAM 400") >= 2)

# --- item 10: TTL a la lecture + rotation commitee ---
bt = body("ttl")
inject(bt, "2020-01-01 00:00:00", json.dumps({"_v": 2, "message": {"role": "assistant", "content": "STALE"},
                                               "usage": {}, "gen_time_ms": 1}))
r = post(bt)
check("T1 entree > TTL -> MISS (pas de HIT perime)", r.status_code == 200 and "STALE" not in r.text
      and last_kind() == "MISS", f"status={r.status_code} kind={last_kind()}")
be = body("rot-expired")
inject(be, "2020-01-01 00:00:00", "x")
post(body("rot-trigger"))
n = db_count("SELECT COUNT(*) FROM cache WHERE prompt_hash=?", key(be))
check("T2 rotation TTL commitee (entree expiree supprimee)", n == 0, f"n={n}")

# --- item 11: fidelite des HIT ---
bf = body("TRUNC-fr", max_tokens=8)
post(bf)
r2 = post(bf)
check("F1 finish_reason=length conserve sur HIT non-stream",
      last_kind() == "HIT" and r2.json()["choices"][0]["finish_reason"] == "length")
st, t = stream(bf)
ch = sse_chunks(t)
check("F2 finish_reason=length sur HIT stream (JSON->SSE)", st == 200 and any(
    c.get("choices") and c["choices"][0].get("finish_reason") == "length" for c in ch))
bc = body("CALLTOOL-ns")
post(bc)
r2 = post(bc)
m2 = r2.json()["choices"][0]
tc = (m2["message"].get("tool_calls") or [{}])[0]
check("F3 tool_calls + finish_reason conserves sur HIT non-stream", last_kind() == "HIT"
      and tc.get("function", {}).get("name") == "f" and tc["function"].get("arguments") == '{"x":1}'
      and m2["finish_reason"] == "tool_calls", f"msg={m2['message']} fin={m2['finish_reason']}")
st, t = stream(bc)
ch = sse_chunks(t)
tcs = [x for c in ch for x in ((c.get("choices") or [{}])[0].get("delta", {}).get("tool_calls") or [])]
check("F4 tool_calls presents sur HIT stream (JSON->SSE)", st == 200 and len(tcs) == 1
      and tcs[0]["function"]["name"] == "f" and tcs[0]["index"] == 0, f"tcs={tcs}")
bs = body("CALLTOOL-st")
stream(bs)
r = post(bs)
mm = r.json()["choices"][0]
tc = (mm["message"].get("tool_calls") or [{}])[0]
check("F5 stream MISS -> non-stream HIT: tool_calls recomposes", last_kind() == "HIT"
      and tc.get("function", {}).get("arguments") == '{"x":1}' and tc.get("function", {}).get("name") == "f"
      and tc.get("id") == "call_1" and mm["finish_reason"] == "tool_calls" and mm["message"].get("content") is None,
      f"msg={mm['message']} fin={mm['finish_reason']}")

# --- item 6: framing SSE ---
def framed(t):
    blocks = [b for b in t.split("\n\n") if b.strip()]
    return len(blocks) >= 2 and all(b.startswith("data: ") and "\ndata: " not in b for b in blocks)

bfr = body("framing")
_, t1 = stream(bfr)
_, t2 = stream(bfr)
check("S1 SSE MISS: events separes par ligne vide", framed(t1))
check("S2 SSE HIT: events separes par ligne vide", framed(t2) and t2.count("[DONE]") == 1)

# --- item 7: client qui coupe un stream ---
ba = body("SLOWSTREAM-abort")
with httpx.stream("POST", URL, json={**ba, "stream": True}, timeout=60) as r:
    it = r.iter_lines()
    next(it)
time.sleep(4)
check("A1 stream coupe: rien en cache", db_count("SELECT COUNT(*) FROM cache WHERE prompt_hash=?", key(ba)) == 0)
lt = log_text()
check("A2 stream coupe: visible dans app.log", "STREAM ABORTED" in lt or "STREAM ERROR" in lt,
      "ABORTED" if "STREAM ABORTED" in lt else ("ERROR" if "STREAM ERROR" in lt else "absent"))

# --- item 8: export ---
r = httpx.get(BASE + "/api/metrics/export", timeout=30)
j = r.json()
check("X1 export: schema + events + summary", r.status_code == 200 and j["schema"].startswith("icycache.metrics.export")
      and j["count"] == len(j["events"]) > 0 and {"kind", "hash", "prompt_tokens", "saved_prompt", "ts"} <= set(j["events"][0])
      and "pct_saved" in j["summary"])
check("X2 export ?since filtre", httpx.get(BASE + "/api/metrics/export", params={"since": "2999-01-01 00:00:00"},
                                           timeout=30).json()["count"] == 0)

# --- item 3: dashboard no-store + cache-clear protege ---
d = httpx.get(BASE + "/dashboard", timeout=30)
check("X3 dashboard Cache-Control no-store", "no-store" in d.headers.get("cache-control", ""))
check("X4 dashboard envoie X-CacheForge-Confirm", "X-CacheForge-Confirm" in d.text)
r = httpx.post(BASE + "/api/cache-clear", timeout=30)
check("X5 cache-clear sans header -> 403 (cache intact)", r.status_code == 403 and db_count("SELECT COUNT(*) FROM cache") > 0,
      f"status={r.status_code}")
r = httpx.post(BASE + "/api/cache-clear", headers={"X-CacheForge-Confirm": "FORCE_DELETE"}, timeout=30)
check("X6 cache-clear avec header -> 200 + vide", r.status_code == 200 and db_count("SELECT COUNT(*) FROM cache") == 0,
      f"status={r.status_code}")

# --- item 2 (P3): bordures de mots (in-process, normalizer reel) ---
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src"))
import server
nz = server.normalizer


def plain(t):
    t = unicodedata.normalize("NFKD", t).lower()
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", t)).strip()

fails, tested = [], 0
for rule in nz.config["rules"]:
    p = rule["pattern"].lower()
    if len(p) >= 2 and re.fullmatch(r"[a-z0-9]+", p):
        tested += 1
        tok = "q" + p + "q"
        if nz.normalize(tok) != plain(tok):
            fails.append((p, nz.normalize(tok)))
check(f"P3a aucune regle ne mute une sous-chaine ({tested} regles testees)", not fails, str(fails[:5]))
named = {"okapi": "okapi", "justesse": "justesse", "025750": "025750", "cacheforge": "cacheforge",
         "httpx": "httpx", "c'estimation": "cestimation"}
diff = {k: nz.normalize(k) for k, v in named.items() if nz.normalize(k) != v}
check("P3b cas nommes (ok/juste/025750/cacheforge/http/c'est) intacts dans un mot plus long", not diff, str(diff))
check("P3c '025750' preserve dans une phrase", "025750" in nz.normalize("reponds juste: ok 025750"),
      nz.normalize("reponds juste: ok 025750"))

print(f"\n{'ALL PASS' if not bad else str(bad) + ' FAIL'}")
sys.exit(1 if bad else 0)
