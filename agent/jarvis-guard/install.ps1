# Install the Jarvis Guard agent on a family PC. Run ELEVATED (as a local
# administrator). This sets the PC up for a child member of the family:
# it enrolls the machine on the family hub, assigns it to the child, mints a
# scoped key for the PC alone, and only ever keeps that scoped key on disk.
#
# Setup needs the username and password of a Jarvis account that may manage
# devices (an admin, the father, or a member with the devices.assign
# permission) - the same account that manages the Devices page. The hub
# decides; this script does not guess from the account's flags.
#
# The child's Windows account must be a STANDARD user. The installer:
#   1. fails safe unless run elevated,
#   2. signs in as the parent (that key is never written to disk),
#   3. registers this PC as a Windows PC and assigns it to the child,
#   4. mints this device's own scoped agent key,
#   5. copies jarvis-guard.exe to %ProgramFiles%\Jarvis Guard (readable by
#      everyone, writable by administrators only, the Windows default there),
#   6. writes the config to %ProgramData%\Jarvis Guard (owned by
#      Administrators, SYSTEM + Administrators only, nothing inherited; a
#      folder found there that this installer did not make is emptied first,
#      see below; the public\ folder the tray reads is Users:Read),
#   7. registers the "Jarvis Guard (service)" task: SYSTEM, at startup, never
#      stopped after a time limit or on battery, restarted if it ever exits,
#   8. adds the tray shield to every sign-in, and starts the guard.
#
# Put this script next to jarvis-guard.exe (the CI artifact holds both), or
# run it from agent/jarvis-guard after build.ps1 (it then uses dist\).
# Remove it with uninstall.ps1, which tells the parents first.
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

# The exe: next to this script (the downloaded artifact), else dist\ (a build).
$exe = @((Join-Path $PSScriptRoot 'jarvis-guard.exe'), (Join-Path $PSScriptRoot 'dist\jarvis-guard.exe')) |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) { throw "jarvis-guard.exe was not found next to this script or in dist\. Download the CI artifact or run build.ps1 first." }
$version = (Get-Item $exe).VersionInfo.ProductVersion
if (-not $version) { $version = '0.0.0' }
$version = ($version -split '\+')[0]

if (-not $HubUrl) { $HubUrl = Read-Host 'Family hub address (for example https://hub.example.com)' }
if (-not $HubUrl.Trim()) { throw 'The family hub address is required.' }
$HubUrl = $HubUrl.Trim().TrimEnd('/')
if ($HubUrl -notmatch '^https?://') { throw "The hub address must start with https:// (got '$HubUrl')." }
if ($HubUrl -like 'http://*') { Write-Warning 'The hub address is not https: the PC key and a parent''s unlock password would cross the network unencrypted.' }
if (-not $AdminUsername) { $AdminUsername = Read-Host 'Jarvis parent or admin username' }
if (-not $AdminPassword) { $AdminPassword = Read-Host "Password for '$AdminUsername' (never written to disk)" -AsSecureString }
if (-not $ChildUsername) { $ChildUsername = Read-Host 'Jarvis username of the child this PC belongs to' }
if (-not $ChildUsername.Trim()) { throw 'A child username is required.' }
$ChildUsername = $ChildUsername.Trim().ToLowerInvariant()

# A child who is a Windows administrator can remove the guard: say so loudly.
try {
    $admins = Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop |
        Where-Object { $_.ObjectClass -eq 'User' } | ForEach-Object { $_.Name }
    Write-Host 'Windows administrators on this PC (each can remove Jarvis Guard; the child must not be one):'
    $admins | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Warning 'Could not list the Windows administrators. Check by hand that the child''s account is a standard user.'
}

function To-Plain([securestring]$s) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

# One call to the hub, with the hub's own reason when it says no.
function Invoke-Hub([string]$Method, [string]$Path, $Body = $null, [hashtable]$Headers = @{}, [string]$Doing) {
    $params = @{ Method = $Method; Uri = "$HubUrl$Path"; Headers = $Headers; ContentType = 'application/json' }
    if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Compress) }
    try { return Invoke-RestMethod @params }
    catch {
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch { }
        $detail = $null
        try { $detail = ($_.ErrorDetails.Message | ConvertFrom-Json).detail } catch { }
        if (-not $detail) { $detail = $_.Exception.Message }
        if ($code -eq 403) { throw "The hub refused to let '$AdminUsername' $Doing (403: $detail). Use an account that may manage devices." }
        if ($code -eq 404) { throw "The hub could not $Doing (404: $detail). Check the hub address, the child's username, and that the hub is up to date." }
        throw "Could not $Doing ($code`: $detail)."
    }
}

if (-not $DeviceKey) {
    $computerGuid = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
    $DeviceKey = "windows-$computerGuid"
}

$plainPassword = To-Plain $AdminPassword
$adminKey = $null
try {
    # 1. Sign in as the parent.
    $login = Invoke-Hub Post '/api/auth/login' @{ username = $AdminUsername; password = $plainPassword } @{} 'sign in'
    $adminKey = if ($login.api_key) { $login.api_key } else { $login.apiKey }
    if (-not $adminKey) { throw "Could not sign in as '$AdminUsername'." }
    $plainPassword = $null
    Write-Host "Signed in as '$AdminUsername'."
    $auth = @{ Authorization = "Bearer $adminKey" }

    # 2. Register this PC as a Windows PC (idempotent on the hub).
    $null = Invoke-Hub Post '/api/user-panel/devices/register' @{
        device_key  = $DeviceKey
        kind        = 'windows-pc'
        model       = $env:COMPUTERNAME
        os_version  = [System.Environment]::OSVersion.VersionString
        app_version = $version
    } $auth 'register this PC'
    Write-Host "Registered this PC as '$DeviceKey'."

    # 3. Assign it to the child.
    $null = Invoke-Hub Patch "/api/user-panel/devices/$DeviceKey" @{ owner_username = $ChildUsername } $auth 'assign this PC to the child'
    Write-Host "Assigned this PC to '$ChildUsername'."

    # 4. Mint THIS DEVICE's scoped agent key (shown once; the PC keeps only this).
    $minted = Invoke-Hub Post "/api/user-panel/devices/$DeviceKey/agent-key" $null $auth 'mint this PC''s key'
    $agentKey = $minted.agent_key
    if (-not $agentKey) { throw 'The hub did not return a device key.' }
    if ($minted.owner_username -and $minted.owner_username -ne $ChildUsername) {
        throw "The hub says this PC belongs to '$($minted.owner_username)', not '$ChildUsername'."
    }
    Write-Host "Minted this PC's own key."
}
finally {
    # The parent's password and key leave memory; only the scoped key is kept, below.
    $plainPassword = $null
    $adminKey = $null
}

$taskName = 'Jarvis Guard (service)'

# A reinstall or an upgrade: stop the running guard so its exe can be replaced.
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
Get-Process -Name 'jarvis-guard' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# The program: %ProgramFiles% keeps its default ACL (everyone may read and
# run, only administrators may change), which the tray in the child's
# session needs to start at all.
$installDir = Join-Path $env:ProgramFiles 'Jarvis Guard'
New-Item -ItemType Directory -Force $installDir | Out-Null
# An earlier version of this script locked this folder to SYSTEM + Administrators
# (so the tray could not start for the child) and kept the key here: undo both.
icacls $installDir /reset /T /Q | Out-Null
Remove-Item -Force -Recurse -ErrorAction SilentlyContinue (Join-Path $installDir 'guard.config.json'), (Join-Path $installDir 'public')
Copy-Item -Force $exe (Join-Path $installDir 'jarvis-guard.exe')
$readme = Join-Path $PSScriptRoot 'README.md'
if (Test-Path $readme) { Copy-Item -Force $readme $installDir }
$uninstaller = Join-Path $PSScriptRoot 'uninstall.ps1'
if (Test-Path $uninstaller) { Copy-Item -Force $uninstaller $installDir }

# The data: %ProgramData%\Jarvis Guard, where the agent reads its config.
# SYSTEM + Administrators only; the key, the cached rules, the offline-code
# verifiers and the log live here.
#
# %ProgramData% lets any user create a folder there, and the creator owns it.
# A child who made "Jarvis Guard" before this ran would own it for good (an
# owner can always rewrite the ACL): they could read the PC's key, and plant
# offline.codes.json or a cached status for the agent to trust. So the folder
# is never trusted as found:
#   1. if "Jarvis Guard" is a link (junction, symlink), the link is removed,
#      never what it points to, and a real folder is made;
#   2. Administrators take ownership of the folder itself, and its ACL is
#      replaced with SYSTEM + Administrators only, inheriting nothing from
#      %ProgramData% (every other entry goes, whoever added it);
#   3. everything inside is checked without following links: anything owned
#      by someone other than SYSTEM or Administrators, any link, and anything
#      an administrator cannot read means the folder was not made by this
#      installer. Then everything in it is deleted, with a warning naming what
#      was found. A fresh install (no guard task yet) deletes it all too, so
#      nothing planted before the guard existed survives;
#   4. a reinstall or upgrade over a folder that passes the check keeps its
#      files (today's minutes, the offline-code verifiers and their lockout),
#      owned by Administrators and inheriting only the folder's ACL.
# Links are never followed: takeown /R and icacls /T would walk through a
# junction and change files elsewhere on the PC, so they only run once the
# check has shown there are no links.
$dataDir = Join-Path $env:ProgramData 'Jarvis Guard'
$trustedOwners = @('S-1-5-18', 'S-1-5-32-544')

function Test-Link($Item) { [bool]($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) }

function Remove-Link([string]$Path) {
    # The link itself; never through it into its target.
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) { [IO.Directory]::Delete($Path, $false) } else { [IO.File]::Delete($Path) }
}

# Everything under $Path, depth first, without walking into links.
function Get-Tree([string]$Path) {
    foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop)) {
        $child
        if ($child.PSIsContainer -and -not (Test-Link $child)) { Get-Tree $child.FullName }
    }
}

# Delete everything under $Path, taking each folder first (it may shut
# administrators out), and links as links.
function Clear-Folder([string]$Path) {
    foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop)) {
        if (Test-Link $child) { Remove-Link $child.FullName; continue }
        if ($child.PSIsContainer) {
            takeown /F $child.FullName /A | Out-Null
            icacls $child.FullName /grant '*S-1-5-32-544:(OI)(CI)F' /Q | Out-Null
            Clear-Folder $child.FullName
            [IO.Directory]::Delete($child.FullName, $false)
        } else {
            Remove-Item -LiteralPath $child.FullName -Force
        }
    }
}

if ((Test-Path -LiteralPath $dataDir) -and (Test-Link (Get-Item -LiteralPath $dataDir -Force))) {
    Write-Warning "$dataDir was a link to somewhere else, not a folder. Removing the link (not its target) and making a real folder."
    Remove-Link $dataDir
}
New-Item -ItemType Directory -Force $dataDir | Out-Null

# The folder itself: Administrators own it, and only SYSTEM and Administrators may use it.
takeown /F $dataDir /A | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not take ownership of $dataDir for Administrators (takeown exit $LASTEXITCODE)." }
icacls $dataDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not lock down $dataDir." }
$acl = Get-Acl -LiteralPath $dataDir
foreach ($rule in @($acl.Access)) {
    $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($trustedOwners -notcontains $sid) { $null = $acl.RemoveAccessRule($rule) }
}
Set-Acl -LiteralPath $dataDir -AclObject $acl
$owner = (Get-Acl -LiteralPath $dataDir).GetOwner([Security.Principal.SecurityIdentifier]).Value
if ($owner -ne 'S-1-5-32-544') { throw "$dataDir is still owned by $owner, not Administrators. Remove the folder by hand and run this again." }

# What is inside: made by this installer and the agent, or not?
$found = @()
try {
    foreach ($item in @(Get-Tree $dataDir)) {
        if (Test-Link $item) { $found += "$($item.FullName) (a link)"; continue }
        $itemOwner = (Get-Acl -LiteralPath $item.FullName).GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($trustedOwners -notcontains $itemOwner) { $found += "$($item.FullName) (owned by $itemOwner)" }
    }
} catch {
    $found += "something an administrator could not read ($($_.Exception.Message))"
}

if ($found.Count -gt 0 -or -not $existing) {
    if ($found.Count -gt 0) {
        Write-Warning "$dataDir held things this installer did not make, so everything in it is deleted:"
        $found | ForEach-Object { Write-Warning "  $_" }
    }
    Clear-Folder $dataDir
    if (@(Get-ChildItem -LiteralPath $dataDir -Force).Count -gt 0) { throw "Could not empty $dataDir. Remove it by hand and run this again." }
} else {
    # No links inside (checked above), so walking the tree is safe: kept files
    # belong to Administrators and inherit the folder's ACL and nothing else.
    icacls $dataDir /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not make Administrators the owner of everything in $dataDir." }
    if (@(Get-ChildItem -LiteralPath $dataDir -Force).Count -gt 0) {
        icacls (Join-Path $dataDir '*') /reset /T /C /Q | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not reset the permissions inside $dataDir." }
    }
}

$config = [ordered]@{
    hubUrl    = $HubUrl
    username  = $ChildUsername
    agentKey  = $agentKey
    deviceKey = $DeviceKey
    version   = $version
} | ConvertTo-Json
Set-Content -Path (Join-Path $dataDir 'guard.config.json') -Value $config -Encoding UTF8
$agentKey = $null

# The one folder the tray and the lock surface read (state.json, no secrets).
$publicDir = Join-Path $dataDir 'public'
New-Item -ItemType Directory -Force $publicDir | Out-Null
icacls $publicDir /grant '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not let users read $publicDir." }

# The SYSTEM guard: at every start, never time-limited, not stopped on
# battery, restarted if it exits. The five-minute repetition starts it again
# if anything ever stops it; a second copy is never started next to it.
$action = New-ScheduledTaskAction -Execute (Join-Path $installDir 'jarvis-guard.exe') -Argument '--serve'
$boot = New-ScheduledTaskTrigger -AtStartup
$again = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($boot, $again) -Principal $principal -Settings $settings `
    -Description 'Jarvis Guard: this PC is managed by the family hub.' -Force | Out-Null

# The tray: runs for everyone who signs in (managed-device signage, and it
# raises the lock surface when the PC locks).
New-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' -Name 'JarvisGuardTray' `
    -Value "`"$installDir\jarvis-guard.exe`" --tray" -PropertyType String -Force | Out-Null

Start-ScheduledTask -TaskName $taskName

Write-Host ""
Write-Host "Jarvis Guard $version is installed for '$ChildUsername' (device $DeviceKey)."
Write-Host "It appears on the Devices page, assigned to '$ChildUsername'. The tray shield appears at the next sign-in."
Write-Host "To remove it: run '$installDir\uninstall.ps1' as an administrator. The parents are told."
