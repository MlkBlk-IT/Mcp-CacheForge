"""IcyCache — passerelle LLM universelle avec cache local.

Le code vit dans `icycache/src/`, avec `config/` et `static/` a cote — exactement
la disposition du depot (`src/`, `config/`, `static/`). Comme dans le depot, les
modules s'importent a plat (`import server`, `import icy_core`, ...) : on ajoute
donc `src/` au `sys.path` pour que les imports fonctionnent a l'identique dans
les deux modes (checkout source et installation pip).

`PROJECT_ROOT` est le repertoire qui contient `config/` et `static/` :
- mode depot    -> la racine du repo
- mode installe -> le paquet lui-meme (site-packages/icycache)
"""
from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.2.0"

_HERE = Path(__file__).resolve().parent

_SRC = _HERE / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# La racine qui porte config/ et static/.
if (_HERE / "config").is_dir():
    PROJECT_ROOT = _HERE
else:
    PROJECT_ROOT = _HERE.parent

__all__ = ["__version__", "PROJECT_ROOT"]
