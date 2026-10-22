# D:\CacheForge\uninstall_autostart.ps1
$taskName = "CacheForge-Tray"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "✓ Tâche '$taskName' supprimée"
} else {
    Write-Host "ℹ Aucune tâche '$taskName' trouvée"
}

# Tuer tout processus tray.py actif
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object {
    try { $_.CommandLine -like "*tray.py*" } catch { $false }
} | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "✓ Processus tray arrêtés (si présents)"
