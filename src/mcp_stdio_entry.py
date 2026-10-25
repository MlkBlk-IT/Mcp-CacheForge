"""Entree MCP stdio pour la distribution installee.

`mcp_server.py` ecrit sur stdout pendant son import (banniere, logs d'init) :
sur le transport stdio, stdout appartient au JSON-RPC. On le detourne donc
vers stderr pendant l'import, puis on rend stdout au protocole.

Equivalent de `mcp_stdio.py` a la racine du depot, mais place dans `src/`
pour que le chemin du paquet installe soit correct.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_real_stdout = sys.stdout
sys.stdout = sys.stderr
try:
    import mcp_server  # noqa: E402
finally:
    sys.stdout = _real_stdout

if __name__ == "__main__":
    mcp_server.mcp.run(transport="stdio")
