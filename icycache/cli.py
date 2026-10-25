"""CLI IcyCache.

    icycache serve            # la gateway HTTP (port 8080)
    icycache mcp              # la couche MCP (SSE 8081 / HTTP 8084 / stdio)
    icycache doctor           # diagnostic : chemins, config, upstreams, base
    icycache paths            # ou sont stockees les donnees
    icycache version

`serve` et `mcp` relancent les vrais scripts (`src/server.py`, `src/mcp_server.py`)
dans un sous-processus : la CLI n'ecrit aucune logique de demarrage, elle ne fait
que choisir le point d'entree et transmettre les arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import PROJECT_ROOT, __version__

_SRC = PROJECT_ROOT / "src"


def _run(script: str, extra: list[str]) -> int:
    target = _SRC / script
    if not target.exists():
        print(f"[icycache] introuvable: {target}", file=sys.stderr)
        return 2
    cmd = [sys.executable, str(target), *extra]
    print(f"[icycache] {' '.join(cmd)}", file=sys.stderr)
    return subprocess.call(cmd)


def _cmd_serve(args) -> int:
    extra: list[str] = []
    if args.host:
        extra += ["--host", args.host]
    if args.port:
        extra += ["--port", str(args.port)]
    return _run("server.py", extra)


def _cmd_mcp(args) -> int:
    # stdio passe par une entree dediee : l'import de mcp_server.py ecrit sur
    # stdout, qui appartient au JSON-RPC sur ce transport.
    if args.mode == "mcp-stdio":
        return _run("mcp_stdio_entry.py", [])
    return _run("mcp_server.py", ["--mode", args.mode])


def _cmd_paths(args) -> int:
    sys.path.insert(0, str(_SRC))
    from icy_paths import DATA_DIR, IS_SOURCE_CHECKOUT, PROJECT_ROOT as ROOT

    print(f"mode            : {'depot (source)' if IS_SOURCE_CHECKOUT else 'installe (pip)'}")
    print(f"PROJECT_ROOT    : {ROOT}")
    print(f"DATA_DIR        : {DATA_DIR}")
    print(f"config gateway  : {ROOT / 'config' / 'icycache.gateway.json'}")
    print(f"config legacy   : {ROOT / 'config' / 'icycache.json'}")
    print(f"base (cache.db) : {DATA_DIR / 'db' / 'cache.db'}")
    print(f"logs            : {DATA_DIR / 'logs'}")
    print(f"static          : {ROOT / 'static'}")
    return 0


def _cmd_doctor(args) -> int:
    sys.path.insert(0, str(_SRC))
    import icy_paths

    problems: list[str] = []

    print("== IcyCache ==")
    print(f"version         : {__version__}")
    print(f"python          : {sys.executable}")
    print(f"PROJECT_ROOT    : {icy_paths.PROJECT_ROOT}")
    print(f"DATA_DIR        : {icy_paths.DATA_DIR}")
    print(f"mode            : {'depot (source)' if icy_paths.IS_SOURCE_CHECKOUT else 'installe (pip)'}")

    cfg_path = Path(os.environ.get("ICYCACHE_CONFIG") or (icy_paths.PROJECT_ROOT / "config" / "icycache.gateway.json"))
    print(f"\n== config ==\n{cfg_path}")
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"config illisible: {exc}")
            cfg = {}
        ups = sorted(cfg.get("upstreams", {}))
        gw = cfg.get("gateway", {})
        print(f"upstreams       : {len(ups)} -> {', '.join(ups)}")
        print(f"default         : {gw.get('default_upstream') or '(aucun - explicite obligatoire)'}")
        print(f"sticky_upstream : {gw.get('sticky_upstream')}")
        cache = cfg.get("cache", {})
        print(f"cache           : max_entries={cache.get('max_entries')} "
              f"semantic={cache.get('enable_semantic')} ttl={cache.get('ttl_seconds')}")
        db = icy_paths.resolve_data_path(cache.get("db_path"), "db/cache.db")
    else:
        problems.append(f"config absent: {cfg_path}")
        db = icy_paths.DATA_DIR / "db" / "cache.db"

    print(f"\n== base ==\n{db}")
    print(f"existe          : {db.exists()}"
          + (f" ({db.stat().st_size} octets)" if db.exists() else ""))
    parent = db.parent
    if not parent.exists():
        problems.append(f"repertoire de base absent (sera cree au demarrage): {parent}")
    elif not os.access(parent, os.W_OK):
        problems.append(f"repertoire de base non inscriptible: {parent}")

    static = icy_paths.PROJECT_ROOT / "static" / "dashboard.html"
    print(f"\n== dashboard ==\n{static}\nexiste          : {static.exists()}")
    if not static.exists():
        problems.append(f"dashboard absent: {static}")

    print()
    if problems:
        print("== PROBLEMES ==")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("== OK ==")
    return 0


def _cmd_version(args) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="icycache",
        description="IcyCache — passerelle LLM universelle avec cache local.",
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="Demarrer la gateway HTTP (defaut 127.0.0.1:8080).")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("mcp", help="Demarrer la couche MCP.")
    p.add_argument("--mode", default="mcp-sse",
                   choices=["proxy", "mcp-stdio", "mcp-sse", "mcp-http", "all"])
    p.set_defaults(func=_cmd_mcp)

    p = sub.add_parser("doctor", help="Diagnostic de l'installation.")
    p.set_defaults(func=_cmd_doctor)

    p = sub.add_parser("paths", help="Afficher ou sont stockees les donnees.")
    p.set_defaults(func=_cmd_paths)

    p = sub.add_parser("version", help="Afficher la version.")
    p.set_defaults(func=_cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
