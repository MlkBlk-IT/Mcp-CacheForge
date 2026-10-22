r"""P1 (cle de cache) + P2 (metrics) + v1 replay. Usage: venv\Scripts\python.exe tests\test_cacheforge.py [model]
Prerequis: serveur up (:8080) et LM Studio up (:1234)."""
import sys, time, json, hashlib, sqlite3, httpx

import os
BASE = os.environ.get("CF_BASE") or sys.exit("Lancer via tests\\run_isolated.py (CF_BASE manquant)")
URL = BASE + "/v1/chat/completions"
DB = os.environ["CF_DB"]
M = sys.argv[1] if len(sys.argv) > 1 else "test"
RUN = str(time.time())
IGNORED = {"stream", "stream_options", "user"}


def body(tool_out="A", tdesc="d1", max_tokens=16, nonce=RUN, **kw):
    return {"model": M, "temperature": 0, "max_tokens": max_tokens,
            "tools": [{"type": "function", "function": {"name": "f", "description": tdesc,
                       "parameters": {"type": "object", "properties": {}}}}],
            "messages": [
                {"role": "user", "content": "p1 " + nonce},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": tool_out},
                {"role": "user", "content": "reponds ok"}], **kw}


def call(b):
    """-> (status, hit, text). hit = kind du dernier event (/api/metrics), pas heuristique sur l'id."""
    if b.get("stream"):
        with httpx.stream("POST", URL, json=b, timeout=180) as r:
            t = "".join(r.iter_text())
            st = r.status_code
    else:
        r = httpx.post(URL, json=b, timeout=180)
        st, t = r.status_code, r.text
    if st != 200:
        return st, None, t
    kind = httpx.get(BASE + "/api/metrics", timeout=30).json()["recent"][0]["kind"]
    return st, kind == "HIT", t


bad = 0


def check(name, ok, detail=""):
    global bad
    bad += (not ok)
    print(f"{'PASS' if ok else 'FAIL'} {name} {detail}")


# --- P1: cle de cache ---
for name, b, exp in [
    ("A1 base MISS", body(), False),
    ("A2 base repeat HIT", body(), True),
    ("B tool_out diff -> MISS", body(tool_out="B"), False),
    ("C tool schema diff -> MISS", body(tdesc="d2"), False),
    ("D max_tokens diff -> MISS", body(max_tokens=17), False),
    ("E top_p diff -> MISS", body(top_p=0.5), False),
    ("F stream == A -> HIT", body(stream=True), True),
]:
    st, hit, _ = call(b)
    check(name, st == 200 and hit == exp, f"status={st} hit={hit} expected={exp}")

# --- P2: stream MISS puis HIT, un seul [DONE] ---
N2 = RUN + "-s"
st, hit, _ = call(body(nonce=N2, stream=True))
check("G stream MISS", st == 200 and hit is False, f"status={st} hit={hit}")
st, hit, txt = call(body(nonce=N2, stream=True))
check("H stream HIT, 1x [DONE]", st == 200 and hit is True and txt.count("[DONE]") == 1,
      f"status={st} hit={hit} done={txt.count('[DONE]')}")


# --- v1 replay (entrees injectees en DB, cle = sha256 du body filtre) ---
def inject_v1(nonce, response):
    b = body(nonce=nonce)
    key = hashlib.sha256(json.dumps({k: v for k, v in b.items() if k not in IGNORED},
                                    sort_keys=True).encode("utf-8")).hexdigest()
    c = sqlite3.connect(DB)
    c.execute("INSERT OR REPLACE INTO cache (prompt_hash, normalized_prompt, original_prompt, response, "
              "is_stream, provider, model) VALUES (?,?,?,?,?,?,?)",
              (key, "v1test", "v1test", response, 0, "test", "test"))
    c.commit(); c.close()
    return b


b = inject_v1(RUN + "-v1a", "texte nu v1")
st, hit, txt = call(b)
check("V1a texte nu -> HIT", st == 200 and hit is True and "texte nu v1" in txt, f"status={st} hit={hit}")
b = inject_v1(RUN + "-v1b", json.dumps({"role": "assistant", "content": "json nu v1"}))
st, hit, txt = call(b)
check("V1b message JSON nu -> HIT", st == 200 and hit is True and "json nu v1" in txt, f"status={st} hit={hit}")
b = inject_v1(RUN + "-v1c", "texte nu v1 stream")
b["stream"] = True
st, hit, txt = call(b)
check("V1c texte nu, requete stream -> HIT", st == 200 and hit is True and "texte nu v1 stream" in txt,
      f"status={st} hit={hit}")

# --- P2: chunk usage seulement si le client l'a demande ---
def stream_text(b):
    with httpx.stream("POST", URL, json=b, timeout=180) as r:
        return r.status_code, "".join(r.iter_text())

st, t = stream_text(body(nonce=RUN + "-u1", stream=True))
check("U1 stream sans include_usage -> pas de chunk usage", st == 200 and '"usage"' not in t, f"status={st}")
st, t = stream_text(body(nonce=RUN + "-u2", stream=True, stream_options={"include_usage": True}))
check("U2 stream avec include_usage -> chunk usage present", st == 200 and '"usage"' in t, f"status={st}")

# --- P2: /api/metrics + dashboard ---
m = httpx.get(BASE + "/api/metrics", timeout=30).json()
print("METRICS", json.dumps({k: v for k, v in m.items() if k != "recent"}))
check("M1 tokens_in_spent>0 et tokens_out_spent>0", m["tokens_in_spent"] > 0 and m["tokens_out_spent"] > 0)
check("M2 tokens_in_saved>0 et pct_saved>0", m["tokens_in_saved"] > 0 and m["pct_saved"] > 0)
check("M3 tks_avg>0", bool(m["tks_avg"]) and m["tks_avg"] > 0, f"tks_avg={m['tks_avg']}")
check("M4 events_without_usage==0 (stream inclus)", m["events_without_usage"] == 0,
      f"n={m['events_without_usage']}")
check("M5 recent: model reel != requete", any(e["model"] and e["model"] != M for e in m["recent"]),
      f"models={sorted({str(e['model']) for e in m['recent']})}")
html = httpx.get(BASE + "/dashboard", timeout=30).text
ids = ["m-in", "m-out", "m-tks", "m-pct", "m-body"]
check("D1 dashboard contient les ids m-*", all(f'id="{i}"' in html for i in ids))
check("D2 meta refresh retire", 'http-equiv="refresh"' not in html)

print(f"\n{'ALL PASS' if not bad else str(bad) + ' FAIL'}")
sys.exit(1 if bad else 0)
