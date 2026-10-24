# D:\IcyCache\uninstall_autostart.ps1
$ErrorActionPreference = "SilentlyContinue"

foreach ($taskName in @("IcyCache-Tray", "CacheForge-Tray")) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        if ($?) { Write-Host "✓ Tâche '$taskName' supprimée" }
        else { Write-Warning "Tâche '$taskName' non supprimée (droits admin requis)" }
    } else {
        Write-Host "ℹ Aucune tâche '$taskName' trouvée"
    }
}

# Raccourci du repli Startup
$startupPath = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
$shortcutPath = [System.IO.Path]::Combine($startupPath, "IcyCache-Tray.lnk")
if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "✓ Raccourci Startup supprimé"
}

# Tuer tout processus tray.py actif
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object {
    try { $_.CommandLine -like "*tray.py*" } catch { $false }
} | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "✓ Processus tray arrêtés (si présents)"
