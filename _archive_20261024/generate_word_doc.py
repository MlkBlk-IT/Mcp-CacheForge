"""Genere CacheForge_Project_Documentation.docx — etat verifie du projet.

Sources de verite (ne jamais inventer de chiffre ici) :
  - logs/isolated_run.log   : suite de tests isoles
  - logs/test_run.log       : mesures hit rate / tokens
  - logs/granite_live.log   : test live modele
  - logs/prefix_proxy.log   : proxy de prefixe (mocks)
  - src/server.py, src/prefix_proxy.py, tray.py, mcp_stdio.py : code
  - config/cacheforge.json  : ports, TTL, regles

Usage: venv\\Scripts\\python.exe generate_word_doc.py
"""
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from pathlib import Path

ROOT = Path(r"D:\CacheForge")
OUT = ROOT / "CacheForge_Project_Documentation.docx"

doc = Document()

# --- Titre ---
title = doc.add_heading("CacheForge : Proxy LLM avec Cache Local", 0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.add_run("Statut : ").bold = True
p.add_run("v0.1 — coeur fonctionnel, en durcissement\n")
p.add_run("Derniere mise a jour : ").bold = True
p.add_run("2026-10-22\n")
p.add_run("Auteur : ").bold = True
p.add_run("Malik (MlkBlk-IT)")
p.add_run("\nDepot : ").bold = True
p.add_run("https://github.com/MlkBlk-IT/Mcp-CacheForge")

# --- 1. Problematique ---
doc.add_heading("1. Problematique", level=1)
doc.add_paragraph(
    "L'utilisation de modeles de langage locaux (via LM Studio) pour des taches IT recurrentes "
    "presente trois goulots d'etranglement :\n"
    "1. Latence : une requete identique rebrule du temps CPU/RAM pour une reponse deja calculee.\n"
    "2. Absence de cache semantique : les systèmes standards hachent le prompt brut. "
    "\"Explique Hyper-V\" et \"S'il te plait, explique Hyper-V stp\" generent deux requetes distinctes.\n"
    "3. Manque d'observabilite : impossible de mesurer les tokens et le temps reellement economises."
)

# --- 2. Architecture ---
doc.add_heading("2. Architecture", level=1)
doc.add_paragraph(
    "CacheForge est un proxy HTTP asynchrone (FastAPI) qui s'intercale entre le client et le "
    "backend LLM (LM Studio :1234). Il met en cache les reponses pour les resservir "
    "instantanement, sans regeneration CPU."
)

doc.add_heading("2.1 Pipeline de traitement", level=2)
for step in [
    "Reception : POST /v1/chat/completions sur 127.0.0.1:8080.",
    "Normalisation : NFKD -> lowercase -> Aho-Corasick (1114 regles de bruit : \"svp\", \"stp\", \"please\"...).",
    "Hachage : SHA256 du payload normalise (model + messages + temperature + tools + stream).",
    "Decision : HIT -> reponse instantanee + metriques | MISS -> forward vers LLM + sauvegarde SQLite.",
]:
    doc.add_paragraph(step, style="List Bullet")

doc.add_heading("2.2 Schema", level=2)
doc.add_paragraph(
    "Client -> PROXY 127.0.0.1:8080 (src/server.py)\n"
    "            |- normalisation NFKD + Aho-Corasick (1114 regles)\n"
    "            |- hash SHA256 du payload normalise\n"
    "            |- HIT  -> reponse immediate + metriques\n"
    "            |- MISS -> forward + sauvegarde (TTL 86400 s, max 10000 entrees)\n"
    "            |- dashboard /dashboard + /api/metrics\n"
    "         -> SQLite db/cache.db\n"
    "         -> UPSTREAM http://localhost:1234/v1/chat/completions (LM Studio)\n"
    "         + MCP 127.0.0.1:8081 (SSE) + stdio  (4 outils)\n"
    "         + PREFIX PROXY 127.0.0.1:8090 (src/prefix_proxy.py, DB separee db/prefix.db)\n"
    "              Anthropic : injecte cache_control | OpenAI : injecte prompt_cache_key\n"
    "              Mesure : GET /api/prefix-stats (tokens lus/ecrits dans le cache fournisseur)"
)

doc.add_heading("2.3 Stack technique", level=2)
for item in [
    "Backend : Python 3.12.10, FastAPI, uvicorn, httpx",
    "Cache : SQLite3 (rotation TTL 24 h / max 10 000 entrees)",
    "Normalisation : pyahocorasick 2.3.1, nltk 3.10.4 (Snowball Stemmer)",
    "MCP : mcp 1.30.0 (dual transport stdio + SSE)",
    "Infrastructure : pythonw.exe (headless), pystray (System Tray), Task Scheduler Windows",
    "Monitoring : dashboard web local (http://127.0.0.1:8080/dashboard)",
]:
    doc.add_paragraph(item, style="List Bullet")

# --- 3. Composants ---
doc.add_heading("3. Composants", level=1)
t = doc.add_table(rows=1, cols=2)
t.style = "Light Shading Accent 1"
t.rows[0].cells[0].text = "Composant"
t.rows[0].cells[1].text = "Role"
for name, role in [
    ("src/server.py", "Proxy FastAPI + serveur MCP (stdio/SSE), cache SQLite, dashboard"),
    ("src/prefix_proxy.py", "Proxy additif :8090, cache de prefixe cloud, ne stocke aucune cle API"),
    ("config/cacheforge.json", "Config proxy, MCP, cache, normalisation, logging"),
    ("config/normalization_rules.json", "1114 regles de normalisation (generees 2026-10-21 22:35)"),
    ("tray.py", "Icone systray : demarre proxy + MCP, ouvre le dashboard"),
    ("mcp_stdio.py", "Wrapper stdio pour Claude Desktop (n'ouvre aucun port)"),
    ("static/dashboard.html", "Dashboard web local (metriques, hit rate, export)"),
    ("tests/", "test_cacheforge.py, test_cacheforge_ext.py, test_prefix_proxy.py, run_isolated.py"),
    ("install_autostart.ps1 / uninstall_autostart.ps1", "Cycle de vie Windows (Task Scheduler)"),
]:
    c = t.add_row().cells
    c[0].text = name
    c[1].text = role

# --- 4. Outils MCP ---
doc.add_heading("4. Outils MCP exposes (4)", level=1)
t = doc.add_table(rows=1, cols=2)
t.style = "Light Shading Accent 1"
t.rows[0].cells[0].text = "Outil"
t.rows[0].cells[1].text = "Role"
for name, role in [
    ("get_cache_stats", "Statistiques du cache (entrees, hit rate, tokens economises)"),
    ("clear_cache", "Vide le cache (exige confirm)"),
    ("normalize_preview", "Previsualise la normalisation d'un texte sans le mettre en cache"),
    ("search_cached_prompts", "Recherche dans les prompts deja mis en cache"),
]:
    c = t.add_row().cells
    c[0].text = name
    c[1].text = role

# --- 5. Routes HTTP ---
doc.add_heading("5. Routes HTTP", level=1)
t = doc.add_table(rows=1, cols=3)
t.style = "Light Shading Accent 1"
for i, h in enumerate(["Route", "Methode", "Role"]):
    t.rows[0].cells[i].text = h
for route, method, role in [
    ("/v1/chat/completions", "POST", "Point d'entree principal (OpenAI-compatible)"),
    ("/health", "GET", "Sante du proxy"),
    ("/dashboard", "GET", "Interface web des metriques"),
    ("/api/dashboard-stats", "GET", "Stats JSON pour le dashboard"),
    ("/api/metrics", "GET", "Metriques agregees"),
    ("/api/metrics/series", "GET", "Series temporelles"),
    ("/api/metrics/export", "GET", "Export JSON (filtre ?since=)"),
    ("/api/cache-clear", "POST", "Vide le cache — exige le header X-CacheForge-Confirm (403 sinon)"),
]:
    c = t.add_row().cells
    c[0].text = route
    c[1].text = method
    c[2].text = role

# --- 6. Mesures reelles ---
doc.add_heading("6. Mesures reelles (preuves)", level=1)
t = doc.add_table(rows=1, cols=2)
t.style = "Light Shading Accent 1"
t.rows[0].cells[0].text = "Source"
t.rows[0].cells[1].text = "Resultat"
for src, res in [
    ("logs/isolated_run.log", "ISOLATED: ALL PASS — test_cacheforge.py + test_cacheforge_ext.py, ports liberes apres teardown"),
    ("logs/test_run.log", "38 requetes, 18 hits, 20 misses, hit rate 47,4 %, 31,0 % de tokens economises, events_without_usage=0"),
    ("logs/granite_live.log", "Test live granite-4.2-8b : 28 tokens in + 97 out economises sur un hit, temps original 20 023 ms"),
    ("logs/prefix_proxy.log", "Proxy prefixe OK sur mocks Anthropic/OpenAI ; retry sans injection si l'amont refuse cache_control"),
]:
    c = t.add_row().cells
    c[0].text = src
    c[1].text = res

# --- 7. Journal de developpement ---
doc.add_heading("7. Journal de developpement", level=1)
t = doc.add_table(rows=1, cols=3)
t.style = "Light Shading Accent 1"
for i, h in enumerate(["Date / Heure (EST)", "Phase / Action", "Resultat"]):
    t.rows[0].cells[i].text = h
for date, action, result in [
    ("2026-10-21 19:00", "Initialisation du proxy basique + forward vers LM Studio.", "Fonctionnel (MISS uniquement)"),
    ("2026-10-21 20:30", "Implementation SQLite + logique HIT/MISS basique.", "Fonctionnel, mais hachage sur texte brut"),
    ("2026-10-22 00:15", "Correction regression SQLite + suppression middleware Auth (Keyless).", "Stable"),
    ("2026-10-22 00:45", "Deploiement infrastructure headless : tray.py, Task Scheduler, dashboard.", "Fonctionnel"),
    ("2026-10-22 01:15", "Reecriture NormalizationPipeline (Aho-Corasick + NFKD).", "Valide : \"test\" == \"SVP test stp\""),
    ("2026-10-22 02:15", "Ajout des metriques avancees (saved_tokens, gen_time_ms).", "ECHEC : erreur 500 Internal Server Error"),
    ("2026-10-22 03:00", "Correction du 500 (injection des variables forward_start / saved_usage).", "RESOLU — suite isolee ALL PASS"),
    ("2026-10-22 13:59", "Bind MCP corrige : FastMCP(host, port) depuis la config + mcp.host 0.0.0.0 -> 127.0.0.1.", "RESOLU — plus aucune exposition LAN"),
]:
    c = t.add_row().cells
    c[0].text = date
    c[1].text = action
    c[2].text = result

# --- 8. Etat actuel ---
doc.add_heading("8. Etat actuel", level=1)
p = doc.add_paragraph()
p.add_run("v0.1 — coeur fonctionnel, en durcissement. ").bold = True
p.add_run(
    "Proxy HIT/MISS stable, normaliseur a frontieres de mot (plus de mutation de sous-chaines), "
    "metriques et dashboard operationnels, 4 outils MCP."
)

doc.add_heading("8.1 Resolu", level=2)
for item in [
    "Bug 500 (2026-10-22 02:15) : resolu, suite isolee ALL PASS.",
    "Exposition reseau MCP : mcp.host = 0.0.0.0 corrige en 127.0.0.1, et FastMCP desormais instancie avec host/port lus de la config (le defaut SDK 127.0.0.1:8000 etait ignore par mcp.run(transport='sse')).",
]:
    doc.add_paragraph(item, style="List Bullet")

doc.add_heading("8.2 Reste a faire / non couvert", level=2)
for item in [
    "test_prefix_proxy.py n'a pas de log de resultat explicite (seul logs/prefix_proxy.log existe, traces de mock OK).",
    "logs/granite_live.log n'a pas ete relu depuis le dernier test live.",
    "Outil MCP set_target_provider supprime (bak9) : le changement de provider se fait par edition de config uniquement.",
]:
    doc.add_paragraph(item, style="List Bullet")

# --- 9. Installation ---
doc.add_heading("9. Installation", level=1)
doc.add_paragraph(
    "1. python -m venv venv\n"
    "2. .\\venv\\Scripts\\Activate.ps1\n"
    "3. pip install -r requirements.txt\n"
    "4. .\\start_cacheforge.ps1        (demarrage manuel)\n"
    "5. .\\install_autostart.ps1        (demarrage au login, Task Scheduler)"
)

t = doc.add_table(rows=1, cols=2)
t.style = "Light Shading Accent 1"
t.rows[0].cells[0].text = "Variable d'environnement"
t.rows[0].cells[1].text = "Role"
for var, role in [
    ("CACHEFORGE_DB", "Chemin de la DB de cache (defaut db/cache.db)"),
    ("CACHEFORGE_LOG", "Chemin du log (defaut logs/app.log)"),
    ("CACHEFORGE_TARGET", "Surcharge de l'upstream (utilise par les tests isoles)"),
    ("PREFIX_DB", "DB du proxy prefixe (defaut db/prefix.db)"),
    ("PREFIX_URL_<NOM>", "Surcharge de l'URL amont du proxy prefixe"),
]:
    c = t.add_row().cells
    c[0].text = var
    c[1].text = role

doc.save(str(OUT))
print(f"OK : {OUT}")
