# D:\IcyCache\tools\fix_scheduled_task.ps1
# A ELEVATION REQUISE (Lance en "Executer en tant qu'administrateur").
#
# Objectif : supprimer la tache heritee "CacheForge-Tray" (qui pointait encore sur
# D:\CacheForge, copie obsolete renommee IcyCache) et installer la tache propre
# "IcyCache-Tray" qui lance D:\IcyCache\tray.py au logon.
#
# Le fichier de la tache heritee appartient aux Administrateurs (TECH\Tech = lecture
# seule), donc la suppression exige une elevation. C'est la seule etape du chantier
# qui ne peut pas etre faite en session normale.

$ErrorActionPreference = "Stop"

$taskName      = "IcyCache-Tray"
$legacyTaskName = "CacheForge-Tray"
$pythonw       = "D:\IcyCache\venv\Scripts\pythonw.exe"
$script        = "D:\IcyCache\tray.py"
$workDir       = "D:\IcyCache"
$backupDir     = "D:\IcyCache\logs"
$resultFile    = "D:\IcyCache\logs\fix_task_result.txt"

$lines = New-Object System.Collections.Generic.List[string]
function Say($m) { $script:lines.Add($m); Write-Host $m }

try {
    if (!(Test-Path $pythonw)) { throw "pythonw introuvable: $pythonw" }
    if (!(Test-Path $script))  { throw "tray.py introuvable: $script" }

    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

    # 1. Sauvegarde du XML de la tache heritee (filet de securite)
    $legacy = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
    if ($legacy) {
        try {
            Export-ScheduledTask -TaskName $legacyTaskName |
                Out-File -FilePath (Join-Path $backupDir "CacheForge-Tray.backup.xml") -Encoding utf8
            Say "OK  : XML de '$legacyTaskName' sauvegarde dans logs\CacheForge-Tray.backup.xml"
        } catch {
            Say "WARN: sauvegarde XML impossible: $($_.Exception.Message)"
        }

        # 2. Suppression de la tache heritee
        try {
            Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
            Say "OK  : tache heritee '$legacyTaskName' supprimee"
        } catch {
            Say "FAIL: suppression de '$legacyTaskName' impossible: $($_.Exception.Message)"
        }
    } else {
        Say "OK  : tache heritee '$legacyTaskName' absente"
    }

    # 3. (Re)creation de la tache IcyCache
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $workDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew

    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "IcyCache proxy + MCP launcher (system tray)" `
        -RunLevel Limited | Out-Null

    Say "OK  : tache '$taskName' installee -> $pythonw `"$script`""

    # 4. Verification
    $t = Get-ScheduledTask -TaskName $taskName
    $i = Get-ScheduledTaskInfo -TaskName $taskName
    Say "INFO: etat=$($t.State) action=$($t.Actions.Item(1).Execute) $($t.Actions.Item(1).Arguments)"
    Say "DONE: reparation de la tache planifiee effectuee."
} catch {
    Say "FAIL: $($_.Exception.Message)"
} finally {
    try { $lines | Out-File -FilePath $resultFile -Encoding utf8 } catch {}
}
