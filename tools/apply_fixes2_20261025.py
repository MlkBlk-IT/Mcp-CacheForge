#!/usr/bin/env python3
"""Patch 2 (2026-10-25) : /api/tools*, mcp.tools dans /api/status, dashboard lit l'upstream par defaut."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors = []


def patch(path, pairs):
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    for tag, old, new in pairs:
        if new in s and old not in s:
            print(f"[SKIP deja applique] {path} {tag}")
            continue
        n = s.count(old)
        if n != 1:
            errors.append(f"{path} {tag}: {n} occurrence(s) (attendu 1)")
            continue
        s = s.replace(old, new)
        print(f"[OK] {path} {tag}")
    p.write_text(s, encoding="utf-8", newline="")


patch("src/server.py", [
    ("mcp.tools dans /api/status",
     '"transports": CONFIG.get("mcp", {}).get("transport", []), "tools": []},',
     '"transports": CONFIG.get("mcp", {}).get("transport", []), "tools": list(_MCP_TOOL_NAMES)},'),

    ("routes /api/tools",
     '@app.get("/api/agent-events")',
     '# Outils exposes par la couche MCP (liste de mcp_server.py ; sert au compteur du dashboard).\n'
     '_MCP_TOOL_NAMES = ("check_tool_loop", "clear_cache", "clear_tool_cache", "get_cache_stats",\n'
     '                   "get_tool_cache_stats", "list_proxy_servers", "list_proxy_tools",\n'
     '                   "normalize_preview", "proxy_tool", "proxy_tools", "search_cached_prompts")\n\n\n'
     '@app.get("/api/tools")\n'
     'async def api_list_tools():\n'
     '    """Serveurs MCP routables via le proxy d\'outils (config/icycache.json -> tool_proxy)."""\n'
     '    try:\n'
     '        import tool_proxy as tp\n'
     '    except Exception as exc:\n'
     '        return JSONResponse({"ok": False, "error": f"tool_proxy indisponible: {exc}"}, status_code=503)\n'
     '    names = tp.server_names()\n'
     '    return {"ok": True, "servers": names, "count": len(names)}\n\n\n'
     '@app.get("/api/tools/{server}")\n'
     'async def api_list_server_tools(server: str):\n'
     '    """Outils exposes par un serveur MCP cible."""\n'
     '    try:\n'
     '        import tool_proxy as tp\n'
     '    except Exception as exc:\n'
     '        return JSONResponse({"ok": False, "error": f"tool_proxy indisponible: {exc}"}, status_code=503)\n'
     '    return await tp.list_server_tools(server)\n\n\n'
     '@app.get("/api/agent-events")'),
])

patch("static/dashboard.html", [
    ("upstream par defaut (st.upstream est un dict par upstream)",
     "const mcp = st.mcp || {}, up = st.upstream || {};",
     "const mcp = st.mcp || {}, upAll = st.upstream || {};\n"
     "    /* /api/status renvoie un dict {nom_upstream: {alive,...}} : on lit l'upstream par defaut. */\n"
     "    const up = (upAll.alive !== undefined) ? upAll : (upAll[(st.gateway || {}).default_upstream] || {});"),
])

if errors:
    print("ERREURS:", *errors, sep="\n - ")
    sys.exit(1)
print("PATCH 2 OK")
