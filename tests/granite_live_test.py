import sys, time, json, httpx
sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8080"
URL = BASE + "/v1/chat/completions"
M = "granite-4.2-8b"
MAXTOK = 100

QS = [
    ("Q1  paire A (base)",            "Explique en deux phrases ce qu'est un cache DNS."),
    ("Q2  paire A (quasi identique)", "Explique en deux phrases ce qu'est un cache DNS, s'il te plaît."),
    ("Q3  paire B (mêmes mots)",      "Quelle est la différence entre un cache DNS et un cache web ? Réponds en deux phrases."),
    ("Q4  paire B (dit autrement)",   "En deux phrases : un cache web et un cache DNS, quelle différence ?"),
    ("Q5  hors scope, mêmes mots",    "Quelle est la différence entre une cache secrète de pirates et un cache web ? Réponds en deux phrases."),
    ("C   contrôle = Q1 à l'identique", None),
]
QS[5] = (QS[5][0], QS[0][1])


def metrics():
    return httpx.get(BASE + "/api/metrics", timeout=30).json()


m0 = metrics()
print(f"MODEL={M} temperature=0 max_tokens={MAXTOK} non-stream\n")
for tag, q in QS:
    t = time.perf_counter()
    r = httpx.post(URL, json={"model": M, "temperature": 0, "max_tokens": MAXTOK,
                              "messages": [{"role": "user", "content": q}]}, timeout=300)
    wall = int((time.perf_counter() - t) * 1000)
    j = r.json()
    ev = metrics()["recent"][0]
    ch = j["choices"][0]
    print("=" * 100)
    print(f"{tag}   -> {ev['kind']}   (HTTP {r.status_code}, {wall} ms côté client)")
    print(f"INPUT  : {q}")
    print(f"OUTPUT : {ch['message'].get('content')}")
    if ev["kind"] == "HIT":
        print(f"TOKENS : spent in=0 out=0 | saved in={j.get('saved_prompt_tokens')} out={j.get('saved_completion_tokens')} "
              f"| temps original={j.get('original_gen_time_ms')} ms | hit_count={j.get('hit_count')} | model={j.get('model')}")
    else:
        u = j.get("usage", {})
        print(f"TOKENS : in={u.get('prompt_tokens')} out={u.get('completion_tokens')} | finish={ch.get('finish_reason')} "
              f"| latence={ev['latency_ms']} ms | tk/s e2e={ev['tks']} | model={j.get('model')}")

m1 = metrics()
d = lambda k: m1[k] - m0[k]
print("=" * 100)
print("DELTA DASHBOARD (ce test seulement):")
print(f" requêtes={d('requests')} hits={d('hits')} misses={d('misses')}")
print(f" tokens in spent={d('tokens_in_spent')} out spent={d('tokens_out_spent')} | in saved={d('tokens_in_saved')} out saved={d('tokens_out_saved')}")
sp = d('tokens_in_spent') + d('tokens_out_spent'); sv = d('tokens_in_saved') + d('tokens_out_saved')
print(f" % saved (ce test) = {round(100*sv/(sv+sp),1) if (sv+sp) else 0} %")
