#!/usr/bin/env python3
"""IcyCache Tray Launcher v2 — background, zéro console, logs fichier"""
import sys, os, threading, time, webbrowser, traceback, subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LOG = PROJECT_ROOT / "logs" / "tray.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

# Interpreteur du projet (redemarrage du tray depuis le menu).
# Priorite au venv du projet; sinon l'interpreteur courant.
_VENV_PY = PROJECT_ROOT / "venv" / "Scripts" / ("pythonw.exe" if os.name == "nt" else "python")
ICYCACHE_PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

# CRITIQUE: sous pythonw.exe sys.stdout/stderr = None -> redirect AVANT tout import
_logf = open(LOG, "a", encoding="utf-8", buffering=1)
sys.stdout = _logf
sys.stderr = _logf

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

sys.path.insert(0, str(PROJECT_ROOT / "src"))

def port_up(port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def existing_service():
    r"""Qui occupe deja le port 8080 ? -> (service, project_root) ou None.

    Permet de distinguer une instance IcyCache deja lancee (on sort) d'une autre
    version du projet (ex: l'ancien D:\CacheForge) qui squatte les ports.
    """
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as r:
            import json as _json
            data = _json.loads(r.read().decode("utf-8", "replace"))
            return data.get("service") or "inconnu", data.get("project_root") or ""
    except Exception:
        return None

def run_uvicorn(app, host, port, name):
    import uvicorn
    try:
        log(f"{name}: start {host}:{port}")
        uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
    except Exception:
        log(f"{name}: CRASH\n{traceback.format_exc()}")

def main():
    log("=== tray.py v2 start ===")
    log(f"PROJECT_ROOT={PROJECT_ROOT}")
    log(f"ICYCACHE_PYTHON={ICYCACHE_PYTHON}")

    # Instance unique: si un IcyCache tourne deja, on ne demarre pas un second
    # tray (sinon conflit de ports et double icone).
    who = existing_service()
    if who:
        svc, root = who
        if svc == "IcyCache" and str(PROJECT_ROOT).lower() == str(root).lower():
            log(f"IcyCache deja actif sur 8080 ({root}) - rien a faire")
            return
        log(f"ALERT: le port 8080 est occupe par '{svc}' ({root or 'inconnu'}). "
            f"Ce n'est pas cette instance ({PROJECT_ROOT}). Arret pour eviter un conflit.")
        return

# server.py = gateway multi-upstream -> :8080 (ex server_new, renomme).
# mcp_server.py = serveur MCP SSE :8081 / HTTP :8084, importe uniquement pour `mcp`
# (son ancien proxy FastAPI interne n'est pas servi ici).
    try:
        from server import app as proxy_app  # gateway multi-upstream -> :8080
        from mcp_server import mcp              # MCP SSE/HTTP -> :8081 / :8084
        log("server (gateway) + mcp_server (MCP) importes OK")
    except Exception:
        log(f"IMPORT CRASH\n{traceback.format_exc()}")
        return

    threading.Thread(target=run_uvicorn, args=(proxy_app, "127.0.0.1", 8080, "PROXY"), daemon=True).start()
    try:
        sse = mcp.sse_app()
        threading.Thread(target=run_uvicorn, args=(sse, "127.0.0.1", 8081, "MCP"), daemon=True).start()
    except Exception:
        log(f"MCP sse_app indisponible:\n{traceback.format_exc()}")

    # Streamable HTTP: transport standard des clients MCP recents.
    # SSE reste sur 8081 pour les clients legacy.
    http_port = 8084
    try:
        import json as _json
        cfg_path = PROJECT_ROOT / "config" / "icycache.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as fh:
                _cfg = _json.load(fh)
            http_port = int(_cfg.get("mcp_http", {}).get("port", 8084))
    except Exception:
        pass
    try:
        http_app = mcp.streamable_http_app()
        threading.Thread(target=run_uvicorn, args=(http_app, "127.0.0.1", http_port, "MCP-HTTP"),
                         daemon=True).start()
        log(f"MCP streamable-http sur 127.0.0.1:{http_port}/mcp")
    except Exception:
        log(f"MCP streamable_http_app indisponible:\n{traceback.format_exc()}")

    for _ in range(30):
        if port_up(8080):
            break
        time.sleep(0.5)
    log(f"PROXY 8080 UP: {port_up(8080)} | MCP 8081 UP: {port_up(8081)}")

    # Tray en thread séparé pour ne pas bloquer le main
    def run_tray():
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (64, 64), (30, 30, 30))
            d = ImageDraw.Draw(img)
            d.rectangle([10, 10, 54, 54], fill=(0, 200, 100), outline=(0, 255, 128), width=2)
            menu = pystray.Menu(
                pystray.MenuItem("Dashboard", lambda i, it: webbrowser.open("http://localhost:8080/dashboard"), default=True),
                pystray.MenuItem("Logs", lambda i, it: os.startfile(str(PROJECT_ROOT / "logs" / "app.log"))),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Restart", lambda i, it: (i.stop(), os.execv(ICYCACHE_PYTHON, [ICYCACHE_PYTHON, str(PROJECT_ROOT / "tray.py")]))),
                pystray.MenuItem("Stop", lambda i, it: (i.stop(), os._exit(0))),
            )
            icon = pystray.Icon("IcyCache", img, "IcyCache — Proxy :8080 | MCP :8081", menu)
            log("tray icon actif")
            icon.run()
        except Exception:
            log(f"TRAY CRASH\n{traceback.format_exc()}")

    threading.Thread(target=run_tray, daemon=True).start()
    
    # Main thread: keep alive, monitor ports
    try:
        while True:
            time.sleep(10)
            if not port_up(8080):
                log("ALERT: Proxy 8080 down!")
            if not port_up(8081):
                log("ALERT: MCP 8081 down!")
    except KeyboardInterrupt:
        log("Shutdown requested")
        os._exit(0)

if __name__ == "__main__":
    main()
