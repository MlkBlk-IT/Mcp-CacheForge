# D:\CacheForge\show_tool_and_hit.py
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
lines = server_path.read_text(encoding="utf-8").splitlines()

print("=== SECTION HIT NON-STREAM (lignes 309-315) ===\n")
for idx in range(308, 315):
    print(f"{idx+1:4d}: {lines[idx]}")

print("\n\n=== SECTION CACHE MISS STORAGE (lignes 350-375) ===\n")
for idx in range(349, 375):
    print(f"{idx+1:4d}: {lines[idx]}")
