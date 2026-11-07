<#
testy dymne NeonSeek na komputerze testowym (D-255). nie jest czescia paczki instalatora.

uruchamiac WYLACZNIE na komputerze testowym:
  powershell -ExecutionPolicy Bypass -File smoke-test.ps1 -TestMachine

po kolei: cicha instalacja starej wersji, import CSV (kolekcja odznaczona), linki z kontrola
liczby utworow, pobranie jednego utworu z TIDAL-a do konca na koncie Soulseek zapisanym przez
NeonSeeka, aktualizacja do nowej wersji przy uruchomionej aplikacji, odinstalowanie z kontrola,
ze dane i biblioteka zostaly. wynik: tabela OK/BLAD na ekranie i w summary.txt, pelny log
w smoke-test.log, oba w folderze wynikow.

  powershell -ExecutionPolicy Bypass -File smoke-test.ps1 -SelfTest
sprawdza tylko funkcje oceniajace wyniki na przykladowych danych (D-261), na kazdym komputerze:
niczego nie instaluje, nie uruchamia NeonSeeka i nie zapisuje plikow.

skrypt nie pyta o haslo, nie czyta go i niczego nie zapisuje w Menedzerze poswiadczen.
#>
[CmdletBinding()]
param(
    # jawna zgoda: bez tego przelacznika skrypt tylko wypisuje plan i konczy
    [switch]$TestMachine,
    # tylko sprawdzenie funkcji oceniajacych na przykladowych danych
    [switch]$SelfTest,
    # folder z NeonSeek-Setup-<stara>.exe i NeonSeek-Setup-<nowa>.exe (domyslnie obok skryptu)
    [string]$SetupDir = '',
    [string]$OldVersion = '0.1.0',
    [string]$NewVersion = '0.1.1',
    # linki z oczekiwana liczba utworow (domyslnie publiczne odpowiedniki z repozytorium)
    [string]$LinksFile = '',
    [string]$CsvFile = '',
    # dane i biblioteka testu oraz wyniki (domyslnie %USERPROFILE%\NeonSeek-smoke\<data>)
    [string]$WorkDir = '',
    [int]$DownloadMinutes = 20,
    # zostawia NeonSeeka zainstalowanego (bez ostatniego kroku)
    [switch]$SkipUninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# the folder of this script, also where $PSScriptRoot is empty in parameter defaults
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $SetupDir) { $SetupDir = $Here }
if (-not $LinksFile) { $LinksFile = Join-Path $Here 'links.public.json' }
if (-not $CsvFile) { $CsvFile = Join-Path $Here 'sample.csv' }

$AppId = '{6F3C1B52-8E0D-4C7A-9B1E-2D4F5A6C7E81}_is1'
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
$AudioExtensions = @('.flac', '.aiff', '.aif', '.wav')
$CsvName = 'Smoke test CSV'
$OK = 'OK'
$FAIL = 'BŁĄD'
$SKIP = 'POMINIĘTO'

# evaluation of the results. no side effects, -SelfTest runs them on sample data.
# windows powershell 5.1 in strict mode has no .Count on a single object or on $null, and a
# function hands an array over item by item: every list goes through @(...) before .Count

function Get-Prop($Object, [string]$Name) {
    # a property that may be missing, without the strict mode error
    if ($null -eq $Object) { return $null }
    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function New-Verdict([string]$Result, [string]$Details, $Collection = $null) {
    return [pscustomobject]@{ Result = $Result; Details = $Details; Collection = $Collection }
}

function ConvertFrom-CollectionsJson([string]$Text) {
    $text = ([string]$Text).Trim()
    if (-not $text -or $text -eq '[]') { return }
    # windows powershell hands a json array over as one object: piping it again unrolls it
    $parsed = $text | ConvertFrom-Json
    @($parsed) | ForEach-Object { $_ } | Where-Object { $null -ne $_ }
}

function Select-NewCollections($BeforeIds, $After) {
    $known = @(@($BeforeIds) | Where-Object { $null -ne $_ })
    @(@($After) | Where-Object { $null -ne $_ -and $known -notcontains $_.id })
}

function Select-AudioFiles($Files) {
    @(@($Files) | Where-Object {
        $null -ne $_ -and $AudioExtensions -contains ([string](Get-Prop $_ 'Extension')).ToLowerInvariant()
    })
}

function Select-PlaceholderLinks($Links) {
    # links whose address or expected number is still a <...> from links.example.json
    @(@($Links) | Where-Object {
        $link = $_
        $null -ne $link -and @(@('url', 'tracks', 'min_tracks', 'entries', 'more_entries_than') |
            Where-Object { ([string](Get-Prop $link $_)) -like '<*' }).Count -gt 0
    })
}

function Get-LastLines([string]$Text, [int]$Count) {
    $lines = @(([string]$Text).Trim() -split "\r?\n" | Where-Object { $_ -ne '' })
    return (@($lines | Select-Object -Last $Count) -join ' ')
}

function Get-PlannedInstallDir($Installed) {
    # inno setup installs a new version into the folder of the installed one, otherwise into
    # {autopf}\NeonSeek, which is %LOCALAPPDATA%\Programs\NeonSeek without admin rights
    $location = [string](Get-Prop $Installed 'InstallLocation')
    if ($location) {
        return [pscustomobject]@{ Dir = $location.TrimEnd('\'); Previous = $true;
            Version = [string](Get-Prop $Installed 'DisplayVersion') }
    }
    return [pscustomobject]@{ Dir = (Join-Path $env:LOCALAPPDATA 'Programs\NeonSeek');
        Previous = $false; Version = '' }
}

function Get-LinkVerdict($Link, $NewCollections, [string]$Output = '') {
    $new = @(@($NewCollections) | Where-Object { $null -ne $_ })
    if ($new.Count -ne 1) {
        return New-Verdict $FAIL ("nowe kolekcje: {0}. {1}" -f $new.Count, (Get-LastLines $Output 2)).Trim()
    }
    $collection = $new[0]
    $tracks = [int](Get-Prop $collection 'tracks')
    # entries: places in the list, a track that is there twice counted twice (D-259)
    $entries = Get-Prop $collection 'entries'
    if ($null -eq $entries) { $entries = $tracks } else { $entries = [int]$entries }
    $wanted = @()
    $good = $true
    $value = Get-Prop $Link 'tracks'
    if ($null -ne $value) {
        $wanted += "dokładnie $value różnych utworów"
        $good = $good -and ($tracks -eq [int]$value)
    }
    $value = Get-Prop $Link 'min_tracks'
    if ($null -ne $value) {
        $wanted += "co najmniej $value różnych utworów"
        $good = $good -and ($tracks -ge [int]$value)
    }
    $value = Get-Prop $Link 'entries'
    if ($null -ne $value) {
        $wanted += "dokładnie $value pozycji"
        $good = $good -and ($entries -eq [int]$value)
    }
    $value = Get-Prop $Link 'more_entries_than'
    if ($null -ne $value) {
        $wanted += "ponad $value pozycji"
        $good = $good -and ($entries -gt [int]$value)
    }
    if (@($wanted).Count -eq 0) { $wanted = @('bez oczekiwanej liczby') }
    $result = $FAIL
    if ($good) { $result = $OK }
    $details = "pozycji listy: {0}, różnych utworów: {1}; oczekiwane: {2} ({3})" -f $entries,
        $tracks, ($wanted -join ', '), (Get-Prop $collection 'name')
    $download = $null
    if ($good -and (Get-Prop $Link 'download_one')) { $download = $collection }
    return New-Verdict $result $details $download
}

function Get-CsvVerdict($Collections, [int]$Expected, [int]$ExitCode) {
    $csv = @(@($Collections) | Where-Object {
        $null -ne $_ -and $_.kind -eq 'csv' -and $_.name -eq $CsvName })
    if ($ExitCode -ne 0) { return New-Verdict $FAIL "kod $ExitCode" }
    if ($csv.Count -ne 1) { return New-Verdict $FAIL ("kolekcje '{0}': {1}" -f $CsvName, $csv.Count) }
    $row = $csv[0]
    if ([int]$row.tracks -ne $Expected -or $row.selected) {
        return New-Verdict $FAIL ("utwory {0} z {1}, zaznaczona: {2}" -f $row.tracks, $Expected, $row.selected)
    }
    return New-Verdict $OK ("{0} utwory, odznaczona" -f $row.tracks)
}

function Get-DownloadVerdict([int]$Done, [int]$AudioBefore, $AudioFiles, [bool]$TimedOut,
                             [string]$Output, [int]$Minutes) {
    $audio = @(Select-AudioFiles $AudioFiles)
    if ($TimedOut) { return New-Verdict $FAIL "przekroczony limit $Minutes minut" }
    if ($Done -ge 1 -and $audio.Count -gt $AudioBefore) {
        $file = @($audio | Sort-Object LastWriteTime | Select-Object -Last 1)[0]
        return New-Verdict $OK ("{0} ({1:N1} MB)" -f $file.Name, ($file.Length / 1MB))
    }
    return New-Verdict $FAIL ("pobrane: {0}, pliki audio: {1}. {2}" -f $Done, $audio.Count,
        (Get-LastLines $Output 3)).Trim()
}

function Get-UninstallVerdict($Problems, $AudioFiles) {
    $found = @(@($Problems) | Where-Object { $_ })
    if ($found.Count) { return New-Verdict $FAIL ($found -join '; ') }
    $files = @(Select-AudioFiles $AudioFiles).Count
    return New-Verdict $OK "program usunięty, dane testu, $files plik(i) audio, %APPDATA%\NeonSeek i konto zostały"
}

function Get-FailedCount($Rows) {
    return @(@($Rows) | Where-Object { $null -ne $_ -and $_.Wynik -eq $FAIL }).Count
}

function Invoke-SelfTest {
    $script:checks = 0
    $script:wrong = @()
    function Check([string]$Name, [bool]$Condition) {
        $script:checks++
        if (-not $Condition) { $script:wrong += $Name }
    }
    function File([string]$Name, [int]$Length, [int]$Minute) {
        [pscustomobject]@{ Name = $Name; Extension = [System.IO.Path]::GetExtension($Name);
            Length = $Length; LastWriteTime = (Get-Date '2026-11-07 12:00').AddMinutes($Minute) }
    }
    function Coll([int]$Id, [int]$Tracks, $Entries = $null, [string]$Kind = 'tidal_album',
                  [string]$Name = 'Lista', [bool]$Selected = $false) {
        $row = [ordered]@{ id = $Id; kind = $Kind; name = $Name; selected = $Selected; tracks = $Tracks }
        if ($null -ne $Entries) { $row['entries'] = $Entries }
        [pscustomobject]$row
    }

    # collections --json: an empty list, one collection, many
    Check 'json pusty' (@(ConvertFrom-CollectionsJson '[]').Count -eq 0)
    Check 'json bez tekstu' (@(ConvertFrom-CollectionsJson '').Count -eq 0)
    $one = @(ConvertFrom-CollectionsJson '[{"id": 7, "tracks": 3, "entries": 4}]')
    Check 'json jeden' ($one.Count -eq 1 -and $one[0].id -eq 7 -and $one[0].entries -eq 4)
    $many = @(ConvertFrom-CollectionsJson '[{"id": 1}, {"id": 2}, {"id": 3}]')
    Check 'json wiele' ($many.Count -eq 3 -and $many[2].id -eq 3)

    # audio files: none, one, many, the same filter the steps use
    Check 'audio null' (@(Select-AudioFiles $null).Count -eq 0)
    Check 'audio jeden' (@(Select-AudioFiles (File 'a.FLAC' 1 0)).Count -eq 1)
    Check 'audio jeden inny' (@(Select-AudioFiles (File 'a.mp3' 1 0)).Count -eq 0)
    Check 'audio wiele' (@(Select-AudioFiles @((File 'a.flac' 1 0), (File 'b.mp3' 1 1),
        (File 'c.aiff' 1 2))).Count -eq 2)

    # new collections after add
    Check 'nowe z pustej' (@(Select-NewCollections @() (Coll 1 3)).Count -eq 1)
    Check 'nowe z null' (@(Select-NewCollections $null $null).Count -eq 0)
    Check 'nowe brak' (@(Select-NewCollections 1 (Coll 1 3)).Count -eq 0)
    Check 'nowe wiele' (@(Select-NewCollections @(1) @((Coll 1 3), (Coll 2 3), (Coll 3 3))).Count -eq 2)

    # links: youtube over 200 places with repeats, exact albums, minimum, errors
    $youtube = [pscustomobject]@{ name = 'YouTube'; url = 'x'; more_entries_than = 200 }
    $verdict = Get-LinkVerdict $youtube (Coll 5 293 353)
    Check 'youtube ok' ($verdict.Result -eq $OK)
    Check 'youtube obie liczby' ($verdict.Details -match '353' -and $verdict.Details -match '293')
    Check 'youtube za malo' ((Get-LinkVerdict $youtube (Coll 5 150 200)).Result -eq $FAIL)
    $album = [pscustomobject]@{ name = 'Album'; url = 'x'; tracks = 16; download_one = $true }
    $verdict = Get-LinkVerdict $album @((Coll 6 16 16))
    Check 'album ok' ($verdict.Result -eq $OK -and $verdict.Collection.id -eq 6)
    Check 'album zla liczba' ((Get-LinkVerdict $album (Coll 6 15 15)).Result -eq $FAIL)
    Check 'album bez entries' ((Get-LinkVerdict $album (Coll 6 16)).Result -eq $OK)
    $verdict = Get-LinkVerdict $album $null 'Dodano: nic'
    Check 'link bez kolekcji' ($verdict.Result -eq $FAIL -and $verdict.Details -match 'nowe kolekcje: 0')
    Check 'link dwie kolekcje' ((Get-LinkVerdict $album @((Coll 6 16), (Coll 7 16))).Result -eq $FAIL)
    $minimum = [pscustomobject]@{ name = 'Playlista'; url = 'x'; min_tracks = 10 }
    Check 'minimum ok' ((Get-LinkVerdict $minimum (Coll 8 16 16)).Result -eq $OK)
    Check 'minimum za malo' ((Get-LinkVerdict $minimum (Coll 8 9 9)).Result -eq $FAIL)
    $exact = [pscustomobject]@{ name = 'Playlista'; url = 'x'; entries = 120 }
    Check 'pozycje ok' ((Get-LinkVerdict $exact (Coll 9 100 120)).Result -eq $OK)
    $free = [pscustomobject]@{ name = 'Bez liczby'; url = 'x' }
    Check 'bez liczby' ((Get-LinkVerdict $free (Coll 9 1)).Result -eq $OK)

    # placeholders left from links.example.json, in the address or in a number
    $ready = [pscustomobject]@{ name = 'A'; url = 'https://x'; tracks = 4 }
    $noUrl = [pscustomobject]@{ name = 'B'; url = '<link>'; tracks = 4 }
    $noCount = [pscustomobject]@{ name = 'C'; url = 'https://x'; entries = '<number of videos>' }
    Check 'znaczniki brak' (@(Select-PlaceholderLinks $null).Count -eq 0)
    Check 'znaczniki jeden gotowy' (@(Select-PlaceholderLinks $ready).Count -eq 0)
    Check 'znaczniki jeden link' (@(Select-PlaceholderLinks $noUrl).Count -eq 1)
    Check 'znaczniki wiele' (@(Select-PlaceholderLinks @($ready, $noUrl, $noCount)).Count -eq 2)

    # csv import
    Check 'csv brak' ((Get-CsvVerdict $null 3 0).Result -eq $FAIL)
    Check 'csv jeden' ((Get-CsvVerdict (Coll 1 3 3 'csv' $CsvName) 3 0).Result -eq $OK)
    Check 'csv zaznaczona' ((Get-CsvVerdict (Coll 1 3 3 'csv' $CsvName $true) 3 0).Result -eq $FAIL)
    Check 'csv dwie' ((Get-CsvVerdict @((Coll 1 3 3 'csv' $CsvName), (Coll 2 3 3 'csv' $CsvName)) 3 0).Result -eq $FAIL)
    Check 'csv wiele innych' ((Get-CsvVerdict @((Coll 1 3 3 'csv' $CsvName), (Coll 2 5), (Coll 3 5)) 3 0).Result -eq $OK)
    Check 'csv kod' ((Get-CsvVerdict (Coll 1 3 3 'csv' $CsvName) 3 1).Result -eq $FAIL)

    # one track downloaded
    Check 'pobranie brak plikow' ((Get-DownloadVerdict 0 0 $null $false 'linia' 20).Result -eq $FAIL)
    $verdict = Get-DownloadVerdict 1 0 (File 'utwor.flac' 31457280 1) $false '' 20
    Check 'pobranie jeden plik' ($verdict.Result -eq $OK -and $verdict.Details -match 'utwor.flac')
    $verdict = Get-DownloadVerdict 1 2 @((File 'a.flac' 1 0), (File 'b.wav' 1 5), (File 'c.flac' 1 2)) $false '' 20
    Check 'pobranie wiele plikow' ($verdict.Result -eq $OK -and $verdict.Details -match 'b.wav')
    Check 'pobranie limit' ((Get-DownloadVerdict 1 0 (File 'a.flac' 1 0) $true '' 20).Result -eq $FAIL)

    # uninstall
    $verdict = Get-UninstallVerdict @() (File 'a.flac' 1 0)
    Check 'deinstalacja jeden plik' ($verdict.Result -eq $OK -and $verdict.Details -match ' 1 plik')
    Check 'deinstalacja bez plikow' ((Get-UninstallVerdict $null $null).Details -match ' 0 plik')
    Check 'deinstalacja problem' ((Get-UninstallVerdict 'NeonSeek.exe został' $null).Result -eq $FAIL)
    Check 'deinstalacja problemy' ((Get-UninstallVerdict @('a', 'b') @()).Details -eq 'a; b')

    # summary and helpers
    $row = [pscustomobject]@{ Krok = 'x'; Wynik = $FAIL; 'Szczegóły' = '' }
    $good = [pscustomobject]@{ Krok = 'y'; Wynik = $OK; 'Szczegóły' = '' }
    Check 'bledy brak' ((Get-FailedCount $null) -eq 0)
    Check 'bledy jeden' ((Get-FailedCount $row) -eq 1)
    Check 'bledy wiele' ((Get-FailedCount @($row, $good, $row)) -eq 2)
    Check 'ostatnie linie pusto' ((Get-LastLines '' 2) -eq '')
    Check 'ostatnie linie jedna' ((Get-LastLines 'a' 2) -eq 'a')
    Check 'ostatnie linie wiele' ((Get-LastLines "a`r`nb`nc`n" 2) -eq 'b c')
    $planned = Get-PlannedInstallDir $null
    Check 'folder domyslny' (-not $planned.Previous -and $planned.Dir -like '*\Programs\NeonSeek')
    $planned = Get-PlannedInstallDir ([pscustomobject]@{ InstallLocation = 'D:\Apps\NeonSeek\'; DisplayVersion = '0.1.0' })
    Check 'folder poprzedni' ($planned.Previous -and $planned.Dir -eq 'D:\Apps\NeonSeek' -and $planned.Version -eq '0.1.0')
    $planned = Get-PlannedInstallDir ([pscustomobject]@{ DisplayVersion = '0.1.0' })
    Check 'folder bez wpisu' (-not $planned.Previous)

    $failed = @($script:wrong)
    Write-Host ("Self test: {0} sprawdzen, {1} bledow, PowerShell {2}" -f $script:checks,
        $failed.Count, $PSVersionTable.PSVersion)
    foreach ($name in $failed) { Write-Host "  BLAD: $name" }
    if ($failed.Count) { return 1 }
    return 0
}

if ($SelfTest) {
    exit (Invoke-SelfTest)
}

# registry entry of the installed NeonSeek, read only

function Get-Installed {
    if (-not (Test-Path -LiteralPath $UninstallKey)) { return $null }
    return Get-ItemProperty -LiteralPath $UninstallKey
}

if (-not $WorkDir) {
    $WorkDir = Join-Path $env:USERPROFILE ('NeonSeek-smoke\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$WorkDir = [System.IO.Path]::GetFullPath($WorkDir)
$Data = Join-Path $WorkDir 'data'
$Library = Join-Path $WorkDir 'library'
$Results = Join-Path $WorkDir 'results'
$SetupCopies = Join-Path $WorkDir 'setup'
$LogFile = Join-Path $Results 'smoke-test.log'
$OldSetup = Join-Path $SetupDir "NeonSeek-Setup-$OldVersion.exe"
$NewSetup = Join-Path $SetupDir "NeonSeek-Setup-$NewVersion.exe"
$UserData = Join-Path $env:APPDATA 'NeonSeek'

# plan

$planned = Get-PlannedInstallDir (Get-Installed)
if ($planned.Previous) {
    $where = @"
  2. Cicho zainstaluje NeonSeek $OldVersion na zainstalowana wersje $($planned.Version), w folderze
     tej instalacji (instalator zawsze bierze folder poprzedniej instalacji):
       $($planned.Dir)
     Uruchomiony NeonSeek zostanie zamkniety przez instalator.
"@
} else {
    $where = @"
  2. Cicho zainstaluje NeonSeek $OldVersion do domyslnego folderu (NeonSeek nie jest teraz
     zainstalowany):
       $($planned.Dir)
"@
}
$plan = @"
NeonSeek - testy dymne na komputerze testowym

Skrypt zrobi po kolei:
  1. Sprawdzi, czy NeonSeek zapisal konto Soulseek w Menedzerze poswiadczen (bez czytania hasla).
$where
  3. Zaimportuje CSV i sprawdzi, ze kolekcja jest odznaczona.
  4. Doda linki z pliku $LinksFile i sprawdzi liczbe pozycji i utworow.
  5. Zaloguje sie do Soulseeka na zapisane konto i pobierze JEDEN utwor z TIDAL-a (jedno
     wyszukiwanie, domyslne limity).
  6. Uruchomi NeonSeek i zainstaluje na nim $NewVersion (aktualizacja przy uruchomionej aplikacji,
     w tym samym folderze).
"@
if ($SkipUninstall) {
    $plan += "`n  7. Odinstalowanie pominiete (-SkipUninstall)."
} else {
    $plan += "`n  7. Odinstaluje NeonSeek i sprawdzi, ze dane i biblioteka zostaly."
}
$plan += @"


Dane i biblioteka testu, wyniki: $WorkDir
Twoje dane w %APPDATA%\NeonSeek nie sa uzywane ani zmieniane, tylko sprawdzane na koniec.
Kopie instalatorow w folderze testu dostaja Unblock-File (SmartScreen sprawdza sie recznie).
"@
Write-Host $plan
if (-not $TestMachine) {
    Write-Host ''
    Write-Host 'Nic nie zostalo zrobione. Ten skrypt instaluje i odinstalowuje NeonSeeka, wiec rusza'
    Write-Host 'tylko na komputerze testowym, z jawnym przelacznikiem:'
    Write-Host '  powershell -ExecutionPolicy Bypass -File smoke-test.ps1 -TestMachine'
    exit 2
}
Write-Host ''
Write-Host 'Start za 10 sekund. Ctrl+C przerywa.'
Start-Sleep -Seconds 10

# results and log

New-Item -ItemType Directory -Force -Path $Data, $Library, $Results, $SetupCopies | Out-Null
$Rows = New-Object System.Collections.Generic.List[object]

function Write-Log([string]$Text) {
    $stamp = Get-Date -Format 'HH:mm:ss'
    Add-Content -LiteralPath $LogFile -Value "[$stamp] $Text" -Encoding UTF8
}

function Add-Row([string]$Step, [string]$Result, [string]$Details) {
    $Rows.Add([pscustomobject]@{ Krok = $Step; Wynik = $Result; 'Szczegóły' = $Details })
    Write-Log "== $Step : $Result : $Details"
    $color = 'Green'
    if ($Result -eq $FAIL) { $color = 'Red' } elseif ($Result -eq $SKIP) { $color = 'Yellow' }
    Write-Host ("{0,-10} {1}  {2}" -f $Result, $Step, $Details) -ForegroundColor $color
}

function Add-Verdict([string]$Step, $Verdict) {
    Add-Row $Step $Verdict.Result $Verdict.Details
}

function Quote-Arg([string]$Arg) {
    # windows command line rules: quotes around spaces, backslashes before a quote doubled
    if ($Arg -ne '' -and $Arg -notmatch '[\s"]') { return $Arg }
    $escaped = [regex]::Replace($Arg, '(\\*)"', { param($m) $m.Groups[1].Value * 2 + '\"' })
    $escaped = [regex]::Replace($escaped, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
    return '"' + $escaped + '"'
}

function Invoke-Logged([string]$File, [string[]]$Arguments, [int]$TimeoutSec = 300) {
    $line = (@($Arguments) | ForEach-Object { Quote-Arg $_ }) -join ' '
    Write-Log "> $File $line"
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = $line
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    # neonseek-cli writes utf-8 into a pipe on every windows (D-260)
    $info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $process = [System.Diagnostics.Process]::Start($info)
    $out = $process.StandardOutput.ReadToEndAsync()
    $err = $process.StandardError.ReadToEndAsync()
    $timedOut = -not $process.WaitForExit($TimeoutSec * 1000)
    if ($timedOut) {
        try { $process.Kill() } catch { }
        $process.WaitForExit(10000) | Out-Null
    } else {
        $process.WaitForExit()
    }
    $result = [pscustomobject]@{
        ExitCode = $(if ($timedOut) { -1 } else { $process.ExitCode })
        Out = [string]$out.Result
        Err = [string]$err.Result
        TimedOut = $timedOut
    }
    if ($result.Out) { Add-Content -LiteralPath $LogFile -Value $result.Out -Encoding UTF8 }
    if ($result.Err) { Add-Content -LiteralPath $LogFile -Value $result.Err -Encoding UTF8 }
    Write-Log ("exit code {0}{1}" -f $result.ExitCode, $(if ($timedOut) { " (limit $TimeoutSec s)" } else { '' }))
    return $result
}

function Get-InstallDir {
    $installed = Get-Installed
    $location = [string](Get-Prop $installed 'InstallLocation')
    if (-not $location) { return $null }
    return $location.TrimEnd('\')
}

function Get-Cli {
    $dir = Get-InstallDir
    if ($null -eq $dir) { return $null }
    $cli = Join-Path $dir 'neonseek-cli.exe'
    if (Test-Path -LiteralPath $cli) { return $cli }
    return $null
}

function Invoke-Cli([string[]]$Arguments, [int]$TimeoutSec = 300) {
    $cli = Get-Cli
    if ($null -eq $cli) { throw 'neonseek-cli.exe nie jest zainstalowany' }
    return Invoke-Logged $cli (@('--data-dir', $Data) + $Arguments) $TimeoutSec
}

function Get-Collections {
    # callers take the result with @(...): none, one or many collections
    $run = Invoke-Cli @('collections', '--json') 120
    if ($run.ExitCode -ne 0) { throw "collections --json: kod $($run.ExitCode)" }
    ConvertFrom-CollectionsJson $run.Out
}

function Test-SoulseekLogin {
    # cmdkey lists targets and user names, never passwords. neonseek keeps the login under the
    # names soulseek-username and soulseek-password of the service NeonSeek
    $listing = @(& cmdkey.exe /list) -join "`n"
    return ($listing -match 'soulseek-username') -and ($listing -match 'soulseek-password')
}

function Get-FolderState([string]$Folder) {
    if (-not (Test-Path -LiteralPath $Folder)) { return '' }
    $files = @(Get-ChildItem -LiteralPath $Folder -Recurse -File -Force -ErrorAction SilentlyContinue |
        Sort-Object FullName |
        ForEach-Object { '{0}|{1}|{2}' -f $_.FullName, $_.Length, $_.LastWriteTimeUtc.Ticks })
    return ($files -join "`n")
}

function Get-AudioFiles([string]$Folder) {
    # callers take the result with @(...)
    if (-not (Test-Path -LiteralPath $Folder)) { return }
    Select-AudioFiles @(Get-ChildItem -LiteralPath $Folder -Recurse -File -ErrorAction SilentlyContinue)
}

function Install-NeonSeek([string]$Setup, [string]$Version, [string]$LogName) {
    # callers take the problems with @(...)
    $copy = Join-Path $SetupCopies (Split-Path $Setup -Leaf)
    Copy-Item -LiteralPath $Setup -Destination $copy -Force
    Unblock-File -LiteralPath $copy
    $setupLog = Join-Path $Results $LogName
    $run = Invoke-Logged $copy @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        '/CLOSEAPPLICATIONS', "/LOG=$setupLog") 600
    $installed = Get-Installed
    $problems = @()
    if ($run.ExitCode -ne 0) { $problems += "kod wyjścia instalatora $($run.ExitCode)" }
    if ($null -eq $installed) {
        $problems += 'brak wpisu na liście programów'
    } elseif ([string](Get-Prop $installed 'DisplayVersion') -ne $Version) {
        $problems += "lista programów: $(Get-Prop $installed 'DisplayVersion')"
    }
    $cli = Get-Cli
    if ($null -eq $cli) {
        $problems += 'brak neonseek-cli.exe'
    } else {
        $version = (Invoke-Logged $cli @('--version') 60).Out.Trim()
        if ($version -ne $Version) { $problems += "neonseek-cli --version: $version" }
        $gui = Join-Path (Get-InstallDir) 'NeonSeek.exe'
        if (-not (Test-Path -LiteralPath $gui)) { $problems += 'brak NeonSeek.exe' }
    }
    $problems
}

Write-Log "smoke test start, work dir $WorkDir"
Write-Log ("windows {0}, powershell {1}" -f [System.Environment]::OSVersion.VersionString, $PSVersionTable.PSVersion)
$stopped = $false

# 1. preconditions

$problems = @()
foreach ($setup in @($OldSetup, $NewSetup)) {
    if (-not (Test-Path -LiteralPath $setup)) { $problems += "brak $setup" }
}
foreach ($file in @($LinksFile, $CsvFile)) {
    if (-not (Test-Path -LiteralPath $file)) { $problems += "brak $file" }
}
$links = @()
if (Test-Path -LiteralPath $LinksFile) {
    try {
        $links = @((Get-Content -LiteralPath $LinksFile -Raw -Encoding UTF8 | ConvertFrom-Json).links |
            ForEach-Object { $_ } | Where-Object { $null -ne $_ })
        $placeholders = @(Select-PlaceholderLinks $links)
        if ($placeholders.Count) {
            $problems += ("w $LinksFile zostały znaczniki <...> zamiast linków albo liczb: {0}" -f
                (@($placeholders | ForEach-Object { Get-Prop $_ 'name' }) -join ', '))
        }
    } catch {
        $problems += "nie da się odczytać $LinksFile ($($_.Exception.Message))"
    }
}
if (@($problems).Count) {
    Add-Row 'Pliki testu' $FAIL ($problems -join '; ')
    $stopped = $true
} else {
    Add-Row 'Pliki testu' $OK ("instalatory $OldVersion i $NewVersion, linki: {0}" -f $links.Count)
}

if (-not $stopped) {
    if (Test-SoulseekLogin) {
        Add-Row 'Konto Soulseek zapisane' $OK 'wpisy NeonSeek w Menedżerze poświadczeń są (hasło nieczytane)'
    } else {
        Add-Row 'Konto Soulseek zapisane' $FAIL 'brak konta zapisanego przez NeonSeeka'
        Write-Host ''
        Write-Host 'Na tym komputerze nie ma konta Soulseek zapisanego przez NeonSeeka.' -ForegroundColor Red
        Write-Host 'Uruchom raz NeonSeeka, w kreatorze w kroku 1 załóż albo wpisz konto testowe'
        Write-Host '(POŁĄCZ I ZAPISZ), zamknij NeonSeeka i uruchom ten skrypt jeszcze raz.'
        $stopped = $true
    }
}

$userDataBefore = Get-FolderState $UserData
$installedBefore = Get-Installed
if ($null -ne $installedBefore) {
    Write-Log "installed before: $(Get-Prop $installedBefore 'DisplayVersion') in $(Get-Prop $installedBefore 'InstallLocation')"
} else {
    Write-Log 'installed before: none'
}

# 2. silent install of the old version

$installedOk = $false
if ($stopped) {
    Add-Row "Cicha instalacja $OldVersion" $SKIP 'warunki wstępne nie są spełnione'
} else {
    $what = 'czysty system'
    if ($null -ne $installedBefore) { $what = "na wersję $(Get-Prop $installedBefore 'DisplayVersion')" }
    $problems = @(Install-NeonSeek $OldSetup $OldVersion "setup-$OldVersion.log")
    if ($problems.Count) {
        Add-Row "Cicha instalacja $OldVersion" $FAIL ($problems -join '; ')
    } else {
        $installedOk = $true
        Add-Row "Cicha instalacja $OldVersion" $OK "$what, $(Get-InstallDir)"
    }
}

if ($installedOk) {
    $run = Invoke-Cli @('settings', 'library_dir', $Library)
    $status = Invoke-Cli @('soulseek', 'status')
    if ($run.ExitCode -ne 0) {
        Add-Row 'Biblioteka i konto w CLI' $FAIL "settings library_dir: kod $($run.ExitCode)"
        $installedOk = $false
    } elseif ($status.ExitCode -ne 0) {
        Add-Row 'Biblioteka i konto w CLI' $FAIL 'soulseek status: brak zapisanego konta'
        $installedOk = $false
    } else {
        Add-Row 'Biblioteka i konto w CLI' $OK $status.Out.Trim()
    }
}

# 3. csv import, the collection stays unselected

$step = 'Import CSV, kolekcja odznaczona'
if (-not $installedOk) {
    Add-Row $step $SKIP 'brak instalacji'
} else {
    try {
        $expected = @(Get-Content -LiteralPath $CsvFile -Encoding UTF8 | Where-Object { $_.Trim() }).Count - 1
        $run = Invoke-Cli @('import', 'csv', $CsvFile, '--name', $CsvName)
        Add-Verdict $step (Get-CsvVerdict @(Get-Collections) $expected $run.ExitCode)
    } catch {
        Add-Row $step $FAIL $_.Exception.Message
    }
}

# 4. links: places in the list and different tracks

$downloadCollection = $null
foreach ($link in $links) {
    $step = "Link: $(Get-Prop $link 'name')"
    if (-not $installedOk) { Add-Row $step $SKIP 'brak instalacji'; continue }
    try {
        $before = @(@(Get-Collections) | ForEach-Object { $_.id })
        $run = Invoke-Cli @('add', [string](Get-Prop $link 'url')) 600
        if ($run.TimedOut) { Add-Row $step $FAIL 'przekroczony limit 10 minut'; continue }
        $new = @(Select-NewCollections $before @(Get-Collections))
        $verdict = Get-LinkVerdict $link $new $run.Out
        Add-Verdict $step $verdict
        if ($null -ne $verdict.Collection) { $downloadCollection = $verdict.Collection }
    } catch {
        Add-Row $step $FAIL $_.Exception.Message
    }
}

# 5. one track from tidal, to the end

$step = 'Pobranie 1 utworu z TIDAL-a'
if (-not $installedOk) {
    Add-Row $step $SKIP 'brak instalacji'
} elseif ($null -eq $downloadCollection) {
    Add-Row $step $SKIP 'żaden link nie ma download_one albo nie został dodany'
} else {
    try {
        $audioBefore = @(Get-AudioFiles $Library).Count
        $run = Invoke-Cli @('download', '--collection', "$($downloadCollection.id)", '--limit', '1',
            '--yes') ($DownloadMinutes * 60)
        Invoke-Cli @('tracks', '--collection', "$($downloadCollection.id)") | Out-Null
        $after = @(@(Get-Collections) | Where-Object { $_.id -eq $downloadCollection.id })
        $done = 0
        if ($after.Count) { $done = [int]$after[0].done }
        Add-Verdict $step (Get-DownloadVerdict $done $audioBefore @(Get-AudioFiles $Library) `
            $run.TimedOut $run.Out $DownloadMinutes)
    } catch {
        Add-Row $step $FAIL $_.Exception.Message
    }
}

# 6. update while the app runs

$step = "Aktualizacja do $NewVersion przy uruchomionej aplikacji"
if (-not $installedOk) {
    Add-Row $step $SKIP 'brak instalacji'
} else {
    try {
        $countBefore = @(Get-Collections).Count
        $gui = Join-Path (Get-InstallDir) 'NeonSeek.exe'
        $app = Start-Process -FilePath $gui -ArgumentList @('--data-dir', (Quote-Arg $Data)) -PassThru
        $deadline = (Get-Date).AddSeconds(90)
        do {
            Start-Sleep -Milliseconds 500
            $app.Refresh()
        } while (-not $app.HasExited -and $app.MainWindowHandle -eq 0 -and (Get-Date) -lt $deadline)
        if ($app.HasExited -or $app.MainWindowHandle -eq 0) {
            Add-Row $step $FAIL 'NeonSeek nie otworzył okna w 90 s'
        } else {
            Write-Log "app running, pid $($app.Id)"
            Start-Sleep -Seconds 3
            $problems = @(Install-NeonSeek $NewSetup $NewVersion "setup-$NewVersion.log")
            $app.Refresh()
            if (-not $app.HasExited) {
                $problems += 'stara aplikacja dalej działa'
                try { $app.Kill() } catch { }
            }
            $countAfter = @(Get-Collections).Count
            if ($countAfter -ne $countBefore) { $problems += "kolekcje przed: $countBefore, po: $countAfter" }
            if ($problems.Count) {
                Add-Row $step $FAIL ($problems -join '; ')
            } else {
                Add-Row $step $OK "aplikacja zamknięta przez instalator, $countAfter kolekcji zostało, $(Get-InstallDir)"
            }
        }
    } catch {
        Add-Row $step $FAIL $_.Exception.Message
    }
}

# 7. uninstall, data and library stay

$step = 'Odinstalowanie, dane i biblioteka zostają'
if ($SkipUninstall) {
    Add-Row $step $SKIP '-SkipUninstall'
} elseif (-not $installedOk) {
    Add-Row $step $SKIP 'brak instalacji'
} else {
    try {
        $installDir = Get-InstallDir
        $dataBefore = Get-FolderState $Data
        $libraryBefore = Get-FolderState $Library
        $uninstaller = ([string](Get-Prop (Get-Installed) 'UninstallString')).Trim('"')
        Invoke-Logged $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') 300 | Out-Null
        # the uninstaller starts a copy of itself and ends at once: wait for the result
        $deadline = (Get-Date).AddMinutes(5)
        while (((Test-Path -LiteralPath $UninstallKey) -or
                (Test-Path -LiteralPath (Join-Path $installDir 'NeonSeek.exe'))) -and
               (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 2
        }
        $problems = @()
        if (Test-Path -LiteralPath $UninstallKey) { $problems += 'wpis na liście programów został' }
        if (Test-Path -LiteralPath (Join-Path $installDir 'NeonSeek.exe')) { $problems += 'NeonSeek.exe został' }
        if ((Get-FolderState $Data) -ne $dataBefore) { $problems += 'dane testu się zmieniły' }
        if (-not (Test-Path -LiteralPath (Join-Path $Data 'neonseek.db'))) { $problems += 'brak bazy danych' }
        if ((Get-FolderState $Library) -ne $libraryBefore) { $problems += 'biblioteka się zmieniła' }
        if ((Get-FolderState $UserData) -ne $userDataBefore) { $problems += '%APPDATA%\NeonSeek się zmienił' }
        if (-not (Test-SoulseekLogin)) { $problems += 'konto Soulseek zniknęło z Menedżera poświadczeń' }
        Add-Verdict $step (Get-UninstallVerdict $problems @(Get-AudioFiles $Library))
    } catch {
        Add-Row $step $FAIL $_.Exception.Message
    }
}

# summary

$table = $Rows | Format-Table -AutoSize -Wrap | Out-String -Width 220
$failed = Get-FailedCount $Rows
$summary = "NeonSeek testy dymne $(Get-Date -Format 'yyyy-MM-dd HH:mm')`n" + $table +
    "Błędy: $failed. Pełny log: $LogFile`n"
Set-Content -LiteralPath (Join-Path $Results 'summary.txt') -Value $summary -Encoding UTF8
Write-Host ''
Write-Host $summary
Write-Log "smoke test end, $failed failed"
if ($failed) { exit 1 }
exit 0
