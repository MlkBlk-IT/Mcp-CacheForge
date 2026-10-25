import json, time, hashlib
from pathlib import Path

p = Path(r'D:\CacheForge\src\server.py')
code = p.read_text(encoding='utf-8')

# FIX 1: Normaliser AVANT de hasher
old_block1 = """    cache_payload = {
        "model": body.get('model', ''),
        "system": next((m.get('content') for m in messages if m.get('role') == 'system'), ''),
        "messages": [m for m in messages if m.get('role') == 'user'],
        "temperature": body.get('temperature', 0.0),
        "tools": [t.get('function', {}).get('name') for t in body.get('tools', [])] if body.get('tools') else [],
        # FIX: stream retiré du hash (évite 2× coût provider)
    }

    # Normalisation du dernier message user (pour logs/display)
    user_prompt = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
    # FIX: Gérer le contenu multimodal (vision) — content peut être une liste
    if isinstance(user_prompt, list):
        # Extraire le texte des blocs textuels
        text_parts = [item.get('text', '') for item in user_prompt if item.get('type') == 'text']
        user_prompt_text = ' '.join(text_parts)
    else:
        user_prompt_text = user_prompt
    normalized = normalizer.normalize(user_prompt_text)
    logger.info(f"Normalized prompt: {normalized[:100]}...")

    # Hash du payload complet (clé de cache robuste)
    import json
    p_hash = hashlib.sha256(json.dumps(cache_payload, sort_keys=True).encode('utf-8')).hexdigest()"""

new_block1 = """    # 1. Normaliser D'ABORD pour que le hash soit basé sur le texte nettoyé
    user_prompt = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
    if isinstance(user_prompt, list):
        text_parts = [item.get('text', '') for item in user_prompt if item.get('type') == 'text']
        user_prompt_text = ' '.join(text_parts)
    else:
        user_prompt_text = str(user_prompt)
    
    normalized_text = normalizer.normalize(user_prompt_text)
    logger.info(f"Normalized prompt: {normalized_text[:100]}...")

    # 2. Construire le payload avec le texte NORMALISÉ (C'est la clé du cache sémantique)
    normalized_messages = [{"role": "user", "content": normalized_text}]
    
    cache_payload = {
        "model": body.get('model', ''),
        "system": next((m.get('content') for m in messages if m.get('role') == 'system'), ''),
        "messages": normalized_messages,
        "temperature": body.get('temperature', 0.0),
        "tools": [t.get('function', {}).get('name') for t in body.get('tools', [])] if body.get('tools') else [],
    }

    import json
    p_hash = hashlib.sha256(json.dumps(cache_payload, sort_keys=True).encode('utf-8')).hexdigest()"""

if old_block1 in code:
    code = code.replace(old_block1, new_block1)
    print('✓ FIX 1: Le hash est maintenant basé sur le texte normalisé.')
else:
    print('⚠ FIX 1: Bloc 1 introuvable (déjà modifié).')

# FIX 2: Sauvegarder les métriques au MISS (Non-stream)
old_block2 = """            content = resp_json.get('choices', [{}])[0].get('message', {}).get('content', '')

            cache.set(
                prompt_hash=p_hash,
                normalized=normalized,
                original=user_prompt,
                response=content,
                is_stream=False,
                provider=target_url,
                model=body.get('model', 'unknown')
            )"""

new_block2 = """            message = resp_json.get('choices', [{}])[0].get('message', {})
            usage = resp_json.get('usage', {})
            gen_time_ms = int((time.time() - forward_start) * 1000)
            
            cache_data = {
                "message": message,
                "usage": usage,
                "gen_time_ms": gen_time_ms,
                "created_ts": int(time.time())
            }
            
            cache.set(
                prompt_hash=p_hash,
                normalized=normalized_text,
                original=user_prompt_text,
                response=json.dumps(cache_data, ensure_ascii=False),
                is_stream=False,
                provider=target_url,
                model=body.get('model', 'unknown')
            )"""

if old_block2 in code:
    code = code.replace(old_block2, new_block2)
    print('✓ FIX 2: Métriques (usage + temps) sauvegardées au MISS.')
else:
    print('⚠ FIX 2: Bloc 2 introuvable.')

# FIX 3: Extraire et renvoyer les métriques au HIT
old_block3 = """                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "cached": True,"""

new_block3 = """                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "saved_prompt_tokens": saved_usage.get("prompt_tokens", 0),
                "saved_completion_tokens": saved_usage.get("completion_tokens", 0),
                "saved_total_tokens": saved_usage.get("total_tokens", 0),
                "original_gen_time_ms": gen_time,
                "cache_age_seconds": cache_age_sec,
                "hit_count": cached.get('access_count', 1),
                "cached": True,"""

if old_block3 in code:
    code = code.replace(old_block3, new_block3)
    print('✓ FIX 3: Métriques injectées dans la réponse HIT.')
else:
    print('⚠ FIX 3: Bloc 3 introuvable.')

# FIX 4: S'assurer que forward_start est défini avant le POST
if 'forward_start = time.time()' not in code:
    code = code.replace(
        'async with httpx.AsyncClient(timeout=timeout) as client:\n            resp = await client.post(target_url, json=body, headers=headers)',
        'forward_start = time.time()\n        async with httpx.AsyncClient(timeout=timeout) as client:\n            resp = await client.post(target_url, json=body, headers=headers)'
    )
    print('✓ FIX 4: Timer forward_start ajouté.')

p.write_text(code, encoding='utf-8')
print('\\n✅ Fichier server.py mis à jour.')
