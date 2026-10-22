# D:\CacheForge\patch_full_metrics.py
from pathlib import Path
import subprocess
import time

server_path = Path(r"D:\CacheForge\src\server.py")
content = server_path.read_text(encoding="utf-8")

# === 1. Stocker les métriques complètes au MISS (non-streaming) ===
# Remplacer le bloc de sauvegarde pour inclure temps de génération + usage complet
old_save = '''            # FIX: Stocker le message complet (gère content ET tool_calls)
            message = resp_json.get('choices', [{}])[0].get('message', {})
            response_to_cache = json.dumps({"message": message, "usage": resp_json.get("usage", {})}, ensure_ascii=False)'''

new_save = '''            # FIX: Stocker le message complet + métriques de performance
            message = resp_json.get('choices', [{}])[0].get('message', {})
            usage = resp_json.get("usage", {})
            gen_time_ms = int((time.time() - forward_start) * 1000)
            cache_data = {
                "message": message,
                "usage": usage,
                "generation_time_ms": gen_time_ms,
                "created_timestamp": int(time.time())
            }
            response_to_cache = json.dumps(cache_data, ensure_ascii=False)'''

content = content.replace(old_save, new_save)
print("✓ Métriques complètes stockées au MISS")

# === 2. Ajouter le timer au début du forward non-streaming ===
old_forward = '            resp = await client.post(target_url, json=body, headers=headers)'
new_forward = '            forward_start = time.time()\n            resp = await client.post(target_url, json=body, headers=headers)'
content = content.replace(old_forward, new_forward, 1)
print("✓ Timer de génération ajouté")

# === 3. Extraire toutes les métriques au HIT ===
old_extract = '''            try:
                cached_data = json.loads(cached['response'])
                cached_message = cached_data.get("message", {"role": "assistant", "content": cached['response']})
                saved_usage = cached_data.get("usage", {})
            except json.JSONDecodeError:
                cached_message = {"role": "assistant", "content": cached['response']}
                saved_usage = {}'''

new_extract = '''            try:
                cached_data = json.loads(cached['response'])
                cached_message = cached_data.get("message", {"role": "assistant", "content": cached['response']})
                saved_usage = cached_data.get("usage", {})
                gen_time_ms = cached_data.get("generation_time_ms", 0)
                created_ts = cached_data.get("created_timestamp", 0)
                cache_age_sec = int(time.time()) - created_ts if created_ts else 0
            except json.JSONDecodeError:
                cached_message = {"role": "assistant", "content": cached['response']}
                saved_usage = {}
                gen_time_ms = 0
                cache_age_sec = 0'''

content = content.replace(old_extract, new_extract)
print("✓ Extraction métriques HIT ajoutée")

# === 4. Renvoyer toutes les métriques dans la réponse HIT ===
old_metrics = '''                "saved_tokens": saved_usage.get("total_tokens", 0),
                "cached": True,'''

new_metrics = '''                "saved_prompt_tokens": saved_usage.get("prompt_tokens", 0),
                "saved_completion_tokens": saved_usage.get("completion_tokens", 0),
                "saved_total_tokens": saved_usage.get("total_tokens", 0),
                "original_generation_time_ms": gen_time_ms,
                "cache_age_seconds": cache_age_sec,
                "hit_count": cached.get('access_count', 1),
                "estimated_cost_saved_usd": round(saved_usage.get("total_tokens", 0) * 0.00003, 6),
                "cached": True,'''

content = content.replace(old_metrics, new_metrics)
print("✓ Toutes les métriques ajoutées à la réponse HIT")

server_path.write_text(content, encoding="utf-8")

# Vérification syntaxe
result = subprocess.run([r"D:\CacheForge\venv\Scripts\python.exe", "-m", "py_compile", str(server_path)], capture_output=True, text=True)
if result.returncode == 0:
    print("✓ Syntaxe valide")
    subprocess.run([r"D:\CacheForge\restart_tray.ps1"], shell=True)
    print("✓ Serveur redémarré")
else:
    print("✗ Erreur syntaxe:")
    print(result.stderr)
