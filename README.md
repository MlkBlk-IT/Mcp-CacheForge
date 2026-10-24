> ## ⚠️ WORK IN PROGRESS — migration en cours (2026-10-24)
> IcyCache est renommé depuis CacheForge. La **gateway universelle multi-upstream** (`src/server_new.py`) est
> écrite et testée, mais elle tourne **à côté** de `src/server.py` : le swap sur le port 8080 se fera
> quand le dashboard sera finalisé, puis les deux Dendroits seront unifiés.
> - `src/server.py` reste le service actif sur `:8080` / `:8081` / `:8084` (inchangé, il fait foi).
> - La gateway est prête à démarrer seule : `python src/server_new.py --port 8090`.
> - `src/server.py` et `static/dashboard.html` ne sont **pas** modifiés par la gateway.
>
> ### Transports MCP disponibles (les trois sont actifs en même temps)
>
> | Transport | Endpoint | Client cible |
> |-----------|----------|--------------|
> | **stdio** | `python src/server.py --mode mcp-stdio` | Claude Desktop, IDE, tout client stdio |
> | **SSE** (legacy) | `http://127.0.0.1:8081/sse` | Hermes, clients anciens |
> | **Streamable HTTP** (standard) | `http://127.0.0.1:8084/mcp` | Clients MCP récents, MCP Inspector |
>
> En stdio, passer par `src/mcp_stdio_bridge.py` : il réserve stdout au JSON-RPC.
> (Le code ne écrit plus rien sur stdout, mais le pont reste la Filet de sécurité.)

# 🔥 IcyCache — Gateway LLM universelle avec cache local

> **« Forger le cache, pas la réponse. »**
> Passerelle HTTP qui s'intercale entre **n'importe quel client** et **n'importe quel upstream**
> (local ou cloud), met en cache les réponses et les ressert **instantanément** — sans régénération CPU.

---

## Le problème

| Problème | Conséquence |
|----------|-------------|
| **Latence** | Une requête identique rebrûle du temps CPU/RAM pour une réponse déjà calculée |
| **Pas de cache sémantique** | Les systèmes standards hachent le prompt brut : `Explique Hyper-V` et `S'il te plaît, explique Hyper-V stp` génèrent **deux** requêtes distinctes |
| **Pas d'observabilité** | Impossible de mesurer les tokens et le temps réellement économisés |
| **Trafic qui contourne le proxy** | Chaque client qui parle directement à son fournisseur casse la promesse du cache |

IcyCache attaque les quatre : normalisation avant hachage, cache SQLite avec TTL, métriques exportables,
et **une seule porte d'entrée** pour tout le trafic LLM.

---

## Architecture cible

```
   N'importe quel client                 (Hermes, Claude Code, scripts, IDE, SDK, CLI)
   base_url = http://127.0.0.1:8080
              │
              ▼
┌───────────────────────────────────────────────────────────────────────┐
│  ICYCACHE GATEWAY  127.0.0.1:8080   (src/server_new.py)              │
│                                                                       │
│  ROUTAGE      préfixe d'URL  /<upstream>/v1/...                       │
│               header      X-IcyCache-Upstream: lmstudio               │
│               body        "upstream": "ollama"                        │
│               défaut      gateway.default_upstream (config)           │
│                                                                       │
│  NORMALISATION NFKD → lowercase → Aho-Corasick → accents → ponctuation│
│  CLÉ DE CACHE  SHA256 du payload NORMALISÉ (+ upstream + endpoint)    │
│  DÉCISION      HIT → réponse instantanée | MISS → forward + save      │
│  TOKENS        usage REEL de l'upstream. Jamais d'estimation.         │
│  OBSERVABLE    WebSocket /ws/events : deltas live + final             │
│  MCP (façade)  stats / clear / search / temps réel / tool-cache      │
└───────────────────────────────────────────────────────────────────────┘
        │                    ▲                              │
        ▼                    │ retour même chemin            │
 { OmniRoute | LM Studio | Ollama | OpenAI | Anthropic | … }
        │
        ▼
   SQLite db/cache.db   (events : upstream, tokens réels, latences, ttft)
```

**Règle : tout ce qui sort d'un client et revient dans ce client passe par IcyCache.**
OmniRoute n'est plus la porte d'entrée : c'est **un upstream parmi d'autres**.

---

## Composants

| Fichier | Rôle |
|---------|------|
| `src/server_new.py` | **Gateway universelle multi-upstream** (FastAPI) : routage, cache, tokens réels, WebSocket |
| `src/mcp_gateway.py` | **Façade MCP** de la gateway : 7 outils, tool-cache, loop guard, API REST miroir |
| `src/icy_core.py` | Noyau partagé : DB (`events`, `agent_events`, `tool_cache`), hub WebSocket, loop guard |
| `src/icy_normalize.py` | Moteur de normalisation autonome (port fidèle, sans effet de bord à l'import) |
| `src/icy_cache_store.py` | Store de cache LLM (compatible table `cache`, colonne `raw_hash` additive) |
| `src/upstream_utils.py` | Normalisation d'usage, en-têtes, mutation cache_control / prompt_cache_key, meter SSE |
| `src/mcp_stdio_bridge.py` | Pont stdio : réserve stdout au JSON-RPC MCP, logs et prints vers stderr |
| `src/server.py` | Service historique (actif sur :8080/:8081) — **sera unifié avec la gateway** |
| `config/icycache.gateway.json` | Config de la gateway : upstreams, cache, loop guard, tool cache |
| `config/normalization_rules.json` | 1114 règles de normalisation |
| `tools/smoke_gateway.py` | Test de fumée **réel** multi-upstream (ports isolés, DB temporaire) |
| `tools/enforce_gateway.ps1` | Règles de pare-feu : rend le contournement impossible |
| `tools/verify_enforcement.py` | **Preuve** : le client peut-il encore joindre un upstream en direct ? |
| `tests/test_gateway.py` | 10 tests isolés (mock upstream, DB temporaire) |

---

## Routage : choisir son upstream

| Méthode | Exemple | Priorité |
|---------|---------|----------|
| Préfixe d'URL | `POST http://127.0.0.1:8080/lmstudio/v1/chat/completions` | 1 |
| Header | `X-IcyCache-Upstream: lmstudio` | 2 |
| Corps | `{"upstream": "lmstudio", ...}` | 3 |
| Défaut de config | `gateway.default_upstream` | 4 |

Aucun défaut **implicite** dans le code : sans source explicite, la requête est refusée en `400`
avec la liste des upstreams disponibles. Un upstream inconnu est refusé en `404`.

---

## Routes HTTP (gateway)

| Route | Méthode | Rôle |
|-------|---------|------|
| `/v1/chat/completions` | POST | OpenAI-compatible (streaming et non-streaming) |
| `/v1/messages` | POST | Anthropic natif (`kind: anthropic`) |
| `/v1/embeddings` | POST | Embeddings (cachés) |
| `/v1/models` | GET | Modèles de l'upstream |
| `/{upstream}/v1/{rest}` | * | Routage explicite par préfixe |
| `/ws/events` | WS | Événements temps réel : `delta` live + `final` (tokens réels) |
| `/health`, `/api/status` | GET | Santé, upstreams joignables |
| `/api/metrics`, `/api/metrics/series`, `/api/metrics/export` | GET | Métriques (formes dashboard) |
| `/api/agent-events` | GET | Événements agent / tool-cache |
| `/api/cache-clear` | POST | Vide le cache — header `X-IcyCache-Confirm: FORCE_DELETE` |
| `/dashboard` | GET | Dashboard web (lecture seule) |

**En-têtes de confirmation :** `X-IcyCache-Confirm` (naming unifié). L'ancien `X-CacheForge-Confirm`
reste accepté par la gateway pour compatibilité.

---

## Tokens : la règle du réel

| Source | Valeur |
|--------|--------|
| Champ `usage` de la réponse upstream | **always authoritative** |
| Streaming OpenAI | `stream_options.include_usage=true` injecté → usage du dernier chunk |
| Streaming Anthropic | `message_start` + `message_delta` |
| Usage absent | `NULL` + `tokens_source: "n/a"` — **jamais d'estimation** |
| HIT | `saved_prompt_tokens` / `saved_completion_tokens` = usage réel du 1er appel |

Colonnes `events` : `upstream`, `client`, `prompt_tokens`, `completion_tokens`, `cached_tokens`,
`cache_write_tokens`, `latency_ms`, `ttft_ms`.

---

## Outils MCP (façade)

### Serveur historique (`src/server.py`) — 11 outils

| Outil | Rôle |
|-------|------|
| `get_cache_stats` | Entrées, hits, répartition par upstream/modèle |
| `clear_cache` | Vide le cache (`confirm='FORCE_DELETE'`) |
| `normalize_preview` | Aperçu de la normalisation + clé de cache |
| `search_cached_prompts` | Recherche dans les prompts normalisés |
| **`proxy_tool`** | **Appelle un outil d'un serveur MCP cible** (tool calling) |
| **`proxy_tools`** | **Appel batch d'outils cibles** |
| **`list_proxy_servers`** | **Serveurs MCP routables** |
| **`list_proxy_tools`** | **Outils exposés par un serveur cible** |
| `get_tool_cache_stats` | État du cache d'outils + événements agent |
| `clear_tool_cache` | Vide le cache d'outils (`confirm='FORCE_DELETE'`) |
| `check_tool_loop` | Détecte une boucle d'agent dans un historique LLM |

### Serveur gateway (`src/mcp_gateway.py`) — 11 outils

Les 7 outils d'observabilité (`get_cache_stats`, `clear_cache`, `normalize_preview`,
`search_cached_prompts`, `get_realtime_tokens`, `list_upstreams`, `tool_cache_stats`)
**plus** les 4 outils de relay (`proxy_tool`, `proxy_tools`, `list_proxy_servers`,
`list_proxy_tools`).

Le MCP est une **façade** : il observe et pilote. Il n'est pas dans le chemin des requêtes
(un client MCP appelle volontairement ses outils — on ne peut pas intercepter un flux
OpenAI/Anthropic avec lui). Le chemin des requêtes, c'est la gateway HTTP.

Boucles d'agent : `icy_core.llm_loop_check()` détecte un même outil rappelé avec les mêmes
arguments **et** les mêmes résultats (formats Anthropic et OpenAI). Seuil : 3 répétitions = warning.

---

## Tool calling : règles de sûreté

Le proxy d'outils (`src/tool_proxy.py`) relaie les appels vers les serveurs MCP cibles.
Trois règles non négociables :

1. **Une erreur MCP n'est jamais mise en cache** et ne renvoie jamais `ok: true`.
   Un `isError: true` devient `ok: false` avec le message réel (pas d'`ExceptionGroup` opaque).
2. **Les outils d'écriture ne sont jamais mis en cache.** Rejouer une réponse en cache
   doit être sans effet de bord ; sinon une écriture est sautée en silence.
   - `cache_tools` présent → **allowlist stricte** (seuls les outils listés à `true` sont cachés).
   - sinon `cache: true` → **heuristique anti-écriture** (`create_`, `write_`, `delete_`, `push_`, … exclus).
   - sinon → pas de cache.
3. **Les sessions MCP sont persistantes** (un processus par serveur, pas par appel).
   Sans cela, chaque appel d'outil respawnait un processus : 21 s mesurés sur `npx`, 12 s sur `uvx`.

### Exemple

```json
// via n'importe quel client MCP
{"server": "github", "tool": "search_repositories", "args": {"query": "icycache"}}
// -> {"ok": true, "cached": false, "latency_ms": 4867, "result": {...}}
```

### Configuration des serveurs cibles

`config/icycache.json` → `tool_proxy.servers`. Deux jetons de portabilité :

| Jeton | Valeur |
|-------|--------|
| `{PROJECT_ROOT}` | racine du projet (déplacer IcyCache ne demande aucune édition) |
| `{PYTHON}` | interpréteur du venv du projet (seul à avoir `fastapi`/`mcp`) |

Sous Windows, les commandes `.cmd` (comme `npx`) sont résolues automatiquement :
`CreateProcess` ne lance pas un script `npx.cmd` sans shell.

---

## Enforcement : rendre le contournement impossible

```powershell
# 1. Installer les règles (PowerShell ELEVÉ)
powershell -ExecutionPolicy Bypass -File tools\enforce_gateway.ps1 -Action apply `
    -TargetProgram "C:\Users\Tech\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe" `
    -AllowPort 8080 -BlockPorts 20128,1234,11434

# 2. La preuve : le client peut-il encore joindre un upstream en direct ?
python tools\verify_enforcement.py --program "D:\IcyCache\venv\Scripts\python.exe" --allow-port 8080
```

Stratégie (Windows Firewall, par programme) : blocage des ports upstreams locaux **y compris sur
127.0.0.1**, blocage de tout le trafic distant, autorisation explicite de `127.0.0.1:8080`.
Le programme `verify_enforcement.py` s'exécute **avec le binaire du client** (les règles sont par
programme) et contrôle que seul IcyCache reste joignable.

---

## Variables d'environnement (naming unifié)

| Variable | Rôle | Ancien nom (toujours lu par `src/server.py` pour `ICYCACHE_*`) |
|----------|------|------|
| `ICYCACHE_DB` | Chemin de la DB de cache | `CACHEFORGE_DB` |
| `ICYCACHE_LOG` | Chemin du log | `CACHEFORGE_LOG` |
| `ICYCACHE_CONFIG` | Fichier de config (gateway) | — |
| `ICYCACHE_TARGET` | Surcharge de l'upstream (tests isolés) | `CACHEFORGE_TARGET` |
| `ICYCACHE_TOOLPROXY_CONFIG` | Config du proxy d'outils (défaut : `config/icycache.json`) | — |

---

## Installation

```powershell
# 1. Environnement (Python 3.12.10)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install websockets        # requis pour /ws/events

# 2. Démarrage manuel
.\start_icycache.ps1

# 3. Démarrage au login (Task Scheduler, ou repli Startup sans admin)
.\install_autostart.ps1
```

---

## Tests

```powershell
# Suite isolée (mock upstream, DB temporaire, aucun port occupé)
.\venv\Scripts\python.exe -m pytest tests\test_gateway.py -v

# Suite d'intégration isolée (DB temporaire + faux upstream :8190/:8191)
.\venv\Scripts\python.exe tests\run_isolated.py

# Test offline du PrefixProxy (faux fournisseur, aucun appel externe)
.\venv\Scripts\python.exe tests\test_prefix_proxy.py

# Test de fumée réel (LM Studio / OmniRoute, port 8090, DB temporaire)
.\venv\Scripts\python.exe tools\smoke_gateway.py --port 8090
```

Le test de fumée ne touche **ni** au service de production (`:8080`) **ni** au dashboard.
`tests\run_isolated.py` utilise une **base temporaire** (`ICYCACHE_DB`) et ne touche jamais
la base de production.

---

## Licence

MIT
