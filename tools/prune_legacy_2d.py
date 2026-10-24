#!/usr/bin/env python3
"""Retire le moteur 2D legacy du dashboard (classes Globe / Bars3D / Loop3D).

Ces trois classes n'etaient plus instancies depuis le passage a IcyWebGL :
un canvas ne peut porter qu'un seul contexte (2D ou WebGL). Elles pesaient
~1900 lignes dans dashboard.html et embarquaient de la logique en conflit
avec le moteur 3D (orbites, palettes, labels).

On supprime le bloc comprises entre le marqueur de section et la ligne
d'instance des modules 3D, en conservant tout le reste a l'octet pres.
"""
import shutil
import sys
from pathlib import Path

DASH = Path(r"D:\IcyCache\static\dashboard.html")
BACKUP = DASH.with_suffix(".html.bak-pre-prune-legacy")

START_MARK = "/* ==================================================================\n   3D NEURAL CORE — rotating vector-network sphere"
END_MARK = "/* =========================================================================\n   INSTANCES"

src = DASH.read_text(encoding="utf-8")
if BACKUP.exists():
    print("sauvegarde deja presente, on repart de la sauvegarde")
    src = BACKUP.read_text(encoding="utf-8")
else:
    shutil.copy2(DASH, BACKUP)

i = src.index(START_MARK)
j = src.index(END_MARK)
removed = src[i:j]
n_lines = removed.count("\n")

replacement = (
    "/* ==================================================================\n"
    "   MOTEUR 3D\n"
    "   ------------------------------------------------------------------\n"
    "   L'ancien moteur 2D (classes Globe / Bars3D / Loop3D) a ete retire :\n"
    "   il n'etait plus instancie depuis le passage a IcyWebGL (un canvas ne\n"
    "   peut porter qu'un seul contexte 2D ou WebGL). Tout le rendu passe\n"
    "   maintenant par static/icywebgl.js : neurone humain, lemniscate 3D,\n"
    "   histogrammes extrudes.\n"
    "   ================================================================== */\n\n"
)

DASH.write_text(src[:i] + replacement + src[j:], encoding="utf-8")
print(f"supprime {n_lines} lignes de code 2D legacy")
print(f"nouvelle taille : {DASH.stat().st_size} octets")
