r"""Wrapper MCP stdio pour Claude Desktop. Redirige stdout->stderr pendant l'import de mcp_server.py
(prints/logs d'init), puis restaure stdout pour le protocole JSON-RPC. N'ouvre aucun port."""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
_real_stdout = sys.stdout
sys.stdout = sys.stderr
import mcp_server  # noqa: E402
sys.stdout = _real_stdout

if __name__ == "__main__":
    mcp_server.mcp.run(transport="stdio")
