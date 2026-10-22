> ## ⚠️ WORK IN PROGRESS — PAS PRÊT (2026-10-22)
> CacheForge est en développement actif. **Ne pas utiliser en production.**
> - **Le bug 500 décrit dans la doc `.docx` (2026-10-22 02:15) est résolu.** La suite de tests isolée passe (`ISOLATED: ALL PASS`), le proxy répond en réel (38 requêtes, 18 hits, 47,4 % de hit rate, 31,0 % de tokens économisés — `logs/test_run.log`).
> - **Non couvert / non vérifié :** `test_prefix_proxy.py` n'a pas de log de résultat explicite (seul `logs/prefix_proxy.log` existe, traces de mock OK) ; `granite_live.log` n'a pas été relu ; la doc `.docx` est **périmée** vs le code actuel (elle mentionne Python 3.11, le code tourne en 3.12.10).
> - **Incohérence connue :** `config/cacheforge.json` a `mcp.host = 0.0.0.0` alors que `tray.py` force `127.0.0.1`. Si le tray n'écrase pas la valeur, le port MCP 8081 serait exposé sur le LAN. À corriger.
> - L'outil MCP `set_target_provider` a été supprimé (bak9) : le changement de provider se fait par édition de config uniquement.

# 🔥 CacheForge — Proxy LLM avec cache local

> **« Forger le cache, pas la réponse. »**
> Proxy HTTP asynchrone (FastAPI) qui s'intercale entre le client et le backend LLM (LM Studio `:1234`) et met en cache les réponses pour les resservir **instantanément** — sans régénération CPU.

---

## Le problème

Un modèle local (LM Studio) a deux goulots d'étranglement pour les tâches IT récurrentes :

| Problème | Conséquence |
|----------|-------------|
| **Latence** | Une requête identique rebrûle du temps CPU/RAM pour une réponse déjà calculée |
| **Pas de cache sémantique** | Les systèmes standards hachent le prompt brut : `Explique Hyper-V` et `S'il te plaît, explique Hyper-V stp` génèrent **deux** requêtes distinctes |
| **Pas d'observabilité** | Impossible de mesurer les tokens et le temps réellement économisés |

CacheForge attaque les trois : normalisation du prompt avant hachage, cache SQLite avec TTL, et métriques exportables.

---

## Architecture

```
Client
  │  POST /v1/chat/completions
  ▼
┌────────────────────────────────────────────────┐
│  PROXY  127.0.0.1:8080   (src/server.py)       │
│  NORMALISATION  NFKD → lowercase → Aho-Corasick│  ← 1114 règles (config/normalization_rules.json)
│  HACHAGE        SHA256 du payload normalisé     │  ← model + messages + temperature + tools + stream
│  DÉCISION       HIT → réponse instantanée      │
│                 MISS → forward + sauvegarde    │
│  MÉTRIQUE       tokens spent / saved, tks, âge │
│  DASHBOARD      /dashboard + /api/metrics      │
└────────────────────────────────────────────────┘
  │                          ▲
  │  SQLite db/cache.db      │  MCP  127.0.0.1:8081 (SSE) + stdio
  │  TTL 86400 s / max 10000 │  4 outils (get_cache_stats, clear_cache,
  ▼                          │  normalize_preview, search_cached_prompts)
  UPSTREAM  http://localhost:1234/v1/chat/completions  (LM Studio)

  + PREFIX PROXY  127.0.0.1:8090  (src/prefix_proxy.py)  ← ADDITIF, DB séparée db/prefix.db
    Anthropic : injecte `cache_control`   |  OpenAI : injecte `prompt_cache_key`
    Mesure : GET /api/prefix-stats  (tokens lus/écrits dans le cache du fournisseur)
```

### Composants

| Composant | Rôle |
|-----------|------|
| `src/server.py` | Proxy FastAPI + serveur MCP (dual transport stdio/SSE), cache SQLite, dashboard |
| `src/prefix_proxy.py` | Proxy additif port 8090 : cache de préfixe cloud (Anthropic/OpenAI), ne stocke aucune clé API |
| `config/cacheforge.json` | Config proxy, MCP, cache, normalisation, logging |
| `config/normalization_rules.json` | 1114 règles de normalisation (générées 2026-10-21 22:35) |
| `tray.py` | Icône systray : démarre proxy + MCP, ouvre le dashboard |
| `mcp_stdio.py` | Wrapper stdio pour Claude Desktop (n'ouvre aucun port) |
| `static/dashboard.html` | Dashboard web local (métriques, hit rate, export) |
| `tests/` | `test_cacheforge.py`, `test_cacheforge_ext.py`, `test_prefix_proxy.py`, `run_isolated.py` |
| `install_autostart.ps1` / `uninstall_autostart.ps1` / `restart_tray.ps1` / `start_cacheforge.ps1` | Cycle de vie Windows (Task Scheduler) |

---

## Outils MCP exposés (4)

| Outil | Rôle |
|-------|------|
| `get_cache_stats` | Statistiques du cache (entrées, hit rate, tokens économisés) |
| `clear_cache` | Vide le cache (exige `confirm`) |
| `normalize_preview` | Prévisualise la normalisation d'un texte sans le mettre en cache |
| `search_cached_prompts` | Recherche dans les prompts déjà mis en cache |

---

## Routes HTTP

| Route | Méthode | Rôle |
|-------|---------|------|
| `/v1/chat/completions` | POST | Point d'entrée principal (OpenAI-compatible) |
| `/health` | GET | Santé du proxy |
| `/dashboard` | GET | Interface web des métriques |
| `/api/dashboard-stats` | GET | Stats JSON pour le dashboard |
| `/api/metrics` | GET | Métriques agrégées |
| `/api/metrics/series` | GET | Séries temporelles |
| `/api/metrics/export` | GET | Export JSON (filtre `?since=`) |
| `/api/cache-clear` | POST | Vide le cache — **exige le header `X-CacheForge-Confirm`** (403 sinon) |

---

## Mesures réelles (preuves)

| Source | Résultat |
|--------|----------|
| `logs/isolated_run.log` | `ISOLATED: ALL PASS` — tests `test_cacheforge.py` + `test_cacheforge_ext.py`, ports libérés après teardown |
| `logs/test_run.log` | 38 requêtes, 18 hits, 20 misses, **hit rate 47,4 %**, **31,0 % de tokens économisés**, `events_without_usage=0` |
| `logs/granite_live.log` | Test live `granite-4.2-8b` : 28 tokens in + 97 out économisés sur un hit, temps original 20 023 ms |
| `logs/prefix_proxy.log` | Proxy préfixe OK sur mocks Anthropic/OpenAI ; retry sans injection si l'amont refuse `cache_control` |

---

## Installation

```powershell
# 1. Environnement (Python 3.12.10)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Démarrage manuel
.\start_cacheforge.ps1

# 3. Démarrage au login (Task Scheduler)
.\install_autostart.ps1
```

| Variable d'environnement | Rôle |
|--------------------------|------|
| `CACHEFORGE_DB` | Chemin de la DB de cache (défaut `db/cache.db`) |
| `CACHEFORGE_LOG` | Chemin du log (défaut `logs/app.log`) |
| `CACHEFORGE_TARGET` | Surcharge de l'upstream (utilisé par les tests isolés) |
| `PREFIX_DB` | DB du proxy préfixe (défaut `db/prefix.db`) |
| `PREFIX_URL_<NOM>` | Surcharge de l'URL amont du proxy préfixe |

---

## État actuel

**v0.1 — cœur fonctionnel, en durcissement.** Proxy HIT/MISS stable, normaliseur à frontières de mot (plus de mutation de sous-chaînes), métriques et dashboard opérationnels, 4 outils MCP. Reste à corriger : l'incohérence `mcp.host`, la couverture de `test_prefix_proxy.py`, et la mise à jour de la doc `.docx`.

---

## Licence

MIT
