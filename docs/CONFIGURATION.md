# IcyCache -- Guide de configuration

> **But** : faire passer TOUT le trafic LLM d'une machine par IcyCache, pour que les reponses deja calculees soient resserties instantanement au lieu d'etre regenerees.
>
> **Regle unique** : tout ce qui sort d'un client et revient dans ce client passe par IcyCache.
> Si un seul client parle directement a un fournisseur, le cache est casse pour ce client.

---

## 1. Le principe en 30 secondes

```
AVANT   Client (Hermes, VS Code, Claude Code, script, IDE...)
            |
            +-----> https://api.openai.com        (chaque appel = facture + latence)

APRES   Client
            |
            +-----> http://127.0.0.1:8080  (IcyCache)
                        |
                        +--> HIT  : reponse servie depuis SQLite (~30 ms)
                        +--> MISS : relais vers l'upstream reel, puis sauvegarde
```

Le client ne change **qu'une seule chose** : son `base_url`. Tout le reste (API key, modele, format) reste identique, parce qu'IcyCache parle nativement l'API OpenAI (`/v1/chat/completions`) et l'API Anthropic (`/v1/messages`).

---

## 2. Pre-requis : demarrer IcyCache

IcyCache doit ecouter avant que les clients ne l'appellent.

```powershell
cd D:\IcyCache
.\start_icycache.ps1          # demarrage manuel
.\install_autostart.ps1       # demarrage automatique au login (recommande)
```

Verification que les ports sont bien ouverts :

```powershell
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/api/status
```

| Port | Transport | Usage |
|------|-----------|-------|
| **8080** | HTTP OpenAI/Anthropic | **le seul port a configurer dans les clients** |
| 8081 | MCP SSE (legacy) | clients MCP anciens |
| 8083 | MCP gateway | facade MCP de la gateway |
| 8084 | MCP Streamable HTTP | clients MCP recents (`http://127.0.0.1:8084/mcp`) |

---

## 3. Choisir un upstream (le routage)

Le meme port 8080 peut servir plusieurs fournisseurs. Quatre methodes, par ordre de priorite :

| Priorite | Methode | Exemple |
|----------|---------|---------|
| 1 | Prefixe d'URL | `POST http://127.0.0.1:8080/lmstudio/v1/chat/completions` |
| 2 | En-tete HTTP | `X-IcyCache-Upstream: lmstudio` |
| 3 | Corps JSON | `{"upstream": "lmstudio", "model": "...", "messages": [...]}` |
| 4 | Defaut de config | `gateway.default_upstream` dans `config/icycache.gateway.json` |

**Aucun defaut implicite dans le code.** Si aucune source n'est fournie et que `default_upstream` vaut `null`, la requete est refusee en `400` avec la liste des upstreams disponibles. Un upstream inconnu est refuse en `404`.

Upstreams declares par defaut dans `config/icycache.gateway.json` :

| Nom | Base URL | Type |
|-----|----------|------|
| `omniroute` | `http://localhost:20128` | openai |
| `lmstudio` | `http://localhost:1234` | openai |
| `ollama` | `http://localhost:11434` | openai |
| `openai` | `https://api.openai.com` | openai |
| `anthropic` | `https://api.anthropic.com` | anthropic |

Pour en ajouter un, editer la section `upstreams` :

```json
"upstreams": {
  "groq": { "base_url": "https://api.groq.com/openai", "kind": "openai" }
}
```

---

## 4. Configurer chaque type de client

### 4.1 Regle generale (tout client compatible OpenAI)

**Remplacer** `base_url` / `OPENAI_BASE_URL` / `openai_base_url` par :

```
http://127.0.0.1:8080/v1
```

Et laisser l'API key telle quelle (IcyCache la transmet a l'upstream).

---

### 4.2 Variables d'environnement (le plus universel)

La plupart des SDK et CLI Python/Node lisent ces variables :

```bash
# bash / zsh
export OPENAI_BASE_URL="http://127.0.0.1:8080/v1"
export OPENAI_API_BASE="http://127.0.0.1:8080/v1"     # clients plus anciens
export ANTHROPIC_BASE_URL="http://127.0.0.1:8080"
```

```powershell
# PowerShell
$env:OPENAI_BASE_URL     = "http://127.0.0.1:8080/v1"
$env:OPENAI_API_BASE     = "http://127.0.0.1:8080/v1"
$env:ANTHROPIC_BASE_URL  = "http://127.0.0.1:8080"
```

Pour rendre ca permanent sous Windows :

```powershell
setx OPENAI_BASE_URL "http://127.0.0.1:8080/v1"
setx ANTHROPIC_BASE_URL "http://127.0.0.1:8080"
# setx n'affecte PAS la session en cours : rouvrir le terminal
```

---

### 4.3 Hermes Agent

Hermes declare ses fournisseurs dans `config.yaml` (section `providers` + `model`).

```yaml
model:
  provider: icycache
  base_url: http://127.0.0.1:8080/v1
  default: hermes-free
  api_mode: chat_completions
  key_env: HERMES_CUSTOM_OMNIROUTE_API_KEY

providers:
  icycache:
    name: icycache
    base_url: http://127.0.0.1:8080/v1
    key_env: HERMES_CUSTOM_OMNIROUTE_API_KEY
    model: hermes-free
    default_model: hermes-free
    discover_models: false
    models:
      hermes-free: {}
    request_timeout_seconds: 120
    stale_timeout_seconds: 120
```

Puis, pour que le changement soit pris en compte :

```powershell
hermes gateway restart --yes
```

> **Note** : le gateway refuse de redemarrer s'il reste des sessions actives.
> `--yes` les interrompt volontairement.

---
### 4.4 Claude Desktop

Fichier : `%APPDATA%\Claude\claude_desktop_config.json`

Claude Desktop parle a Anthropic. Deux options :

**Option A -- IcyCache en service MCP (tools)** :

```json
{
  "mcpServers": {
    "icycache": {
      "command": "D:\\IcyCache\\venv\\Scripts\\python.exe",
      "args": ["D:\\IcyCache\\src\\mcp_stdio_bridge.py", "--mode", "mcp-stdio"]
    }
  }
}
```

**Option B -- Streamable HTTP** (clients MCP recents) :

```json
{
  "mcpServers": {
    "icycache": { "url": "http://127.0.0.1:8084/mcp" }
  }
}
```

> Claude Desktop ne permet pas de detourner son `base_url` Anthropic : il ne beneficiera pas du cache LLM, seulement des outils MCP d'observabilite (`get_cache_stats`, `proxy_tool`, ...).

---

### 4.5 Cursor / VS Code (extensions OpenAI-compatible)

Dans les parametres de l'extension (ex. Continue, Cline, Roo Code) :

```json
{
  "models": [
    {
      "title": "IcyCache",
      "provider": "openai",
      "model": "hermes-free",
      "apiBase": "http://127.0.0.1:8080/v1",
      "apiKey": "none"
    }
  ]
}
```

Cline / Roo Code (Settings > API Provider > OpenAI Compatible) :
- **Base URL** : `http://127.0.0.1:8080/v1`
- **API Key** : n'importe quelle chaine non vide (ex. `icycache`)
- **Model ID** : le modele de l'upstream

---

### 4.6 Ollama / LM Studio en tant que clients

Ces outils sont normalement des **upstreams**, pas des clients. Mais leur propre UI peut pointer vers IcyCache pour profiter du cache :

- **Ollama** : `OLLAMA_HOST=http://127.0.0.1:8080`
- **SDK OpenAI Python** :

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="none")
```

---

### 4.7 Modeles **directement** (sans client intermediaire)

Pour router des modeles en direct (scripts, notebooks, autres agents) :

**Python -- SDK OpenAI** :
```python
import os
os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:8080/v1"

from openai import OpenAI
r = OpenAI().chat.completions.create(
    model="hermes-free",
    messages=[{"role": "user", "content": "Explique Hyper-V"}],
    temperature=0,
)
```

**Appel HTTP brut (curl)** :
```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"hermes-free","temperature":0,"messages":[{"role":"user","content":"Explique Hyper-V"}]}'
```

**Forcer un upstream precis** :
```bash
# par prefixe d'URL
curl -s http://127.0.0.1:8080/lmstudio/v1/chat/completions -d '{...}'

# par en-tete
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'X-IcyCache-Upstream: ollama' -d '{...}'

# par corps
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -d '{"upstream":"openai","model":"gpt-4o-mini","messages":[...]}'
```

---

## 5. Ce qui est mis en cache (et ce qui ne l'est jamais)

| Condition | Resultat |
|-----------|----------|
| Requete identique apres normalisation + `temperature=0` | **HIT** (~30 ms, id `chatcmpl-cache-*`) |
| `temperature` non defini et `cache_when_temperature_unset: true` | mis en cache |
| `temperature > 0` | **MISS** systematique (reponse non deterministe) |
| Erreur MCP / erreur upstream | **jamais mis en cache** |
| Outil d'ecriture (`create_`, `write_`, `delete_`, `push_`) | **jamais mis en cache** |

**Regle d'or** : `temperature=0` partout ou tu veux des hits de cache.

TTL par defaut : `86400` s (24 h). Taille max : 10 000 entrees.

---

## 6. Verifier que ca marche (preuve en 3 lignes)

```bash
# 1er appel : MISS (aller-retour reel vers l'upstream)
# 2e appel : HIT (servi par SQLite)
```

**Test Python** :
```python
import json, urllib.request, time

URL = "http://127.0.0.1:8080/v1/chat/completions"

def call(tag):
    body = {"model": "hermes-free", "temperature": 0,
            "messages": [{"role": "user", "content": "What is 2+2?"}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode())
    print(f"{tag}: {time.time()-t:.2f}s id={d['id']}")

call("CALL-1")   # MISS attendu
call("CALL-2")   # HIT attendu (id commence par chatcmpl-cache-)
```

**Sortie attendue** :
```
CALL-1: 2.70s id=chatcmpl-6e6e4dc2-...      <- MISS
CALL-2: 0.03s id=chatcmpl-cache-bd7480b3    <- HIT (90x plus rapide)
```

Le prefixe `chatcmpl-cache-` dans l'`id` est la **preuve** que la reponse vient du cache.

---

## 7. Observabilite

| Route | Role |
|-------|------|
| `GET /health` | sante du service |
| `GET /api/status` | upstreams joignables |
| `GET /api/metrics` | entrees, hits, misses, tokens sauves |
| `GET /api/metrics/export` | export complet |
| `WS  /ws/events` | evenements temps reel (deltas + tokens reels) |
| `GET /dashboard` | dashboard web lecture seule |

Vider le cache (operation destructive, confirmation obligatoire) :
```bash
curl -X POST http://127.0.0.1:8080/api/cache-clear \
  -H 'X-IcyCache-Confirm: FORCE_DELETE'
```

---

## 8. Depannage

| Symptome | Cause probable | Correctif |
|----------|----------------|-----------|
| `Connection refused` | IcyCache n'ecoute pas | lancer `start_icycache.ps1`, verifier `/health` |
| `400 No upstream specified` | aucun upstream nomme, `default_upstream=null` | ajouter prefixe URL, header, ou definir `default_upstream` |
| `404 Unknown upstream` | nom absent de la section `upstreams` | ajouter l'upstream dans `config/icycache.gateway.json` |
| Toujours des MISS | `temperature > 0`, ou prompt different a chaque appel | forcer `temperature=0` |
| Le client ignore `base_url` | variable d'env ecrasee par un fichier de config local | verifier la precedence : fichier client > variable d'env |
| Hits = 0 apres 24 h | TTL expire | normal ; augmenter `cache.ttl_seconds` |
| Le client joint le fournisseur en direct | contournement du proxy | voir section 9 (enforcement) |

Logs : `D:\IcyCache\logs\app.log`

---

## 9. Enforcement : rendre le contournement impossible (optionnel)

Le cache ne sert a rien si un client peut joindre l'upstream en direct. Deux scripts fournis :

```powershell
# 1. Installer les regles de pare-feu (PowerShell ELEVE)
powershell -ExecutionPolicy Bypass -File tools\enforce_gateway.ps1 -Action apply `
    -TargetProgram "C:\chemin\vers\MonClient.exe" `
    -AllowPort 8080 -BlockPorts 20128,1234,11434

# 2. Prouver que le client ne peut plus joindre un upstream en direct
python tools\verify_enforcement.py --program "D:\IcyCache\venv\Scripts\python.exe" --allow-port 8080
```

Strategie : blocage des ports upstream **y compris sur 127.0.0.1**, blocage du trafic distant, autorisation explicite de `127.0.0.1:8080`.

> Les regles sont **par programme** : lancer `verify_enforcement.py` avec le binaire du client a tester.

---

## 10. Variables d'environnement d'IcyCache

| Variable | Role | Ancien nom |
|----------|------|------------|
| `ICYCACHE_DB` | chemin de la DB de cache | `CACHEFORGE_DB` |
| `ICYCACHE_LOG` | chemin du log | `CACHEFORGE_LOG` |
| `ICYCACHE_CONFIG` | fichier de config gateway | -- |
| `ICYCACHE_TARGET` | surcharge de l'upstream (tests) | `CACHEFORGE_TARGET` |
| `ICYCACHE_TOOLPROXY_CONFIG` | config du proxy d'outils | -- |

---

## 11. Checklist de deploiement

- [ ] IcyCache demarre et `curl http://127.0.0.1:8080/health` repond
- [ ] Upstreams necessaires declares dans `config/icycache.gateway.json`
- [ ] `default_upstream` defini (ou chaque client nomme son upstream)
- [ ] Chaque client pointe sur `http://127.0.0.1:8080/v1`
- [ ] Test MISS puis HIT concluant (id `chatcmpl-cache-*`)
- [ ] `temperature=0` pour les flux qui doivent beneficier du cache
- [ ] Autostart installe (`install_autostart.ps1`)
- [ ] Enforcement applique si le contournement doit etre impossible
- [ ] Dashboard accessible : `http://127.0.0.1:8080/dashboard`
