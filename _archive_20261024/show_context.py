# D:\CacheForge\show_context.py
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
lines = server_path.read_text(encoding="utf-8").splitlines()

print("Contexte autour de la ligne 291 :\n")
start = max(0, 275 - 1)
end = min(len(lines), 330)
for idx in range(start, end):
    print(f"{idx+1:4d}: {lines[idx]}")
