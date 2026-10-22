# D:\CacheForge\show_indent_error.py
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
lines = server_path.read_text(encoding="utf-8").splitlines()

print("=== CONTEXTE AUTOUR DE LA LIGNE 278 ===\n")
for idx in range(269, min(290, len(lines))):
    print(f"{idx+1:4d}: {lines[idx]}")
