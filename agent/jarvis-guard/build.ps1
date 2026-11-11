# Builds the single-file, self-contained win-x64 Jarvis Guard binary.
# Requires the .NET 10 SDK. The output is one jarvis-guard.exe carrying the
# service, the tray and the lock surface in --serve/--tray/--overlay modes.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dist = Join-Path $root 'dist'
Remove-Item $dist -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $dist | Out-Null

dotnet publish (Join-Path $root 'jarvis-guard.csproj') `
    -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:DebugType=None `
    -o $dist
if ($LASTEXITCODE -ne 0) { throw "publish failed" }
Write-Host "Published to $dist"
