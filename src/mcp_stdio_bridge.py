#!/usr/bin/env python3
"""IcyCache - pont stdio pour le serveur MCP.

Probleme : en transport MCP stdio, stdout EST le canal JSON-RPC. Or le serveur
historique (src/server.py) ecrit des `print()` de demarrage ET configure son
logging sur sys.stdout. Chaque ligne non-JSON pollue le flux protocole :
les clients MCP stricts echouent, et les permissifs loguent des erreurs.

Le `print("✓ ...")` pose en plus un crash : l'encodage console Windows par
defaut (cp1252) ne sait pas encoder le caractere de coche.

Ce pont :
  - garde stdout REEL pour le protocole JSON-RPC (via .buffer),
  - redirige tout ce qui est ecrit en texte (prints, logs) vers stderr,
  - force l'encodage UTF-8 sur les deux flux.

Usage (dans la config MCP d'un client) :
    D:/IcyCache/venv/Scripts/python.exe D:/IcyCache/src/mcp_stdio_bridge.py --mode mcp-stdio
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
TARGET = SRC_DIR / "server.py"


class _StdoutToStderr:
    """Expose .buffer (stdout reel, utilise par le SDK MCP) mais route le texte vers stderr."""

    def __init__(self, real_stdout, sink):
        self._real = real_stdout
        self._sink = sink
        self.buffer = real_stdout.buffer          # <- le SDK MCP ecrit ici
        self.encoding = "utf-8"

    def write(self, data):
        return self._sink.write(data)

    def flush(self):
        return self._sink.flush()

    def isatty(self):
        return False

    def fileno(self):
        return self._sink.fileno()

    def writable(self):
        return True

    def __getattr__(self, item):
        return getattr(self._sink, item)


def main() -> None:
    # Encodage UTF-8 explicite : evite le crash cp1252 sur les caracteres accentues.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    if not TARGET.exists():
        print(f"[IcyCache] server.py introuvable : {TARGET}", file=sys.stderr)
        sys.exit(2)

    real_stdout = sys.stdout
    sys.stdout = _StdoutToStderr(real_stdout, sys.stderr)

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.argv = [str(TARGET)] + sys.argv[1:]
    sys.path.insert(0, str(SRC_DIR))
    runpy.run_path(str(TARGET), run_name="__main__")


if __name__ == "__main__":
    main()
