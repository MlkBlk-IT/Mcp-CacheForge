# D:\CacheForge\audit_3_4.py
from pathlib import Path
import sqlite3

root = Path(r"D:\CacheForge")
p = root / "src" / "server.py"
lines = p.read_text(encoding="utf-8").splitlines()

def dump(start, end, title):
    print("\n" + "=" * 72)
    print(f"{title} | lignes {start}-{end}")
    print("=" * 72)
    for i in range(start - 1, min(end, len(lines))):
        print(f"{i + 1:4d}: {lines[i]}")

dump(1, 45, "IMPORTS + CONFIG")
dump(90, 165, "CacheManager : init / get / set")
dump(300, 400, "PROXY : HIT + MISS")

print("\n" + "=" * 72)
print("SCHEMA SQLITE — CONNEXION LECTURE SEULE")
print("=" * 72)
db = root / "db" / "cache.db"
con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
try:
    for (sql,) in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
        print(sql)
    count = con.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    print(f"\nEntrées cache : {count}")
    print("\nÉchantillon (5 lignes) :")
    for row in con.execute("SELECT prompt_hash, is_stream, substr(response,1,120) FROM cache LIMIT 5"):
        print(row)
finally:
    con.close()

print("\n✓ Audit terminé. Aucune écriture effectuée.")
