"""Permet `python -m icycache ...` en plus du script console `icycache`."""
from __future__ import annotations

import sys

from icycache.cli import main

if __name__ == "__main__":
    sys.exit(main())
