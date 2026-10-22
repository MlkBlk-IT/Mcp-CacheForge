# D:\CacheForge\install_autostart.ps1
# Installe CacheForge comme tâche planifiée (démarre au logon, caché)

$taskName = "CacheForge-Tray"
$pythonw = "D:\CacheForge\venv\Scripts\pythonw.exe"
$script = "D:\CacheForge\tray.py"
$workDir = "D:\CacheForge"

# Vérifications
if (!(Test-Path $pythonw)) { Write-Error "pythonw.exe introuvable: $pythonw"; exit 1 }
if (!(Test-Path $script)) { Write-Error "tray.py introuvable: $script"; exit 1 }

# Supprimer tâche existante si présente
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "✓ Ancienne tâche supprimée"
}

# Créer la tâche (version simplifiée, pas de UserId explicite)
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $script -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "CacheForge proxy + MCP launcher (system tray)" `
    -RunLevel Limited | Out-Null

# Vérifier que la tâche existe vraiment
$check = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (!$check) {
    Write-Error "✗ Échec création tâche. Essayons l'alternative Startup folder..."
    
    # Fallback: dossier Startup
    $startupPath = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
    $shortcutPath = [System.IO.Path]::Combine($startupPath, "CacheForge-Tray.lnk")
    
    $WshShell = New-Object -ComObject WScript.Shell
    $shortcut = $WshShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = $script
    $shortcut.WorkingDirectory = $workDir
    $shortcut.WindowStyle = 7  # Minimized
    $shortcut.Save()
    
    Write-Host "✓ Raccourci créé dans Startup folder"
    Write-Host "  - Emplacement: $shortcutPath"
    Write-Host "  - Démarre automatiquement au prochain logon"
    Write-Host ""
    Write-Host "Pour démarrer MAINTENANT :"
    Write-Host "   Start-Process '$pythonw' -ArgumentList '$script' -WorkingDirectory '$workDir' -WindowStyle Hidden"
    exit 0
}

Write-Host "✓ Tâche '$taskName' installée avec succès"
Write-Host "  - Déclencheur : à chaque logon"
Write-Host "  - Exécutable : $pythonw (ZÉRO fenêtre console)"
Write-Host "  - Script : $script"
Write-Host ""
Write-Host "Pour démarrer MAINTENANT :"
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Pour désinstaller :"
Write-Host "  .\uninstall_autostart.ps1"
