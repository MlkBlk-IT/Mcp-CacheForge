# D:\IcyCache\restart_tray.ps1
# Redémarre IcyCache en arrière-plan SANS bloquer PowerShell

$ErrorActionPreference = "Stop"
$pythonw = "D:\IcyCache\venv\Scripts\pythonw.exe"
$script = "D:\IcyCache\tray.py"
$workDir = "D:\IcyCache"
$taskName = "IcyCache-Tray"

# 1. Arrêter toute instance précédente
Write-Host "🛑 Arrêt des instances précédentes..." -NoNewline

# Tuer la tâche planifiée si active
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

# Tuer tous les pythonw qui exécutent tray.py
$procs = Get-CimInstance Win32_Process | Where-Object { 
    $_.Name -eq "pythonw.exe" -and $_.CommandLine -like "*tray.py*" 
}
if ($procs) {
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# Tuer aussi les serveurs orphelins sur 8080/8081
$portProcs = @()
netstat -ano | Select-String ":8080\s+.*LISTENING", ":8081\s+.*LISTENING" | ForEach-Object {
    if ($_ -match '\s(\d+)\s*$') { $portProcs += $matches[1] }
}
$portProcs | Select-Object -Unique | ForEach-Object {
    Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
}

Write-Host " ✓" -ForegroundColor Green
Start-Sleep -Seconds 1

# 2. Vérifier la tâche planifiée (auto-start au logon)
#    NE PAS tenter de la (re)créer ici : le fichier de tâche appartient aux
#    Administrateurs, donc Unregister-ScheduledTask échoue sans élévation et
#    interrompait tout le redémarrage du service. On se contente de
#    constater son état ; l'installation se fait via tools\fix_scheduled_task.ps1
#    (lancé en admin) ou install_autostart.ps1.
Write-Host "📋 Vérification tâche planifiée..." -NoNewline
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    $act = ($existing.Actions | Select-Object -First 1)
    $exe = "$($act.Execute) $($act.Arguments)"
    if ($exe -like "*$script*") {
        Write-Host " ✓ '$taskName' OK" -ForegroundColor Green
    } else {
        Write-Host " ⚠ '$taskName' pointe ailleurs ($exe) — exécuter tools\fix_scheduled_task.ps1 en admin" -ForegroundColor Yellow
    }
} else {
    Write-Host " ⚠ '$taskName' absente — le repli Startup folder assure l'auto-démarrage" -ForegroundColor Yellow
}

# 3. Démarrer MAINTENANT en arrière-plan (non-bloquant, zéro fenêtre)
Write-Host "🚀 Démarrage en arrière-plan..." -NoNewline
$proc = Start-Process -FilePath $pythonw `
    -ArgumentList "`"$script`"" `
    -WorkingDirectory $workDir `
    -WindowStyle Hidden `
    -PassThru

Write-Host " PID $($proc.Id)" -ForegroundColor Green

# 4. Vérifier que le port est up
Write-Host "⏳ Vérification port 8080..." -NoNewline
$up = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Milliseconds 500
    if (netstat -ano | Select-String ":8080\s+.*LISTENING" -Quiet) {
        $up = $true
        break
    }
}

if ($up) {
    Write-Host " ✓ UP" -ForegroundColor Green
    Write-Host ""
    Write-Host "✅ IcyCache actif:" -ForegroundColor Cyan
    Write-Host "   • Proxy  : http://localhost:8080"
    Write-Host "   • MCP    : http://localhost:8081"
    Write-Host "   • Dashboard : http://localhost:8080/dashboard"
    Write-Host ""
    Write-Host "📌 Redémarre automatiquement à chaque logon Windows"
    Write-Host "📌 Logs : D:\IcyCache\logs\app.log"
} else {
    Write-Host " ✗ TIMEOUT" -ForegroundColor Red
    Write-Host ""
    Write-Host "⚠ Le serveur n'a pas démarré en 7.5s."
    Write-Host "   Vérifie les logs :"
    Write-Host "   Get-Content D:\IcyCache\logs\app.log -Tail 30"
    Write-Host ""
    Write-Host "   Test manuel (avec console visible) :"
    Write-Host "   .\venv\Scripts\python.exe tray.py"
}
