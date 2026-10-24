# D:\IcyCache\install_autostart.ps1
# Installe IcyCache comme tâche planifiée (démarre au logon, caché).
# Sans droits administrateur : repli automatique sur le dossier Startup de l'utilisateur.

$ErrorActionPreference = "Stop"

$taskName = "IcyCache-Tray"
$legacyTaskName = "CacheForge-Tray"
$pythonw = "D:\IcyCache\venv\Scripts\pythonw.exe"
$script = "D:\IcyCache\tray.py"
$workDir = "D:\IcyCache"

# Vérifications
if (!(Test-Path $pythonw)) { Write-Error "pythonw.exe introuvable: $pythonw"; exit 1 }
if (!(Test-Path $script)) { Write-Error "tray.py introuvable: $script"; exit 1 }

# La tâche héritée du renommage pointait encore sur D:\CacheForge : elle est retirée
# (sinon le prochain logon redémarre l'ancien projet, avec sa propre base).
$legacy = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
if ($legacy) {
    try {
        Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
        Write-Host "✓ Tâche héritée '$legacyTaskName' (ancien D:\CacheForge) supprimée"
    } catch {
        Write-Warning "Tâche héritée '$legacyTaskName' non supprimée (droits admin requis) : $($_.Exception.Message)"
        Write-Warning "Elle redémarra l'ancien D:\CacheForge au prochain logon. À supprimer en admin :"
        Write-Warning "  Unregister-ScheduledTask -TaskName '$legacyTaskName' -Confirm:`$false"
    }
}

# Créer la tâche planifiée (version simplifiée, pas de UserId explicite)
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $workDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$registered = $false
try {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        # La tâche appartient aux Administrateurs : sa suppression échoue sans
        # élévation. On tente, mais l'échec ne doit pas interrompre
        # l'installation (le repli Startup folder prend le relais).
        try {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            Write-Host "✓ Ancienne tâche '$taskName' supprimée"
        } catch {
            Write-Host "• Tâche '$taskName' déjà installée et non modifiable (droits admin requis) — conservée"
            $existing = $null
        }
    }
    if (-not $existing) {
        Register-ScheduledTask `
            -TaskName $taskName `
            -Action $action `
            -Trigger $trigger `
            -Settings $settings `
            -Description "IcyCache proxy + MCP launcher (system tray)" `
            -RunLevel Limited | Out-Null
        $registered = $true
        Write-Host "✓ Tâche '$taskName' installée"
    } else {
        $registered = $true
    }
} catch {
    Write-Warning "Tâche planifiée non enregistrée ($($_.Exception.Message))."
    Write-Warning "Repli sur le dossier Startup (pas de droits admin requis)."
}

if (!$registered) {
    $startupPath = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup")
    $shortcutPath = [System.IO.Path]::Combine($startupPath, "IcyCache-Tray.lnk")
    $WshShell = New-Object -ComObject WScript.Shell
    $shortcut = $WshShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = "`"$script`""
    $shortcut.WorkingDirectory = $workDir
    $shortcut.WindowStyle = 7  # Minimized
    $shortcut.Save()
    Write-Host "✓ Raccourci créé : $shortcutPath"
}

Write-Host ""
Write-Host "Pour démarrer MAINTENANT :"
Write-Host "   Start-Process '$pythonw' -ArgumentList '`"$script`"' -WorkingDirectory '$workDir' -WindowStyle Hidden"
Write-Host ""
Write-Host "Pour désinstaller :"
Write-Host "   .\uninstall_autostart.ps1"
