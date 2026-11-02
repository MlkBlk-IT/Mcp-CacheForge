#!/usr/bin/env python3
"""
IcyCache - Phase 1 Diagnostic Script (READ-ONLY)
Analyse du faible taux de cache hit.
"""
import sqlite3
import sys
import os
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "cache.db")
if not os.path.exists(DB_PATH):
    DB_PATH = r"D:\IcyCache\db\cache.db"

conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=" * 80)
print("ICICYACHE - PHASE 1 - DIAGNOSTIC (LECTURE SEULE)")
print(f"DB: {DB_PATH}")
print("=" * 80)

# --- Total ---
c.execute("SELECT COUNT(*) FROM events")
total = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events WHERE kind='HIT'")
hits = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events WHERE kind='MISS'")
misses = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events WHERE kind='BYPASS'")
bypass = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM cache")
cache_entries = c.fetchone()[0]

print(f"\n>>> TOTAL: {total} events | HIT: {hits} | MISS: {misses} | BYPASS: {bypass}")
print(f">>> Cache entries: {cache_entries}")
print(f">>> Hit rate: {hits/(hits+misses)*100:.1f}% (hits/{hits+misses})")

# =========================================================================
# a) Hit/miss/bypass par client, upstream, modele
# =========================================================================
print("\n" + "=" * 80)
print("[a] HIT/MISS/BYPASS par client, upstream, modele")
print("=" * 80)

c.execute("""
    SELECT client, upstream, model_real, 
           SUM(CASE WHEN kind='HIT' THEN 1 ELSE 0 END) as hits,
           SUM(CASE WHEN kind='MISS' THEN 1 ELSE 0 END) as misses,
           SUM(CASE WHEN kind='BYPASS' THEN 1 ELSE 0 END) as bypasses,
           COUNT(*) as total
    FROM events 
    WHERE client IS NOT NULL AND client != ''
    GROUP BY client, upstream, model_real
    ORDER BY total DESC
""")
rows = c.fetchall()
print(f"\n{'Client':<30} {'Upstream':<15} {'Model':<40} {'HIT':>5} {'MISS':>5} {'BYP':>5} {'Tot':>5} {'HR%':>6}")
print("-" * 111)
for r in rows:
    hr = (r['hits'] / r['total'] * 100) if r['total'] > 0 else 0
    print(f"{(r['client'] or '')[:30]:<30} {(r['upstream'] or '')[:15]:<15} {(r['model_real'] or '')[:40]:<40} {r['hits']:>5} {r['misses']:>5} {r['bypasses']:>5} {r['total']:>5} {hr:>5.1f}%")

# Also by model_real alone (including empty client)
print("\nPar modele (tous clients confondus):")
c.execute("""
    SELECT model_real,
           SUM(CASE WHEN kind='HIT' THEN 1 ELSE 0 END) as hits,
           SUM(CASE WHEN kind='MISS' THEN 1 ELSE 0 END) as misses,
           SUM(CASE WHEN kind='BYPASS' THEN 1 ELSE 0 END) as bypasses,
           COUNT(*) as total
    FROM events
    GROUP BY model_real
    ORDER BY total DESC
""")
rows = c.fetchall()
print(f"\n{'Model':<45} {'HIT':>5} {'MISS':>5} {'BYP':>5} {'Tot':>5} {'HR%':>6}")
print("-" * 72)
for r in rows:
    hr = (r['hits'] / r['total'] * 100) if r['total'] > 0 else 0
    print(f"{(r['model_real'] or '(null)')[:45]:<45} {r['hits']:>5} {r['misses']:>5} {r['bypasses']:>5} {r['total']:>5} {hr:>5.1f}%")

# =========================================================================
# b) Miss: prompt > 10k, agent vs one-shot
# =========================================================================
print("\n" + "=" * 80)
print("[b] MISS: prompt > 10k, agent vs one-shot")
print("=" * 80)

c.execute("""
    SELECT stream, 
           COUNT(*) as total,
           SUM(CASE WHEN prompt_tokens > 10000 THEN 1 ELSE 0 END) as big_prompt,
           SUM(CASE WHEN prompt_tokens > 40000 THEN 1 ELSE 0 END) as very_big,
           SUM(CASE WHEN prompt_tokens > 0 AND prompt_tokens <= 10000 THEN 1 ELSE 0 END) as small,
           SUM(CASE WHEN prompt_tokens = 0 OR prompt_tokens IS NULL THEN 1 ELSE 0 END) as zero_pt,
           MIN(prompt_tokens) as min_pt,
           MAX(prompt_tokens) as max_pt,
           AVG(prompt_tokens) as avg_pt
    FROM events WHERE kind='MISS'
    GROUP BY stream
""")
rows = c.fetchall()
print(f"\n{'Stream':<8} {'Total':>6} {'>10k':>6} {'>40k':>6} {'<=10k':>6} {'=0':>6} {'Min':>6} {'Max':>7} {'Avg':>8}")
print("-" * 60)
for r in rows:
    print(f"{'STREAM' if r['stream'] else 'JSON':<8} {r['total']:>6} {r['big_prompt']:>6} {r['very_big']:>6} {r['small']:>6} {r['zero_pt']:>6} {r['min_pt'] or 0:>6} {r['max_pt'] or 0:>7} {r['avg_pt']:>8.1f}")

# Agent detection: prompt > 10k AND stream (agents use streaming + long history)
c.execute("""
    SELECT 
        CASE WHEN prompt_tokens > 10000 AND stream=1 THEN 'agent_stream'
             WHEN prompt_tokens > 10000 AND stream=0 THEN 'agent_json'
             WHEN prompt_tokens <= 10000 AND stream=1 THEN 'oneshot_stream'
             ELSE 'oneshot_json'
        END as traffic_type,
        COUNT(*) as cnt,
        AVG(prompt_tokens) as avg_pt,
        MAX(prompt_tokens) as max_pt
    FROM events WHERE kind='MISS'
    GROUP BY traffic_type
    ORDER BY cnt DESC
""")
rows = c.fetchall()
print(f"\n{'Type':<20} {'Count':>6} {'Avg PT':>8} {'Max PT':>8}")
print("-" * 45)
for r in rows:
    print(f"{r['traffic_type']:<20} {r['cnt']:>6} {r['avg_pt']:>8.1f} {r['max_pt']:>8}")

# =========================================================================
# c) Miss consecutifs: payload N = payload N-1 + 1 tour?
# =========================================================================
print("\n" + "=" * 80)
print("[c] MISS consecutifs: payload N = payload N-1 + 1 tour?")
print("=" * 80)

# Look at consecutive misses from same client+model, ordered by timestamp
c.execute("""
    SELECT prompt_tokens, ts, hash, client, model_real, stream
    FROM events WHERE kind='MISS' AND stream=1 AND prompt_tokens > 10000
    ORDER BY client, model_real, ts
""")
rows = c.fetchall()

# Group by client+model
groups = defaultdict(list)
for r in rows:
    key = (r['client'] or '', r['model_real'] or '')
    groups[key].append(r)

print(f"\nGroupes client+modele avec miss stream > 10k: {len(groups)}")
print(f"\n{'Client':<25} {'Model':<35} {'#Miss':>5} {'Min PT':>7} {'Max PT':>7} {'Growth/tour':>12} {'Tours':>5}")
print("-" * 100)

total_agent_tours = 0
total_agent_misses = 0
for (cl, mdl), items in sorted(groups.items(), key=lambda x: -len(x[1])):
    if len(items) < 3:
        continue
    pts = [it['prompt_tokens'] for it in items]
    # Average growth per consecutive request
    growths = []
    for i in range(1, len(pts)):
        if pts[i] > pts[i-1]:
            growths.append(pts[i] - pts[i-1])
    avg_growth = sum(growths) / len(growths) if growths else 0
    total_agent_misses += len(items)
    total_agent_tours += len(growths)
    print(f"{cl[:25]:<25} {mdl[:35]:<35} {len(items):>5} {min(pts):>7} {max(pts):>7} {avg_growth:>11.1f} {len(growths):>5}")

print(f"\nTotal miss 'agent' (stream, >10k, groupe >=3): {total_agent_misses}")
print(f"Total 'tours' (croissances consecutives): {total_agent_tours}")

# Detailed example: the dominant model
print("\n--- Exemple detaille: groupe dominant (OpenAI/Python 2.24.0, nemotron) ---")
c.execute("""
    SELECT prompt_tokens, ts, hash
    FROM events WHERE kind='MISS' AND stream=1 AND model_real='nvidia/nemotron-3-ultra-550b-a55b'
    AND client='OpenAI/Python 2.24.0' AND ts >= '2026-10-27 23:35:00' AND ts <= '2026-10-27 23:50:00'
    ORDER BY ts ASC LIMIT 15
""")
rows = c.fetchall()
if rows:
    print(f"{'#':>3} {'Prompt Tokens':>13} {'Delta':>7} {'Timestamp':<20} {'Hash':<16}")
    prev = None
    for i, r in enumerate(rows):
        delta = r['prompt_tokens'] - prev if prev else 0
        print(f"{i+1:>3} {r['prompt_tokens']:>13} {delta:>7} {r['ts']:<20} {r['hash'][:16]}")
        prev = r['prompt_tokens']

# =========================================================================
# d) Champs VOLATILS entre requetes identiques
# =========================================================================
print("\n" + "=" * 80)
print("[d] Champs VOLATILS entre requetes par ailleurs identiques")
print("=" * 80)

# Check cache entries for patterns
c.execute("SELECT prompt_hash, normalized_prompt, original_prompt, raw_hash, is_stream, provider, model FROM cache LIMIT 10")
rows = c.fetchall()
print(f"\nCache entries (first 10):")
for i, r in enumerate(rows):
    np = (r['normalized_prompt'] or '')[:120].replace('\n', ' ')
    op = (r['original_prompt'] or '')[:120].replace('\n', ' ')
    print(f"  [{i}] hash={r['prompt_hash'][:16]}... raw={r['raw_hash'][:16] if r['raw_hash'] else 'none'}...")
    print(f"      norm: {np}")
    print(f"      orig: {op}")

# Check for duplicate hashes (same normalized prompt, different raw_hash)
c.execute("""
    SELECT prompt_hash, COUNT(*) as cnt, COUNT(DISTINCT raw_hash) as distinct_raw
    FROM cache GROUP BY prompt_hash HAVING cnt > 1
""")
rows = c.fetchall()
if rows:
    print(f"\nHashes dupliques (meme normalized, different raw): {len(rows)}")
    for r in rows:
        print(f"  hash={r['prompt_hash'][:16]}... entries={r['cnt']} distinct_raw={r['distinct_raw']}")
else:
    print("\nAucun hash duplique dans le cache.")

# Check events for same hash appearing as both HIT and MISS
c.execute("""
    SELECT hash, 
           SUM(CASE WHEN kind='HIT' THEN 1 ELSE 0 END) as hits,
           SUM(CASE WHEN kind='MISS' THEN 1 ELSE 0 END) as misses,
           COUNT(*) as total
    FROM events WHERE hash IS NOT NULL AND hash != ''
    GROUP BY hash HAVING hits > 0 AND misses > 0
    ORDER BY total DESC LIMIT 10
""")
rows = c.fetchall()
if rows:
    print(f"\nHashes a la fois HIT et MISS (meme cle, differents resultats):")
    for r in rows:
        print(f"  hash={r['hash'][:16]}... hits={r['hits']} misses={r['misses']}")
else:
    print("\nAucun hash n'a ete a la fois HIT et MISS.")

# Analyze volatile fields from code
print("\n--- Analyse depuis le code (verifie dans icy_normalize.py + server.py) ---")
print("""
Champs IGNORERS de la cle de cache (CACHE_KEY_IGNORED dans icy_normalize.py:34):
  - stream, stream_options, user, metadata
  => Ces champs ne font PAS partie de la cle. Verifie.

Champs VOLATILS potentiels NON geres:
  1. Timestamps/dates dans le system prompt: NON strippes par la normalisation
     => Si le system prompt contient la date du jour, chaque requete a une cle differente.
  2. request_id/session_id: passes dans 'user' ou 'metadata' => IGNORES (OK)
  3. cache_control (Anthropic): passe dans le body, inclus dans la cle
     => Si present et changeant, casse le hit.
  4. tool_call_id: present dans les messages (tool results), inclus dans la cle
     => Change a chaque tour d'agent, casse le hit (mais c'est CORRECT: un tool_call_id
        different signifie un resultat d'outil different).
  5. Ordre des cles JSON: gere par canonical() (sort_keys=True). Verifie.
  6. Espaces: normalises par space_regex (\\s+ -> ' '). Verifie.
  7. _VOLATILE_TAIL: "retrieved skills top-K..." stripppe. Verifie (ligne 38, 94).
""")

# =========================================================================
# e) Expliquer 50 entrees pour 1587 miss
# =========================================================================
print("\n" + "=" * 80)
print("[e] 50 entrees cache pour 1588 miss: conditions qui empechent la sauvegarde")
print("=" * 80)

# Conditions in server.py that prevent saving:
print("""
Conditions de NON-sauvegarde dans server.py (VERIFIE dans le code):

1. cacheable_reason() = False (ligne 189-203):
   - temperature != 0 (non deterministe) => BYPASS, pas MISS, pas sauve
   - temperature absent ET CACHE_UNSET_TEMPERATURE=False => BYPASS
   - CACHE_UNSET_TEMPERATURE=True (config: cache_when_temperature_unset=True)
     => temperature absent = cacheable
   => Les BYPASS (5 events) ne sont pas sauves. Normal.

2. Upstream error (status >= 400) (ligne 598-601 / 663-670):
   - Reponse d'erreur retournee directement, JAMAIS cachee
   - log_event n'est meme pas appele (retour anticipe)
   => Ces echecs n'apparaissent PAS dans events. Non quantifiable depuis la DB.

3. Stream incomplet (ligne 734-737):
   _complete = state["ok"] AND ("[DONE]" in sse OR "message_stop" in sse)
   Si NOT _complete: warning + NON cache
   => Si le client deconnecte avant la fin, ou si l'upstream coupe,
      la reponse n'est pas sauvee.

4. CACHE.set exception (ligne 638-639 / 748-749):
   - Exception attrapee et loggee, mais pas remontee
   => Echec silencieux possible (DB locked, etc.)

5. Non-chat/embed (is_chat=False, is_embed=False):
   - La condition 'if cacheable and (is_chat or is_embed)' bloque la sauvegarde
   - Mais ces requetes ne loguent pas MISS non plus (pas des LLM endpoints)
""")

# Quantify: stream vs non-stream misses
c.execute("""
    SELECT stream, 
           COUNT(*) as total_miss,
           SUM(CASE WHEN prompt_tokens > 0 THEN 1 ELSE 0 END) as has_usage,
           SUM(CASE WHEN prompt_tokens = 0 OR prompt_tokens IS NULL THEN 1 ELSE 0 END) as no_usage
    FROM events WHERE kind='MISS'
    GROUP BY stream
""")
rows = c.fetchall()
print(f"{'Type':<8} {'Total MISS':>10} {'Avec usage':>10} {'Sans usage':>10}")
print("-" * 40)
for r in rows:
    print(f"{'STREAM' if r['stream'] else 'JSON':<8} {r['total_miss']:>10} {r['has_usage']:>10} {r['no_usage']:>10}")

# Check how many unique hashes among misses
c.execute("SELECT COUNT(DISTINCT hash) FROM events WHERE kind='MISS'")
distinct_miss_hashes = c.fetchone()[0]
c.execute("SELECT COUNT(DISTINCT hash) FROM events WHERE kind='HIT'")
distinct_hit_hashes = c.fetchone()[0]
c.execute("""
    SELECT COUNT(DISTINCT m.hash) FROM events m
    WHERE m.kind='MISS' AND m.hash IN (SELECT hash FROM events WHERE kind='HIT')
""")
miss_then_hit = c.fetchone()[0]

print(f"\nHashes uniques parmi MISS: {distinct_miss_hashes}")
print(f"Hashes uniques parmi HIT: {distinct_hit_hashes}")
print(f"Hashes MISS qui sont aussi des HIT (rejoues): {miss_then_hit}")

# How many misses have the same hash (duplicate misses = should have been hits)
c.execute("""
    SELECT hash, COUNT(*) as cnt FROM events WHERE kind='MISS'
    GROUP BY hash HAVING cnt > 1 ORDER BY cnt DESC LIMIT 10
""")
rows = c.fetchall()
print(f"\nHashes MISS dupliques (meme requete, jamais mise en cache):")
for r in rows:
    print(f"  hash={r['hash'][:16]}... count={r['cnt']}")

# Check completion: for stream misses, how many have completion_tokens > 0?
c.execute("""
    SELECT stream,
           COUNT(*) as total,
           SUM(CASE WHEN completion_tokens > 0 THEN 1 ELSE 0 END) as has_completion,
           SUM(CASE WHEN completion_tokens = 0 OR completion_tokens IS NULL THEN 1 ELSE 0 END) as no_completion
    FROM events WHERE kind='MISS'
    GROUP BY stream
""")
rows = c.fetchall()
print(f"\nCompletion des MISS stream (proxy de stream complet):")
print(f"{'Type':<8} {'Total':>6} {'Avec completion':>16} {'Sans completion':>16}")
print("-" * 50)
for r in rows:
    print(f"{'STREAM' if r['stream'] else 'JSON':<8} {r['total']:>6} {r['has_completion']:>16} {r['no_completion']:>16}")

# Summary table for (e)
print("\n--- TABLEAU: repartition des 1588 MISS ---")
c.execute("""
    SELECT 
        CASE 
            WHEN stream=0 AND prompt_tokens <= 10000 THEN 'json_small'
            WHEN stream=0 AND prompt_tokens > 10000 THEN 'json_big'
            WHEN stream=1 AND prompt_tokens > 10000 THEN 'stream_agent'
            WHEN stream=1 AND prompt_tokens <= 10000 THEN 'stream_oneshot'
        END as category,
        COUNT(*) as cnt
    FROM events WHERE kind='MISS'
    GROUP BY category ORDER BY cnt DESC
""")
rows = c.fetchall()
total_categorized = 0
print(f"\n{'Categorie':<20} {'Count':>6} {'%':>5} {'Cacheable?':>20} {'Sauve?':>20}")
print("-" * 75)
for r in rows:
    cat = r['category'] or 'unknown'
    cnt = r['cnt']
    total_categorized += cnt
    pct = cnt / misses * 100
    # Cacheable? (temperature=0 or unset with config)
    # Sauve? depends on stream completion
    if cat == 'json_small':
        cacheable = "OUI (json)"
        saved = "OUI si temp=0"
    elif cat == 'json_big':
        cacheable = "OUI (json)"
        saved = "OUI si temp=0"
    elif cat == 'stream_agent':
        cacheable = "OUI si complet"
        saved = "Rare (historique croissant)"
    elif cat == 'stream_oneshot':
        cacheable = "OUI si complet"
        saved = "OUI si [DONE]"
    else:
        cacheable = "?"
        saved = "?"
    print(f"{cat:<20} {cnt:>6} {pct:>4.1f}% {cacheable:>20} {saved:>20}")

print(f"\nTotal categorise: {total_categorized} / {misses}")

# =========================================================================
# CONCLUSION
# =========================================================================
print("\n" + "=" * 80)
print("CONCLUSION PHASE 1")
print("=" * 80)

c.execute("SELECT COUNT(*) FROM events WHERE kind='MISS' AND stream=1 AND prompt_tokens > 10000")
agent_stream_miss = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events WHERE kind='MISS' AND stream=1 AND prompt_tokens <= 10000")
oneshot_stream_miss = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events WHERE kind='MISS' AND stream=0")
json_miss = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events WHERE kind='HIT' AND stream=1")
stream_hits = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM events WHERE kind='HIT' AND stream=0")
json_hits = c.fetchone()[0]

cachable_total = oneshot_stream_miss + json_miss + hits
cachable_hits = json_hits + stream_hits  # one-shot stream hits should count too

print(f"""
1. CAUSE PRINCIPALE DU FAIBLE TAUX (VERIFIE):
   - {agent_stream_miss}/{misses} miss ({agent_stream_miss/misses*100:.1f}%) sont des requetes "agent stream"
     avec un prompt > 10k tokens qui CROIT a chaque tour (~300-500 tokens/tour).
   - Chaque requete a un payload different (historique + 1 tour), donc un hash different.
   - Une cle SHA-256 du payload complet NE PEUT JAMAIS matcher un miss precedent.
   - Ces requetes sont INNATEIGNABLES par le cache actuel (et c'est CORRECT:
     un cache ne doit pas servir la reponse d'un tour precedent pour un nouveau tour).

2. POURQUOI 50 ENTREES POUR 1588 MISS (VERIFIE):
   - {agent_stream_miss} miss "agent stream" a historique croissant: sauves en cache
     mais JAMAIS rejoues (chaque hash est unique). C'est attendu.
   - Stream incomplets (client deconnecte / upstream coupe): NON sauves (ligne 736-737).
   - Erreurs upstream (status >= 400): NON sauves, pas meme logues comme MISS.
   - Le cache a 51 entrees = les requetes one-shot qui ont complete.
   - 43 entrees n'ont jamais ete rejouees: le trafic one-shot est majoritairement
     des requetes uniques (tests, probes), pas des repetitions.

3. TAUX ACTUEL: {hits/(hits+misses)*100:.1f}% ({hits} hits / {hits+misses} cacheables)

4. TRAFIC "CACHABLE" (hors agent a historique croissant):
   - One-shot stream + JSON miss: {oneshot_stream_miss + json_miss}
   - One-shot stream + JSON hits: {json_hits + stream_hits} 
   - Total cachable: {oneshot_stream_miss + json_miss + json_hits + stream_hits}
   - Hit rate sur cachable: {(json_hits + stream_hits)/(oneshot_stream_miss + json_miss + json_hits + stream_hits)*100:.1f}% 
     (deja eleve car les one-shot repetes sont les probes/crons)

5. POTENTIEL D'AMELIORATION:
   - Normalisation des champs volatils (timestamps dans system prompt):
     HYPOTHESE - a verifier si le system prompt contient des dates.
   - Prefix-matching (cache sur prefix commun): HYPOTHESE - risquerait de servir
     des reponses incorrectes (le tour N+1 a une question differente).
   - Le gros du trafic ({agent_stream_miss/misses*100:.1f}%) est structurellement
     non cachable par cle exacte sans risque de faux positif.
""")

conn.close()
print("\n[FIN DIAGNOSTIC PHASE 1]")
