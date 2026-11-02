#!/usr/bin/env python3
"""Phase 4: Replay du trafic reel contre la nouvelle cle de cache.

Mesure honnetement 3 taux:
1. Taux global brut (tous events)
2. Taux sur trafic cachable (excluant agent stream context-growing)
3. Taux par type de trafic

Limitation: seules les entrees en cache ont leur original_prompt.
Pour les events sans prompt en cache, on utilise le hash ancien comme proxy.
"""
import sys, json, sqlite3, hashlib
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path("D:/IcyCache/src")))
from icy_normalize import normalize_payload, cache_key

DB = "D:/IcyCache/db/cache.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

# --- 1. Charger les entrees cache avec original_prompt ---
cache_rows = conn.execute("SELECT prompt_hash, original_prompt, model, is_stream FROM cache").fetchall()
print(f"Cache entries: {len(cache_rows)}")

# Compute new keys for cache entries
new_key_map = {}  # new_key -> [old_hashes]
old_to_new = {}   # old_hash -> new_key
parse_errors = 0

for row in cache_rows:
    try:
        body = json.loads(row["original_prompt"])
        nk = cache_key(body)
        old_to_new[row["prompt_hash"]] = nk
        new_key_map.setdefault(nk, []).append(row["prompt_hash"])
    except Exception as e:
        parse_errors += 1

print(f"Parse errors: {parse_errors}")
print(f"Unique old keys: {len(cache_rows)}")
print(f"Unique new keys: {len(new_key_map)}")

merged = sum(1 for k, v in new_key_map.items() if len(v) > 1)
print(f"Merged keys (multiple old -> same new): {merged}")
if merged:
    for k, v in new_key_map.items():
        if len(v) > 1:
            print(f"  Merge: {len(v)} old keys -> 1 new key")

# --- 2. Charger tous les events ---
events = conn.execute("""
    SELECT id, ts, kind, hash, model_req, model_real, stream, 
           prompt_tokens, completion_tokens, upstream, client
    FROM events ORDER BY ts ASC
""").fetchall()
print(f"\nTotal events: {len(events)}")

# --- 3. Simuler le cache avec les nouvelles cles ---
seen_keys = set()
sim_hits = 0
sim_miss = 0
sim_bypass = 0

type_stats = defaultdict(lambda: {"total": 0, "hit": 0, "miss": 0, "bypass": 0})

for ev in events:
    kind = ev["kind"]
    is_stream = bool(ev["stream"])
    pt = ev["prompt_tokens"] or 0

    if pt > 10000 and is_stream:
        traffic_type = "agent_stream_growing"
    elif is_stream:
        traffic_type = "stream_one_shot"
    else:
        traffic_type = "json_one_shot"

    type_stats[traffic_type]["total"] += 1

    if kind == "BYPASS":
        sim_bypass += 1
        type_stats[traffic_type]["bypass"] += 1
        continue

    old_hash = ev["hash"]
    if old_hash in old_to_new:
        sim_key = old_to_new[old_hash]
    else:
        sim_key = f"proxy:{old_hash}"

    if sim_key in seen_keys:
        sim_hits += 1
        type_stats[traffic_type]["hit"] += 1
    else:
        sim_miss += 1
        type_stats[traffic_type]["miss"] += 1
        seen_keys.add(sim_key)

total_non_bypass = sim_hits + sim_miss
raw_global = sim_hits / total_non_bypass * 100 if total_non_bypass else 0

cachable_total = sum(v["total"] - v["bypass"] for k, v in type_stats.items() if k != "agent_stream_growing")
cachable_hits = sum(v["hit"] for k, v in type_stats.items() if k != "agent_stream_growing")
cachable_rate = cachable_hits / cachable_total * 100 if cachable_total else 0

# --- 4. Rapport ---
print()
print("=" * 70)
print("PHASE 4 - RAPPORT DE HIT RATE (mesure honnete)")
print("=" * 70)

print()
print("1. TAUX GLOBAL BRUT")
print(f"   Hits: {sim_hits} / {total_non_bypass} (non-bypass) = {raw_global:.1f}%")
print(f"   BYPASS exclus: {sim_bypass}")
print(f"   Trafic agent stream growing: {type_stats['agent_stream_growing']['total']} events")
print(f"   -> Hash unique a chaque tour (context croissant)")
print(f"   -> Structurellement non-cacheable")

print()
print("2. TAUX SUR TRAFIC CACHABLE")
print(f"   Definition: trafic NON agent-stream-growing (one-shot JSON + one-shot stream)")
print(f"   Exclut: {type_stats['agent_stream_growing']['total']} events agent context-growing")
print(f"   Cachable total: {cachable_total} events")
print(f"   Cachable hits: {cachable_hits}")
print(f"   Taux: {cachable_rate:.1f}%")
target = "ATTEINT" if 35 <= cachable_rate <= 55 else ("AU-DESSUS" if cachable_rate > 55 else "SOUS LA CIBLE")
print(f"   Cible: 35-55% -> {target}")

print()
print("3. TAUX PAR TYPE DE TRAFIC")
header = f"   {'Type':<25} {'Total':>6} {'HIT':>5} {'MISS':>5} {'BYPASS':>6} {'Rate':>7}"
print(header)
print(f"   {'-'*25} {'-'*6} {'-'*5} {'-'*5} {'-'*6} {'-'*7}")
for t, s in sorted(type_stats.items()):
    non_bypass = s["total"] - s["bypass"]
    rate = s["hit"] / non_bypass * 100 if non_bypass else 0
    print(f"   {t:<25} {s['total']:>6} {s['hit']:>5} {s['miss']:>5} {s['bypass']:>6} {rate:>6.1f}%")

print()
print("LIMITATIONS (honnêtete):")
print(f"- {len(cache_rows)} entrees cache ont leur original_prompt (sur {len(events)} events)")
print("- Pour les events sans prompt en cache, ancien hash utilise comme proxy")
print("- Le proxy sous-estime les HITs: deux events avec ancien hash different mais")
print("  nouveau hash identique (apres strip dates/tool_call_id) ne sont PAS detectes")
print("- Les 3 fixes activent le cross-session caching pour les futures sessions")
print("- Mesure reelle requiert l accumulation de trafic avec le nouveau code")

print()
print("IMPACT MESURABLE DES 3 FIXES SUR LE CACHE ACTUEL")
print(f"- {len(cache_rows)} entrees cache, {len(new_key_map)} cles uniques apres fixes")
if merged > 0:
    total_merged = sum(len(v) for v in new_key_map.values() if len(v) > 1)
    print(f"- {merged} merge(s): {total_merged} entrees -> {merged} cles")
    print("  -> Ces entrees auraient maintenant des HITs cross-session")
else:
    print("- 0 merge sur les entrees actuelles (toutes de la meme journee)")
    print("  -> Les merges se produiront sur des sessions de jours differents")

conn.close()
