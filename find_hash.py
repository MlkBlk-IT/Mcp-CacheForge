# D:\CacheForge\find_hash.py
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
lines = server_path.read_text(encoding="utf-8").splitlines()

print("Lignes contenant 'sha256', 'hash', ou 'cache' :\n")
for idx, line in enumerate(lines, 1):
    if any(term in line.lower() for term in ["sha256", "hash", "cache"]):
        print(f"{idx:4d}: {line}")
