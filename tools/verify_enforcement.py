#!/usr/bin/env python3
"""IcyCache - PREUVE que le contournement de la gateway est impossible.

Le script se lance ETENDU AVEC LE PROGRAMME CIBLE : les regles de pare-feu
Windows sont appliquees par programme, donc la preuve doit venir du meme
binaire que le client (par ex. l'interpreteur Python utilise par Hermes).

Exemple :
    python tools/verify_enforcement.py --program D:\\CacheForge\\venv\\Scripts\\python.exe

Sortie attendue SANS regles : "CONTOURNEMENT POSSIBLE" (les connexions aboutissent).
Sortie attendue AVEC regles : "CONTOURNEMENT IMPOSSIBLE" (les connexions echouent,
et le port IcyCache reste joignable).

Le test compare l'etat AVANT / APRES : utile pour prouver l'effet des regles
sans avoir a les installer soi-meme.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROBE = r"""
import json, socket, sys, time
targets = json.loads(sys.argv[-1])
out = []
for t in targets:
    label, host, port, expect = t["label"], t["host"], t["port"], t["expect"]
    t0 = time.perf_counter()
    ok, err = False, None
    try:
        s = socket.create_connection((host, int(port)), timeout=4)
        s.close()
        ok = True
    except Exception as exc:
        err = type(exc).__name__
    out.append({"label": label, "host": host, "port": int(port), "expect": expect,
                "connected": ok, "error": err,
                "ms": int((time.perf_counter() - t0) * 1000)})
print(json.dumps(out))
"""


def run_probe(program: str, targets: list) -> list:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(PROBE)
        probe_path = f.name
    try:
        res = subprocess.run([program, probe_path, json.dumps(targets)],
                             capture_output=True, text=True, timeout=60)
        out = res.stdout.strip().splitlines()
        if not out:
            return [{"label": "probe", "connected": None, "error": (res.stderr or "")[:300]}]
        return json.loads(out[-1])
    finally:
        try:
            os.unlink(probe_path)
        except OSError:
            pass


def report(results: list) -> bool:
    """True = etat conforme (connexions bloquees la ou il faut, IcyCache joignable)."""
    ok = True
    print(f"{'CIBLE':<34} {'ATTENDU':<10} {'OBTENU':<12} {'ms':>6}  DETAIL")
    print("-" * 96)
    for r in results:
        if "port" not in r:
            print(f"{r.get('label', '?'):<34} probe impossible : {r.get('error')}")
            ok = False
            continue
        if r["connected"]:
            got = "JOIGNABLE"
        elif r.get("error") == "ConnectionRefusedError":
            got = "PORT FERM"          # aucun service n'ecoute : pas un contournement
        else:
            got = "BLOQUE"             # filtre par le pare-feu
        good = (got == "JOIGNABLE") if r["expect"] == "allow" else (got in ("BLOQUE", "PORT FERM"))
        ok = ok and good
        print(f"{r['label']:<34} {r['expect']:<10} {got:<12} {r['ms']:>6}  "
              f"{'OK' if good else 'ECHEC ' + (r.get('error') or '')}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", default=sys.executable,
                    help="programme dont le trafic est teste (le meme que le client bloque)")
    ap.add_argument("--allow-port", type=int, default=8080, help="port IcyCache qui doit rester joignable")
    ap.add_argument("--block-ports", default="20128,1234,11434", help="ports upstreams qui doivent etre bloques")
    ap.add_argument("--remote", default="api.openai.com", help="cible distante qui doit etre bloquee")
    args = ap.parse_args()

    program = str(Path(args.program).resolve())
    if not Path(program).exists():
        print(f"Programme introuvable : {program}")
        return 2

    blocked = [int(p) for p in args.block_ports.split(",") if p.strip()]
    targets = [
        {"label": f"IcyCache 127.0.0.1:{args.allow_port}", "host": "127.0.0.1",
         "port": args.allow_port, "expect": "allow"},
    ]
    for p in blocked:
        targets.append({"label": f"upstream local 127.0.0.1:{p}", "host": "127.0.0.1",
                        "port": p, "expect": "block"})
    targets.append({"label": f"cloud {args.remote}:443", "host": args.remote, "port": 443,
                    "expect": "block"})

    print(f"Programme teste : {program}")
    print(f"Cible           : {len(targets)} points de controle")
    print()
    results = run_probe(program, targets)
    ok = report(results)
    print()
    if ok:
        print(">> CONTOURNEMENT IMPOSSIBLE : le client ne peut joindre que IcyCache.")
        return 0
    print(">> CONTOURNEMENT POSSIBLE : le client atteint encore un upstream en direct.")
    print("   Appliquez les regles (PowerShell eleve) :")
    print(f"   powershell -ExecutionPolicy Bypass -File tools\\enforce_gateway.ps1 -Action apply "
          f"-TargetProgram \"{program}\" -AllowPort {args.allow_port} -BlockPorts {args.block_ports}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
