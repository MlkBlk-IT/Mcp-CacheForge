# CacheForge — PROGRESS

Généré le 2026-10-22 à partir de : listing du dossier, logs (`logs/`), diffs des `src/server.py.bak*` (horodatés), `patch_20261022.py`, `config/cacheforge.json`.
Rien n'a été exécuté pour produire ce fichier (lecture seule). Les points marqués **[non vérifié]** n'ont pas de preuve runtime.

## 1. Architecture (lu dans le code/config)
- Proxy HTTP FastAPI `127.0.0.1:8080` -> upstream `http://localhost:1234/v1/chat/completions` (LM Studio), timeout 120 s
- MCP `127.0.0.1:8081` (SSE) + `mcp_stdio.py` (wrapper stdio pour Claude Desktop, n'ouvre aucun port)
- Cache SQLite `db/cache.db`, TTL 86400 s, max 10000 entrées, `enable_semantic: false`
- Normaliseur : `config/normalization_rules.json` (1114 règles, générées 2026-10-21 22:35) via Aho-Corasick + stemmers NLTK
- `tray.py` (icône systray, démarre proxy + MCP), scripts `install_autostart.ps1` / `uninstall_autostart.ps1` / `restart_tray.ps1` / `start_cacheforge.ps1`
- `src/prefix_proxy.py` : proxy additif port 8090 (cache de préfixe Anthropic `cache_control`, OpenAI `prompt_cache_key`), stats `GET /api/prefix-stats`, DB séparée `db/prefix.db`
- Dashboard `static/dashboard.html` + `/api/metrics`, `/api/metrics/export`, `/api/cache-clear` (exige header `X-CacheForge-Confirm`)

## 2. Chronologie reconstituée (horodatage des fichiers .bak)
Un `.bak` = état de `server.py` avant l'édition suivante. Heures = mtime fichier.

| Date/heure | Fichier | Taille | Changement vers la version suivante (diff) |
|---|---|---|---|
| 21/10 18:57 | `fix.log` | – | Install : venv Python 3.12.10, deps pinnées, DB, config, `start_cacheforge.ps1` |
| 21/10 22:35 | `normalization_rules.json` | 125 KB | Règles générées (1114) |
| 21/10 23:01–23:09 | bak, bak2, bak3, bak4 | 15323 B | Identiques entre eux (0 ligne de diff) |
| 21/10 23:42 | bak5 | 15973 B | Clé de cache = sha256 d'un payload (model, system, messages user, temperature, tools) ; 400 si pas de message user |
| 21/10 23:44 | bak6 | 16787 B | `stream` ajouté à la clé ; réponse HIT reformatée en `chat.completion` complet |
| 21/10 23:46 | bak7 | 16916 B | Cache du `message` complet (JSON) au lieu du seul `content` |
| 21/10 23:50 | bak8 | 17510 B | Bypass cache si `temperature != 0` (log `CACHE BYPASS`) |
| 21/10 23:54 | bak9 | 16949 B | Outil MCP `set_target_provider` **supprimé** (raison inconnue) |
| 22/10 00:00 | bak10 | 17478 B | Fix stream : `full_response`, `is_stream=True`, skip du cache si non déterministe |
| 22/10 00:09 | bak11 | 21694 B | Rotation TTL + max_entries ; middleware token API `verify_api_token` ajouté |
| 22/10 00:36 | bak12 | 21181 B | Middleware token API **retiré** ; `[DONE]` exclu de l'accumulation stream |
| 22/10 00:51 | bak13 | 23363 B | Réécriture `NormalizationPipeline` (règles JSON). `dashboard.html.bak1` créé |
| 22/10 01:12 | bak14 | 24483 B | Routes `/dashboard`, `/api/dashboard-stats`, `/api/cache-clear` ; `tray.py.bak1` à 01:07 |
| 22/10 02:03 | bak15 | 22858 B | Pipeline NFKD -> lowercase -> Aho-Corasick -> strip accents -> cleanup |
| 22/10 02:40 | bak16 | 24922 B | Version intermédiaire du normaliseur (retour à un état proche de bak14, fallback regex modifié) |
| 22/10 02:57 | bak17 | 25925 B | `import time` global, usage/`gen_time_ms` sauvegardés (`_v: 2`), âge du cache, timezone UTC |
| 22/10 03:01 | bak18 | 25948 B | Dashboard : `datetime(created_at,'localtime')` |
| 22/10 03:14 | bak19 | 31616 B | Table `events` + `log_event`, `/api/metrics`. `dashboard.html.bak2` 03:15 |
| 22/10 03:48 | `server.py` actuel | 37681 B | `patch_20261022.py` : env `CACHEFORGE_DB/LOG/TARGET`, normaliseur à frontières de mot (`_isw`), `generate_and_cache(client, response, t_fwd)`, `/api/metrics/export`, `cache-clear` protégé |
| 22/10 03:47–04:28 | tests | – | `run_isolated.py`, `test_cacheforge_ext.py`, `test_prefix_proxy.py` (04:28) |

## 3. Bugs observés dans `tray.log` (et statut)
- `HTTP 400` upstream LM Studio à 01:12 et 02:08 -> exception ASGI non gérée à l'époque ; aujourd'hui E1/E2 (400 relayé status+body) passent dans `isolated_run.log`
- `UnboundLocalError: time` (`server.py` l.467/l.508) entre 02:20 et 02:48 ; plus d'occurrence après 02:57 (cohérent avec `import time` global dans bak17). **[non vérifié dans le code actuel]**
- Normaliseur qui mutait des sous-chaînes (ex. "certificate authorityche", "quser interface") entre 02:08 et 03:26 ; corrigé par le patch (P3a/P3b/P3c PASS)

## 3bis. Preuves runtime disponibles
- `logs/isolated_run.log` : `test_cacheforge.py` ALL PASS, `test_cacheforge_ext.py` ALL PASS, ports libres après teardown, stub :8191 / proxy :8190 (DB et logs dans un dossier temp)
- `logs/test_run.log` : run sur l'instance réelle, ALL PASS (METRICS : 38 req, 18 hits, hit rate 47.4 %, 31.0 % tokens économisés)
- `logs/app.log` : dernière activité 09:03 (requêtes MCP `ListToolsRequest`), tray lancé 07:51

## 4. Non couvert / à valider
- `test_prefix_proxy.py` (04:28) : **aucun log de résultat trouvé** ; `logs/prefix_proxy.log` existe mais n'a pas été lu **[non vérifié]**
- `test_cacheforge.py` et `test_cacheforge_ext.py` n'incluent pas `test_prefix_proxy.py` dans `isolated_run.log`
- `granite_live_test.py` / `logs/granite_live.log` : non lus
- `CacheForge_Project_Documentation.docx` : non lu (peut être périmé vs code actuel)
- `.git` : dépôt initialisé, `refs/heads` vide -> a priori aucun commit **[non vérifié, `git` non exécuté]**
- `.gitignore` exclut `*.bak*` et `fix_*.py` : l'historique des bak ne serait pas versionné

## 5. Incohérences constatées
- `config/cacheforge.json` : `mcp.host = 0.0.0.0` alors que le tray démarre MCP en `127.0.0.1` (tray.log 07:51)
- `config/cacheforge.json` : bloc `normalization.canonical_synonyms` (ancien système) probablement mort depuis `normalization_rules.json` **[non vérifié]**
- `config/cacheforge.json.bak1` (00:09, 866 B) vs actuel (03:48, 868 B) : 2 octets d'écart, contenu non comparé
- Chemins codés `D:\CacheForge` vs dossier `D:\CACHEFORGE` : OK sous Windows (insensible à la casse)
- Middleware token API ajouté (bak11) puis retiré (bak12) : `proxy.api_token` n'est donc plus appliqué ; `0.0.0.0` côté MCP l'expose potentiellement sur le LAN si le tray n'écrase pas la valeur **[non vérifié]**
- Outil MCP `set_target_provider` supprimé en bak9 : le changement de provider ne se fait plus que par édition de config

## 6. Fichiers de travail (hors runtime)
`fix_*.py`, `patch_*.py`, `audit_*.py`, `deploy_exhaustive.py`, `rewrite_normalizer.py`, `update_pipeline.py`, `show_*.py`, `find_*.py`, `add_debug_log.py`, `check_normalizer_class.py`, `generate_word_doc.py` : scripts one-shot de patch/diagnostic déjà exécutés, conservés tels quels.
