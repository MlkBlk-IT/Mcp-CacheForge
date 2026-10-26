# BUG-002 — IcyCache ne fait pas de cache sémantique (match exact uniquement)

**Date**: 2026-10-26
**Sévérité**: Medium (limitation fonctionnelle, pas un crash)
**Statut**: Open — Feature gap / Enhancement
**Composant**: `src/icy_normalize.py` (moteur de normalisation + clé de cache)
**Découvert par**: test de sondage sémantique (2 prompts, même sens, wording diff)

## Symptôme

IcyCache calcule sa clé de cache sur le **texte normalisé exact**, pas sur le sens. Deux prompts avec **le même sens** mais un **wording différent** → 2 entrées de cache séparées. Le modèle est rappelé même si une réponse identique aurait pu être servie du cache.

Conséquence : **pertes d'opportunités de hit** sur les reformulations, paraphrases, changements de casse/ponctuation mineurs qui ne tombent pas sous les règles de normalisation existantes.

## Reproduction

```bash
# P1 (remplit le cache)
curl -X POST http://127.0.0.1:8080/lmstudio/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5-9b-anwar-hermes","messages":[{"role":"user","content":"What is 2+2? Answer with just the number."}],"max_tokens":15,"temperature":0}'
# → 1.19s, upstream, 25→2 tokens, réponse "4"

# P1 encore (HIT confirmé)
# → 0.03s, cache, 0/0 tokens, réponse "4"

# P2 — MÊME SENS, wording diff
curl -X POST http://127.0.0.1:8080/lmstudio/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5-9b-anwar-hermes","messages":[{"role":"user","content":"Compute the sum of two and two, output only the digit."}],"max_tokens":15,"temperature":0}'
# → 3.51s, upstream, 25→2 tokens, réponse "4"  ← MISS (modèle rappelé inutilement)
```

P1 et P2 = même question ("combien font 2+2"), même réponse ("4"), mais P2 = miss complet.

## Preuve (sortie de test)

| Appel | Prompt | Latence | Source | Tokens | Verdict |
|-------|--------|---------|--------|--------|---------|
| P1 #1 froid | `What is 2+2? Answer with just the number.` | 1.19s | `upstream` | 25→2 | MISS (remplit cache) |
| P1 #2 chaud | (identique) | 0.03s | `cache` | 0/0 | HIT ✓ |
| **P2 #1 (sondage sém.)** | `Compute the sum of two and two, output only the digit.` | **3.51s** | **`upstream`** | **25→2** | **MISS** ❌ |
| P2 #2 chaud | (identique) | 0.04s | `cache` | 0/0 | HIT ✓ |
| P1 #3 (encore caché?) | (identique P1) | 0.02s | `cache` | 0/0 | HIT ✓ |

P1 et P2 retournent tous deux `4`, mais P2 #1 a rappelé le modèle (3.51s, 27 tokens consommés) au lieu de servir P1 du cache.

## Cause (racine, confirmée dans le code)

`src/icy_normalize.py` — classe `Normalizer` (ligne 46-92) :

```
normalize(text):
  NFKD → lowercase → _replace_rules (Aho-Corasick) → strip accents → strip ponctuation → strip espaces → strip volatile tail
```

Clé de cache = `sha256(normalized_text)`. **Aucune étape sémantique** (pas d'embedding, pas de similarité cosine, pas de clustering).

Les règles de remplacement (`config/normalization_rules.json`, 1111 règles) ne couvrent que :
- **Politesse** : `could you please` → ``, `s'il vous plait` → ``, `merci beaucoup` → ``
- **Typos** : `responsability` → `responsability`, `administrateur` → `administrator`
- **Quelques synonymes** : `redimensionner` → `resize`

Aucune règle ne fait `two plus two` ↔ `2+2` ou `compute the sum` ↔ `what is`. Le normalizer gère bien les **variations de surface** (casse, accents, ponctuation, politesse) mais **pas les reformulations**.

## Impact

- **Cache hit ratio sous-optimal** : toute reformulation d'un prompt déjà posé = miss.
- **Coût réel** : sur des workloads avec reformulations fréquentes (utilisateurs humains, agents qui rephrasent), IcyCache rate des hits qui auraient économisé des tokens + latence.
- **Cas concret mesuré ici** : 3.51s + 27 tokens perdus sur un simple "2+2" reformulé. Sur un prompt à 2000 tokens, ça serait ~55s + 2200 tokens perdus.

## Fichiers à inspecter

- `src/icy_normalize.py:81` — `Normalizer.normalize()` (pipeline actuel)
- `src/icy_normalize.py:120` — `get_hash()` (clé de cache = SHA256 du texte normalisé)
- `src/icy_normalize.py:157` — `normalize_payload()` (où le body complet est normalisé pour la clé)
- `config/normalization_rules.json` — 1111 règles (politesse + typos, pas sémantique)
- `src/icy_core.py:49` — `short_hash()` (utilisé pour les clés secondaires)
- `src/mcp_server.py:316` — recherche `WHERE normalized_prompt LIKE ?` (déjà un LIKE, mais pas fuzzy/sémantique)

## Fix suggéré (3 options, à valider ensemble)

### Option A — Règles de synonymes ciblées (quick win, limité)
Ajouter des paires de synonymes dans `normalization_rules.json` (ex: `two` → `2`, `compute the sum of` → `what is`). Marche seulement pour les cas qu'on code explicitement. Non-généralisable.

### Option B — Embedding + similarité cosinus (vrai cache sémantique)
1. À l'insertion au cache : calculer l'embedding du prompt normalisé (modèle `text-embedding-qwen3-embedding-4b` déjà dispo dans LM Studio).
2. Stocker l'embeddings dans Qdrant (collection dédiée `icycache_semantic`), avec comme payload la réponse cachée.
3. Au lookup : embedder le prompt → recherche kNN dans Qdrant → si similarité cosin > seuil (ex: 0.92) → servir la réponse du cache.
4. Garder le hash exact comme fast-path (hit instantané sur match exact), embedding comme fallback (hit sémantique).
- **Pro** : généralise à toute reformulation.
- **Con** : latence d'embedding (~50-100ms CPU), dépendance Qdrant, risque de faux-positifs (deux prompts similaires mais réponse différente).
- **Mitigation** : seuil élevé (0.92+), + check métadonnées (même modèle, même `max_tokens`, même `temperature`).

### Option C — Pre-recherche sémantique dans Qdrant avant hash lookup
Hybride : hash d'abord (instantané), si miss → recherche Qdrant → si hit sémantique, servir ; sinon → upstream. Évite l'overhead d'embedding sur les hits exacts.

## Recommandation

Option C (hybride hash + Qdrant sémantique) — préserve la performance actuelle sur les hits exacts, ajoute la couverture sémantique seulement quand nécessaire. Qdrant est déjà déployé (`C:\AI\EmbeddingDB\hermes_qdrant_mcp.py`), modèle d'embedding déjà loadé dans LM Studio (`text-embedding-qwen3-embedding-4b`).

## Confirmation sur tool calling (2026-10-26)

Bug identique confirmé sur les **tool calls** (pas seulement chat plain-text). Test avec outil `get_weather` défini dans la requête (`tools` + `tool_choice: auto`), modèle `qwen3.5-9b-anwar-hermes` :

| Appel | Prompt | Latence | Source | Tokens | Tool call | Verdict |
|-------|--------|---------|--------|--------|-----------|---------|
| T1 froid | `What is the weather in Montreal?` | 23.76s | `upstream` | 309→27 | `get_weather(city="Montreal")` | MISS (remplit cache) |
| T2 chaud | (identique T1) | 0.02s | `cache` | 0/0 | `get_weather(city="Montreal")` | HIT ✓ |
| **T3 sémantique** | `Tell me the current temperature in Montreal` | **8.46s** | **`upstream`** | **309→27** | `get_weather(city="Montreal")` | **MISS** ❌ |
| T4 T3 chaud | (identique T3) | 0.02s | `cache` | 0/0 | `get_weather(city="Montreal")` | HIT ✓ |
| T5 ville diff | `What is the weather in Quebec City?` | 7.9s | `upstream` | 310→28 | `get_weather(city="Quebec City")` | MISS (correct — args diff) |
| T6 T5 chaud | (identique T5) | 0.02s | `cache` | 0/0 | `get_weather(city="Quebec City")` | HIT ✓ |

**Points clés** :
- Tool calling **est bien caché** sur match exact : T2 et T6 = hit instant (0.02s, 0 token), tool call identique servi du cache. IcyCache sérialise correctement le `tool_calls` dans la réponse cachée.
- **Même bug sémantique** que le chat : T3 (reformulation de T1, même intent `get_weather(Montreal)`) = miss à 8.46s + 336 tokens consommés, alors que la réponse cachée de T1 aurait pu être servie.
- **Bonne nouvelle** : le cache ne sur-matche pas — T5 (ville différente = `Quebec City`) = miss correct, prouvant que le cache ne sert pas une réponse `Montreal` pour `Quebec City`. Le hash exact empêche les faux-positifs sémantiques, mais au prix de rater les vrais positifs sémantiques.

**Conclusion** : BUG-002 s'applique uniformément au chat ET au tool calling. Le fix (option C hybride hash + Qdrant sémantique) doit couvrir les deux chemins — la normalisation + le lookup sémantique doivent s'appliquer au prompt utilisateur même quand la réponse est un tool call.

## Contexte matériel

- LM Studio 0.4.26, model `qwen3.5-9b-anwar-hermes` (9B), ctx 8k, CPU-only
- IcyCache v1.30.0, proxy :8080
- Qdrant dispo : `C:\AI\EmbeddingDB\hermes_qdrant_mcp.py`
- Embedding model dispo : `text-embedding-qwen3-embedding-4b` (LM Studio)
- Dell Latitude 5420, i7-1185G7, 32 GB RAM, pas de GPU
