# D:\CacheForge\find_cache_key.py
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
lines = server_path.read_text(encoding="utf-8").splitlines()

print("Lignes contenant 'cache_key' :\n")
for idx, line in enumerate(lines, 1):
    if "cache_key" in line.lower():
        print(f"{idx:4d}: {line}")
