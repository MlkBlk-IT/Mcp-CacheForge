# CacheForge Launcher
$ProjectRoot = "D:\CacheForge"
$venvPath = "$ProjectRoot\venv\Scripts\Activate.ps1"

# Activation du venv
& $venvPath

# Lancement du serveur
Write-Host "╔════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  CACHEFORGE - Démarrage                                   ║" -ForegroundColor Cyan
Write-Host "╚════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""
Write-Host "[*] Proxy HTTP: http://localhost:8080" -ForegroundColor Green
Write-Host "[*] MCP SSE:    http://localhost:8081/sse" -ForegroundColor Green
Write-Host "[*] MCP stdio:  Disponible via Claude Desktop" -ForegroundColor Green
Write-Host ""
Write-Host "[*] Logs: D:\CacheForge\logs\app.log" -ForegroundColor Gray
Write-Host "[*] DB:   D:\CacheForge\db\cache.db" -ForegroundColor Gray
Write-Host ""

python "$ProjectRoot\src\mcp_server.py"
