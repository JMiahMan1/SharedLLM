# Install the Jarvis Guard agent on a family PC. Run ELEVATED (as a local
# administrator). This sets the PC up for a child member of the family:
# it enrolls the machine on the family hub, assigns it to the child, mints a
# scoped key for the PC alone, and only ever keeps that scoped key on disk.
#
# Setup requires a Jarvis ADMIN (or father/mother) username and password -
# the same account a parent uses to manage the Devices page.
#
# The child account must be a STANDARD user. The installer:
#   1. fails safe unless run elevated,
#   2. signs in as the admin (that key is never written to disk),
#   3. registers this PC as a Windows PC and assigns it to the child,
#   4. mints this device's own scoped agent key,
#   5. writes the config (scoped key ACL'd to SYSTEM + Administrators),
#   6. creates "Jarvis Guard (service)", a SYSTEM boot task, and the
#      "This PC is managed by the family hub" tray for every sign-in.
#
# Uninstall is Admin-only and is reported to the parents as guard_missing.
param(
    [string]$HubUrl = '',
    [string]$AdminUsername = '',
    [securestring]$AdminPassword = $null,
    [string]$ChildUsername = '',
    [string]$DeviceKey = ''
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this installer from an elevated (administrator) PowerShell.' }

if (-not $HubUrl) { $HubUrl = Read-Host 'Family hub address (e.g. https://jarvis.example.com)' }
if (-not $HubUrl.Trim()) { throw 'The family hub address is required.' }
$HubUrl = $HubUrl.Trim().TrimEnd('/')
if (-not $AdminUsername) { $AdminUsername = Read-Host 'Jarvis admin username (a parent or admin account)' }
if (-not $AdminPassword) { $AdminPassword = Read-Host "Password for '$AdminUsername' (never written to disk)" -AsSecureString }
if (-not $ChildUsername) { $ChildUsername = Read-Host 'Child username to assign this PC to' }
if (-not $ChildUsername.Trim()) { throw 'A child username is required.' }

function To-Plain([securestring]$s) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

$dist = Join-Path $PSScriptRoot 'dist'
$exe = Join-Path $dist 'jarvis-guard.exe'
if (-not (Test-Path $exe)) { throw "Missing $exe - run build.ps1 first." }

if (-not $DeviceKey) {
    $computerGuid = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
    $DeviceKey = "windows-$computerGuid"
}

$HubUrl = $HubUrl.TrimEnd('/')
$plainPassword = To-Plain $AdminPassword
$authHeaders = $null
$adminKey = $null
try {
    # 1. Sign in as the admin. Only something with is_admin=true may continue.
    $login = Invoke-RestMethod -Method Post -Uri "$HubUrl/api/auth/login" -ContentType 'application/json' `
        -Body (@{ username = $AdminUsername; password = $plainPassword } | ConvertTo-Json)
    if (-not $login.api_key -and -not $login.apiKey) { throw "Could not sign in as '$AdminUsername'." }
    $adminKey = if ($login.api_key) { $login.api_key } else { $login.apiKey }
    if (-not $login.is_admin -and -not $login.isAdmin) {
        throw "'$AdminUsername' is not an admin account. Setup requires a parent or admin account."
    }
    Write-Host "Signed in as '$AdminUsername'."

    $jsonHeaders = @{ Authorization = "Bearer $adminKey" }

    # 2. Register this PC as a Windows PC (idempotent on the hub).
    $registerBody = @{
        device_key  = $DeviceKey
        kind        = 'windows-pc'
        platform    = 'windows'
        model       = $env:COMPUTERNAME
        os_version  = [System.Environment]::OSVersion.VersionString
        app_version = '0.1.0'
        label       = $env:COMPUTERNAME
    } | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri "$HubUrl/api/user-panel/devices/register" -ContentType 'application/json' `
        -Body $registerBody -Headers $jsonHeaders | Out-Null
    Write-Host "Registered this PC as '$DeviceKey'."

    # 3. Assign it to the child.
    $assignBody = @{ owner_username = $ChildUsername.Trim().ToLowerInvariant() } | ConvertTo-Json
    Invoke-RestMethod -Method Patch -Uri "$HubUrl/api/user-panel/devices/$DeviceKey" -ContentType 'application/json' `
        -Body $assignBody -Headers $jsonHeaders | Out-Null
    Write-Host "Assigned this PC to '$ChildUsername'."

    # 4. Mint THIS DEVICE's scoped agent key (shown once; the PC keeps only this).
    $minted = Invoke-RestMethod -Method Post -Uri "$HubUrl/api/user-panel/devices/$DeviceKey/agent-key" -Headers $jsonHeaders
    $agentKey = $minted.agent_key
    if (-not $agentKey) { $agentKey = $minted.agentKey }
    if (-not $agentKey) { $agentKey = $minted.key }
    if (-not $agentKey) { throw 'The hub did not return a device key.' }
    Write-Host "Minted this PC's scoped key."
}
finally {
    # The admin password leaves memory; only the scoped key is kept, below.
    $plainPassword = $null
    $authHeaders = $null
    $adminKey = $null
}

$installDir = Join-Path $env:ProgramFiles 'Jarvis Guard'
New-Item -ItemType Directory -Force $installDir | Out-Null
Copy-Item -Force $exe (Join-Path $installDir 'jarvis-guard.exe')
Copy-Item -Force $dist\README.md $installDir -ErrorAction SilentlyContinue

$config = [ordered]@{
    hubUrl    = $HubUrl
    username  = $ChildUsername.Trim().ToLowerInvariant()
    agentKey  = $agentKey
    deviceKey = $DeviceKey
    version   = '0.1.0'
} | ConvertTo-Json
$configPath = Join-Path $installDir 'guard.config.json'
Set-Content -Path $configPath -Value $config -Encoding UTF8

# Root folder and the config file: SYSTEM + Administrators only.
icacls $installDir /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F' | Out-Null

# The tray reads exactly one world-readable file; nothing else needs to be.
$publicDir = Join-Path $installDir 'public'
New-Item -ItemType Directory -Force $publicDir | Out-Null
if (-not (Test-Path (Join-Path $publicDir 'state.json'))) {
    Set-Content -Path (Join-Path $publicDir 'state.json') -Value '{}' -Encoding UTF8
}
icacls $publicDir /grant:r 'Users:R' 'SYSTEM:F' 'BUILTIN\Administrators:F' | Out-Null

# The SYSTEM guard: boots with the PC, signs locked sessions out.
schtasks /create /tn 'Jarvis Guard (service)' /tr "`"$installDir\jarvis-guard.exe`" --serve" /sc onstart /ru SYSTEM /rl HIGHEST /f | Out-Null

# The tray: runs for everyone who signs in (managed-device signage).
New-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' -Name 'JarvisGuardTray' -Value "`"$installDir\jarvis-guard.exe`" --tray" -PropertyType String -Force | Out-Null

schtasks /run /tn 'Jarvis Guard (service)' | Out-Null

Write-Host ""
Write-Host "Jarvis Guard is installed for '$ChildUsername' (device $DeviceKey)."
Write-Host "It already appears on the Devices page, assigned to '$ChildUsername'."
Write-Host "To remove it: schtasks /delete /tn 'Jarvis Guard (service)' /f ; delete the Run entry 'JarvisGuardTray'; remove $installDir"
Write-Host "Removing it is reported to the parents; nothing about that is silent."
