# IcyCache — PROGRESS

Généré le 2026-10-25 à partir du code source (`src/`), des configs (`config/`), de la suite de tests
**exécutée localement**, de l'historique git et des documents internes (`README.md`, `UPDATE-NOTES.md`,
`docs/CONFIGURATION.md`).

Ce fichier remplace la version du 2026-10-22, qui décrivait le projet sous son ancien nom
(**CacheForge**) et une configuration désormais obsolète.

Convention de marquage :
- **[mesuré]** — chiffre releuvé par exécution le 2026-10-25.
- **[lu]** — déduit du code ou d'un document, sans exécution.
- **[non vérifié]** — affirmation non confirmée par une source disponible.

---

## 1. Identité

| Champ | Valeur |
|---|---|
| Dépôt | https://github.com/MlkBlk-IT/icycache |
| Renommage | `CacheForge` → `IcyCache` le 2026-10-23 (commit `af9ec4f`) |
| Stack | FastAPI 0.156.6 + uvicorn 0.54.0, MCP 1.30.0, SQLite, FastMCP |
| Python | 3.12.10, venv `D:\IcyCache\venv` |
| Licence | MIT (`LICENSE`) |
| Langue | code, docs et commits en **français** ; seul `README.md` est en anglais |
| Point d'entrée | `src/server.py` (gateway) · `tray.py` (icône systray qui démarre tout) |

## 2. Architecture

### 2.1 Ports et processus [lu]

| Port | Processus | Fichier | Rôle |
|---|---|---|---|
| **8080** | Gateway HTTP | `src/server.py` | Point d'entrée unique de tout le trafic LLM |
| **8081** | MCP SSE | `src/mcp_server.py` | Transport SSE (Hermes, clients legacy) |
| **8082** | Config `gateway` | `config/icycache.gateway.json` | Port de référence de la gateway, utilisé pour l'URL WebSocket |
| **8083** | MCP gateway | `src/mcp_gateway.py` | Façade MCP de la gateway |
| **8084** | MCP Streamable HTTP | `src/mcp_server.py` (via `tray.py`) | Transport standard, surchargeable via `mcp_http.port` |
| 8090 | Proxy de préfixe | `src/prefix_proxy.py` | Cache `cache_control` Anthropic / `prompt_cache_key` OpenAI |

`tray.py` démarre la gateway (8080) et le serveur MCP (8081 + 8084) dans un seul processus et refuse
de démarrer si 8080 est déjà occupé par un autre service.

### 2.2 Modules `src/` [lu]

| Module | Rôle |
|---|---|
| `server.py` | **La gateway** — routage multi-upstream, normalisation, cache, tokens réels, WebSocket `/ws/events` |
| `icy_core.py` | Noyau partagé (normalisation + store), lit la même config que la gateway |
| `icy_normalize.py` | Normalisation autonome (NFKD → minuscules → Aho-Corasick → accents → ponctuation) |
| `icy_cache_store.py` | Store de cache SQLite |
| `mcp_server.py` | Couche MCP historique (SSE 8081 / Streamable HTTP 8084 / stdio) |
| `mcp_gateway.py` | Façade MCP de la gateway |
| `mcp_stdio_bridge.py` | Pont stdio |
| `tool_proxy.py` | Proxy d'outils MCP avec cache par outil + garde anti-boucle |
| `prefix_proxy.py` | Proxy additif de cache de préfixe (DB séparée `db/prefix.db`) |
| `upstream_utils.py` | Helpers upstream |

### 2.3 Routage de la gateway [lu]

L'upstream se détermine, par ordre de priorité :
1. préfixe d'URL `/<upstream>/v1/...`
2. header `X-IcyCache-Upstream`
3. champ `"upstream"` dans le body
4. `gateway.default_upstream` de la config — **explicite, jamais de fallback code** : si absent, le
   client doit nommer son upstream (erreur 400).

`sticky_upstream: true` est activé : l'upstream retenu est mémorisé pour la session.

## 3. Configuration effective [lu]

Fichier lu par la gateway : `config/icycache.gateway.json` (surchargeable par la variable
d'environnement `ICYCACHE_CONFIG`, que `server.py` ré-exporte pour `icy_core.py`).

| Section | Valeurs |
|---|---|
| `proxy` | `127.0.0.1:8080` |
| `mcp` | `127.0.0.1:8081` |
| `mcp_gateway` | `127.0.0.1:8083` |
| `gateway` | `127.0.0.1:8082`, `default_upstream: omniroute`, `ws_delta_interval_s: 0.25`, `sticky_upstream: true` |
| `cache` | `db_path: D:\IcyCache\db\cache.db`, `max_entries: 50000`, `enable_semantic: false`, `semantic_threshold: 0.95`, `ttl_seconds: 604800` (7 j) |
| `tool_cache` | `enabled: true`, `default: allow`, `max_result_bytes: 10485760` (10 Mio) — 12 serveurs descendants avec TTL par serveur |
| `loop_guard` | `enabled: true`, `window_seconds: 300`, `warn_after: 3`, `block_after: 5` |
| `normalization` | règles externes dans `config/normalization_rules.json` — **1111 règles** (1114 avant le commit `3794a93`) |
| `logging` | `INFO`, `D:\IcyCache\logs\app.log`, rotation 10 Mio |

### 3.1 Upstreams — 18 [lu]

`omniroute` (défaut), `lmstudio`, `ollama`, `openai`, `anthropic`, `nvidia`, `groq`, `cerebras`,
`openrouter`, `kilo`, `nous`, `copilot`, `ai-gateway`, `huggingface`, `google-aistudio`, `llm7io`,
`mistral`, `cloudflare`.

`google-aistudio` utilise le flag `strip_v1` (géré par `target_url()` dans `src/server.py`) car sa base
se termine déjà par `/v1beta/openai`.

### 3.2 ⚠️ Config morte à nettoyer [lu]

`config/icycache.gateway.json` contient **deux blocs `cache`** :

| Emplacement | `max_entries` | `enable_semantic` | `ttl_seconds` | Lu par le code ? |
|---|---|---|---|---|
| `cache` (racine) | 50000 | `false` | 604800 | **Oui** — `CACHE_CFG = CONFIG.get("cache", {})` (`src/server.py:82`) |
| `gateway.cache` (imbriqué) | 200000 | `true` | 2592000 | **Non** — aucun lecteur de `gateway.cache` |

Le bloc `gateway.cache` **ne fait rien** : c'est lui qui porte le seuil sémantique `0.80` et le TTL de
30 jours, mais il n'est jamais lu. Le même décalage existe dans `config/icycache.json`
(`max_entries: 200000`, `enable_semantic: true`, `ttl: 2592000`), fichier lu par `icy_core.py`,
`mcp_server.py` et `tool_proxy.py` — mais dont le bloc `cache` racine n'est pas consommé par la gateway.

**Conséquence : le cache sémantique annoncé dans le README est désactivé à l'exécution**
(`enable_semantic: false` dans le bloc effectivement lu). À trancher : soit faire lire
`gateway.cache`, soit supprimer le bloc imbriqué et aligner le README.

## 4. État des tests — **ALL PASS** [mesuré]

```
cd D:\IcyCache
venv\Scripts\python.exe tests\run_isolated.py
```

→ `EXIT test_cacheforge.py=0` · `EXIT test_cacheforge_ext.py=0` · `ISOLATED: ALL PASS` ·
`PORTS LIBRES apres teardown: True`

Le runner monte un **faux upstream** sur `:8191` et un proxy de test sur `:8190`, avec une base
temporaire : ni la DB de prod ni LM Studio ne sont touchés.

### 4.1 Ce qui est couvert

| Suite | Assertions | Résultat |
|---|---|---|
| `test_cacheforge.py` | métriques, hit rate, dashboard, events | ALL PASS |
| `test_cacheforge_ext.py` | erreurs 400 relayées, TTL/peremption, `finish_reason`, `tool_calls`, SSE, stream interrompu, export, `cache-clear` protégé, normalisation (P3a/P3b/P3c) | ALL PASS |
| Normalisation | 1010 règles testées, aucune mutation de sous-chaîne | ALL PASS |

### 4.2 Ce qui n'est **pas** couvert [lu]

| Fichier | Statut |
|---|---|
| `tests/test_gateway.py` | **Hors runner** — jamais exécuté par `run_isolated.py` |
| `tests/test_prefix_proxy.py` | **Hors runner** — jamais exécuté |
| `tests/granite_live_test.py` | Test live (upstream réel), exclu volontairement |
| `tests/run_isolated.py` | Ne boucle que sur `test_cacheforge.py` et `test_cacheforge_ext.py` |

Un `pytest` direct **échoue** : `tests/test_cacheforge.py` appelle `sys.exit()` si `CF_BASE` est
absent (« Lancer via tests\run_isolated.py »). Le passage par le runner est obligatoire.

## 5. Chronologie depuis le 2026-10-22 [lu]

| Commit | Date | Sujet |
|---|---|---|
| `d676b7f` | 2026-10-22 | Fix exposition MCP + doc à jour |
| `f6c5bc6` | 2026-10-23 | Dashboard : nœuds vivants, jauge électrique, palette cyberpunk |
| `af9ec4f` | 2026-10-23 | Rebrand CacheForge → IcyCache |
| `7c25dee` | 2026-10-23 | Ajout de `glama.json` (auto-découverte) |
| `d837da8` | 2026-10-24 | Gateway universelle + modules IcyCache + refonte dashboard |
| `f662705` | 2026-10-24 | README traduit en anglais + bandeau d'avertissement |
| `e9d3a13` | 2026-10-24 | Guide de configuration complet + rangement archive + captures dashboard |
| `0957112` | 2026-10-24 | chore : `.gitignore` élargi, artefacts de build retirés du suivi |
| `2933a8f` | 2026-10-25 | Sync sync |
| `3794a93` | 2026-10-25 | feat : gateway multi-upstream, cache sémantique, entrypoint de warm-up |

### 5.1 Contenu de `3794a93` (dernier push)

- **Gateway** : `sticky_upstream` activé ; 13 nouveaux upstreams.
- **Tool cache** : `default` passé de `deny` à `allow`, `max_result_bytes` de 1 Mio à 10 Mio ;
  12 serveurs descendants enregistrés avec une policy de TTL par serveur.
- **Cache** : `max_entries` 200000, `enable_semantic` `true` (seuil 0.80), TTL 30 j — **dans le bloc
  `gateway.cache`, non lu** (voir §3.2) ; `window_seconds` du tool cache 300 → 1800.
- **Serveur** : support du flag `strip_v1` dans `target_url()` ; `/health` expose désormais `service`
  et `project_root`.
- **Normalisation** : suppression de 3 règles trop larges (`right`, `cd`, `so`) — 1114 → 1111 règles.
- **Nouveaux fichiers** : `entrypoint.sh` (serveur + warm-up en arrière-plan) et
  `warmup_cache.py` ( amorce le cache depuis les prompts système/template d'Hermes).
- **Chore** : `scratch/` ajouté au `.gitignore` (copies isolées, DB, logs, captures).

## 6. Écarts entre le README et le code [lu]

| Sujet | README | Code réel |
|---|---|---|
| Nombre d'upstreams | « 5 upstreams » (bandeau du 2026-10-24) | **18** |
| Cache sémantique | présenté comme actif | `enable_semantic: false` dans le bloc lu (§3.2) |
| Ports | « gateway 8080, SSE 8081, HTTP 8084 » | conforme, mais `mcp_gateway` (8083) et `gateway` (8082) ne sont pas documentés |
| `entrypoint.sh`, `warmup_cache.py` | non mentionnés | présents depuis `3794a93` |

## 7. Sécurité et secrets [lu]

- Aucune clé en clair dans le dépôt : `api_key` est `null`, et les secrets sont référencés par
  `@hermes:mcp_servers.<nom>` (résolus à l'exécution par le mécanisme de secrets d'Hermes).
- Scan de secrets passé sur les 8 fichiers modifiés + ajoutés : aucune fuite.
- **Chemins absolus codés en dur** dans les configs et les scripts : `D:\IcyCache\db\cache.db`,
  `D:\IcyCache\logs\app.log`, `D:\IcyCache\prompts.txt`,
  `C:\Users\Tech\AppData\Local\hermes\config.yaml` (`warmup_cache.py`),
  `D:/Loom/.venv/Scripts/loom-mcp.exe` (downstream du tool cache).
  → Le repo est public : ces chemins sont des informations propres à une machine locale, pas des
  secrets, mais ils rendent le projet non portable tel quel.
- `tool_cache.default` est passé à `allow` : tout outil exposé est désormais cachable par défaut.
  `max_result_bytes` passe à 10 Mio — à surveiller côté mémoire.

## 8. Points de vigilance

1. **`gateway.cache` n'est pas lu** (§3.2) : le cache sémantique est inactif à l'exécution malgré
   l'annonce du commit et du README.
2. **`tests/test_gateway.py` et `tests/test_prefix_proxy.py` ne sont jamais exécutés** — la gateway,
   qui est le cœur du projet, n'est pas couverte par la suite verte.
3. `pytest` direct est cassé (`SystemExit` sans `CF_BASE`) : seule la commande `run_isolated.py` est
   fonctionnelle.
4. Chemins absolus et noms d'utilisateur codés en dur dans les fichiers versionnés.
5. `scratch/` contient une copie isolée complète de l'application (DB de 4,7 Mo, logs, captures
   `.bak`) — désormais ignoré par git, mais il reste présent sur le disque.
6. `CacheForge_Project_Documentation.docx` (40 Ko) est antérieur au rebrand et au gateway ;
   `start_cacheforge.ps1` et `start_icycache.ps1` coexistent.

## 9. Prochaines étapes proposées

1. Trancher le sort du bloc `gateway.cache` : soit le faire lire par `server.py` (cache sémantique
   réellement actif), soit le supprimer et corriger le README.
2. Intégrer `test_gateway.py` et `test_prefix_proxy.py` au runner isolé — la gateway doit être
   couverte par la suite verte, pas seulement la couche historique.
3. Rendre le runner utilisable via `pytest` (gestion propre de `CF_BASE`) ou documenter la commande
   unique dans le README.
4. Externaliser les chemins absolus (variables d'environnement ou chemins relatifs au `PROJECT_ROOT`),
   notamment dans `warmup_cache.py` et `config/icycache.gateway.json`.
5. Régénérer ou retirer `CacheForge_Project_Documentation.docx` et le script `start_cacheforge.ps1`.
6. Documenter les ports 8082 / 8083 dans le README.
