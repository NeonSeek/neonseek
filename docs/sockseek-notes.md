# Sockseek: wyniki fazy 0 i ręcznych sprawdzeń fazy 2

Stan na 2026-10-07. Wszystko sprawdzone na przypiętej binarce **Sockseek 3.0.5**. Sekcje 1-10 i 12 to wyniki fazy 0: Sockseek uruchamiany wyłącznie z `--mock-files-dir` (bez połączenia z Soulseekiem) i z fałszywymi danymi logowania (`--user spikeuser --pass SpikeSecret123`). Sekcja 11 opisuje, czego w fazie 0 nie dało się sprawdzić bez Soulseeka, a sekcja 11.1 zawiera wyniki ręcznych sprawdzeń na prawdziwym serwerze Soulseek z początku fazy 2. Tam, gdzie wniosek pochodzi z kodu źródłowego, a nie z uruchomienia, jest to wyraźnie zaznaczone.

- Skrypt spike'a: [scripts/spike_sockseek.py](../scripts/spike_sockseek.py). Odtworzenie: `uv run python scripts/spike_sockseek.py --work <pusty katalog>` (wymaga ffmpeg w PATH, tylko do wygenerowania plików audio).
- Surowe wyjścia: [docs/sockseek-samples/](sockseek-samples/). Ścieżka katalogu roboczego zastąpiona tam przez `<WORK>`, poza tym bajty bez zmian.
- Źródła do odczytu: tag `v3.0.5`, commit `8d1d8153df6cb86a353ca379051d9ca7b6997a89`.
- Decyzje właściciela po fazie 0 (2026-10-07) są wpisane do PLAN.md (sekcje 1, 2, 3, 5, 7, 8, 9, 10, 11, 12) i zebrane w sekcji 12 tego pliku.

## 1. Przypięta wersja i paczka

| | |
|---|---|
| Wersja | `3.0.5` (`sockseek.exe --version` wypisuje `3.0.5`, kod 0) |
| Wydanie | https://github.com/fiso64/sockseek/releases/tag/v3.0.5 (2026-08-10, nie prerelease) |
| Paczka Windows | `sockseek_3.0.5_win-x64.zip`, 50 404 459 B |
| sha256 | `1b5c1189dcfc24cc9fea22dc67a58b7a5f0a127d0367355cf2a8718100044802` (zgodne z digestem z API GitHuba) |
| Zawartość | tylko `LICENSE` (AGPL-3.0, 34 523 B) i `sockseek.exe` (114 581 730 B) |
| Samodzielna | **tak**. Binarka jest zbudowana pod .NET 10 (`.NETCoreApp,Version=v10.0` w pliku), a na maszynie testowej są tylko runtime'y .NET 6, 8 i 9, mimo to działa. Skrypt budowania (`build/PublishZip.targets`) domyślnie używa `--self-contained=true -p:PublishSingleFile=true`. Ostateczne potwierdzenie na czystym koncie: faza 4 |
| Pin w repo | [scripts/sockseek-pin.json](../scripts/sockseek-pin.json), pobieranie: [scripts/fetch_sockseek.ps1](../scripts/fetch_sockseek.ps1) (sprawdza sha256 i `--version`, zapisuje `vendor/sockseek/SOURCE.txt` ze wskazaniem źródeł dla AGPL) |

Nazwy zasobów zmieniały się między wydaniami (`sldl_win-x86_self-contained.zip` w 2.x, `sockseek_win-x64.zip` w 3.0.0-dev.14, `sockseek_<wersja>_win-x64.zip` od 3.0.4), dlatego pin trzyma pełny URL, a nie wzorzec.

## 2. Dlaczego spike nie dotyka sieci

Na podstawie kodu (`Sockseek.Core/Soulseek/SoulseekClientManager.cs`, `LocalFilesSoulseekClient.cs`):

- Gdy `MockFilesDir` jest ustawione, `CreateClientInstance` tworzy `LocalFilesSoulseekClient` zamiast `SoulseekClient`. Nie powstaje też listener TCP.
- `LocalFilesSoulseekClient.ConnectAsync` to `return Task.CompletedTask;`, a `SetSharedCountsAsync` też nic nie robi.
- Z mockiem nie są wymagane `--user`/`--pass` (`GetMissingCredentialMessage` zwraca `null`).
- HTTP w CLI występuje tylko dla `--remote` i ekstraktorów Bandcamp, MusicBrainz i YouTube. Wejście CSV z nich nie korzysta.
- Mock ma jednego „użytkownika” o nazwie `local`, a każdy plik z katalogu jest jego wynikiem. Ścieżka zdalna to ścieżka względna wewnątrz katalogu mock (`Alpha Artist\First Album\01 - First Song.flac`). Długość, bitrate, sample rate i bit depth czyta przez TagLib.

## 3. Flagi z sekcji 7 na wersji 3.0.5

Każda flaga jest w `--help` i była użyta w realnym uruchomieniu (`main` i pozostałe), z kodem wyjścia innym niż `2` (błąd użycia).

| Flaga | Stan | Uwagi |
|---|---|---|
| `<run.csv>` (pozycyjnie) | ok | typ wejścia wykrywany automatycznie (`--input-type csv` też istnieje) |
| `--user`, `--pass` | ok | z mockiem ignorowane, nie trafiają do żadnego wyjścia ani logu. Używać formy `--user=<u> --pass=<p>` (sekcja 10) |
| `--no-config` | ok, **sprawdzone działanie** | `sockseek.conf` z `write-playlist = true` obok exe: bez `--no-config` powstały pliki `.m3u8`, z `--no-config` nie. Config szukany w `~/.config/sockseek/`, `%APPDATA%\sockseek\`, `$XDG_CONFIG_HOME/sockseek/` i obok exe |
| `--output-dir` | ok | alias `-o`, starsza nazwa `-p/--path` |
| `--format` | ok | warunek twardy, **porównuje tylko rozszerzenie pliku** (sekcja 8) |
| `--pref-format` | ok | tylko ranking, domyślnie `mp3` |
| `--length-tol 3` | ok | domyślnie 3, granica włącznie: plik 60 s pasuje do 57 i 63, nie pasuje do 56 i 64 (`lengthtol`) |
| `--strict-title` | ok | tytuł musi być w nazwie pliku |
| `--strict-artist` | ok | artysta musi być w pełnej ścieżce. Pusty artysta wyłącza ten warunek (sekcja 4) |
| `--remove-ft` | ok | usuwa `feat.` i resztę przed wyszukiwaniem. Zdarzenia raportują tytuł **po** usunięciu |
| `--no-write-index` | ok | żaden plik indeksu nie powstał w katalogu wyjściowym |
| `--no-skip-existing` | ok | |
| `--name-format "{row}"` | ok | sekcja 4 |
| `--progress-json` | ok | sekcja 5 |
| `--log-file` | ok | pisze log na poziomie debug niezależnie od `-v` |

Pozostałe sprawdzone przy okazji: `--mock-files-dir`, `--mock-files-slow`, `--mock-files-fail-downloads <n>`, `--searches-per-time` (domyślnie 34), `--searches-renew-time` (domyślnie 220), `--no-listen`, `--listen-port` (domyślnie 49998), `--concurrent-searches` (domyślnie 2), `--concurrent-jobs` (domyślnie 20), `-v`. Tryb `sockseek daemon` (domyślnie `127.0.0.1:5030`) i opcje Spotify istnieją. Nie używamy ich.

## 4. Wejście CSV (`run.csv`)

Potwierdzone uruchomieniem (`main`, `rows`, `quoting`):

- Nagłówki `Artist,Title,Album,Length` są rozpoznawane bez dodatkowych flag. Log na stderr: `Using columns: artist:"Artist", album:"Album", title:"Title", length:"Length".`
- `Length` jest w sekundach (domyślne `--time-format s`).
- Plik czytany jako UTF-8 (`StreamReader(path, UTF8)`). Polskie znaki i inne spoza ASCII działają (`Zażółć Album`, `Café Ünïcode Song`).
- Cytowanie CSV działa: `Rho Artist,"Comma, Don't Song",Rho Album,48` pobrał się poprawnie.
- **`{row}` liczy nagłówek.** Pierwszy wiersz danych to `2` (`main`: wiersz „Alpha Artist – First Song” dał `2.flac`).
- **`{row}` to numer rekordu CSV, nie linii fizycznej.** Pusty wiersz zwiększa licznik, a komórka wieloliniowa liczy się raz. W `rows.csv` (nagłówek, pusta linia, rekord na dwóch liniach, właściwy wiersz) właściwy wiersz dostał `4`, a nie `5`. Wniosek dla adaptera: `run.csv` bez pustych wierszy i bez znaków nowej linii w polach. Wtedy `row = pozycja + 2`.
- **Pusty `Length`:** utwór jest szukany i pobierany bez filtra długości (`Iota Artist – Ninth Song` dał `10.flac`). Na stderr pojawia się `Couldn't parse track length "" with format "s"`, a w `track_list` brak klucza `length`.
- **Pusty `Artist` przy `--strict-artist`:** warunek jest pomijany (`aname.Length == 0` daje `true`). `Tenth Song` z pustym artystą pobrał się z `Kappa Artist\Tenth Album\10 - Tenth Song.flac`. W zdarzeniach brak klucza `artist`.
- Uwaga z kodu (`Csv.cs`): wiersz z pustym `Title` i niepustym `Album` staje się **pobieraniem całego albumu**. Kolumny o nazwach `url`, `trackurl`, `uri`, `id`, `youtubeid` są czytane jako URI utworu. `run.csv` nie może mieć pustego tytułu ani takich kolumn.

## 5. Schemat `--progress-json`

Format: jedna linia JSON na zdarzenie na **stdout**: `{"type": ..., "timestamp": ISO-8601 UTC, "data": {...}}`, pola w camelCase, pola `null` pomijane. Logi czytelne dla człowieka idą na **stderr** (tak przełącza je `--progress-json`).

Zastrzeżenia potwierdzone uruchomieniem:

- **stdout nie jest czystym JSON-em.** Zaraz po `track_list` Sockseek wypisuje na stdout podgląd listy jako zwykły tekst (`  Alpha Artist - First Song (60s)`), a na końcu pustą linię. Adapter musi pomijać linie, które nie parsują się jako obiekt JSON.
- Oba strumienie są w UTF-8, mimo strony kodowej konsoli 850 (`Console.OutputEncoding = UTF8` w `Program.cs`). Znaki spoza ASCII w JSON-ie są escapowane (`Café`).
- `timestamp` nie jest monotoniczny w kolejności linii, bo jest generowany przed blokadą zapisu. Kolejność zdarzeń brać z kolejności linii.

Typy zdarzeń, które realnie się pojawiają (lokalne CLI, `JsonStreamProgressReporter.Attach(ICliBackend)`):

**`track_list`**: raz, na starcie. `index` jest od 0, w kolejności wierszy CSV. `title` jest już po `--remove-ft`.
```json
{"type":"track_list","timestamp":"2026-10-07T16:02:02.9528967Z","data":{"total":15,"pending":15,"existing":0,"notFound":0,"tracks":[{"index":0,"artist":"Alpha Artist","title":"First Song","album":"First Album","length":60,"lifecycleState":"Pending","activityPhase":"None","terminalOutcome":"None","skipReason":"None"},{"index":1,"artist":"Beta Artist","title":"Second Song","album":"Second Album","length":50,"lifecycleState":"Pending","activityPhase":"None","terminalOutcome":"None","skipReason":"None"}, ...]}}
```

**`search_start`**
```json
{"type":"search_start","timestamp":"2026-10-07T16:02:03.0211152Z","data":{"artist":"Alpha Artist","title":"First Song","album":"First Album"}}
{"type":"search_start","timestamp":"2026-10-07T16:02:03.0453240Z","data":{"title":"Tenth Song","album":"Tenth Album"}}
```

**`download_start`**: przy każdej próbie pobrania, także przy przejściu na kolejnego kandydata. `filename` to ścieżka po stronie Soulseeka.
```json
{"type":"download_start","timestamp":"2026-10-07T16:02:03.0522853Z","data":{"artist":"Iota Artist","title":"Ninth Song","username":"local","filename":"Iota Artist\\Ninth Album\\09 - Ninth Song.flac","size":2680773,"extension":"flac"}}
```

**`download_progress`**: **tylko `jobId`**, bez artysty, tytułu i pliku. `jobId` nie występuje w żadnym innym zdarzeniu JSON. Dławione globalnie do jednego zdarzenia na 500 ms dla wszystkich pobrań razem.
```json
{"type":"download_progress","timestamp":"2026-10-07T16:02:58.6285833Z","data":{"jobId":"cc67db04-495c-4344-840b-dd564a47aec7","bytesTransferred":0,"totalBytes":3919605,"percent":0}}
{"type":"download_progress","timestamp":"2026-10-07T16:02:59.1406568Z","data":{"jobId":"cc67db04-495c-4344-840b-dd564a47aec7","bytesTransferred":2155782,"totalBytes":3919605,"percent":55}}
```
`totalBytes` równa się `size` z odpowiadającego `download_start` (3919605 w `slow`, 10584078 w `fail1`). Jedyne praktyczne powiązanie postępu z utworem to więc para `jobId` i `totalBytes == size`.

**`track_state`**: raz na utwór, tylko w stanie końcowym (`lifecycleState: "Terminal"`).
```json
{"type":"track_state","timestamp":"2026-10-07T16:02:03.0940825Z","data":{"artist":"Mu Artist","title":"Twelfth Song","lifecycleState":"Terminal","activityPhase":"None","terminalOutcome":"Succeeded","skipReason":"None","downloadPath":"<WORK>\\out\\main\\13.flac","username":"local","filename":"Mu Artist\\Twelfth Album\\12 - Twelfth Song.flac","size":2876390,"bitRate":521,"extension":"flac","rawResultCount":1,"lockedCount":0}}
{"type":"track_state","timestamp":"2026-10-07T16:02:03.0508068Z","data":{"artist":"Gamma Artist","title":"Third Song","lifecycleState":"Terminal","activityPhase":"None","terminalOutcome":"Failed","skipReason":"None","failureReason":"NoSearchResults","rawResultCount":0,"lockedCount":0}}
{"type":"track_state","timestamp":"2026-10-07T16:03:27.9250338Z","data":{"artist":"Alpha Artist","title":"First Song","lifecycleState":"Terminal","activityPhase":"None","terminalOutcome":"Failed","skipReason":"None","failureReason":"AllDownloadsFailed","rawResultCount":2,"lockedCount":0}}
```

Czego w strumieniu **nie ma** (potwierdzone uruchomieniem, przyczyna w kodzie):

- **Numeru wiersza CSV** ani żadnego identyfikatora wspólnego dla wszystkich zdarzeń.
- **Stanu oczekiwania na limit wyszukiwań.** Silnik ma wewnętrzne zdarzenie `search.rate-limited`, ale `JsonStreamProgressReporter` go nie obsługuje (sekcja 9).
- Zdarzenia o nieudanej próbie pobrania (przed przejściem na kolejnego kandydata). Widać tylko kolejne `download_start` dla tego samego utworu.
- Zdarzenia końca przebiegu i zdarzeń `progress`, `list_progress`, `extraction_failed`. Te są tylko w nieużywanym przez CLI wariancie `Attach(DownloadEvents)`. Przy brakującym pliku CSV stdout jest pusty, a błąd jest tylko na stderr (`missing_csv.stderr.txt`).

Wartości pól (z `Sockseek.Core/Common/Enums.cs`):

- `terminalOutcome`: `None`, `Succeeded`, `Failed`, `Skipped`, `Cancelled`, `PartialSuccess`
- `failureReason`: `InvalidSearchString`, `OutOfDownloadRetries`, `AllDownloadsFailed`, `Other`, `ExtractionFailed`, `Cancelled`, `ChildJobsFailed`, `NoSearchResults`, `NoMatchingResults`
- `skipReason`: `None`, `AlreadyExists`, `NotFoundLastTime`, `Manual`, `Filtered`
- `activityPhase`: `None`, `WaitingForSearchConcurrency`, `SearchRateLimited`, `Searching`, `ProcessingSearchResults`, `Extracting`, `Downloading`, `RetrievingFolder`, `RunningChildren`, `Organizing`, `RunningOnComplete`, `RunningFallback`

Zaobserwowane: `NoSearchResults` przy braku pliku, przy pliku odrzuconym przez `--format` (MP3, `.aif`, `.m4a`) i przy niezgodnej długości (`rawResultCount` jest wtedy 0, bo liczy wyniki po filtrze). `AllDownloadsFailed` przy padnięciu wszystkich kandydatów. Mapowanie przyjęte w PLAN.md (sekcja 7): `Succeeded` przechodzi do `verify`, a potem `done`. `NoSearchResults` i `NoMatchingResults` dają `not_found`. Reszta `Failed` daje `failed`. `Cancelled` wraca do `pending`. Rozróżnienie `not_found` i `failed` decyduje o przebiegu 2 (sekcja 8).

## 6. Kody wyjścia

Z kodu (`Program.cs`): `0 Success`, `1 WorkFailed`, `2 UsageError`, `130 Cancelled`.

Zaobserwowane: `0` gdy wszystkie utwory `Succeeded`. `1` gdy choć jeden `Failed` (`main`, `fmt_*`, `fail_all`, `lengthtol`) i przy brakującym pliku CSV. `2` przy nieznanej fladze (`Input error: Unknown argument: --definitely-not-a-flag`). Kod `1` oznacza więc też normalny przebieg z częścią nieznalezionych utworów. Stan utworów brać ze zdarzeń, nie z kodu wyjścia.

## 7. Mapowanie wiersza na utwór (zatwierdzone, PLAN.md sekcja 7)

Zdarzenia identyfikują utwór tylko przez `artist`/`title` po przetworzeniu (np. `--remove-ft`), co nie jest unikalne. W `main` dwa wiersze `Xi Artist – Same Song` (40 s i 62 s) mają identyczne `search_start`, `download_start` i `track_state`, a różni je tylko `downloadPath`:

```
"downloadPath":"<WORK>\\out\\main\\14.flac", ... "filename":"Xi Artist\\Short Album\\13 - Same Song.flac"
"downloadPath":"<WORK>\\out\\main\\15.flac", ... "filename":"Xi Artist\\Long Album\\13 - Same Song.flac"
```

Wybór:

1. **Sukces:** `--name-format "{row}"`, z `track_state.downloadPath` bierzemy nazwę pliku bez rozszerzenia, czyli `row`. `row - 2` to pozycja w `run.csv`, a pozycja wskazuje `track_id`. Jest to jednoznaczne niezależnie od duplikatów i od `--remove-ft`.
2. **Porażka** (`track_state` bez `downloadPath`): z `track_list` budujemy tabelę `index -> (artist, title)` w wersji po przetworzeniu Sockseeka (`index = row - 2`). `track_state` dopasowujemy po `(artist, title)` do jeszcze nierozstrzygniętych pozycji. Przy duplikatach rozstrzyga eliminacja: sukcesy są już przypisane przez `row`, a jeśli kilka identycznych pozycji kończy się porażką, dostają ten sam powód.
3. **Postęp:** `download_start` daje użytkownika, ścieżkę zdalną i `size` dla `(artist, title)`. `download_progress` przypisujemy do aktywnego pobrania o `size == totalBytes`. Przy dwóch równoległych pobraniach o identycznym rozmiarze procent może trafić do złego wiersza. To błąd wyłącznie kosmetyczny, bo wynik i tak bierzemy z `track_state`.
4. Po zakończeniu procesu każda pozycja bez `track_state` (np. po anulowaniu) wraca do `pending`.

Mapowanie po `(artist, title)` w roli podstawowej odrzucone, bo nie odróżnia duplikatów i zależy od przetwarzania tytułu w Sockseeku.

## 8. Polityka formatu

### FLAC kontra WAV przy `--format flac,wav,aiff,alac --pref-format flac`

Mock `mock_rank`, trzy przypadki, trzy powtórzenia (`rank1`–`rank3`), wynik za każdym razem identyczny:

| Przypadek | Pliki | Wybrany |
|---|---|---|
| A | `Alpha Artist\First Album\01 - Song Aaa.flac` i `.wav` w tym samym folderze | FLAC (`2.flac`) |
| B | `Alpha Artist\random rips\01 - Song Bbb.flac` i `Alpha Artist\Bbb Album\01 - Song Bbb.wav` | **WAV** (`3.wav`) |
| C | FLAC 96 kHz/24 bit i WAV 44,1 kHz/16 bit w tym samym folderze | FLAC (`4.flac`) |

`--print results-full` pokazuje przyczynę (`rank_print.stdout.txt`):
```
Results for Alpha Artist - Song Bbb (61s):
[61s /44.1kHz1411kbps/10.3MB] (0.00MB/s) local\Alpha Artist\Bbb Album\01 - Song Bbb.wav (nec:Satisfied, prf:Format fails)
[61s /44.1kHz521kbps/ 3.8MB] (0.00MB/s) local\Alpha Artist\random rips\01 - Song Bbb.flac (nec:Satisfied, prf:StrictAlbum fails)
```
Klucz sortowania (`ResultSorter.PackHighFlags`) jest leksykograficzny, w tej kolejności: użytkownik nie jest obniżony za porażki, warunki twarde, warunki użytkownika, znana długość, nawiasy, dopasowanie tytułu, **albumu**, **artysty**, `pref-length-tol`, **dopiero potem `formatMatch`**, a za nim bitrate, sample rate, bit depth i wolny slot. Autor zrobił to celowo. Komentarz w kodzie: „Identity beats quality preferences: an MP3 from the requested album is a better candidate than a FLAC from an unrelated album.”

Druga ścieżka do WAV mimo dostępnego FLAC-a (`fail1`): przy `--mock-files-fail-downloads 1` pierwsza próba (FLAC) pada i Sockseek od razu pobiera następnego kandydata, czyli WAV z tego samego folderu (`2.wav`, `terminalOutcome: Succeeded`).

**Wniosek: jedno wywołanie nie gwarantuje FLAC-a.** Obowiązuje wariant dwuprzebiegowy (`rank_flac_only`: z `--format flac` we wszystkich trzech przypadkach FLAC).

**Decyzja (PLAN.md, sekcja 7):**
- Każdy utwór ma etap `downloads.stage`: `flac` albo `fallback`, domyślnie `flac`. `attempts` liczy porażki w bieżącym etapie (`max_attempts`, domyślnie 3).
- Przebieg 1 (`--format flac`): etap `flac`, status `pending` albo `failed` z `attempts < max_attempts`.
- Przejście do etapu `fallback` (tylko `lossless`, na początku przebiegu 2, z bazy): utwory spełniające warunek „czeka na przebieg zastępczy” (`stage = flac` oraz `not_found` albo `failed` z wyczerpanymi próbami) dostają `stage = fallback`, `status = pending`, `attempts = 0`.
- Przebieg 2 (`--format wav,aiff,aif`): etap `fallback`, status `pending` albo `failed` z `attempts < max_attempts`. `not_found` jest tu ostateczny, a `failed` staje się ostateczny po wyczerpaniu prób. Utwór z etapu `fallback` nie jest już szukany jako FLAC.
- Utwór, którego FLAC został znaleziony, ale pobieranie się nie udało, zostaje w etapie `flac` i dalej próbuje FLAC-a, aż wyczerpie próby.
- Anulowanie, limit czasu, awaria i start aplikacji: utwory w toku (`searching`, `downloading`) wracają do `pending`, a `stage` i `attempts` zostają bez zmian.
- Przy `on_missing_flac = not_found` przebieg 2 się nie odpala, a niepobrane utwory z etapu `fallback` wracają na początku uruchomienia do `flac` i `not_found`.
- Ponowienie z raportu: `stage = flac`, `attempts = 0`, `status = pending`.
- `--pref-format` nie jest już przekazywane. Wyjątek od fazy 2: przebieg 2 dostaje `--pref-format aiff,aif` (D-082, podsekcja niżej).

### AIFF przed WAV-em w przebiegu 2 (`--pref-format aiff,aif`, faza 2, 2026-10-08)

Mock z plikami 50 s, `--format wav,aiff,aif` i resztą flag z PLAN.md sekcja 7, trzy powtórzenia każdego wariantu, wynik za każdym razem identyczny:

| Przypadek | Pliki | Bez `--pref-format` | Z `--pref-format aiff,aif` |
|---|---|---|---|
| A | `.wav` i `.aiff` w tym samym folderze | AIFF | AIFF |
| B | `.wav` i `.aif` w tym samym folderze | **WAV** | **AIF** |
| C | WAV w folderze nazwanym jak album, AIFF w `random rips` | WAV | WAV |
| D | jak A, inna kolejność plików | AIFF | AIFF |

`--print results-full` z `--pref-format aiff,aif` pokazuje przy WAV-ie `prf:Format fails`. W przypadku C AIFF ma `prf:StrictAlbum fails`, a dopasowanie albumu jest w kluczu sortowania przed formatem. Wniosek: flaga daje AIFF, gdy oba formaty leżą w tym samym folderze. Bez niej wybór zależy od przypadku (A i D dały AIFF, B dał WAV). WAV z lepiej nazwanego folderu nadal wygrywa, tak jak FLAC z WAV-em w sekcji wyżej. Decyzja właściciela: przebieg 2 dostaje `--pref-format aiff,aif` (D-082). Test integracyjny: `tests/test_download.py::test_pass_2_prefers_aiff_over_wav_in_the_same_folder`.

### Wartości `aiff` i `alac`

`--format` porównuje rozszerzenie pliku dokładnie, bez znajomości kodeka (`FileConditions.FormatSatisfies`: `Path.GetExtension(fname).TrimStart('.').ToLower()` porównane z listą). Wartości nie są walidowane. Uruchomienia na `formats.csv` (pliki `.aiff`, `.aif`, ALAC w `.m4a`, AAC w `.m4a`):

| `--format` | Pobrane |
|---|---|
| `aiff` | tylko `.aiff` (`2.aiff`), **`.aif` odrzucony** |
| `aif` | tylko `.aif` (`3.aif`) |
| `alac` | **nic**. ALAC w `.m4a` nie pasuje, bo `alac` łapie tylko pliki z rozszerzeniem `.alac` |
| `m4a` | **oba**: ALAC (`4.m4a`) i AAC (`5.m4a`) |
| `nonsense` | nic, bez błędu i ostrzeżenia, kod wyjścia 1 |

Sockseek nie odróżnia ALAC od AAC w `.m4a`, a wyniki wyszukiwania Soulseeka nie niosą informacji o kodeku. Rozróżnić je może dopiero nasz krok `verify` po pobraniu. Zestaw rozszerzeń audio znanych Sockseekowi (`Utils.KnownAudioExtensions`): `.mp3 .flac .ogg .m4a .opus .wav .aac .alac .aif .aiff`.

**Decyzja (PLAN.md, sekcje 7 i 11):** formaty zastępcze to `wav`, `aiff` i `aif`. ALAC i `m4a` wypadają z MVP w całości: polityka formatu, `verify`, `cover`, kolumna `format` i testy. Do roadmapy trafia ALAC w `.m4a` jako osobny przebieg z warunkiem bitrate.

## 9. Limiter wyszukiwań

Eksperyment `ratelimit` / `ratelimit_v` (6 utworów, `--searches-per-time 2 --searches-renew-time 15`, obniżone tylko na mocku, bez sieci): wyszukiwania ruszają w paczkach po 2 co 15 s (`0.34 s`, `15.22 s`, `30.23 s`).

- **stdout (`--progress-json`): brak jakiegokolwiek sygnału oczekiwania.** Widać tylko przerwę między `search_start`.
- **stderr bez `-v`: brak sygnału.**
- **stderr z `-v`:** linia tekstowa przy każdym zablokowanym wyszukiwaniu, bez linii „wznowiono”:
  ```
  [   0.39] [005] SongJob: rate limited: Delta Artist - Fourth Song
  [  15.24] [007] SongJob: rate limited: Xi Artist - Same Song
  ```
  `-v` nie zmienia zawartości stdout (22 linie JSON jak bez `-v`).
- **`--log-file`:** te same linie na poziomie debug (`[debug] [jobs] [5] SongJob: rate limited: Delta Artist - Fourth Song`), także bez `-v`.
- Z kodu: silnik zna moment odnowienia (`SearchRateLimitedEventDto(ResetsAt)`), ale w CLI dostaje go tylko reporter dla człowieka, nie JSON.

**Decyzja (PLAN.md, sekcje 7 i 10):** adapter uruchamia Sockseeka z `-v` i parsuje ze stderr linię `SongJob: rate limited: <artysta> - <tytuł>`. Stan kończy się przy następnym `search_start`. Jeśli linia nie pasuje do wzorca, UI pokazuje zwykły stan wyszukiwania i nic się nie wywraca. Wzorca pilnuje test kontraktu na przypiętej wersji (faza 2, mock z obniżonym `--searches-per-time`).

## 10. Anulowanie, pliki tymczasowe, hasło

- Przerwanie procesu w trakcie pobierania (`kill`, `--mock-files-slow`, kill po 6 s) zostawiło `<output-dir>\.sockseek-staging\<guid>\13 - Same Song.flac.incomplete`. Plik w toku ma **oryginalną nazwę zdalną** plus `.incomplete` (nie `{row}`) i leży w podkatalogu `.sockseek-staging`. Gotowe pliki (`2.flac` … `6.flac`) zostały na miejscu. Sprzątanie po anulowaniu musi więc usuwać rekursywnie `.sockseek-staging`, a nie tylko `*.incomplete` w katalogu głównym.
- Po normalnym zakończeniu `.sockseek-staging` znika, a w katalogu wyjściowym zostają tylko pliki `{row}.<ext>`.
- Rozszerzenie pliku wynikowego to rozszerzenie pliku zdalnego (`.aif` zostaje `.aif`, `.m4a` zostaje `.m4a`).
- Fałszywe hasło `SpikeSecret123` i login `spikeuser` nie pojawiły się w stdout, stderr ani w `--log-file` żadnego uruchomienia, także z `-v` i z `-vv` (0 trafień w 54 liniach stdout, 130 liniach stderr i 130 liniach logu na `main.csv`). Dotyczy to trybu mock, w którym klient od razu zgłasza stan „zalogowany” i ścieżka logowania się nie wykonuje. Z kodu: w źródłach 3.0.5 żaden log nie zawiera hasła, a przy prawdziwym logowaniu Sockseek loguje tylko `Logging in as <login>..`. Hasło i tak jest widoczne w linii poleceń procesu dla innych procesów tego samego użytkownika.
- **Hasło zaczynające się od `-` w formie `--pass <hasło>` wycieka w komunikacie błędu** (kod 2, przebieg się nie zaczyna). To sprawdzenie i powyższe sprawdzenie `-v`/`-vv` zrobiłem ręcznie, poza skryptem spike'a: `main.csv`/`one.csv` na `mock_main`, `--format flac`, reszta flag jak w sekcji 3.
  ```
  --pass --SpikeDash123  ->  [error] [cli] Input error: Option '--pass' requires a parameter, but '--SpikeDash123' looks like another option. To use it as the value, pass '--pass=--SpikeDash123'.
  --pass -SpikeDash123   ->  [error] [cli] Input error: Option '--pass' requires a parameter, but '-S' looks like another option. To use it as the value, pass '--pass=-S'.
  ```
  Forma `--pass=<hasło>` przyjmuje `-SpikeDash123`, `--SpikeDash123`, `Spike Space 123` i `a=b;c"d` (kod 0, utwór pobrany, 0 trafień hasła w wyjściu). Wniosek wpisany do PLAN.md: zawsze `--user=<u> --pass=<p>`, a hasło i tak wycinamy z każdego zapisywanego logu.

## 11. Czego nie dało się zweryfikować bez Soulseeka

Te punkty wymagają prawdziwego połączenia, więc w fazie 0 sprawdzono tylko kod. Według decyzji właściciela sprawdzamy je jednorazowo i ręcznie na początku fazy 2 (PLAN.md, sekcje 10 i 12), z dopisaniem wyniku tutaj:

- **Błędny login lub hasło** (`DownloadRunScope.EnsureServicesInitializedAsync`, `SoulseekClientManager`, `Program.cs`): błąd uznany za trwały kończy przebieg wyjątkiem `SoulseekConnectionUnavailableException`, z komunikatem `Soulseek login failed: <powód>` na stderr i kodem `1`. Kodem `1` kończy się też zwykły przebieg z nieznalezionymi utworami, więc rozpoznanie wymaga tekstu ze stderr albo braku `track_list`. Czy odrzucenie hasła przez serwer jest klasyfikowane jako trwałe, zależy od typu wyjątku z Soulseek.NET. Wynik na żywo: sekcja 11.1 (błędne hasło, błąd trwały bez ponawiania).
- **Brak sieci lub timeout logowania:** według kodu błąd przejściowy (`TimeoutException`, `SocketException`, `*ConnectionException`) **nie kończy procesu**. Sockseek loguje `Initial Soulseek login failed. Reconnection will be attempted automatically in the background` i czeka w `WaitUntilReadyAsync`, ponawiając co 1–8 s. Wtedy na stdout może nie pojawić się żaden JSON. W trybie mock `--log-file` pokazuje `Soulseek services initialized` **przed** odczytem CSV, więc `track_list` pojawia się dopiero po zalogowaniu. Adapter potrzebuje własnego limitu czasu na pierwsze zdarzenie (PLAN.md sekcja 7: limit startu). Na żywo nie sprawdzane, sekcja 11.1 tego nie obejmuje (ma tylko zbanowane konto z dokumentacji protokołu, które też wygląda jak brak odpowiedzi).
- **Zakładanie konta na wolną nazwę** i błąd przy zajętej nazwie: brak logiki w Sockseeku, zależy od serwera Soulseek. Wynik na żywo: sekcja 11.1.
- **Zapora Windows:** w trybie mock listener nie powstaje, więc pytania zapory nie da się wywołać. `--no-listen` istnieje i według kodu tworzy klienta z `enableListener: false`. Wynik na żywo: sekcja 11.1 (zajęty port 49998, zapora przy `--no-listen`).
- **Brak hasła w `-v` przy prawdziwym logowaniu** (w mocku ścieżka logowania się nie wykonuje). Wynik na żywo: sekcja 11.1.
- **Cisza przy kolejce u innego użytkownika:** czy Sockseek wypisuje cokolwiek (stdout, stderr przy `-v`), gdy pobieranie czeka w kolejce u innego użytkownika, i jak długo taka cisza trwa. W mocku kolejki nie ma, bo mock zgłasza wolny slot. Od wyniku zależy domyślny `idle_timeout_minutes` (10 minut). Z kodu warto przy tym sprawdzić, jak taka kolejka ma się do `--max-stale-time` (domyślnie 30 s bez postępu). Wynik na żywo i z kodu: sekcja 11.1 („Cisza na wyjściu”), kolejki na żywo jeszcze nie zaobserwowano.
- Kolejność i obniżanie rangi przy wielu prawdziwych użytkownikach (wolny slot, prędkość, `--fails-to-downrank`). Na żywo nie sprawdzane, sekcja 11.1 tego nie obejmuje.

### 11.1 Wyniki ręcznych sprawdzeń (faza 2, 2026-10-07)

Narzędzie: [scripts/live_check_soulseek.py](../scripts/live_check_soulseek.py), Sockseek 3.0.5, prawdziwy serwer Soulseek, dane w katalogu tymczasowym poza repo i poza `%APPDATA%\NeonSeek`. Skrypt czyta login z Credential Manager, wycina hasło i login z każdego zapisywanego wyjścia i ubija proces przy pierwszej linii o nieudanym logowaniu, najpóźniej po 30 s. Wyszukiwania tylko z domyślnymi limitami.

**Port nasłuchu 49998 może być zajęty.** Pierwsze uruchomienie z domyślnym nasłuchem skończyło się po 0,22 s, kod 1, bez kontaktu z serwerem:
```
[soulseek] Logging in as <user>..
[error] [cli] Soulseek login failed: Failed to start listening on 0.0.0.0:49998; the IP and/or port may be in use or are otherwise unavailable
```
Port trzymała usługa innego programu zainstalowanego na komputerze właściciela jako lokalny port połączenia na pętli zwrotnej. 49998 leży w dynamicznym zakresie portów Windows (49152–65535, `netsh int ipv4 show dynamicport tcp`), z którego system przydziela porty połączeniom wychodzącym, więc kolizja może trafić każdego użytkownika losowo. Błąd jest traktowany jako trwały (linia `[cli]` z głównego `catch` w `Program.cs`), bez ponawiania. Okno zapory się nie pojawiło, bo nasłuch nie wystartował. Sockseek nie ma UPnP ani NAT-PMP (brak w źródłach 3.0.5), więc za domowym routerem bez przekierowania portu nasłuch i tak nie przyjmie połączeń z zewnątrz.

**Zakładanie konta na wolną nazwę** (`--no-listen`, losowa nazwa konta próbnego i losowe hasło, konto jednorazowe, hasło nigdzie niezapisane): serwer założył konto przy pierwszym logowaniu, bez żadnego komunikatu o tym w wyjściu. Logowanie trwało 1,75 s, przebieg z pustą listą skończył się sam, kod 0. Na stderr przy `-v` nazwa użytkownika pojawia się 3 razy (`Logging in as`, `Setting share count for`, `Logged in as`).

**Błędne hasło** (to samo konto próbne, inne losowe hasło, jedna próba): po 0,76 s
```
[error] [cli] Soulseek login failed: The server rejected login attempt: INVALIDPASS
```
kod 1. To linia z głównego `catch` w `Program.cs`, tuż przed `return WorkFailed`, a linii `Reconnection will be attempted` nie było, więc odrzucenie hasła jest błędem trwałym i Sockseek kończy się sam, bez ponawiania. Skrypt i tak ubił proces na tej linii, zanim zdążyła się pojawić linia `Exiting CLI session`. Ten sam komunikat dostaje zajęta nazwa z cudzym hasłem: serwer nie odróżnia „zajętej nazwy” od „złego hasła”.

**Hasło w wyjściu `-v` przy prawdziwym logowaniu:** przy różnych losowych nazwie i haśle konta próbnego hasło pojawiło się 0 razy w stdout, stderr i `--log-file`, także przy udanym logowaniu. Uwaga dla redaktora: bardzo krótkie hasło może przypadkiem pasować do fragmentu innego tekstu (tu: losowego numeru `starting token` w logu). Wycięcie takiego fragmentu jest nieszkodliwe, ale liczba trafień nie dowodzi wtedy wycieku.

**Zapora przy `--no-listen`:** okno zapory się nie pojawiło (właściciel obserwował ekran podczas przebiegów), a po wszystkich przebiegach z `--no-listen` nie ma żadnej reguły zapory dla `sockseek.exe` (`Get-NetFirewallApplicationFilter`).

**Logowanie na konto właściciela z pustą listą** (`--no-listen`): logowanie 0,36 s, przebieg skończył się sam, kod 0, bez żadnego wyszukiwania. Pusty `run.csv` (sam nagłówek) to więc test połączenia, który nie zużywa limitu wyszukiwań: Sockseek loguje się przed odczytem listy (`Soulseek services initialized` przed `ExtractJob`), a przy pustej liście nie wypisuje na stdout żadnego JSON-a. Wcześniejsze próby na tym koncie: jedna bez kontaktu z serwerem (zajęty port) i jedna odrzucona `INVALIDPASS` przez źle zapisane hasło, poprawione przez właściciela.

**Pobieranie z `--no-listen`** (4 utwory z playlisty właściciela, wywołanie jak w PLAN.md sekcja 7, `--format flac`, domyślne limity wyszukiwań): 3 pobrane, 1 `NoSearchResults` (`KRØMA - Rebound`, nazwy w tej sekcji wymyślone, wyszukiwanie poszło z poprawnym `Ø`, serwer zwrócił 0 wyników). Całość 44,7 s, kod wyjścia 1 (jeden nieznaleziony). Pliki to poprawne FLAC-i 44,1 kHz/16 bit o długości 306,6, 182,7 i 267,2 s przy 307, 183 i 267 s ze Spotify. Każdy miał już osadzoną okładkę. Pobieranie bez nasłuchu działa, więc nasłuch nie jest potrzebny.

**Cisza na wyjściu:** najdłuższa przerwa między liniami stdout i stderr przy `-v` to 9,7 s (`waiting search`, czekanie na wolne miejsce w `--concurrent-searches 2`), potem 2,1 s. Żadne pobieranie nie czekało w kolejce u innego użytkownika, więc kolejki na żywo nie zaobserwowano. Z kodu (`StaleDownloadCoordinator`, `SearchSettings.MaxStaleTime = 30_000`): pobranie bez zmiany stanu i bez nowych bajtów przez 30 s jest anulowane i Sockseek bierze kolejnego kandydata (nowe `download_start`). Kolejka u innego użytkownika daje więc ciszę rzędu 30 s, a `idle_timeout_minutes = 10` ma duży zapas. Obserwację na żywo powtarzamy przy pierwszym pobraniu kolekcji testowej.

**Pierwsze pobranie kolekcji testowej (2026-10-08).** Playlista testowa właściciela (21 utworów), `neonseek download` z gotowym runnerem, domyślne limity wyszukiwań, `--no-listen`. Najpierw `--limit 5`, zaraz potem cała kolekcja:

| Uruchomienie | Przebieg | Utwory | Wyszukiwania | `--searches-per-time` | Najdłuższa cisza | Czas |
|---|---|---|---|---|---|---|
| 1 (`--limit 5`) | 1 (`flac`) | 5 | 5 | domyślne (34) | 15,1 s, po `track_state` | 1 min 3 s razem |
| 1 | 2 (`wav,aiff,aif`) | 1 | 1 | 29 | 5,0 s, przy `searching` | |
| 2 (całość) | 1 | 16 | 16 | 28 | 7,5 s, przy `waiting search` | 1 min 44 s razem |
| 2 | 2 | 4 | 4 | 12 | 5,0 s, przy `waiting search` | |

- Wynik: 15 pobranych FLAC-ów (13 × 44,1 kHz/16 bit, 1 × 44,1/24, 1 × 48/24), 5 `not_found` w obu przebiegach (`NoSearchResults`, w tym oba utwory `KRØMA`, jak w poprzednim sprawdzeniu), 1 `failed`. Długości plików różnią się od Spotify o najwyżej 2,3 s.
- Limit wyszukiwań między procesami (D-070) zadziałał: każdy kolejny proces w tym samym oknie 220 s dostał tylko resztę limitu (29, 28, 12), a łącznie w około 3 minutach poszło 26 wyszukiwań.
- **Pobranie bez transferu:** jedyny `failed` (jeden utwór z jednym kandydatem) zaczął się pobierać i przez 30 s nie przyszedł od użytkownika ani jeden bajt. Sockseek przerwał go linią `Download attempt became stale after 30000ms without peer transfer activity` i zgłosił `AllDownloadsFailed`. Log nie mówi, czy plik czekał w kolejce u tego użytkownika, ale to ten sam mechanizm `--max-stale-time` co przy kolejce. W tym czasie inne pobrania wypisywały postęp, więc cisza na wyjściu nie przekroczyła 7,5 s. Nawet bez innych pobrań taka cisza kończy się po około 30 s, więc `idle_timeout_minutes = 10` zostaje.
- Hasło pojawiło się 0 razy w 10 plikach logów (`run-*.log`, `sockseek-*.log`, `sockseek-*.csv`). Nazwa użytkownika jest w logach Sockseeka (24 razy), tak jak opisuje D-055.
- Pliki od uploaderów miały już osadzone okładki JPEG, więc krok `cover` niczego nie zmienił. Krok `tag` w trybie `fill` uzupełnił puste pola i wpisał gatunek kolekcji `Hardcore Techno` do wszystkich 15 plików, a pola uploadera (np. `TITLE` z `feat.`, `TRACKNUMBER` `1/1`) zostawił.

**Litery spoza ASCII w nazwie artysty (2026-10-08, 2 wyszukiwania, bez pobierania).** Oba utwory `KRØMA` z kolekcji testowej skończyły jako `NoSearchResults`, a log pokazuje `0 results found: KRØMA - Rebound`, czyli serwer nie zwrócił nic. Sprawdzenie skryptem `scripts/live_check_soulseek.py search` (`--print results-full`, warunki dopasowania jak w aplikacji, bez `--format`, żeby było widać każdy format):

| Zapytanie | Wyniki |
|---|---|
| `KROMA - Rebound` (339 s) | 6, wszystkie MP3 320 kbps (`KROMA - Rebound (Original Mix).mp3` w folderach kilku użytkowników), każdy spełnia warunki dopasowania |
| `KROMA - Afterglow - Original Mix` (360 s) | 0 |

Wniosek: Soulseek nie utożsamia `Ø` z `O`, a ludzie podpisują pliki tego artysty jako `KROMA`. To samo może dotyczyć każdej nazwy z literami spoza ASCII. Akurat dla tych dwóch utworów zapis przez `O` nie dałby dziś pliku bezstratnego. Hasło 0 razy w wyjściu i logu. Flagi Sockseeka 3.0.5 związane z tym przypadkiem (`--help all`): `--regex` (zamiana tekstu w tytułach i artystach przed wyszukiwaniem, dla całego przebiegu), `--no-remove-special-chars` (zostawia znaki specjalne, czyli odwrotny kierunek), `--artist-maybe-wrong` (dodatkowe wyszukiwanie bez artysty) i `--desperate`. Żadna nie robi transliteracji sama. Decyzja właściciela (2026-10-08, D-087): dodatkowe wyszukiwanie z tekstem przepisanym na ASCII trafia do roadmapy (PLAN.md, sekcja 11, punkt 11), MVP go nie wdraża.

**Zasady serwera a konta generowane automatycznie.** Dokumentacja protokołu Nicotine+ ([SLSKPROTOCOL.md](https://github.com/nicotine-plus/nicotine-plus/blob/master/doc/SLSKPROTOCOL.md), komunikat Login) mówi: „It is unacceptable to use randomly generated usernames, as such automated scripting is disallowed by the official server rules”. Zasady serwera ([slsknet.org/news/node/681](https://www.slsknet.org/news/node/681)) zabraniają łączenia się automatycznym klientom i skryptom, które nie implementują pełnego zestawu funkcji Soulseeka, i dopuszczają najwyżej 5 klientów z jednego IP. Ograniczenia nazwy według tej samej dokumentacji (szczegóły odrzucenia `INVALIDUSERNAME`): nazwa niepusta, najwyżej 30 znaków, tylko drukowalne ASCII, bez spacji na początku i na końcu. Hasło: jedyne opisane ograniczenie to niepuste (`EMPTYPASSWORD`), a dokumentacja dodaje, że hasła nie da się odzyskać, więc dane logowania trzeba pamiętać lokalnie. O generowanym haśle ani zasady serwera, ani dokumentacja nic nie mówią. Pozostałe kody odrzucenia: `INVALIDVERSION`, `SVRFULL`, `SVRPRIVATE` (serwer nie przyjmuje rejestracji). `BANNED` „No longer sent, server omits login response instead”: zbanowane konto nie dostaje odpowiedzi i wygląda jak brak połączenia, co łapie limit czasu startu adaptera. Sprawdzenie zakładania konta zrobiliśmy jednorazowo, na jednym koncie próbnym, za zgodą właściciela. Decyzje o koncie w aplikacji: D-052 do D-056.

## 12. Decyzje właściciela po fazie 0 (2026-10-07)

Wszystkie są wpisane do PLAN.md.

1. **Formaty zastępcze to `wav`, `aiff` i `aif`.** ALAC i `m4a` wypadają z MVP w całości: polityka formatu, `verify`, `cover`, kolumna `format` i testy. ALAC w `.m4a` jako osobny przebieg z warunkiem bitrate trafia do roadmapy (PLAN.md sekcja 11, punkt 4).
2. **Polityka formatu jest dwuprzebiegowa.** Przebieg 1: tylko `flac`. Przebieg 2 (`wav,aiff,aif`): tylko przy `on_missing_flac = lossless`. Utwór, którego FLAC został znaleziony, ale pobieranie się nie udało, zostaje `failed` i w kolejnych uruchomieniach dalej próbuje FLAC-a. Do przebiegu 2 trafia dopiero po wyczerpaniu limitu prób. Kto trafia do przebiegu 2, określa punkt 9.
3. **Limit wyszukiwań:** uruchomienie z `-v` i parsowanie linii `SongJob: rate limited:` ze stderr, z testem kontraktu na przypiętej wersji. Linia niepasująca do wzorca daje zwykły stan wyszukiwania, bez wyjątku. `-v` sprawdzone pod kątem hasła (sekcja 10), a hasło i tak jest wycinane z logów.
4. **Dwa limity czasu w adapterze.** Start: brak pierwszego zdarzenia JSON kończy się błędem „nie mogę połączyć się z Soulseekiem”. Bezczynność: limit dłuższy niż okno limitu wyszukiwań (220 s), żeby nie ubijać procesu, który tylko czeka.
5. **Mapowanie przez `{row}` i `track_list` zostaje** w wersji z sekcji 7. `{row}` liczy nagłówek.
6. **Sekcja 12 planu ma statusy.** Punkty Spotify czekają na fazę 1. Punkty wymagające połączenia z Soulseekiem (błędny login, zakładanie konta, zapora, hasło w `-v`, cisza przy kolejce u innego użytkownika) czekają na jednorazowe ręczne sprawdzenie na początku fazy 2. Punkty rekordboxa czekają na koniec fazy 2.
7. **Limit prób:** ustawienie `max_attempts` w `settings.toml`, domyślnie 3. `attempts` liczy porażki w bieżącym etapie (punkt 9). Przycisk ponowienia w raporcie ustawia `stage = flac`, `attempts = 0`, `status = pending`, więc utwór zaczyna znowu od FLAC-a.
8. **Limit bezczynności:** ustawienie `idle_timeout_minutes`, domyślnie 10 minut. Po ubiciu procesu przez którykolwiek limit czasu utwory pobrane wcześniej zostają `done`, a te w toku wracają do `pending`, jak przy anulowaniu.
9. **Etap pobierania `downloads.stage`** (`flac` | `fallback`, domyślnie `flac`) zamiast wcześniej rozważanego `fallback_tried`. `attempts` liczy porażki w bieżącym etapie.
   - Przebieg 1 bierze z etapu `flac` utwory `pending` albo `failed` z `attempts < max_attempts`.
   - Przy `lossless`, na początku przebiegu 2, każdy utwór z kolekcji uruchomienia spełniający warunek „czeka na przebieg zastępczy” (`stage = flac`, `not_found` albo `failed` z wyczerpanymi próbami) dostaje `stage = fallback`, `status = pending`, `attempts = 0`.
   - Przebieg 2 bierze z etapu `fallback` utwory `pending` albo `failed` z `attempts < max_attempts`. Jego `not_found` jest ostateczny, a `failed` po wyczerpaniu prób.
   - Anulowanie, limit czasu, awaria i start aplikacji cofają utwory w toku do `pending` bez zmiany `stage` i `attempts`.
   - Przy `on_missing_flac = not_found` przebieg 2 się nie odpala, a niepobrane utwory z etapu `fallback` wracają do `flac` i `not_found`.
   - Ponowienie z raportu ustawia `stage = flac`, `attempts = 0`, `status = pending`.
   - Jedna funkcja-warunek „czeka na przebieg zastępczy” służy i do przejścia do etapu `fallback`, i do oznaczenia w tabeli oraz raporcie, także dla `failed` z wyczerpanymi próbami.

   Faza 2 testuje przerwanie w przebiegu 1 i w przebiegu 2, zmianę ustawienia w obie strony, nieudane pobranie WAV-a, które udaje się przy kolejnym uruchomieniu bez ponownego szukania FLAC-a, oraz odzysk po awarii.

Wnioski techniczne dopisane do planu przy okazji:
- forma `--user=<u> --pass=<p>` (sekcja 10),
- rezygnacja z `--pref-format`,
- osobny `--log-file` dla każdego przebiegu,
- `run.csv` bez pustego `Title` i bez kolumn `uri`/`id`/`url`,
- rekursywne usuwanie `.sockseek-staging` przy anulowaniu,
- postęp per utwór przez heurystykę `jobId` i `totalBytes == size`.

Kolejne drobne decyzje implementacyjne są w `docs/decisions.md`.
