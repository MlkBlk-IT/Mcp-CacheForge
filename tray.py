#!/usr/bin/env python3
"""CacheForge Tray Launcher v2 — background, zéro console, logs fichier"""
import sys, os, threading, time, webbrowser, traceback
from pathlib import Path

PROJECT_ROOT = Path(r"D:\CacheForge")
LOG = PROJECT_ROOT / "logs" / "tray.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

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

def run_uvicorn(app, host, port, name):
    import uvicorn
    try:
        log(f"{name}: start {host}:{port}")
        uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
    except Exception:
        log(f"{name}: CRASH\n{traceback.format_exc()}")

def main():
    log("=== tray.py v2 start ===")
    try:
        from server import app as proxy_app, mcp
        log("server module importé OK")
    except Exception:
        log(f"IMPORT CRASH\n{traceback.format_exc()}")
        return

    threading.Thread(target=run_uvicorn, args=(proxy_app, "127.0.0.1", 8080, "PROXY"), daemon=True).start()
    try:
        sse = mcp.sse_app()
        threading.Thread(target=run_uvicorn, args=(sse, "127.0.0.1", 8081, "MCP"), daemon=True).start()
    except Exception:
        log(f"MCP sse_app indisponible:\n{traceback.format_exc()}")

    for _ in range(30):
        if port_up(8080):
            break
        time.sleep(0.5)
    log(f"PROXY 8080 UP: {port_up(8080)} | MCP 8081 UP: {port_up(8081)}")

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
            pystray.MenuItem("Restart", lambda i, it: (i.stop(), os.execv(sys.executable, [sys.executable, str(PROJECT_ROOT / "tray.py")]))),
            pystray.MenuItem("Stop", lambda i, it: (i.stop(), os._exit(0))),
        )
        icon = pystray.Icon("CacheForge", img, "CacheForge — Proxy :8080 | MCP :8081", menu)
        log("tray icon actif")
        icon.run()
    except Exception:
        log(f"TRAY CRASH\n{traceback.format_exc()}")
        while True:
            time.sleep(60)

if __name__ == "__main__":
    main()
