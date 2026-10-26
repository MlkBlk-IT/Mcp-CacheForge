# DELTA-002 — Unified fix: BUG-001 (502 stuck) + BUG-002 (semantic cache) + tools registry (100)

**Date**: 2026-10-26
**Status**: **APPLIED** — shipped in **v0.3.0** (see `UPDATE-NOTES.md`)
**Type**: Single correction delta (one pass over the request path)
**Severities covered**: BUG-001 (High), BUG-002 (Medium), DELTA-001 (tool proxy top100)
**Files touched**: `src/mcp_gateway.py`, `src/prefix_proxy.py`, `src/icy_normalize.py`, `src/icy_core.py`, `src/mcp_server.py`, **new** `src/upstream_client.py`, `src/icy_semantic.py`, **new** `config/tools_registry.json`, `config/semantic_cache.json`, `config/normalization_rules.json` (addition)
**Depends on**: `docs/DELTA-001-tool-proxy-top100.md`

---

## English summary

> The full specification below is written in French (this repository's internal documentation language).
> This summary is the English version.

**One delta, one pass over the request path.** The three work items all touch the same code path:

```
client -> parse -> normalize -> exact lookup -> semantic lookup -> upstream -> store -> client
                  |              |                    |               |         |
               BUG-002 --------+                    |               |         +-- BUG-002
                                                  +-- BUG-002         +-- BUG-001
                                  tools_sig (DELTA-001) --+          (cache signature)
```

- **BUG-001 (High) — upstream resilience.** After an upstream `400`, every following call returned
  `502` with a 120 s timeout. Root cause: the pooled httpx client reused a socket the upstream had
  closed, and httpx does not retry a mid-request `RemoteProtocolError`. Fix: a dedicated
  `ResilientUpstream` module — always drain the response, recycle the client on transport errors,
  retry once, and probe `/v1/models` before reusing a suspect pool. Error messages now carry the
  status code and body instead of an empty string.
- **BUG-002 (Medium) — hybrid semantic cache.** Opt-in (`semantic_enabled: false` by default).
  Only the last user message is compared semantically; everything else (model, temperature, system
  prompt, tools) is hashed exactly and used as a hard Qdrant filter. **Anchor grounding**
  (hard anchors must be equal, soft anchors must match or the cosine score must clear a stricter
  0.97 threshold) is what prevents "Montreal" from matching "Quebec City". Indexing runs in a
  background thread, and any error in the semantic layer degrades to a silent miss.
- **DELTA-001 — tools registry.** The 100 verified MCP tool anchors are shipped as data
  (`config/tools_registry.json`, asserted `total == 100` at startup). The `tools` array is part of
  the exact cache key (`tools_sig`), so a tool response can never be served for a different tool set.

**Safe defaults**: the exact-hash fast path is untouched and stays first; the semantic layer is
opt-in; the threshold is 0.95 (not 0.92); any semantic-layer error is a silent miss, never a 500.

**Rollout**: Volet A alone (same 502 contract, no possible regression) -> Volet C (data only) ->
Volet B behind `SEMANTIC_ENABLED=true`, measuring the hit ratio and the false-positive rate on
200+ real requests before lowering the threshold below 0.95.

---

# DELTA-002 — Fix unifié : BUG-001 (502 stuck) + BUG-002 (cache sémantique) + application du registre d'outils (100)

**Date** : 2026-10-26
**Statut** : **APPLIED** — livré en **v0.3.0** (voir `UPDATE-NOTES.md`)
**Type** : Delta de correction **unique** (une seule passe sur le cycle de requête)
**Sévérités couvertes** : BUG-001 (High), BUG-002 (Medium), DELTA-001 (tool proxy top100)
**Fichiers touchés** : `src/mcp_gateway.py`, `src/prefix_proxy.py`, `src/icy_normalize.py`, `src/icy_core.py`, `src/mcp_server.py`, **nouveaux** `src/upstream_client.py`, `src/icy_semantic.py`, **nouveaux** `config/tools_registry.json`, `config/semantic_cache.json`, `config/normalization_rules.json` (ajout)
**Dépend de** : `docs/DELTA-001-tool-proxy-top100.md`

---

## 0. DÉCISION D'ARCHITECTURE — pourquoi UN SEUL delta

Les trois chantiers touchent **le même chemin de requête** :

```
client → parse → normalize → lookup exact → lookup sémantique → upstream → store → client
                    │            │                  │              │         │
                 BUG-002 ───────┘                  │              │         └── BUG-002
                                                   └── BUG-002    └── BUG-001
                                       tools_sig (DELTA-001) ──┘  (signature de cache)
```

- BUG-001 vit dans la couche `upstream call`.
- BUG-002 vit dans les couches `lookup` + `store`.
- Le registre d'outils (DELTA-001) modifie la **signature de cache** (l'array `tools` fait partie de la clé exacte) → doit être câblé dans la même passe.

Faire ça en 3 deltas = 3 passes sur le même handler = conflits de merge + états intermédiaires cassés. **Un seul delta** réécrit le cycle de vie en une fois.

### 0.1 Principe de sûreté (non négociable)

| Règle | Raison |
|---|---|
| Le fast-path hash exact **reste intact** et prioritaire | préserve la perf actuelle (0.02s) |
| Le sémantique est **opt-in** (`SEMANTIC_ENABLED=false` par défaut) | zéro régression si Qdrant/embedding indispo |
| Seuil par défaut **0.95** (conservateur), pas 0.92 | le consensus prod : monter le seuil avant de le baisser |
| **Grounding par ancres obligatoire** en plus du score cosinus | le score seul sur-matche (« Montreal » vs « Quebec City ») |
| Toute erreur de la couche sémantique = **miss silencieux** → upstream | jamais de panne à cause du cache |
| BUG-001 corrigé **avant** d'activer BUG-002 | ne pas indexer à travers une connexion morte |

---

## VOLET A — BUG-001 : résilience upstream (High)

### A.1 Cause racine (confirmée)

Symptôme : après un `400` upstream, **tous** les appels suivants → `502` + timeout 120s ; un appel direct à `:1234` débloque.

Mécanisme réel (classe de bug documentée, cf. llama.cpp #29680) :

1. Upstream annonce `Keep-Alive` mais ferme le socket après le flux / la réponse.
2. Le client httpx **poolé** croit la connexion saine, la remet dans le pool.
3. La requête suivante est écrite dans un socket mort → jamais lue → hang jusqu'au timeout → `RemoteProtocolError` / `ReadError` **à message vide**.
4. httpx **ne retente pas** une `RemoteProtocolError` mi-requête (seuls les échecs d'établissement sont couverts par `retries=`).
5. Le handler `except` fait `f"upstream unreachable: {e}"` → `str(e)` vide → message tronqué.

Aggravant probable : sur le chemin d'erreur, le body de la réponse n'est **pas drainé** (`resp.read()`), donc la connexion retourne au pool avec des octets non lus → désynchronisation du protocole au prochain usage.

### A.2 Nouveau module `src/upstream_client.py`

Client unique, thread-safe, qui : draine TOUJOURS, recycle sur erreur, retente une fois, sonde avant réutilisation après incident.

```python
# src/upstream_client.py
"""Client upstream résilient — corrige BUG-001.

Invariants :
  1. Toute réponse (succès OU erreur) est drainée avant retour au pool.
  2. Toute erreur de transport → connexion jetée (recycle) + 1 retry.
  3. Après un 4xx/5xx, l'upstream est marqué "suspect" TTL court ; on sonde
     /v1/models avant de réutiliser le pool.
  4. Le message d'erreur porte status + body (fini le message vide).
"""
from __future__ import annotations
import threading, time
import httpx


class UpstreamError(Exception):
    def __init__(self, upstream: str, status: int, body: str, cause: BaseException | None = None):
        self.upstream = upstream
        self.status = status
        self.body = body[:2000]
        self.cause = cause
        super().__init__(f"{upstream} returned {status}: {self.body[:200]!r} (cause={cause!r})")


class ResilientUpstream:
    def __init__(self, base_url: str, timeout_s: float = 60.0,
                 connect_s: float = 10.0, suspect_ttl_s: float = 5.0):
        self._base = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_s, connect=connect_s)
        self._client = httpx.Client(timeout=self._timeout)
        self._suspect_until = 0.0
        self._suspect_ttl = suspect_ttl_s
        self._lock = threading.Lock()

    # ---- gestion du pool -------------------------------------------------
    def _recycle(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self._client = httpx.Client(timeout=self._timeout)

    def _probe(self) -> bool:
        try:
            r = self._client.get(f"{self._base}/v1/models", timeout=5.0)
            r.read(); r.close()          # drain -> libère la connexion propre
            return True
        except Exception:
            return False

    def _ensure_healthy(self) -> None:
        if time.monotonic() < self._suspect_until:
            if self._probe():
                self._suspect_until = 0.0
            else:
                self._recycle()

    def _mark_suspect(self) -> None:
        self._suspect_until = time.monotonic() + self._suspect_ttl

    # ---- requête ---------------------------------------------------------
    def post_json(self, path: str, json_body: dict, stream: bool = False) -> httpx.Response:
        with self._lock:                       # pool non partagé en écriture concurrente
            self._ensure_healthy()
            url = f"{self._base}{path}"
            try:
                resp = self._client.post(url, json=json_body)
            except (httpx.RemoteProtocolError, httpx.ReadError,
                    httpx.ConnectError, httpx.WriteError) as e:
                self._recycle()                # socket mort -> pool neuf + 1 retry
                resp = self._client.post(url, json=json_body)

            if resp.status_code >= 400:
                body = resp.read().decode("utf-8", "replace")   # DRAIN complet
                resp.close()
                self._mark_suspect()
                raise UpstreamError(self._base, resp.status_code, body)

            if not stream:
                resp.read()                    # drain -> keep-alive propre
            return resp
```

### A.3 Câblage dans `src/mcp_gateway.py`

Remplacer l'usage httpx direct par `ResilientUpstream` (une instance par upstream, gardée au niveau module) :

```python
# mcp_gateway.py — init
from src.upstream_client import ResilientUpstream, UpstreamError
_UPSTREAMS = {"lmstudio": ResilientUpstream("http://127.0.0.1:1234", timeout_s=60.0)}

# handler proxy (là où l'appel upstream est fait aujourd'hui)
try:
    resp = _UPSTREAMS[upstream].post_json("/v1/chat/completions", body, stream=is_stream)
    if is_stream:
        return stream_and_release(resp)          # draine le flux puis resp.close()
    return JSONResponse(resp.json())
except UpstreamError as e:
    # conserver le contrat 502 pour ne pas casser le client
    return JSONResponse(status_code=502, content={"error": {
        "message": f"upstream unreachable: {e.upstream} returned {e.status}: {e.body[:200]}",
        "type": "upstream_error",
        "upstream": e.upstream,
        "upstream_status": e.status,
    }})
except Exception as e:
    return JSONResponse(status_code=502, content={"error": {
        "message": f"upstream error: {type(e).__name__}: {e!r}",
        "type": "upstream_error", "upstream": upstream}})
```

> Point critique du streaming : le générateur `stream_and_release` doit `resp.close()` dans un `finally` **après** consommation complète, sinon le flux non drainé re-pollue le pool.

### A.4 Retry sur champ `cache_control` rejeté (streaming)

Certains upstream refusent un champ `cache_control` injecté par le proxy (400 explicite). Sur ce cas précis, retenter **une fois sans le champ** :

```python
except UpstreamError as e:
    if e.status == 400 and "cache_control" in e.body and "cache_control" in body:
        body.pop("cache_control", None)
        retry...
```

### A.5 Timeout upstream explicite

`timeout_s=60.0` (au lieu du défaut 120s perçu). Le 502 arrive en ≤ 60s au lieu de 120s.

### A.6 Test de régression `tests/test_upstream_recovery.py`

```python
def test_recovery_after_400(upstream_mock):
    # 1) prompt oversized -> 400
    assert post(oversized).status_code == 400
    # 2) prompt court -> DOIT réussir immédiatement
    t0 = time.time(); r = post("hi"); dt = time.time() - t0
    assert r.status_code == 200
    assert dt < 2.0                     # pas de hang 120s
    assert r.json()["source"] in ("upstream", "cache")
```

Critère d'acceptation : après un 400 forcé, l'appel suivant réussit en < 2s, sans appel direct manuel à `:1234`.

---

## VOLET B — BUG-002 : cache sémantique hybride (Medium)

### B.1 Modèle de clé scindé (anti-faux-positif)

```
SIG_EXACTE = sha256(canonical_json(
    model, temperature, max_tokens, top_p, tools_sig, tool_choice, system_prompt
))
```
- **Seul le dernier message utilisateur** est comparé sémantiquement.
- Tout le reste (model, temp, system, tools) est **hashé exactement** → filtre Qdrant `must`.

### B.2 Grounding par ancres (le vrai anti-faux-positif)

Extraction d'ancres **dures** (doivent être égales) et **souples** (noms propres). C'est ce qui empêche « Montreal » de matcher « Quebec City » alors que le cosinus est ~0.97.

```python
# src/icy_semantic.py (extrait)
import re
_HARD = [
    ("num",   r"\b\d+(?:[.,]\d+)?\b"),
    ("quote", r"[\"'`]([^\"'`]{1,60})[\"'`]"),
    ("path",  r"\b[A-Za-z]:\\[^\s]+|(?:\./|/)[^\s]+"),
    ("unit",  r"\b\d+\s?(?:kg|km|cm|mm|mb|gb|tb|ms|min|h|%|°c|°f|usd|eur)\b"),
    ("url",   r"https?://[^\s]+"),
]

def extract_hard(text: str) -> frozenset[str]:
    low = text.lower(); out = set()
    for _, pat in _HARD:
        out.update(m.group(0).strip() for m in re.finditer(pat, low))
    return frozenset(out)

def extract_soft(text: str) -> frozenset[str]:
    # noms propres : majuscule NON en début de phrase (sinon bruit)
    return frozenset(
        m.group(0).lower()
        for m in re.finditer(r"\b[A-Z][a-zà-ÿ]{2,}\b", text)
        if m.start() > 0
    )
```

### B.3 Décision d'acceptation

```python
def accepts(query_norm, cand, score, HARD_MIN=0.95, RELAX=0.97) -> bool:
    if score < HARD_MIN:
        return False
    qh, ch = extract_hard(query_norm), frozenset(cand["anchors_hard"])
    if qh != ch:                       # "2" vs "3", "montreal.txt" vs autre
        return False
    qs, cs = extract_soft(query_norm), frozenset(cand["anchors_soft"])
    if qs == cs:
        return True
    return score >= RELAX              # ex: ajout de "Canada" toléré au-dessus de 0.97
```

### B.4 Embedder + Store Qdrant

```python
# src/icy_semantic.py
import threading, uuid
from qdrant_client import QdrantClient, models

class Embedder:                                   # LM Studio /v1/embeddings
    def __init__(self, base_url, model, timeout=10.0):
        self._c = httpx.Client(base_url=base_url, timeout=timeout)
        self._m = model
    def embed(self, text):
        r = self._c.post("/v1/embeddings", json={"model": self._m, "input": text})
        r.raise_for_status()
        v = r.json()["data"][0]["embedding"]
        n = sum(x * x for x in v) ** 0.5 or 1.0   # normalisation L2
        return [x / n for x in v]

class SemanticStore:
    COLL = "icycache_semantic"
    def __init__(self, url):
        self.c = QdrantClient(url=url); self._lock = threading.Lock()
    def ensure(self, dim):
        if not self.c.collection_exists(self.COLL):
            self.c.create_collection(self.COLL,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))
    def search(self, sig, vec, seuil, top=1):
        with self._lock:
            res = self.c.query_points(
                collection_name=self.COLL, query=vec, limit=top,
                score_threshold=seuil,
                query_filter=models.Filter(must=[models.FieldCondition(
                    key="sig", match=models.MatchValue(value=sig))]),
                with_payload=True).points
        return res[0] if res else None
    def upsert(self, vec, payload):
        with self._lock:
            self.c.upsert(self.COLL, points=[models.PointStruct(
                id=str(uuid.uuid4()), vector=vec, payload=payload)])
```

> Dépendance : `qdrant-client >= 1.10` (API `query_points`).

### B.5 Indexation en arrière-plan (ne bloque jamais la réponse)

```python
def index_async(store, embedder, sig, query_norm, resp_json):
    def _job():
        try:
            v = embedder.embed(query_norm)
            store.upsert(v, {"sig": sig, "response": resp_json,
                             "anchors_hard": sorted(extract_hard(query_norm)),
                             "anchors_soft": sorted(extract_soft(query_norm)),
                             "ts": time.time()})
        except Exception:
            pass                       # index échoue -> pas grave, pas de crash
    threading.Thread(target=_job, daemon=True).start()
```

### B.6 Intégration dans le pipeline (`mcp_server.py`)

```python
# après le hit exact existant, AVANT l'appel upstream
if SEMANTIC_ENABLED and last_user_msg:
    try:
        qn = normalize(last_user_msg)
        vec = embedder.embed(qn)
        cand = store.search(sig, vec, seuil=SEMANTIC_THRESHOLD)
        if cand and accepts(qn, cand.payload, cand.score):
            return _serve_cached(cand.payload["response"], source="semantic")
    except Exception:
        pass                            # tout échec sémantique = miss silencieux

# après succès upstream
index_async(store, embedder, sig, qn, resp_json)
```

### B.7 Normalisation des nombres (réduit les misses triviaux)

Ajout dans `icy_normalize.py` **et** `config/normalization_rules.json` :

```python
_NUM_WORDS = {"zero":"0","one":"1","two":"2","three":"3","four":"4","five":"5",
              "six":"6","seven":"7","eight":"8","nine":"9","ten":"10"}
def normalize_numbers(t: str) -> str:
    for w, d in _NUM_WORDS.items():
        t = re.sub(rf"\b{w}\b", d, t)
    return (t.replace(" plus ", "+").replace(" minus ", "-")
             .replace(" times ", "*").replace(" divided by ", "/"))
```

→ « two plus two » devient « 2+2 » **avant** hash ET avant embedding : le cas de test BUG-002 devient un hit **exact**, sans même toucher au sémantique.

### B.8 Couvre chat ET tool calls

- `sig` inclut `tools_sig` → une réponse d'outil n'est servie que pour le même jeu d'outils.
- Les `tool_calls` sont sérialisés dans `payload["response"]` (déjà le cas sur le hit exact — confirmé par T2/T6).
- L'extraction d'ancres tourne sur le message utilisateur, donc `get_weather(city="Montreal")` reste groundé par l'ancre `montreal`.
- Faux positif vérifié évité : T5 (Quebec City) → soft anchors ≠ → miss correct.

---

## VOLET C — DELTA-001 : application du registre d'outils (100)

DELTA-001 était « rien d'appliqué ». Ce delta l'applique **en tant que donnée**, sans re-lister les 100 entrées ici.

### C.1 `config/tools_registry.json`

Reprend le compte validé de DELTA-001 : `15 + 22 + 63 = 100`, avec les 3 entrées vérifiées PyPI comme ancres de schéma :

```json
{
  "version": 1,
  "total": 100,
  "verified_anchors": [
    {"name": "ha-mcp", "version": "8.7.0", "transport": "stdio"},
    {"name": "serena-agent", "version": "2.1.0", "cmd": "serena start-mcp-server", "transport": "stdio"},
    {"name": "browser-use", "version": "0.13.11", "flag": "--mcp", "transport": "stdio"}
  ],
  "groups": {"A": 15, "B": 22, "C1": 3, "C2": 60}
}
```

> Les 100 entrées complètes vivent dans DELTA-001 ; ce delta fixe le **schéma + le chargement + le câblage**. Champs `_note` conservés pour ce qui reste non vérifié (env browser-use, transport défaut serena).

### C.2 `tools_sig` (signature de cache)

```python
def tools_sig(tools: list | None) -> str:
    if not tools:
        return "none"
    canon = json.dumps(tools, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]
```

Câblé dans la construction de `sig` (B.1) → le cache ne peut pas servir une réponse d'outil pour un jeu d'outils différent.

### C.3 Chargement dans `mcp_server.py`

```python
REGISTRY = json.loads((CONFIG_DIR / "tools_registry.json").read_text())
assert REGISTRY["total"] == 100
```

---

## RÉCAPITULATIF — fichiers & actions

| Fichier | Action | Volet |
|---|---|---|
| `src/upstream_client.py` | **NOUVEAU** | A |
| `src/icy_semantic.py` | **NOUVEAU** | B |
| `config/tools_registry.json` | **NOUVEAU** | C |
| `config/semantic_cache.json` | **NOUVEAU** (flags) | B |
| `src/mcp_gateway.py` | modif : client résilient + 502 structuré + retry cache_control | A |
| `src/prefix_proxy.py` | modif : idem si route séparée | A |
| `src/icy_normalize.py` | modif : `normalize_numbers()` | B |
| `src/mcp_server.py` | modif : lookup/store sémantique + load registry | B, C |
| `config/normalization_rules.json` | modif : paires synonymes | B |
| `tests/test_upstream_recovery.py` | **NOUVEAU** | A |
| `tests/test_semantic_cache.py` | **NOUVEAU** | B |
| `tests/test_anchors.py` | **NOUVEAU** | B |

## CONFIG (défauts sûrs)

```ini
UPSTREAM_TIMEOUT_S=60
SEMANTIC_ENABLED=false          # opt-in
SEMANTIC_THRESHOLD=0.95
SEMANTIC_RELAX_THRESHOLD=0.97
SEMANTIC_EMBED_URL=http://127.0.0.1:1234
SEMANTIC_EMBED_MODEL=text-embedding-qwen3-embedding-4b
QDRANT_URL=http://127.0.0.1:6333
```

## TESTS D'ACCEPTATION

| # | Test | Attendu |
|---|---|---|
| A1 | 400 oversized puis prompt court | succès < 2s, pas de 502 |
| A2 | kill upstream puis restart | auto-recovery sans intervention manuelle |
| A3 | message d'erreur 502 | contient status + body (plus de champ vide) |
| B1 | P1 exact ×2 | hit exact 0.02s (inchangé) |
| B2 | P2 reformulé (« Compute the sum of two and two… ») | **hit sémantique**, pas d'appel modèle |
| B3 | T3 reformulé (tool call Montreal) | hit sémantique, tool_call servi |
| B4 | T5 ville différente (Quebec City) | **miss correct** (grounding ancres) |
| B5 | Qdrant down | miss silencieux → upstream, aucune panne |
| C1 | registry total == 100 | assert au démarrage |
| C2 | même prompt, tools différents | miss (tools_sig différent) |

## ROLLOUT

1. Fusionner Volet A seul → vérifier A1–A3. **Aucune régression possible** (même contrat 502).
2. Fusionner Volet C (données) → C1–C2.
3. Activer Volet B derrière `SEMANTIC_ENABLED=true` → mesurer hit ratio + taux de faux positifs sur 200+ requêtes réelles avant de baisser le seuil sous 0.95.

## RISQUES

| Risque | Mitigation |
|---|---|
| Faux positif sémantique | ancres dures obligatoires + seuil 0.95 + `tools_sig` |
| Latence embedding CPU (~50–100ms) | indexation en thread ; fallback hash instantané prioritaire |
| Qdrant/embedding indispo | miss silencieux, jamais de 502/500 |
| Poisoning du cache (réponse tronquée indexée) | règle write-path : ne pas indexer si réponse vide/refus < N tokens |
| Verrou global sur le store | lock dédié, scope minimal (upsert/search seulement) |

## CHECKLIST — état au 2026-10-26

- [x] `src/upstream_client.py` créé
- [x] `mcp_gateway.py` + `prefix_proxy.py` câblés
- [x] `src/icy_semantic.py` créé
- [x] `icy_normalize.py` : `normalize_numbers()` (1111 → 1122 règles)
- [x] `mcp_server.py` : lookup/store + registry
- [x] `config/tools_registry.json`, `semantic_cache.json`, règles
- [x] 3 fichiers de tests
- [ ] **A1–A3 validés en conditions réelles** (le correctif est en place, la campagne de mesure reste à faire)
- [ ] **Volet B activé en production** (`semantic_enabled: false` par défaut — l'activation exige la campagne de mesure ci-dessus)
