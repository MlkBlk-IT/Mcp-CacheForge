import sys
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
lines = server_path.read_text(encoding="utf-8").splitlines()

# Backup
backup = server_path.with_suffix(".py.bak13")
backup.write_text(server_path.read_text(encoding="utf-8"), encoding="utf-8")

# Ajouter les imports HTMLResponse et FileResponse si absents
for i, line in enumerate(lines):
    if "from fastapi.responses import" in line and "HTMLResponse" not in line:
        lines[i] = line.rstrip() + ", HTMLResponse"
        if "FileResponse" not in lines[i]:
            lines[i] = lines[i] + ", FileResponse"
        break

# Trouver la ligne après @app.get("/health") bloc pour insérer les nouvelles routes
insert_idx = None
for i, line in enumerate(lines):
    if line.strip().startswith("return {") and i > 0 and "'status':" in lines[i-1] or "'status':" in line:
        # Trouver la fin du health endpoint (ligne vide suivante ou prochaine route)
        for j in range(i, min(i + 10, len(lines))):
            if lines[j].strip() == '' or (lines[j].strip().startswith('@app.') and j > i):
                insert_idx = j
                break
        if insert_idx:
            break

if insert_idx:
    dashboard_routes = [
        "",
        "@app.get(\"/dashboard\", response_class=HTMLResponse)",
        "async def dashboard():",
        "    \"\"\"Dashboard web pour monitoring cache\"\"\"",
        "    html_path = PROJECT_ROOT / \"static\" / \"dashboard.html\"",
        "    return HTMLResponse(html_path.read_text(encoding=\"utf-8\"))",
        "",
        "@app.get(\"/api/dashboard-stats\")",
        "async def dashboard_stats():",
        "    \"\"\"Stats JSON pour le dashboard (auto-refresh)\"\"\"",
        "    conn = sqlite3.connect(DB_FILE)",
        "    cursor = conn.cursor()",
        "    cursor.execute(\"SELECT prompt_hash, model, normalized_prompt, access_count, created_at FROM cache ORDER BY last_accessed DESC LIMIT 50\")",
        "    prompts = [{\"hash\": r[0][:8] + \"...\", \"model\": r[1], \"normalized_prompt\": r[2][:80] + (\"...\" if len(r[2]) > 80 else \"\"), \"access_count\": r[3], \"created_at\": r[4]} for r in cursor.fetchall()]",
        "    conn.close()",
        "    return {\"stats\": cache.get_stats(), \"target_provider\": CONFIG[\"proxy\"][\"target_provider\"], \"prompts\": prompts}",
        "",
        "@app.post(\"/api/cache-clear\")",
        "async def api_clear_cache():",
        "    \"\"\"Vide le cache via API\"\"\"",
        "    cache.clear()",
        "    return {\"message\": \"Cache vidé avec succès\", \"success\": True}",
    ]
    lines[insert_idx:insert_idx] = dashboard_routes
    print(f"✓ Routes /dashboard + /api ajoutées à la ligne {insert_idx}")
else:
    print("✗ Impossible de trouver le point d'insertion")
    sys.exit(1)

server_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

# Vérifier syntaxe
import subprocess
result = subprocess.run([r"D:\CacheForge\venv\Scripts\python.exe", "-m", "py_compile", str(server_path)], capture_output=True, text=True)
if result.returncode == 0:
    print("✓ Syntaxe valide")
else:
    print("✗ Erreur syntaxe:")
    print(result.stderr)
    sys.exit(1)
