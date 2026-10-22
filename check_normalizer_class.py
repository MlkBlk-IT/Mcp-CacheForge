# D:\CacheForge\check_normalizer_class.py
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
lines = server_path.read_text(encoding="utf-8").splitlines()

print("=== RECHERCHE DE LA CLASSE NORMALIZER ===\n")

# Chercher toutes les classes qui pourraient être le normalizer
for i, line in enumerate(lines):
    if 'class' in line and ('Normali' in line or 'Pipeline' in line):
        print(f"Ligne {i+1}: {line}")
        # Afficher les 5 lignes suivantes
        for j in range(i+1, min(i+6, len(lines))):
            print(f"  {j+1}: {lines[j]}")
        print()

print("\n=== INSTANCIATION DU NORMALIZER ===\n")
for i, line in enumerate(lines):
    if 'normalizer =' in line or 'normalizer=' in line:
        print(f"Ligne {i+1}: {line}")
        # Contexte
        for j in range(max(0, i-3), min(i+3, len(lines))):
            print(f"  {j+1}: {lines[j]}")
        print()
