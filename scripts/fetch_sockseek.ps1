# downloads the pinned sockseek release for windows into vendor/sockseek
# usage: powershell -ExecutionPolicy Bypass -File scripts\fetch_sockseek.ps1 [-Force]
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# the progress bar makes invoke-webrequest very slow on windows powershell 5.1
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$pin = Get-Content -Raw -Encoding UTF8 -Path (Join-Path $PSScriptRoot 'sockseek-pin.json') | ConvertFrom-Json
$target = Join-Path $root 'vendor\sockseek'
$exe = Join-Path $target 'sockseek.exe'

function Get-SockseekVersion([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    $out = & $path --version 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($out | Select-Object -First 1).Trim()
}

$installed = Get-SockseekVersion $exe
if (-not $Force -and $installed -eq $pin.version) {
    Write-Host "sockseek $installed already in $target"
    exit 0
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("neonseek-sockseek-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $zip = Join-Path $work $pin.asset
    Write-Host "downloading $($pin.url)"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $pin.url -OutFile $zip -UseBasicParsing

    $hash = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLowerInvariant()
    if ($hash -ne $pin.sha256) {
        throw "sha256 mismatch for $($pin.asset): expected $($pin.sha256), got $hash"
    }

    $extracted = Join-Path $work 'extracted'
    Expand-Archive -Path $zip -DestinationPath $extracted
    $newExe = Join-Path $extracted 'sockseek.exe'
    if (-not (Test-Path $newExe)) { throw "sockseek.exe not found in $($pin.asset)" }

    $got = Get-SockseekVersion $newExe
    if ($got -ne $pin.version) {
        throw "downloaded binary reports version '$got', expected '$($pin.version)'"
    }

    # replace everything except the tracked placeholder
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Get-ChildItem -Path $target -Force | Where-Object { $_.Name -ne '.gitkeep' } |
        Remove-Item -Recurse -Force -Confirm:$false
    Copy-Item -Path (Join-Path $extracted '*') -Destination $target -Recurse

    # agpl: keep a pointer to the exact sources next to the binary
    $sourceNote = @(
        "Sockseek $($pin.version) ($($pin.license))",
        "Release: https://github.com/fiso64/sockseek/releases/tag/$($pin.tag)",
        "Source:  $($pin.source)",
        "Asset:   $($pin.asset) sha256 $($pin.sha256)"
    )
    Set-Content -Path (Join-Path $target 'SOURCE.txt') -Value $sourceNote -Encoding UTF8

    Write-Host "sockseek $got installed to $target"
}
finally {
    Remove-Item -Recurse -Force -Confirm:$false -Path $work -ErrorAction SilentlyContinue
}
