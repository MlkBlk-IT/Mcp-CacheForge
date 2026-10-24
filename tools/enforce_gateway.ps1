<#
.SYNOPSIS
    IcyCache - enforcement du passage obligatoire par la gateway (D3).

.DESCRIPTION
    Cree des regles de pare-feu Windows sortantes, par programme, qui interdisent
    a un client (Hermes, script, IDE...) d'atteindre un upstream DIRECTEMENT.
    Seul 127.0.0.1:<port IcyCache> reste autorise.

    Les regles BLOCK de Windows Firewall sont prioritisees sur les regles ALLOW :
    on ne peut donc pas dire "tout bloquer sauf 8080" avec une seule regle.
    La strategie retenue est donc :
      1. BLOCK  ports upstreams locaux (20128 OmniRoute, 1234 LM Studio, 11434 Ollama)
                 y compris sur 127.0.0.1 -> le client ne peut plus court-circuiter la gateway
      2. BLOCK  toutes les adresses IPv4/IPv6 DISTANTES (le cloud passe par la gateway)
      3. ALLOW  127.0.0.1:<port IcyCache> (regle explicite, redondante par securite)

    Le perimetre est limite au programme cible (Hermes.exe, python.exe du venv...)
    : IcyCache lui-meme et les autres applications ne sont pas affectes.

.PARAMETER Action
    apply | remove | status

.PARAMETER TargetProgram
    Chemin complet de l'executable a contraindre. Par defaut : Hermes.exe.

.PARAMETER AllowPort
    Port IcyCache autorise (defaut 8080).

.PARAMETER BlockPorts
    Ports upstreams locaux a interdire (defaut 20128,1234,11434).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\enforce_gateway.ps1 -Action status
    powershell -ExecutionPolicy Bypass -File tools\enforce_gateway.ps1 -Action apply
#>
[CmdletBinding()]
param(
    [ValidateSet('apply', 'remove', 'status')]
    [string]$Action = 'status',
    [string]$TargetProgram = '',
    [int]$AllowPort = 8080,
    [string]$BlockPorts = '20128,1234,11434'
)

$ErrorActionPreference = 'Stop'
$RulePrefix = 'IcyCache-Enforce'
$Root = Split-Path -Parent $PSScriptRoot

if (-not $TargetProgram) {
    $candidates = @(
        "$env:LOCALAPPDATA\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe",
        "$Root\venv\Scripts\python.exe"
    )
    $TargetProgram = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-Rules {
    Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like "$RulePrefix*" }
}

Write-Host "IcyCache - enforcement du passage par la gateway" -ForegroundColor Cyan
Write-Host "  programme cible : $TargetProgram"
Write-Host "  port autorise  : 127.0.0.1:$AllowPort  (IcyCache)"
Write-Host "  ports bloques  : $BlockPorts  (upstreams locaux)"
Write-Host ""

if ($Action -eq 'status') {
    $rules = Get-Rules
    if (-not $rules) {
        Write-Host "Aucune regle '$RulePrefix*' installee." -ForegroundColor Yellow
        Write-Host "Etat : NON CONTRAINT (le client peut encore joindre un upstream en direct)."
    } else {
        Write-Host "Regles installees :" -ForegroundColor Green
        $rules | ForEach-Object {
            $pf = $_ | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
            $af = $_ | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue
            $af2 = $_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue
            Write-Host ("  {0,-45} {1,-6} port={2} addr={3} prog={4}" -f `
                $_.DisplayName, $_.Direction, ($pf.LocalPort -join ','), ($af.RemoteAddress -join ','),
                ($af2.Program -replace '^.*\\', ''))
        }
    }
    Write-Host ""
    Write-Host "Verification du contournement :"
    Write-Host "  python tools\verify_enforcement.py --program `"$TargetProgram`" --allow-port $AllowPort"
    return
}

if (-not (Test-Admin)) {
    Write-Host "Droits administrateur requis pour $Action." -ForegroundColor Red
    Write-Host "Relancez dans une console PowerShell elevee :" -ForegroundColor Yellow
    Write-Host "  Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -File `"$PSCommandPath`" -Action $Action -TargetProgram `"$TargetProgram`" -AllowPort $AllowPort -BlockPorts $BlockPorts'"
    exit 2
}

if ($Action -eq 'remove') {
    Get-Rules | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Host "Regles '$RulePrefix*' supprimees." -ForegroundColor Green
    return
}

# ---- apply ----
Get-Rules | Remove-NetFirewallRule -ErrorAction SilentlyContinue

$blockList = $BlockPorts -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }

# 1. Bloquer les upstreams locaux (y compris via 127.0.0.1)
$ports = ($blockList -join ',')
New-NetFirewallRule -DisplayName "$RulePrefix-BlockLocalUpstreams" `
    -Direction Outbound -Action Block -Program $TargetProgram `
    -Protocol TCP -RemotePort $ports -RemoteAddress Any `
    -Profile Any -Enabled True | Out-Null

# 2. Bloquer tout le distant IPv4 (le cloud doit passer par la gateway)
New-NetFirewallRule -DisplayName "$RulePrefix-BlockRemoteIPv4" `
    -Direction Outbound -Action Block -Program $TargetProgram `
    -Protocol TCP -RemoteAddress "0.0.0.0-126.255.255.255,128.0.0.0-255.255.255.255" `
    -Profile Any -Enabled True | Out-Null

# 3. Bloquer tout le distant IPv6 (sauf ::1)
New-NetFirewallRule -DisplayName "$RulePrefix-BlockRemoteIPv6" `
    -Direction Outbound -Action Block -Program $TargetProgram `
    -Protocol TCP -RemoteAddress "::-ffff:0:0/96,2001::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff,2002::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff,fc00::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff,fe80::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff" `
    -Profile Any -Enabled True | Out-Null

# 4. Autoriser explicitement la gateway
New-NetFirewallRule -DisplayName "$RulePrefix-AllowIcyCache" `
    -Direction Outbound -Action Allow -Program $TargetProgram `
    -Protocol TCP -RemotePort $AllowPort -RemoteAddress "127.0.0.1,::1" `
    -Profile Any -Enabled True | Out-Null

Write-Host "Regles installees :" -ForegroundColor Green
Get-Rules | ForEach-Object { Write-Host "  $($_.DisplayName)  [$($_.Action)]" }
Write-Host ""
Write-Host "Verifiez que le contournement est impossible :" -ForegroundColor Cyan
Write-Host "  python tools\verify_enforcement.py --program `"$TargetProgram`" --allow-port $AllowPort"
