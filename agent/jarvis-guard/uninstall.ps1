# Remove Jarvis Guard from this PC. Run ELEVATED (as a local administrator).
#
# Removing it is never silent: before anything is deleted, the PC tells the
# family hub with its own key (POST /api/guard/tamper, event guard_missing),
# and the hub tells the child's parents. If the hub cannot be reached the
# removal stops, unless -Offline is given (then nobody is told; say so to the
# parents yourself).
param([switch]$Offline)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an elevated (administrator) PowerShell.' }

$installDir = Join-Path $env:ProgramFiles 'Jarvis Guard'
$dataDir = Join-Path $env:ProgramData 'Jarvis Guard'
$configPath = Join-Path $dataDir 'guard.config.json'
$taskName = 'Jarvis Guard (service)'

if (Test-Path $configPath) {
    $config = Get-Content -Raw $configPath | ConvertFrom-Json
    try {
        $body = @{ event = 'guard_missing'; device_key = $config.deviceKey; detail = 'removed by a Windows administrator' } | ConvertTo-Json -Compress
        Invoke-RestMethod -Method Post -Uri "$($config.hubUrl.TrimEnd('/'))/api/guard/tamper" -ContentType 'application/json' `
            -Headers @{ Authorization = "Bearer $($config.agentKey)" } -Body $body | Out-Null
        Write-Host 'The family hub was told that Jarvis Guard is being removed.'
    } catch {
        if (-not $Offline) { throw "Could not tell the family hub ($($_.Exception.Message)). Try again online, or pass -Offline." }
        Write-Warning 'The family hub could not be told. Tell the parents yourself.'
    }
}

Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' -Name 'JarvisGuardTray' -ErrorAction SilentlyContinue
Get-Process -Name 'jarvis-guard' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $dataDir
# This script may be running from the install folder; everything else in it goes.
Get-ChildItem $installDir -ErrorAction SilentlyContinue | Where-Object { $_.FullName -ne $PSCommandPath } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host 'Jarvis Guard is removed. The PC stays on the Devices page until a parent removes it there.'
