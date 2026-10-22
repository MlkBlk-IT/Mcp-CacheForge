# D:\CacheForge\audit_normalize.py
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CacheForge\src")

# 1. Vérifier les règles
rules_path = Path(r"D:\CacheForge\config\normalization_rules.json")
rules_data = json.loads(rules_path.read_text(encoding="utf-8"))

print(f"=== DICTIONNAIRE ===")
print(f"Nombre total de règles: {len(rules_data['rules'])}")
print(f"Premières 5 règles:")
for r in rules_data['rules'][:5]:
    print(f"  {r}")

# Chercher "svp"
print(f"\n=== RECHERCHE 'svp' DANS RÈGLES ===")
svp_rules = [r for r in rules_data['rules'] if 'svp' in r['pattern'].lower() or 'plait' in r['pattern'].lower()]
print(f"Trouvé {len(svp_rules)} règles contenant 'svp' ou 'plait':")
for r in svp_rules[:10]:
    print(f"  pattern='{r['pattern']}' → replace='{r['replace']}'")

# 2. Tester le normalizer
print(f"\n=== TEST DU NORMALIZER ===")
try:
    # Import dynamique pour voir si le nouveau pipeline est en place
    import importlib.util
    spec = importlib.util.spec_from_file_location("server", r"D:\CacheForge\src\server.py")
    server = importlib.util.module_from_spec(spec)
    
    # Monkey-patch pour éviter le lancement FastAPI
    import sys
    class FakeArgs:
        mode = "proxy"
    sys.argv = ["server.py", "--mode", "proxy"]
    
    # On ne peut pas charger server.py complètement, on va juste instancier le normalizer manuellement
    print("Chargement manuel du NormalizationPipeline...")
    
    # Charger la classe directement
    import ahocorasick
    import unicodedata
    from nltk.stem.snowball import SnowballStemmer
    
    # Copier la classe NormalizationPipeline du fichier
    # Pour l'instant, testons juste le contenu des règles
    print(f"\nRègles commençant par 's':")
    s_rules = [r for r in rules_data['rules'] if r['pattern'].lower().startswith('s')]
    for r in s_rules[:10]:
        print(f"  '{r['pattern']}' (len={len(r['pattern'])})")
        
except Exception as e:
    print(f"ERREUR: {e}")
    import traceback
    traceback.print_exc()
