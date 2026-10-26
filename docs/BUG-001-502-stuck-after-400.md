# BUG-001 — IcyCache 502 stuck après 400 upstream

**Date**: 2026-10-26
**Sévérité**: High
**Statut**: Open
**Composant**: `src/mcp_gateway.py` (gestion erreur upstream)
**Découvert par**: test automatisé IcyCache (3-size probe)

## Symptôme

Quand l'upstream (LM Studio) retourne `HTTP 400` (ex: prompt trop long pour le context window), IcyCache laisse sa connexion upstream dans un état cassé. **Tous les appels suivants** — même avec des prompts valides — retournent :

```
HTTP 502
{
  "error": {
    "message": "upstream unreachable: ",
    "type": "upstream_error",
    "upstream": "lmstudio"
  }
}
```

Avec un timeout de **~120s** par appel (le client attend pendant 2 minutes avant de recevoir le 502).

## Reproduction

1. Démarrer LM Studio + IcyCache (proxy :8080, upstream `lmstudio` → :1234)
2. Envoyer un prompt qui dépasse le context window du modèle loadé :
   ```bash
   curl -X POST http://127.0.0.1:8080/lmstudio/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"model":"qwen3.5-9b-anwar-hermes","messages":[{"role":"user","content":"<11000 tokens>"}],"max_tokens":300}'
   ```
   → LM Studio retourne 400 (normal)
3. Envoyer un prompt valide court :
   ```bash
   curl -X POST http://127.0.0.1:8080/lmstudio/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{"model":"qwen3.5-9b-anwar-hermes","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
   ```
   → **502 "upstream unreachable"** + 120s timeout (BUG)
4. Un appel **direct** à LM Studio (`:1234`) fonctionne → preuve que LM Studio est OK, c'est IcyCache qui est stuck.

## Workaround temporaire

Envoyer un appel direct à LM Studio (`http://127.0.0.1:1234/...`) → reset la connexion upstream côté IcyCache. Les appels suivants via IcyCache refonctionnent.

## Cause probable (hypothèse à vérifier dans le code)

- La gestion d'erreur dans `mcp_gateway.py` (ou `prefix_proxy.py`) attrape le 400 de l'upstream mais **ne ferme pas / ne reset pas** la connexion HTTP keep-alive vers LM Studio.
- La connexion reste dans le pool httpx/urllib3 dans un état "en erreur" → les requêtes suivantes réutilisent cette connexion morte → timeout 120s → 502.
- Le message `"upstream unreachable: "` (note l'espace vide après `unreachable:`) suggère que l'exception d'origine (ConnectionError / RemoteProtocolError) a un message vide, donc l'error handler ne capture pas le vrai détail.

## Fichiers à inspecter

- `src/mcp_gateway.py` — handler proxy, lignes ~900-1140 (où `uvicorn.run` + gestion upstream)
- `src/prefix_proxy.py` — si le prefix `/lmstudio/` route via un handler séparé
- Chercher : `except`, `HTTPError`, `upstream unreachable`, `httpx.AsyncClient`, `keepalive`, `connection pool`

## Fix suggéré (à valider ensemble)

1. Sur tout `4xx` / `5xx` de l'upstream : **fermer explicitement** la connexion (`response.close()` ou `aclose()` si async).
2. Ne pas réutiliser une connexion qui a reçu un 4xx/5xx — la marquer comme stale dans le pool.
3. Inclure le `status_code` et le `response.text` (tronqué) dans le message d'erreur 502 au lieu d'un espace vide :
   `f"upstream unreachable: {upstream} returned {status}: {body[:200]}"`
4. Ajouter un timeout upstream explicite (ex: 60s) au lieu de laisser le client attendre 120s.
5. Test de régression : après un 400 forcé, un appel valide doit réussir immédiatement.

## Données du test (preuve)

```
TEST LARGE (prompt 11000 tokens, > 8k ctx)
  CALL 1 (cold): HTTP 400 → LM Studio refuse (normal)
  CALL 2 (warm): HTTP 502 → upstream unreachable (BUG)
  → Tous les appels suivants = 502 + 120s timeout
  → Fix temporaire: appel direct à :1234 → reset → ça refonctionne
```

## Contexte matériel

- LM Studio 0.4.26, model `qwen3.5-9b-anwar-hermes` (9B), context window 8k
- IcyCache v1.30.0, proxy :8080, MCP :8081, upstream `lmstudio` → :1234
- CPU-only (Dell Latitude 5420, i7-1185G7, 32 GB RAM)
