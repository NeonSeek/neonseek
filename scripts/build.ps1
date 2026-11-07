# builds NeonSeek from scratch: tests, manual pdfs, pyinstaller (onedir), licenses, installer
# and portable zip (PLAN.md phase 4, D-215).
# usage: powershell -ExecutionPolicy Bypass -File scripts\build.ps1 [-BuildRoot C:\NeonSeekBuild]
#        [-SkipTests] [-SkipInstaller] [-Iscc <path to ISCC.exe>] [-OutDir <folder>]
#        [-Version <x.y.z>]
# -Version builds another version number for testing an update. it changes pyproject.toml only
# in the copy inside the build folder, never in the repository (D-224).
#
# the build runs on a copy of the committed tree (git archive HEAD) in a folder outside the
# user profile, with python, the uv cache, TEMP, APPDATA and the home folder all inside the
# build folder and a PATH without user folders. so no path of the building machine ends up
# in the package (D-219). results land in dist\ of the repository (or -OutDir):
#   dist\NeonSeek-Setup-<version>.exe, dist\NeonSeek-<version>-portable.zip
[CmdletBinding()]
param(
    [string]$BuildRoot = (Join-Path $env:SystemDrive 'NeonSeekBuild'),
    [string]$Iscc = '',
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$OutDir = '',
    [string]$Version = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'

# the python of the package, pinned so a build can be repeated
$PythonVersion = '3.12.12'

$repo = Split-Path -Parent $PSScriptRoot
$BuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)

function Fail([string]$message) {
    Write-Host "build failed: $message" -ForegroundColor Red
    exit 1
}

function Step([string]$name) {
    Write-Host ''
    Write-Host "== $name" -ForegroundColor Cyan
}

function Run([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$([System.IO.Path]::GetFileName($exe)) exited with $LASTEXITCODE" }
}

function Is-Inside([string]$path, [string]$folder) {
    $p = $path.TrimEnd('\') + '\'
    $f = $folder.TrimEnd('\') + '\'
    return $p.StartsWith($f, [System.StringComparison]::OrdinalIgnoreCase)
}

# --- tools, found before the environment is cleaned
Step 'tools'
if (Is-Inside $BuildRoot $env:USERPROFILE) { Fail "the build folder must be outside $env:USERPROFILE" }
if (Is-Inside $BuildRoot $repo) { Fail 'the build folder must be outside the repository' }
$uv = (Get-Command uv.exe -ErrorAction SilentlyContinue)
if (-not $uv) { Fail 'uv is missing: https://docs.astral.sh/uv/' }
$uv = $uv.Source
$git = (Get-Command git.exe -ErrorAction SilentlyContinue)
if (-not $git) { Fail 'git is missing' }
$git = $git.Source
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
if (-not $SkipInstaller) {
    if (-not $Iscc) {
        $candidates = @(
            (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
        )
        # an installation in another folder is found through its uninstall entry
        $uninstall = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1'
        foreach ($key in $uninstall) {
            $item = Get-ItemProperty $key -ErrorAction SilentlyContinue
            if ($item -and $item.PSObject.Properties['InstallLocation']) {
                $candidates += (Join-Path $item.InstallLocation 'ISCC.exe')
            }
        }
        $Iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $Iscc -or -not (Test-Path $Iscc)) {
        Fail 'Inno Setup 6 is missing (https://jrsoftware.org/isdl.php), or pass -Iscc, or -SkipInstaller'
    }
}
$pin = Get-Content -Raw -Encoding UTF8 (Join-Path $repo 'scripts\sockseek-pin.json') | ConvertFrom-Json
$vendor = Join-Path $repo 'vendor\sockseek'
foreach ($name in 'sockseek.exe', 'LICENSE', 'SOURCE.txt') {
    if (-not (Test-Path (Join-Path $vendor $name))) {
        Fail "vendor\sockseek\$name is missing: run scripts\fetch_sockseek.ps1"
    }
}
$sockseekVersion = (& (Join-Path $vendor 'sockseek.exe') --version | Select-Object -First 1).Trim()
if ($sockseekVersion -ne $pin.version) {
    Fail "vendor\sockseek is $sockseekVersion, the pin is $($pin.version): run scripts\fetch_sockseek.ps1 -Force"
}
$dirty = & $git -C $repo status --porcelain --untracked-files=no
if ($dirty) { Write-Host 'note: uncommitted changes are not part of the build (it uses HEAD)' -ForegroundColor Yellow }
$commit = (& $git -C $repo rev-parse --short HEAD).Trim()
Write-Host "uv $uv"
Write-Host "commit $commit, sockseek $sockseekVersion, build folder $BuildRoot"

# --- a clean copy of the committed tree
Step 'source'
$src = Join-Path $BuildRoot 'src'
$out = Join-Path $BuildRoot 'out'
foreach ($dir in $src, $out, (Join-Path $BuildRoot 'tmp'), (Join-Path $BuildRoot 'home')) {
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    New-Item -ItemType Directory -Path $dir | Out-Null
}
$archive = Join-Path $BuildRoot 'tmp\source.tar'
Run $git @('-C', $repo, 'archive', '--format=tar', '-o', $archive, 'HEAD')
Run $tar @('-xf', $archive, '-C', $src)
Remove-Item $archive
New-Item -ItemType Directory -Path (Join-Path $src 'vendor\sockseek') -Force | Out-Null
Copy-Item (Join-Path $vendor '*') (Join-Path $src 'vendor\sockseek') -Exclude '.gitkeep'
if ($Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') { Fail "-Version must look like 1.2.3, not '$Version'" }
    $pyproject = Join-Path $src 'pyproject.toml'
    $text = [System.IO.File]::ReadAllText($pyproject)
    $text = [regex]::Replace($text, '(?m)^version = "[^"]*"', "version = `"$Version`"", 1)
    [System.IO.File]::WriteAllText($pyproject, $text)
    Write-Host "test build: version $Version in the build copy only" -ForegroundColor Yellow
}

# --- an environment without the user profile
$saved = @{}
$home_ = Join-Path $BuildRoot 'home'
$clean = @{
    'TEMP' = (Join-Path $BuildRoot 'tmp'); 'TMP' = (Join-Path $BuildRoot 'tmp')
    'USERPROFILE' = $home_; 'HOME' = $home_
    'APPDATA' = (Join-Path $home_ 'AppData\Roaming'); 'LOCALAPPDATA' = (Join-Path $home_ 'AppData\Local')
    'UV_CACHE_DIR' = (Join-Path $BuildRoot 'uv-cache')
    'UV_PYTHON_INSTALL_DIR' = (Join-Path $BuildRoot 'python')
    'UV_PROJECT_ENVIRONMENT' = (Join-Path $BuildRoot 'venv')
    'UV_PYTHON_PREFERENCE' = 'only-managed'
    'UV_NO_CONFIG' = '1'
    'PATH' = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    'PYTHONPATH' = $null; 'VIRTUAL_ENV' = $null; 'PYTHONHOME' = $null; 'PYTHONSTARTUP' = $null
    'NEONSEEK_SOCKSEEK' = $null
}
foreach ($key in $clean.Keys) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, $clean[$key], 'Process')
}
New-Item -ItemType Directory -Force -Path $env:APPDATA, $env:LOCALAPPDATA | Out-Null

Push-Location $src
try {
    Step "python $PythonVersion and packages (uv.lock)"
    Run $uv @('sync', '--frozen', '--group', 'build', '--python', $PythonVersion)
    $version = (& $uv run --frozen --no-sync python -c 'import neonseek; print(neonseek.__version__)').Trim()
    $sourceUrl = (& $uv run --frozen --no-sync python -c 'import neonseek, neonseek.project as p; print(p.source_url(neonseek.__version__))').Trim()
    Write-Host "NeonSeek $version, source code: $sourceUrl"

    if (-not $SkipTests) {
        Step 'tests'
        Run $uv @('run', '--frozen', '--no-sync', 'pytest', '-q', '-p', 'no:cacheprovider')
    }

    Step 'manual pdfs'
    Run $uv @('run', '--frozen', '--no-sync', 'python', 'scripts\build_manual.py')

    Step 'pyinstaller'
    $versionFile = Join-Path $BuildRoot 'tmp\version.txt'
    Run $uv @('run', '--frozen', '--no-sync', 'python', 'scripts\exe_version.py', '--out', $versionFile)
    $env:NEONSEEK_VERSION_FILE = $versionFile
    $env:NEONSEEK_SOCKSEEK_DIR = Join-Path $src 'vendor\sockseek'
    Run $uv @('run', '--frozen', '--no-sync', 'pyinstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
        '--distpath', (Join-Path $out 'dist'), '--workpath', (Join-Path $out 'work'), 'installer\neonseek.spec')
    $app = Join-Path $out 'dist\NeonSeek'

    Step 'manual, licenses and readme next to the program'
    $manual = Join-Path $app 'manual'
    New-Item -ItemType Directory -Path $manual | Out-Null
    Copy-Item (Join-Path $src 'docs\manual\*.md') $manual
    Copy-Item -Recurse (Join-Path $src 'docs\manual\images') $manual
    Copy-Item -Recurse (Join-Path $src 'docs\manual\pdf') $manual
    Run $uv @('run', '--frozen', '--no-sync', 'python', 'scripts\collect_licenses.py', '--out', $app,
        '--sockseek', (Join-Path $src 'vendor\sockseek'))
    $check = (& (Join-Path $app 'neonseek-cli.exe') --version | Select-Object -First 1).Trim()
    if ($check -ne $version) { Fail "neonseek-cli.exe --version says '$check', expected $version" }

    $dist = Join-Path $BuildRoot 'dist'
    if (-not (Test-Path $dist)) { New-Item -ItemType Directory -Path $dist | Out-Null }

    Step 'portable zip'
    $zip = Join-Path $dist "NeonSeek-$version-portable.zip"
    Run $uv @('run', '--frozen', '--no-sync', 'python', '-c',
        "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2], 'NeonSeek')",
        ($zip -replace '\.zip$', ''), (Join-Path $out 'dist'))

    if (-not $SkipInstaller) {
        Step 'installer'
        Run $Iscc @('/Q', "/DAppVersion=$version", "/DSourceDir=$app", "/DSourceUrl=$sourceUrl",
            "/O$dist", (Join-Path $src 'installer\neonseek.iss'))
    }
}
finally {
    Pop-Location
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
    Remove-Item Env:\NEONSEEK_VERSION_FILE, Env:\NEONSEEK_SOCKSEEK_DIR -ErrorAction SilentlyContinue
}

Step 'results'
$results = if ($OutDir) { [System.IO.Path]::GetFullPath($OutDir) } else { Join-Path $repo 'dist' }
if (-not (Test-Path $results)) { New-Item -ItemType Directory -Path $results | Out-Null }
$files = @(Get-ChildItem $dist -File | Where-Object { $_.Name -like "NeonSeek-*$version*" })
foreach ($file in $files) {
    Copy-Item $file.FullName $results -Force
    $hash = (Get-FileHash -Algorithm SHA256 $file.FullName).Hash.ToLowerInvariant()
    Write-Host ("{0}  {1,8:N1} MB  {2}" -f $hash, ($file.Length / 1MB), (Join-Path $results $file.Name))
}
Write-Host "program folder: $app"
