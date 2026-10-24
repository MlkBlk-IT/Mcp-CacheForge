# IcyCache — Notes de mise à jour (2026-10-24)

## Ajouts
- Nouvelle gateway universelle `src/server_new.py` (port 8080) avec noyau partagé `src/icy_core.py`
- Normalisation autonome `src/icy_normalize.py`, store de cache `src/icy_cache_store.py`
- Façade MCP `src/mcp_gateway.py`, pont stdio `src/mcp_stdio_bridge.py`
- Proxy d'outils MCP `src/tool_proxy.py`, helpers upstream `src/upstream_utils.py`
- Tests isolés de la gateway `tests/test_gateway.py`
- Outils `tools/`, packaging `setup.py`, `package.json`, `Cargo.toml`
- Config gateway `config/icycache.gateway.json`, scripts autostart/mise à jour

## Modifications
- `src/server.py` — exposition MCP + documentation (+350 lignes)
- `README.md` — rebrand et documentation
- `static/dashboard.html` — refonte dashboard (nœuds vivants, jauge électrique, palette cyberpunk)
- `tray.py`, scripts `.ps1`, `requirements.txt`, `config/icycache.json`

## Exclus volontairement
- Captures PNG (`static/*.png`) et dossier `backup_pre_gateway/` (artefacts locaux, non versionnés)
