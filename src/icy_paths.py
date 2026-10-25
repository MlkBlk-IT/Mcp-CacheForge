"""Chemins IcyCache: racine du projet et repertoire de donnees inscriptible.

Deux modes cohabitent:

- **Mode depot** (checkout source, `.git` present) : `PROJECT_ROOT` est le repo,
  les donnees (`db/`, `logs/`) restent a cote du code. C'est le comportement
  historique : `D:\\IcyCache\\db\\cache.db`, `D:\\IcyCache\\logs\\app.log`.
- **Mode installe** (pip / uvx) : `PROJECT_ROOT` est le repertoire du paquet
  (site-packages), qui n'est pas inscriptible. Les donnees vont alors dans un
  repertoire utilisateur : `%LOCALAPPDATA%\\IcyCache` sous Windows,
  `~/.local/share/IcyCache` ailleurs.

Regle de resolution : un chemin **absolu** dans la config est utilise tel quel ;
un chemin **relatif** est resolu contre `DATA_DIR`. `ICYCACHE_HOME` force
`DATA_DIR`, `ICYCACHE_DB` force le chemin de base.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "PROJECT_ROOT",
    "IS_SOURCE_CHECKOUT",
    "DATA_DIR",
    "default_data_dir",
    "resolve_data_path",
]

# parent de src/ : le repo en mode depot, le paquet en mode installe.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

IS_SOURCE_CHECKOUT = (PROJECT_ROOT / ".git").exists()


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)


def _is_in_site_packages(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return "site-packages" in parts or "dist-packages" in parts


def default_data_dir() -> Path:
    """Repertoire inscriptible pour `db/` et `logs/`."""
    env = os.environ.get("ICYCACHE_HOME")
    if env:
        return Path(env).expanduser()

    if IS_SOURCE_CHECKOUT:
        return PROJECT_ROOT

    # Installation editable hors site-packages : on garde la racine du projet.
    if not _is_in_site_packages(PROJECT_ROOT) and _writable(PROJECT_ROOT):
        return PROJECT_ROOT

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "IcyCache"


DATA_DIR = default_data_dir()


def resolve_data_path(value, default_relative: str) -> Path:
    """Absolu -> tel quel. Relatif (ou vide) -> resolu contre `DATA_DIR`."""
    raw = str(value) if value else default_relative
    path = Path(raw)
    return path if path.is_absolute() else (DATA_DIR / path)
