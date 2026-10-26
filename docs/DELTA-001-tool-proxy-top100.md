# DELTA-001 — Top 100 outils agents IA pour IcyCache

**Statut : PROPOSÉ — NON APPLIQUÉ.** Date des vérifications : 2026-10-25 (npm, PyPI, docs OpenAI et Anthropic).
**Cible : `config/icycache.json` → `tool_proxy.servers`** (section MCP) · outils natifs = hors config (section native).
Placeholders `<...>` laissés tels quels, à remplir plus tard. Repo public : mettre les secrets en `@hermes:mcp_servers.<nom>`, jamais en clair.
Tous les serveurs ajoutés en `"cache": false` (à affiner plus tard avec `cache_tools`).

---

## A. Outils natifs OpenAI (1-15) — passent dans `tools[]` via la gateway, rien à enregistrer

1. `function`
2. `custom`
3. `namespace`
4. `web_search`
5. `web_search_preview`
6. `file_search`
7. `code_interpreter`
8. `image_generation`
9. `computer`
10. `computer_use_preview`
11. `local_shell`
12. `shell`
13. `apply_patch`
14. `tool_search`
15. `mcp`

## B. Outils natifs Anthropic (16-37) — idem

16. `web_search_20260318`
17. `web_search_20260209`
18. `web_search_20250305`
19. `web_fetch_20260318`
20. `web_fetch_20260309`
21. `web_fetch_20260209`
22. `web_fetch_20250910`
23. `code_execution_20260521`
24. `code_execution_20260120`
25. `code_execution_20250825`
26. `advisor_20260301`
27. `tool_search_tool_regex_20251119`
28. `tool_search_tool_bm25_20251119`
29. `mcp_toolset`
30. `memory_20250818`
31. `bash_20250124`
32. `text_editor_20250728`
33. `text_editor_20250124`
34. `computer_toolset_20260801`
35. `computer_20251124`
36. `computer_20250124`
37. `browser_toolset_20260801`

---

## C. Serveurs MCP (38-100) — à ajouter dans `tool_proxy.servers`

### C.1 Déjà présents dans la config actuelle (clé existante, ne pas dupliquer)

| # | Paquet | Clé existante | Action |
|---|---|---|---|
| 42 | `@modelcontextprotocol/server-filesystem` 2.0.1 | `filesystem` | inchangé (pin `@2.0.1` optionnel) |
| 88 | `mcp-server-fetch` 2026.10.24 | `fetch` | inchangé (actuel pin `2026.10.10`, bump optionnel) |
| 54 | `@brave/brave-search-mcp-server` 2.1.4 | `websearch` | **remplacer** `args` actuel (`mcp-server-brave-search@2026.10.10`, 404 sur PyPI) par `["-y","@brave/brave-search-mcp-server@2.1.4"]` avec `"command":"npx"` ; env `BRAVE_API_KEY` déjà en place |

### C.2 Nouvelles entrées (60 serveurs : 38-41, 43-53, 55-87, 89-100)

```json
"playwright": {
  "command": "npx", "args": ["-y", "@playwright/mcp@0.0.83"], "cache": false
},
"chrome-devtools": {
  "command": "npx", "args": ["-y", "chrome-devtools-mcp@1.12.0"], "cache": false
},
"context7": {
  "command": "npx", "args": ["-y", "@upstash/context7-mcp@4.4.4"],
  "env": { "CONTEXT7_API_KEY": "<CONTEXT7_API_KEY>" },
  "cache": false
},
"firebase": {
  "command": "npx", "args": ["-y", "firebase-tools@15.34.1", "mcp"],
  "env": { "GOOGLE_APPLICATION_CREDENTIALS": "<GOOGLE_APPLICATION_CREDENTIALS>" },
  "cache": false
},
"memory": {
  "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory@2.0.0"],
  "env": { "MEMORY_FILE_PATH": "<MEMORY_FILE_PATH>" },
  "cache": false
},
"sequential-thinking": {
  "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking@2.0.0"], "cache": false
},
"everything": {
  "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything@2.0.1"], "cache": false
},
"sentry": {
  "command": "npx", "args": ["-y", "@sentry/mcp-server@0.42.0"],
  "_note": "variables d'env non trouvées dans le README npm",
  "cache": false
},
"supabase": {
  "command": "npx", "args": ["-y", "@supabase/mcp-server-supabase@0.14.0"],
  "_note": "variables d'env non trouvées dans le README npm",
  "cache": false
},
"notion": {
  "command": "npx", "args": ["-y", "@notionhq/notion-mcp-server@2.5.2"],
  "env": { "NOTION_TOKEN": "<NOTION_TOKEN>", "OPENAPI_MCP_HEADERS": "<OPENAPI_MCP_HEADERS>" },
  "cache": false
},
"stripe": {
  "command": "npx", "args": ["-y", "@stripe/mcp@0.3.3"],
  "env": { "STRIPE_SECRET_KEY": "<STRIPE_SECRET_KEY>" },
  "_note": "arguments de lancement non vérifiés (README mentionne la clé)",
  "cache": false
},
"azure": {
  "command": "npx", "args": ["-y", "@azure/mcp@4.0.0-beta.3"],
  "_note": "beta ; arguments de lancement (ex. server start) non vérifiés",
  "cache": false
},
"tavily": {
  "command": "npx", "args": ["-y", "tavily-mcp@0.2.22"],
  "env": { "TAVILY_API_KEY": "<TAVILY_API_KEY>" },
  "cache": false
},
"exa": {
  "command": "npx", "args": ["-y", "exa-mcp-server@3.4.2"],
  "env": { "EXA_API_KEY": "<EXA_API_KEY>" },
  "_note": "nom de variable non trouvé dans le README npm",
  "cache": false
},
"firecrawl": {
  "command": "npx", "args": ["-y", "firecrawl-mcp@3.34.1"],
  "env": { "FIRECRAWL_API_KEY": "<FIRECRAWL_API_KEY>", "FIRECRAWL_API_URL": "<FIRECRAWL_API_URL>" },
  "cache": false
},
"apify": {
  "command": "npx", "args": ["-y", "@apify/actors-mcp-server@0.19.3"],
  "env": { "APIFY_TOKEN": "<APIFY_TOKEN>" },
  "_note": "nom de variable non trouvé dans le README npm",
  "cache": false
},
"gitlab": {
  "command": "npx", "args": ["-y", "@zereight/mcp-gitlab@2.3.11"],
  "_note": "variables d'env non trouvées dans le README npm",
  "cache": false
},
"desktop-commander": {
  "command": "npx", "args": ["-y", "@wonderwhy-er/desktop-commander@0.3.1"], "cache": false
},
"repomix": {
  "command": "npx", "args": ["-y", "repomix@1.18.1", "--mcp"],
  "_note": "flag --mcp non vérifié",
  "cache": false
},
"supergateway": {
  "command": "npx", "args": ["-y", "supergateway@4.2.1", "<ARGS_SUPERGATEWAY>"], "cache": false
},
"mcp-remote": {
  "command": "npx", "args": ["-y", "mcp-remote@0.15.2", "<REMOTE_MCP_URL>"], "cache": false
},
"inspector": {
  "command": "npx", "args": ["-y", "@modelcontextprotocol/inspector@2.12.0"],
  "_note": "outil de debug MCP, pas un serveur stdio d'outils",
  "cache": false
},
"n8n": {
  "command": "npx", "args": ["-y", "n8n-mcp@2.99.1"],
  "env": { "N8N_API_URL": "<N8N_API_URL>", "N8N_API_KEY": "<N8N_API_KEY>" },
  "cache": false
},
"figma": {
  "command": "npx", "args": ["-y", "figma-developer-mcp@0.13.2"],
  "_note": "variables d'env non trouvées dans le README npm",
  "cache": false
},
"slack": {
  "command": "npx", "args": ["-y", "slack-mcp-server@1.3.0"],
  "_note": "variables d'env non extraites du README npm",
  "cache": false
},
"netlify": {
  "command": "npx", "args": ["-y", "@netlify/mcp@1.20.0"],
  "env": { "NETLIFY_PERSONAL_ACCESS_TOKEN": "<NETLIFY_PERSONAL_ACCESS_TOKEN>" },
  "cache": false
},
"heroku": {
  "command": "npx", "args": ["-y", "@heroku/mcp-server@1.2.12"],
  "env": { "HEROKU_API_KEY": "<HEROKU_API_KEY>" },
  "cache": false
},
"contentful": {
  "command": "npx", "args": ["-y", "@contentful/mcp-server@1.23.25"],
  "env": {
    "CONTENTFUL_MANAGEMENT_ACCESS_TOKEN": "<CONTENTFUL_MANAGEMENT_ACCESS_TOKEN>",
    "SPACE_ID": "<SPACE_ID>",
    "ENVIRONMENT_ID": "<ENVIRONMENT_ID>",
    "CONTENTFUL_HOST": "<CONTENTFUL_HOST>"
  },
  "cache": false
},
"mapbox": {
  "command": "npx", "args": ["-y", "@mapbox/mcp-server@0.16.0"],
  "env": { "MAPBOX_ACCESS_TOKEN": "<MAPBOX_ACCESS_TOKEN>" },
  "_note": "nom de variable non trouvé dans le README npm",
  "cache": false
},
"clickup": {
  "command": "npx", "args": ["-y", "@taazkareem/clickup-mcp-server@0.14.6"],
  "env": { "CLICKUP_API_KEY": "<CLICKUP_API_KEY>", "CLICKUP_TEAM_ID": "<CLICKUP_TEAM_ID>" },
  "cache": false
},
"shopify": {
  "command": "npx", "args": ["-y", "@shopify/dev-mcp@1.17.0"], "cache": false
},
"pinecone": {
  "command": "npx", "args": ["-y", "@pinecone-database/mcp@0.3.0"],
  "env": { "PINECONE_API_KEY": "<PINECONE_API_KEY>" },
  "cache": false
},
"mysql": {
  "command": "npx", "args": ["-y", "@benborla29/mcp-server-mysql@2.0.9"],
  "env": {
    "MYSQL_HOST": "<MYSQL_HOST>", "MYSQL_PORT": "<MYSQL_PORT>", "MYSQL_USER": "<MYSQL_USER>",
    "MYSQL_PASS": "<MYSQL_PASS>", "MYSQL_DB": "<MYSQL_DB>"
  },
  "cache": false
},
"postman": {
  "command": "npx", "args": ["-y", "@postman/postman-mcp-server@2.14.0"],
  "env": { "POSTMAN_API_KEY": "<POSTMAN_API_KEY>" },
  "cache": false
},
"upstash": {
  "command": "npx", "args": ["-y", "@upstash/mcp-server@0.4.0"],
  "env": { "UPSTASH_EMAIL": "<UPSTASH_EMAIL>", "UPSTASH_API_KEY": "<UPSTASH_API_KEY>" },
  "cache": false
},
"aikido": {
  "command": "npx", "args": ["-y", "@aikidosec/mcp@1.0.33"],
  "env": { "AIKIDO_API_KEY": "<AIKIDO_API_KEY>" },
  "cache": false
},
"magic": {
  "command": "npx", "args": ["-y", "@21st-dev/magic@0.2.3"],
  "env": { "API_KEY": "<API_KEY>" },
  "_note": "README cite API_KEY et API_KEY_21ST : nom exact non tranché",
  "cache": false
},
"mobile": {
  "command": "npx", "args": ["-y", "@mobilenext/mobile-mcp@1.0.13"], "cache": false
},
"fiori": {
  "command": "npx", "args": ["-y", "@sap-ux/fiori-mcp-server@1.15.15"], "cache": false
},
"ui5": {
  "command": "npx", "args": ["-y", "@ui5/mcp-server@0.3.2"], "cache": false
},
"next-devtools": {
  "command": "npx", "args": ["-y", "next-devtools-mcp@0.4.0"], "cache": false
},
"browser-tools": {
  "command": "npx", "args": ["-y", "@agentdeskai/browser-tools-mcp@2.0.2"], "cache": false
},
"playwright-executeautomation": {
  "command": "npx", "args": ["-y", "@executeautomation/playwright-mcp-server@1.0.12"], "cache": false
},
"git-cyanheads": {
  "command": "npx", "args": ["-y", "@cyanheads/git-mcp-server@2.15.3"],
  "env": { "MCP_TRANSPORT_TYPE": "stdio", "GIT_BASE_DIR": "<GIT_BASE_DIR>" },
  "cache": false
},
"gmail": {
  "command": "npx", "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp@1.1.11"],
  "env": { "GMAIL_OAUTH_PATH": "<GMAIL_OAUTH_PATH>", "GMAIL_CREDENTIALS_PATH": "<GMAIL_CREDENTIALS_PATH>" },
  "cache": false
},
"git": {
  "command": "uvx", "args": ["mcp-server-git@2026.10.19"], "cache": false
},
"time": {
  "command": "uvx", "args": ["mcp-server-time@2026.10.19"], "cache": false
},
"clickhouse": {
  "command": "uvx", "args": ["mcp-clickhouse@0.7.0"],
  "env": {
    "CLICKHOUSE_HOST": "<CLICKHOUSE_HOST>", "CLICKHOUSE_PORT": "<CLICKHOUSE_PORT>",
    "CLICKHOUSE_USER": "<CLICKHOUSE_USER>", "CLICKHOUSE_PASSWORD": "<CLICKHOUSE_PASSWORD>",
    "CLICKHOUSE_SECURE": "<CLICKHOUSE_SECURE>", "CLICKHOUSE_VERIFY": "<CLICKHOUSE_VERIFY>"
  },
  "cache": false
},
"chroma": {
  "command": "uvx", "args": ["chroma-mcp@0.2.6"],
  "_note": "arguments de lancement (client-type, etc.) non vérifiés",
  "cache": false
},
"qdrant": {
  "command": "uvx", "args": ["mcp-server-qdrant@0.8.1"],
  "env": {
    "QDRANT_URL": "<QDRANT_URL>", "QDRANT_API_KEY": "<QDRANT_API_KEY>",
    "COLLECTION_NAME": "<COLLECTION_NAME>", "EMBEDDING_MODEL": "<EMBEDDING_MODEL>"
  },
  "cache": false
},
"atlassian": {
  "command": "uvx", "args": ["mcp-atlassian@0.23.1"],
  "env": {
    "JIRA_URL": "<JIRA_URL>", "JIRA_USERNAME": "<JIRA_USERNAME>", "JIRA_API_TOKEN": "<JIRA_API_TOKEN>",
    "CONFLUENCE_URL": "<CONFLUENCE_URL>", "CONFLUENCE_USERNAME": "<CONFLUENCE_USERNAME>",
    "CONFLUENCE_API_TOKEN": "<CONFLUENCE_API_TOKEN>"
  },
  "cache": false
},
"google-workspace": {
  "command": "uvx", "args": ["workspace-mcp@2.2.0"],
  "env": { "GOOGLE_OAUTH_CLIENT_ID": "<GOOGLE_OAUTH_CLIENT_ID>", "GOOGLE_OAUTH_CLIENT_SECRET": "<GOOGLE_OAUTH_CLIENT_SECRET>" },
  "cache": false
},
"markitdown": {
  "command": "uvx", "args": ["markitdown-mcp@0.0.1a8"],
  "_note": "version alpha",
  "cache": false
},
"docling": {
  "command": "uvx", "args": ["--from", "docling-mcp@3.3.0", "docling-mcp-server"],
  "env": {
    "DOCLING_MCP_CONVERSION_MODE": "<DOCLING_MCP_CONVERSION_MODE>",
    "DOCLING_MCP_SERVICE_URL": "<DOCLING_MCP_SERVICE_URL>",
    "DOCLING_MCP_SERVICE_API_KEY": "<DOCLING_MCP_SERVICE_API_KEY>"
  },
  "cache": false
},
"excel": {
  "command": "uvx", "args": ["excel-mcp-server@2.2.2"],
  "env": { "EXCEL_FILES_PATH": "<EXCEL_FILES_PATH>" },
  "cache": false
},
"pdf": {
  "command": "uvx", "args": ["mcp-pdf@2026.9.22"],
  "env": { "MCP_PDF_ALLOWED_PATHS": "<MCP_PDF_ALLOWED_PATHS>" },
  "cache": false
},
"grafana": {
  "command": "uvx", "args": ["mcp-grafana@2.1.1"],
  "env": { "GRAFANA_URL": "<GRAFANA_URL>", "GRAFANA_SERVICE_ACCOUNT_TOKEN": "<GRAFANA_SERVICE_ACCOUNT_TOKEN>" },
  "cache": false
},
"home-assistant": {
  "command": "uvx", "args": ["ha-mcp@8.7.0"],
  "env": { "HOMEASSISTANT_URL": "<HOMEASSISTANT_URL>", "HOMEASSISTANT_TOKEN": "<HOMEASSISTANT_TOKEN>" },
  "_note": "PyPI 8.7.0 (2026-10-14), Production/Stable, console_script ha-mcp ; requires-python >=3.13,<3.15 ; env vérifiées dans le code (ha_mcp/config_settings.py)",
  "cache": false
},
"serena": {
  "command": "uvx", "args": ["--from", "serena-agent@2.1.0", "serena", "start-mcp-server"],
  "_note": "PyPI serena-agent 2.1.0 (2026-10-19), console_script serena ; sous-commande start-mcp-server vérifiée dans serena/cli.py ; option --project optionnelle, transport par défaut non vérifié",
  "cache": false
},
"browser-use": {
  "command": "uvx", "args": ["browser-use@0.13.11", "--mcp"],
  "_note": "PyPI 0.13.11 (2026-10-07), console_script browser-use ; flag --mcp vérifié dans browser_use/cli.py ; variables d'env (clé LLM, ex. OPENAI_API_KEY) non vérifiées",
  "cache": false
}
```

### C.3 Numéros 85-87 — serveurs lançables vérifiés (PyPI, 2026-10-25)

| # | Paquet | Version | Date PyPI | Exécutable | Lancement |
|---|---|---|---|---|---|
| 85 | `ha-mcp` | 8.7.0 | 2026-10-14 | `ha-mcp` | `uvx ha-mcp@8.7.0` |
| 86 | `serena-agent` | 2.1.0 | 2026-10-19 | `serena` | `uvx --from serena-agent@2.1.0 serena start-mcp-server` |
| 87 | `browser-use` | 0.13.11 | 2026-10-07 | `browser-use` | `uvx browser-use@0.13.11 --mcp` |

Remplacent `@mastra/mcp`, `mcp-use`, `@modelcontextprotocol/sdk` (SDK/frameworks, non lançables). Aucun yanked ; entrées JSON en C.2.

---

## D. Index complet 1-100

| Bloc | Numéros | Où |
|---|---|---|
| OpenAI natifs | 1-15 | section A |
| Anthropic natifs | 16-37 | section B |
| MCP npm | 38-84 | C.1 (42, 54), C.2 |
| MCP PyPI | 85-100 | C.1 (88), C.2 (85-87, 89-100) |

Détail MCP : 38 playwright · 39 chrome-devtools · 40 context7 · 41 firebase · 42 filesystem* · 43 memory · 44 sequential-thinking · 45 everything · 46 sentry · 47 supabase · 48 notion · 49 stripe · 50 azure · 51 tavily · 52 exa · 53 firecrawl · 54 brave* · 55 apify · 56 gitlab · 57 desktop-commander · 58 repomix · 59 supergateway · 60 mcp-remote · 61 inspector · 62 n8n · 63 figma · 64 slack · 65 netlify · 66 heroku · 67 contentful · 68 mapbox · 69 clickup · 70 shopify · 71 pinecone · 72 mysql · 73 postman · 74 upstash · 75 aikido · 76 magic · 77 mobile · 78 fiori · 79 ui5 · 80 next-devtools · 81 browser-tools · 82 playwright-executeautomation · 83 git-cyanheads · 84 gmail · 85 home-assistant · 86 serena · 87 browser-use · 88 fetch* · 89 git · 90 time · 91 clickhouse · 92 chroma · 93 qdrant · 94 atlassian · 95 google-workspace · 96 markitdown · 97 docling · 98 excel · 99 pdf · 100 grafana
(* = déjà présent dans la config, voir C.1)

Sources : doc Anthropic tool-reference, doc OpenAI guides/tools, registry.npmjs.org, pypi.org (2026-10-25).
