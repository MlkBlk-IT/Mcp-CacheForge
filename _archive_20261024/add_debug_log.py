# D:\CacheForge\add_debug_log.py
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
lines = server_path.read_text(encoding="utf-8").splitlines()

# Chercher la ligne où on forward au provider
for i, line in enumerate(lines):
    if 'async with httpx.AsyncClient(timeout=timeout) as client:' in line and i > 380:
        indent = "        "
        # Ajouter un debug log juste avant
        new_lines = [
            "",
            f"{indent}# DEBUG: Log du body envoyé au provider",
            f"{indent}logger.debug(f\"Forwarding body: {{json.dumps(body, ensure_ascii=False)}}\")",
            f"{indent}logger.debug(f\"Forwarding headers: {{headers}}\")"
        ]
        lines[i:i] = new_lines
        print(f"✓ Debug logging ajouté (avant ligne {i+1})")
        break

server_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("✓ Redémarre le serveur avec --log-level DEBUG pour voir les logs détaillés")
