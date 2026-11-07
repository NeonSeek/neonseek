# NeonSeek - plan implementacji (dla Claude Code)

Nazwa robocza: **NeonSeek**. Desktopowa aplikacja na Windows, która bierze listy utworów z kilku źródeł (konto Spotify, wklejony URL Spotify, plik CSV), szuka tych utworów w sieci Soulseek i pobiera je w jakości bezstratnej (FLAC w pierwszej kolejności) do lokalnej biblioteki pod rekordboxa. Jest przeznaczona do rozdania innym osobom, z instalatorem. Stan wiedzy o zewnętrznych API: 7 października 2026.

## 0. Zasady pracy (przeczytaj najpierw)

- Idź fazami z sekcji 10, po kolei. Nie zaczynaj kolejnej fazy, dopóki kryteria akceptacji poprzedniej nie przechodzą.
- Wszystko oznaczone **[WERYFIKUJ]** to rzecz, której nie wolno zgadywać. Sprawdź ją na przypiętej wersji narzędzia (`sockseek --help`, realna odpowiedź API) i zapisz wynik w `docs/`.
- Logika (core) nie importuje Qt. GUI jest cienką warstwą na core. Każda funkcja dostępna z GUI musi dać się odpalić też z CLI.
- Testy nigdy nie łączą się z prawdziwym Soulseekiem ani Spotify. Soulseek testujemy przez `--mock-files-dir`, Spotify przez zapisane fixture'y JSON.
- Nie podnoś limitów wyszukiwania Sockseeka ponad domyślne. Serwer Soulseek banuje na 30 minut za zbyt wiele wyszukiwań.
- Sekrety (hasło Soulseek, refresh token Spotify) tylko w Windows Credential Manager przez `keyring`. Nigdy w repo, w logach ani w plikach konfiguracyjnych.
- Komentarze w kodzie: po angielsku, proste, małymi literami.
- Ikony interfejsu jako SVG, bo są przemalowywane w kodzie pod aktywny motyw. Ikona exe jako `.ico`. Okładki utworów to zwykłe pliki graficzne w formacie, w jakim przyszły ze źródła.
- Aplikacja ma być gotowa do rozdania innym osobom. Żadnych ścieżek, kont ani identyfikatorów właściciela w kodzie i w paczce, cała konfiguracja przez UI, dane każdego użytkownika w jego profilu Windows.
- Każdy użytkownik podaje własne konto Soulseek i własny Spotify Client ID. Nie wbudowuj wspólnego Client ID.
- Nazwę konta Soulseek zawsze wpisuje użytkownik. Aplikacja nigdy nie generuje ani nie podpowiada nazw kont (zasady serwera i dokumentacja protokołu uznają losowe nazwy za niedopuszczalne). Przy zakładaniu nowego konta hasło generuje aplikacja i zapisuje je w Windows Credential Manager.
- Interfejs po angielsku i po polsku (decyzja właściciela, D-163). Każdy tekst okna, komunikat błędu, podpowiedź, kreator i tekst CLI idzie przez `tr()` (liczba mnoga przez `ntr()`), a tłumaczenia są danymi w core, bez Qt (`src/neonseek/locales/`). Polski ma brzmieć naturalnie, nie jak tłumaczenie słowo w słowo. Nazwy własne i techniczne zostają bez tłumaczenia (Soulseek, Spotify, rekordbox, FLAC, Reload Tag). Test pilnuje, że każdy tekst ma tłumaczenie z tymi samymi polami do podstawienia.
- Instrukcja obsługi (decyzja właściciela, D-164) ma jedno źródło na język: `docs/manual/en.md` i `docs/manual/pl.md`. Zmiana funkcji, którą instrukcja opisuje, zmienia obie wersje w tym samym kroku.
- Komunikaty błędów mają mówić, co zrobić dalej. Użytkownik nie jest programistą i nie ma terminala.
- Pliki w bibliotece (decyzja właściciela, `docs/decisions.md` D-127): aplikacja nigdy nie usuwa plików sama. Usuwa wyłącznie na wyraźne polecenie użytkownika, po potwierdzeniu z liczbą plików, zawsze do Kosza Windows, nigdy trwale, i rusza tylko pliki, które sama umieściła w bibliotece. Gdy biblioteka leży na dysku bez Kosza (pendrive, dysk sieciowy), odmawia z czytelnym komunikatem zamiast usuwać trwale. Jedyny wyjątek (decyzja właściciela, D-153): przy przenoszeniu biblioteki na inny dysk plik źródłowy jest usuwany trwale, ale dopiero gdy kopia jest sprawdzona (ten sam rozmiar co oryginał), a okno pytające o przeniesienie mówi to użytkownikowi. Usuwanie i przenoszenie plików biblioteki nie działa w trakcie pobierania. Szczegóły w sekcji 8, „Usuwanie i przenoszenie plików”.
- NeonSeek to otwarty kod na licencji GPL-3.0-or-later (decyzja właściciela, D-203), rozdawany za darmo z odnośnikiem do dobrowolnych wpłat. Każda nowa zależność jest zgłaszana właścicielowi razem z licencją, która musi być zgodna z GPL-3.0-or-later, i wpisywana do `THIRD_PARTY_LICENSES`. Sockseek (AGPL-3.0) zostaje osobnym programem uruchamianym jako podproces i dołączonym do paczki z tekstem licencji i wskazaniem źródeł (sekcja 3).

## 1. Cel i zakres

### MVP (fazy 0-4)

1. Trzy źródła list utworów, wszystkie zapisywane do lokalnej bazy SQLite jako kolekcje:
   - konto Spotify: logowanie, playlisty użytkownika plus Polubione, z pełnymi metadanymi,
   - wklejony URL Spotify: playlista, album albo pojedynczy utwór,
   - plik CSV: import bez udziału Spotify.

   Aplikacja musi działać także bez skonfigurowanego Spotify, na samym CSV.
2. Pobranie brakujących utworów z Soulseeka przez Sockseek, tylko w jakości bezstratnej. FLAC ma pierwszeństwo, a na wypadek jego braku jest wybieralne ustawienie: inny format bezstratny (WAV albo AIFF) albo `not_found`. Format stratny nigdy. ALAC jest poza MVP (sekcja 11).
3. Jedna biblioteka plików (jeden plik na utwór, bez duplikatów między kolekcjami) plus plik `.m3u8` na każdą kolekcję.
4. Sync przyrostowy: kolejne uruchomienie dociąga tylko nowe utwory.
5. Raport utworów nieznalezionych i nieudanych, z ponowieniem, które od razu pobiera wskazane utwory.
6. GUI z sześcioma motywami kolorów (domyślny cyberpunk/hacker), miniaturami okładek i wyszukiwarką utworów.
7. Okładka osadzana w pobranym pliku, żeby była widoczna także w rekordboxie.
8. Tagi zapisywane w pobranych plikach tak, żeby czytał je rekordbox: pola ze źródła (tytuł, artyści, album, artysta albumu, data wydania, numer utworu i dysku, ISRC) oraz gatunek i komentarz, które użytkownik ustawia dla kolekcji (sekcja 8, krok `tag`).
9. Kreator pierwszego uruchomienia i instalator dla Windows, tak żeby aplikację postawił ktoś inny niż właściciel.

### Poza MVP (sekcja 11, nie implementuj teraz, ale nie zamykaj drogi)

- Pobieranie metadanych z internetu (np. po ISRC) i uzupełnianie nimi pól, których źródło nie podało. Pola ze źródła i tagi kolekcji zapisuje już MVP.
- Porządkowanie nazewnictwa i struktury folderów według szablonów.
- Konwersja i ujednolicanie formatu (wybieralny format docelowy).
- Okno z propozycją innych wersji utworu (extended, remiksy) do dobrania.

## 2. Ustalone decyzje

| Temat | Decyzja |
|---|---|
| Silnik Soulseek | Sockseek (dawniej sldl / slsk-batchdl), binarka dołączona do aplikacji, sterowana jako podproces |
| Spotify | Własny cienki klient HTTP, także dla wklejonych URL-i. Do Sockseeka trafia wygenerowany CSV, nie URL |
| Format | Tylko bezstratne. FLAC ma pierwszeństwo. Gdy FLAC-a nie ma, decyduje ustawienie użytkownika: inny format bezstratny (AIFF przed WAV-em), co jest domyślne, albo `not_found`. Sockseek uruchamiany w dwóch przebiegach: najpierw tylko FLAC, potem formaty zastępcze (sekcja 7). Format stratny nigdy nie trafia do biblioteki. ALAC (`.m4a`) poza MVP |
| Dopasowanie wersji | Dokładnie ta wersja co w źródle, po długości utworu. Bez polowania na extended mixy. Okno z propozycją innych wersji jest w roadmapie |
| Źródła | Trzy: konto Spotify (playlisty własne i współtworzone plus Polubione), wklejony URL Spotify (playlista, album, utwór), plik CSV. Spotify jest opcjonalne, aplikacja działa na samym CSV |
| Cudze playlisty Spotify | Zależy od wieku aplikacji deweloperskiej (sekcja 3). Nowa aplikacja (od lutego 2026, czyli każdy nowy użytkownik NeonSeek) według dokumentacji nie dostaje ich zawartości, ani z konta, ani z URL. Starsza dostaje. Czytelność kod zawsze ustala po odpowiedzi API, a nieczytelne playlisty są widoczne jako zablokowane. Playlisty Spotify, prywatne i usunięte są nieczytelne w obu wariantach. Obejście: kopia do własnej playlisty albo CSV |
| Identyfikator utworu | Własny, wewnętrzny. ID Spotify i ISRC to pola opcjonalne |
| Układ plików | Jeden plik na utwór w bibliotece plus `.m3u8` per kolekcja |
| Tryb | Automat. Niepewne i nieznalezione lądują w raporcie |
| Udostępnianie | Brak. Sockseek nie udostępnia folderów |
| Konto Soulseek | Każdy użytkownik ma własne. Zalecane osobne konto tylko dla aplikacji. Nazwę zawsze wpisuje użytkownik, aplikacja nazw nie generuje ani nie podpowiada. Dwie drogi: nowe konto (użytkownik wpisuje tylko nazwę, hasło generuje aplikacja i zapisuje w Credential Manager, pierwsze logowanie zakłada konto) albo własne istniejące konto (nazwa i hasło wpisane ręcznie). Obie z testem połączenia |
| Dystrybucja | Aplikacja do rozdania innym. Każdy podaje własny Spotify Client ID albo używa samego CSV. Żadnych danych właściciela w paczce |
| GUI | PySide6 (Qt 6). Motywy jako dane, sześć wbudowanych, przełączane bez restartu |
| Okładki | Miniatury w UI z pamięci podręcznej, okładka osadzana w pobranym pliku |
| Tagi | Krok `tag` po `cover` zapisuje pola ze źródła (ustawienie `tags_from_source`: `overwrite`, co jest domyślne, `fill` albo `off`) oraz gatunek i komentarz kolekcji, tak żeby czytał je rekordbox. FLAC: Vorbis comments. AIFF: ID3. WAV: RIFF INFO (jedyne tagi WAV, które czyta rekordbox) plus ID3, a rekordbox nie pokazuje w WAV-ie okładki ani numeru utworu. Nigdy ID3 we FLAC-u (sekcja 8) |
| Język interfejsu | Angielski i polski (D-163), ustawienie `language`. Przy pierwszym uruchomieniu polski, gdy Windows jest po polsku, w innym razie angielski. Zmiana w Ustawieniach i w pierwszym kroku kreatora, bez restartu. Wszystkie teksty przez `tr()`, tłumaczenia jako dane w core |
| Instrukcja obsługi | W aplikacji (przycisk `HELP`, w języku interfejsu, ze spisem treści) i jako PDF, po jednym na język, z jednego źródła Markdown na język (D-164). PDF i podgląd w oknie przez Qt, bez nowych zależności |
| Licencja | Otwarty kod na GPL-3.0-or-later (D-203), publikacja na GitHubie, za darmo, z odnośnikiem do dobrowolnych wpłat. Pełny tekst w `LICENSE`, licencje części w `THIRD_PARTY_LICENSES` |
| Pakowanie | PyInstaller onedir plus instalator Inno Setup, instalacja per użytkownik bez uprawnień administratora |
| Źródło prawdy o stanie | Nasza baza SQLite, nie indeks Sockseeka |

## 3. Ograniczenia zewnętrzne

### Spotify Web API (Development Mode, po zmianach z lutego 2026)

Zachowanie API zależy od wieku aplikacji deweloperskiej. Spotify odroczył zmiany z lutego 2026 dla aplikacji założonych wcześniej. Starsza aplikacja (tak jest u właściciela projektu) czyta cudze playlisty użytkowników i nadal dostaje stare pola oraz endpointy. Nowa aplikacja, a taką zakłada każdy inny użytkownik NeonSeek, podlega ograniczeniom opisanym niżej. Dlatego kod nie zakłada żadnego z wariantów: czytelność playlisty ustala po odpowiedzi API (`docs/decisions.md`, D-027) i nie używa żadnego elementu wymienionego niżej jako usunięty. Wyniki dla obu wariantów są w `docs/spotify-notes.md`.

- Aplikacja deweloperska wymaga aktywnego Premium na koncie jej właściciela. Każdy użytkownik NeonSeek zakłada własną aplikację deweloperską, więc każdy, kto chce źródeł Spotify, potrzebuje Premium. Bez Premium zostaje CSV.
- W Development Mode jedna aplikacja deweloperska obsługuje najwyżej 5 użytkowników. Dlatego NeonSeek nie ma wbudowanego, wspólnego Client ID.
- Nowa aplikacja: zawartość playlisty (`items`) jest zwracana tylko dla playlist, których użytkownik jest właścicielem lub współtwórcą. Dla pozostałych przychodzą same metadane, a pola `items` nie ma w odpowiedzi. Sprawdzone na nowej aplikacji: `GET /playlists/{id}` cudzej playlisty daje 200 bez pola `items`, a `GET /playlists/{id}/items` daje 403 `Forbidden`. Starsza aplikacja dostaje także zawartość cudzych playlist użytkowników. Playlisty Spotify, prywatne i usunięte dają w obu wariantach 404, tak samo jak nieistniejące id.
- Endpointy: `GET /playlists/{id}/tracks` usunięty, zastępuje go `GET /playlists/{id}/items`. Batchowe `GET /tracks?ids=` usunięte. Sprawdzone: nowa aplikacja dostaje na obu 403 `Forbidden`. Starsza nadal dostaje oba, ale kod ich nie używa.
- Pola przemianowane: `tracks` -> `items`, `tracks.tracks` -> `items.items`, `tracks.tracks.track` -> `items.items.item`. Starsze tutoriale i biblioteki mogą używać starych nazw. Sprawdzone: nowa aplikacja dostaje w obiektach playlist tylko `items`, a we wpisach tylko `item`. Starsza dostaje stare i nowe nazwy jednocześnie. W obu wariantach wpisy `GET /me/tracks` nadal mają `track`, a stronicowanie utworów w obiekcie albumu nadal nazywa się `tracks`. Kod czyta nowe nazwy, a do starej sięga tylko tam, gdzie nowej nie ma (D-031).
- `external_ids` (ISRC) na obiekcie utworu jest dostępne (usunięcie cofnięto w marcu 2026). Usunięte z utworu: `available_markets`, `linked_from`, `popularity`. Sprawdzone: w nowej aplikacji nie ma ich ani `preview_url`, a w albumie nie ma `label` i `popularity`. Starsza aplikacja nadal dostaje `available_markets` i `popularity`. Kod nie używa żadnego z tych pól.
- Dostępne i potrzebne nam: `GET /me`, `GET /me/playlists`, `GET /playlists/{id}`, `GET /playlists/{id}/items`, `GET /me/tracks`, `GET /albums/{id}`, `GET /albums/{id}/tracks`, `GET /tracks/{id}`.
- Każde wywołanie wymaga tokenu. Używamy wyłącznie tokenu użytkownika (PKCE), więc wklejony URL Spotify działa dopiero po zalogowaniu.
- Ograniczenie zawartości playlist dotyczy też wklejonych URL-i: w nowej aplikacji publiczna, ale cudza playlista nie zwróci utworów.
- BPM i tonacja (audio features) są niedostępne dla nowych aplikacji. Nie planuj ich z tego źródła.
- Redirect URI: `localhost` jest niedozwolony. Musi być jawny adres loopback, np. `http://127.0.0.1:PORT/callback`.
- Przy przekroczeniu limitu przychodzi `429`, może mieć `reason: QUOTA_EXCEEDED`. Obsłuż `Retry-After`.

### Sockseek

- Repozytorium: https://github.com/fiso64/sockseek (licencja AGPL-3.0). Projekt niedawno zmienił nazwę i miał łamiące zmiany w wersji 3.0, więc wersja jest przypięta: **3.0.5** (`scripts/sockseek-pin.json`, pobieranie przez `scripts/fetch_sockseek.ps1`). Flagi i zachowanie sprawdzone na niej w fazie 0, wyniki z próbkami wyjścia w `docs/sockseek-notes.md`. Zmiana wersji wymaga powtórzenia spike'a (`scripts/spike_sockseek.py`).
- Dołączenie binarki do instalatora to jej dalsze rozprowadzanie na AGPL-3.0: paczka musi zawierać tekst licencji i wskazanie źródeł dokładnie tej wersji. NeonSeek tylko uruchamia Sockseeka jako osobny proces i nie zawiera jego kodu.
- Wejście CSV: wystarczą kolumny o typowych nazwach (`Artist`, `Title`, `Album`, `Length`). Długość domyślnie w sekundach.
- `--format` to warunek twardy, `--pref-format` wpływa tylko na ranking i niczego nie odrzuca. Listy formatów są nieuporządkowane: `--format flac,wav` samo nie daje pierwszeństwa FLAC-owi.
- `--format` porównuje wyłącznie rozszerzenie pliku, bez znajomości kodeka, a wartości nie są walidowane (nieznana wartość niczego nie dopuszcza i nie daje błędu).
- Domyślnie akceptowane formaty to `mp3,flac,ogg,m4a,opus,wav,aac,alac`. AIFF na tej liście nie ma, ale `--format aiff,aif` działa: `aiff` łapie tylko `.aiff`, `aif` tylko `.aif`. `alac` łapie tylko pliki z rozszerzeniem `.alac`, a ALAC w praktyce leży w `.m4a`. `m4a` łapie ALAC i AAC bez rozróżnienia.
- Domyślna tolerancja długości to 3 sekundy (`--length-tol`).
- `--strict-title` i `--strict-artist` wymagają tytułu w nazwie pliku i artysty w ścieżce. Domyślne ustawienia faworyzują znalezienie czegokolwiek kosztem trafności.
- `--progress-json` wypisuje zdarzenia postępu jako linie JSON na stdout. To jest nasz kanał komunikacji. Schemat sprawdzony w fazie 0 (`docs/sockseek-notes.md`, sekcja 5): `track_list`, `search_start`, `download_start`, `download_progress` (tylko z `jobId`) i `track_state`. Na stdout trafiają też zwykłe linie tekstowe. Zdarzenia nie niosą numeru wiersza CSV ani stanu oczekiwania na limit wyszukiwań.
- `--mock-files-dir <dir>` zastępuje sieć Soulseek lokalnym katalogiem plików. Są też `--mock-files-slow` i `--mock-files-fail-downloads <n>`. Na tym opieramy testy.
- Limiter wyszukiwań: domyślnie 34 wyszukiwania na 220 sekund. Przy 500 utworach samo wyszukiwanie trwa około godziny. UI musi pokazywać stan oczekiwania na limit. W `--progress-json` tego stanu nie ma, a podczas czekania Sockseek nic nie wypisuje. Stan czytamy ze stderr przy `-v` (sekcja 7).
- Pobierane pliki mają tymczasowe rozszerzenie `.incomplete` i leżą w `<output-dir>\.sockseek-staging\<guid>\` pod oryginalną nazwą zdalną. Po normalnym zakończeniu ten katalog znika.
- Istnieje tryb demona (`sockseek daemon`, HTTP/SignalR na `127.0.0.1:5030`, opis w `docs/api.md` repozytorium). Dokumentacja sama określa to API jako eksperymentalne, z możliwymi łamiącymi zmianami. W MVP go nie używamy.
- Sockseek ma własną obsługę Spotify. Nie używamy jej.
- Sockseek przyjmuje też URL-e YouTube, Bandcamp i MusicBrainz. Nie używamy tego: YouTube czytamy przez yt-dlp (faza 4b, D-231), Bandcamp i MusicBrainz to źródła poza MVP (sekcja 11).

## 4. Architektura

```
+-----------------------------------------------------------+
|  ui/ (PySide6)          cli.py (te same use case'y)       |
+---------------------------+-------------------------------+
                            |
+---------------------------v-------------------------------+
|  pipeline/runner.py   orkiestracja jednego przebiegu      |
+------+--------------------+--------------------+----------+
       |                    |                    |
+------v------+    +--------v--------+   +-------v---------+
| sources/    |    | engine/         |   | postprocess/    |
| spotify acct|    | base (Protocol) |   | verify, cover,  |
| spotify url |    | sockseek adapter|   | tag, place      |
| csv file    |    |                 |   | (później: rename|
|             |    |                 |   | i convert)      |
+------+------+    +--------+--------+   +-------+---------+
       |                    |                    |
+------v------+             |                    |
| spotify/    |             |                    |
| auth, client|             |                    |
+------+------+             |                    |
       |                    |                    |
+------v--------------------v--------------------v----------+
|  db/ (SQLite)   config.py   secrets (keyring)             |
+-----------------------------------------------------------+
```

### Stos

Python 3.12, `uv` do zależności, PySide6, httpx, keyring, mutagen, platformdirs, pytest, ruff, PyInstaller, Inno Setup 6 (instalator). SQLite przez standardowe `sqlite3`, bez ORM.

### Struktura repo

```
neonseek/
  pyproject.toml
  PLAN.md
  CLAUDE.md                  # krótkie zasady z sekcji 0 + komendy dev
  README.md                  # opis po angielsku, link do README.pl.md
  README.pl.md               # opis po polsku
  LICENSE                    # pełny tekst GPL-3.0
  THIRD_PARTY_LICENSES       # licencje zależności, czcionki i Sockseeka
  licenses/                  # teksty licencji, których nie ma żaden pakiet (LGPL-3.0, biblioteki Pythona)
  docs/
    sockseek-notes.md        # wyniki fazy 0: flagi, schemat progress-json
    spotify-notes.md         # realne kształty odpowiedzi
    manual/                  # instrukcja: en.md, pl.md, images/<język>/*.jpg (faza 3c)
  src/neonseek/
    __main__.py
    cli.py
    project.py               # adres kodu źródłowego (REPO_URL), jedyne miejsce (D-214)
    config.py                # ścieżki, ustawienia (plik toml w %APPDATA%)
    secrets.py               # keyring wrapper
    i18n.py                  # tr(), ntr(), język, polska liczba mnoga
    locales/                 # tłumaczenia jako dane (pl.json), faza 3c
    manual.py                # instrukcja: pliki, spis treści, bez qt
    db/
      schema.sql
      repo.py
    spotify/
      auth.py                # pkce + lokalny serwer callback
      client.py              # httpx, paginacja, 429
      models.py
      urls.py                # parsowanie url i uri spotify
    sources/
      base.py                # Source protocol, typy kolekcji i utworów
      ingest.py              # zapis do db + deduplikacja utworów
      spotify_account.py     # playlisty konta + polubione
      spotify_url.py         # playlista / album / utwór z url
      csv_file.py            # import csv, mapowanie kolumn
    engine/
      base.py                # DownloadEngine protocol + typy zdarzeń
      sockseek.py            # adapter podprocesu
    pipeline/
      runner.py
      csv_export.py
      postprocess/
        base.py              # interfejs kroku
        verify.py
        cover.py             # osadzanie okładki w pliku
        tag.py               # tagi ze źródła i kolekcji: vorbis, id3, riff info
        riff_info.py         # zapis bloku LIST/INFO w wav
        place.py
      playlists.py           # generowanie m3u8
    covers.py                # pobieranie i pamięć podręczna okładek
    ui/
      app.py
      main_window.py
      workers.py             # QThread wrappery na core
      onboarding.py          # kreator pierwszego uruchomienia
      widgets/
      theme/
        loader.py            # wczytanie motywu, render qss, barwienie ikon
        themes/              # neon, daylight, mono, phosphor, ice, noir (.toml)
        style.qss.tmpl
        fonts/
        icons/               # svg
        logo/                # logo aplikacji: pełne i uproszczone (svg)
  vendor/sockseek/           # binarka, poza gitem
  scripts/
    fetch_sockseek.ps1       # pobiera przypiętą wersję
    build.ps1                # czysty build: testy, pdf-y, pyinstaller, licencje, instalator, zip
    collect_licenses.py      # LICENSE.txt, THIRD_PARTY_LICENSES.txt z pełnymi tekstami, README.txt paczki
    exe_version.py           # zasób wersji exe z pyproject.toml
    build_icon.py            # installer/neonseek.ico z logo (svg)
    unpack_build.py          # wypakowanie archiwów z exe do skanu
  installer/
    neonseek.spec            # pyinstaller onedir: NeonSeek.exe i neonseek-cli.exe
    neonseek.iss             # skrypt inno setup
    neonseek.ico
  tests/
    fixtures/
```

### Dane aplikacji

- `%APPDATA%\NeonSeek\`: `neonseek.db`, `settings.toml`, `logs/`, `covers/` (pamięć podręczna okładek), `themes/` (motywy użytkownika). Wszystko osobno dla każdego użytkownika Windows.
- Biblioteka muzyki: katalog wskazany przez użytkownika, np. `D:\Music\Library\`.
  - `<biblioteka>\tracks\` - pliki audio, płasko.
  - `<biblioteka>\playlists\` - pliki `.m3u8`.
  - `<biblioteka>\.staging\` - katalog roboczy Sockseeka, czyszczony po przebiegu.

## 5. Model danych (SQLite)

Kolekcja to dowolna lista utworów: playlista, album, Polubione, import CSV albo wbudowana lista pojedynczych utworów. Utwór ma własne id, bo nie każdy pochodzi ze Spotify.

```sql
collections(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,           -- spotify_playlist | spotify_liked | spotify_album | singles | csv
  origin TEXT NOT NULL,         -- account | url | file | builtin
  external_id TEXT,             -- spotify id playlisty lub albumu, null dla csv i singles
  source_ref TEXT,              -- wklejony url albo ścieżka pliku csv
  name TEXT, owner_id TEXT, is_readable INTEGER DEFAULT 1,
  snapshot_id TEXT,             -- snapshot_id ze spotify albo sha256 treści csv
  import_map_json TEXT,         -- mapowanie kolumn csv, do ponownego importu
  image_url TEXT, track_count INTEGER,
  selected INTEGER DEFAULT 0,   -- zaznaczenie przez użytkownika, każda kolekcja startuje z 0 (sekcja 8)
  genre TEXT, comment TEXT,     -- tagi użytkownika (sekcja 8, krok tag), null = nic nie jest wpisywane
  last_synced_at TEXT,
  UNIQUE (kind, external_id)
)
tracks(
  id INTEGER PRIMARY KEY,       -- własne id, używane wszędzie dalej
  spotify_id TEXT UNIQUE,       -- null dla utworów spoza spotify
  isrc TEXT,
  title TEXT NOT NULL, artists_json TEXT, album TEXT,
  album_artists_json TEXT, release_date TEXT,
  duration_ms INTEGER,          -- null, gdy źródło nie podało długości
  track_number INTEGER, disc_number INTEGER, cover_url TEXT, explicit INTEGER,
  match_key TEXT                -- znormalizowane "pierwszy artysta|tytuł"
)
collection_tracks(collection_id INTEGER, track_id INTEGER, position INTEGER, added_at TEXT,
  PRIMARY KEY (collection_id, position))
downloads(
  track_id INTEGER PRIMARY KEY,
  status TEXT,                  -- pending | searching | downloading | done | not_found | failed | excluded
  stage TEXT DEFAULT 'flac',    -- flac | fallback: etap polityki formatu (sekcja 7)
  excluded_from TEXT,           -- status sprzed wykluczenia: pending | not_found | failed (wersja 3)
  file_path TEXT,               -- ścieżka bezwzględna pliku w bibliotece
  format TEXT,                  -- flac | wav | aiff (plik .aif to też aiff)
  sample_rate INTEGER, bit_depth INTEGER, duration_ms INTEGER,
  length_verified INTEGER,      -- 0, gdy źródło nie podało długości do porównania
  source_user TEXT, source_path TEXT,   -- użytkownik i ścieżka w soulseek
  attempts INTEGER DEFAULT 0,   -- porażki (failed) w bieżącym etapie, zerowane przy zmianie etapu i ponowieniu
  last_error TEXT, updated_at TEXT
)
runs(id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, stats_json TEXT)
searches(at REAL NOT NULL)      -- czas każdego wyszukiwania Sockseeka, limit między procesami (sekcja 7)
file_moves(                     -- dziennik przenoszenia biblioteki (sekcja 8), wersja 3
  track_id INTEGER PRIMARY KEY, from_path TEXT NOT NULL, to_path TEXT NOT NULL)
```

Zasady:

- Utwór należący do kilku kolekcji ma jeden wiersz w `tracks` i jeden w `downloads`.
- Zapisujemy wszystkie metadane, jakie daje źródło, nawet jeśli MVP ich nie używa. Krok `tag` (sekcja 8) zapisuje z nich do plików pola podstawowe, reszta czeka na roadmapę.
- Gatunek i komentarz kolekcji (`genre`, `comment`) ustawia wyłącznie użytkownik. Import, sync, dodanie linku i ponowny import ich nie ustawiają ani nie zmieniają, a aplikacja niczego tu nie zgaduje, także nie bierze nazwy playlisty. Kolumny dochodzą migracją schematu do wersji 2.
- Ze Spotify pomijamy elementy, które nie są utworami (podcasty) oraz pliki lokalne (`is_local`).
- `snapshot_id` pozwala pominąć ponowne przetwarzanie niezmienionego źródła.
- Jedna playlista Spotify to jeden wiersz w `collections`, niezależnie od tego, czy przyszła z konta, czy z URL.
- Usunięcie kolekcji usuwa jej wiersz i powiązania w `collection_tracks`. Utwory i wiersze `downloads` zostają. Pliki też zostają, chyba że użytkownik zaznaczy w potwierdzeniu „also move downloaded files to the Recycle Bin” (domyślnie wyłączone, sekcja 8, „Usuwanie i przenoszenie plików”). Aplikacja nigdy nie usuwa plików sama (sekcja 0).
- **Wykluczenie** (decyzja właściciela, D-126): status `excluded` oznacza utwór, którego użytkownik nie chce pobierać. Nie wybiera go żaden przebieg (sekcja 7), ani `DOWNLOAD`, ani `DOWNLOAD CHECKED`. Wykluczyć można tylko utwór bez pliku (`pending`, `not_found`, `failed`). Status sprzed wykluczenia trafia do `excluded_from`, a `stage`, `attempts` i `last_error` zostają, więc przywrócenie („Include again”) wraca dokładnie do niego i niczego nie szuka drugi raz (zasada 1). Utwór `done` nie jest wykluczany wprost: służy do tego „Delete file”, które przenosi plik do Kosza, czyści kolumny pliku i ustawia `excluded` z `excluded_from = pending`, `stage = flac`, `attempts = 0`.
- `file_moves` to dziennik przenoszenia plików przy zmianie folderu biblioteki. Wiersz powstaje przed ruszeniem pliku i znika w tej samej transakcji, w której `downloads.file_path` dostaje nową ścieżkę. Wiersz zostawiony przez przerwane przenoszenie jest uzgadniany z dyskiem przy następnym starcie (sekcja 8).
- Kolumna `excluded_from` i tabela `file_moves` dochodzą migracją schematu do wersji 3.
- Schemat ma numer wersji w `PRAGMA user_version`. Każda zmiana schematu to migracja w przód uruchamiana przy starcie, bo użytkownicy będą aktualizować aplikację na istniejących danych.

### Deduplikacja utworów

Jedna funkcja w `sources/ingest.py`, używana przez wszystkie źródła. Dla każdego przychodzącego utworu szukaj istniejącego w tej kolejności:

1. To samo `spotify_id`.
2. Ten sam ISRC i długości różne o najwyżej 3 sekundy (albo jedna nieznana).
3. Ten sam `match_key` i długości różne o najwyżej 3 sekundy (albo jedna nieznana). Ta reguła działa tylko wtedy, gdy przynajmniej jeden z dwóch utworów nie ma ani `spotify_id`, ani ISRC. Dwa utwory ze Spotify o różnych identyfikatorach nie są sklejane po samej nazwie.
4. Brak trafienia: nowy utwór.

Przy trafieniu uzupełnij puste pola istniejącego wiersza danymi z nowego. Nie nadpisuj pól już wypełnionych i nie ruszaj `downloads`.

`match_key`: `casefold`, usunięty fragment `feat.` / `ft.` z resztą nawiasu, interpunkcja zamieniona na spacje, wielokrotne spacje zwinięte. Nie usuwaj oznaczeń wersji (`Extended Mix`, `Remix`, `Live`), bo to różne nagrania.

## 6. Źródła - szczegóły

Każde źródło zwraca to samo: kolekcję z uporządkowaną listą rekordów utworów. Źródła nie piszą do bazy same, zapis idzie przez `sources/ingest.py`. Dodanie kolejnego rodzaju źródła nie może wymagać zmian w pipeline ani w UI poza dialogiem dodawania.

### 6.1 Spotify: logowanie

- Authorization Code z PKCE, bez client secret.
- Redirect URI: `http://127.0.0.1:43821/callback`. Lokalny serwer HTTP wstaje tylko na czas logowania, otwieramy domyślną przeglądarkę.
- Scope'y: `playlist-read-private`, `playlist-read-collaborative`, `user-library-read`.
- Refresh token w keyring. Access token tylko w pamięci, odświeżany automatycznie.
- Client ID podaje użytkownik w kreatorze albo w ustawieniach (zapis w `settings.toml`, to nie jest sekret). W kodzie i w paczce nie ma żadnego domyślnego Client ID.
- Logowanie jest opcjonalne. Bez niego działa tylko źródło CSV, a funkcje Spotify proszą o zalogowanie zamiast zgłaszać błąd.

### 6.2 Spotify: konto

1. `GET /me` po id użytkownika.
2. `GET /me/playlists` z paginacją po polu `next`.
3. Czytelność playlisty wynika z odpowiedzi na żądanie treści, a nie z `owner.id` ani z obecności pola `items` na liście (D-027, `docs/spotify-notes.md` sekcja 3). Własne i współtworzone (`owner.id == me.id` albo `collaborative == true`) są czytelne bez dodatkowego zapytania. Cudza playlista widziana pierwszy raz dostaje jedno zapytanie `GET /playlists/{id}/items?limit=1`, a odpowiedź 403 albo 404 przy treści oznacza playlistę zablokowaną.
4. Dla zaznaczonych, czytelnych playlist ze zmienionym `snapshot_id`: `GET /playlists/{id}/items` z paginacją. Sprawdzone maksimum `limit`: 100 dla `/playlists/{id}/items`, 50 dla `/me/tracks`, `/me/playlists` i `/albums/{id}/tracks`. Wartość o jeden większa daje 400 `Invalid limit`.
5. Polubione: `GET /me/tracks` z paginacją, zapisywane jako kolekcja `spotify_liked`.
6. Parser pisz pod nowe nazwy pól (`item` zamiast `track`). Zapisz realne odpowiedzi jako fixture'y i przetestuj parser na nich.
7. Usunięcie utworu z playlisty na Spotify usuwa powiązanie w `collection_tracks`, ale nie kasuje pliku ani wiersza `downloads`.

### 6.3 Spotify: wklejony URL

- Przyjmowane formy: `https://open.spotify.com/{playlist|album|track}/{id}`, także z segmentem języka (`/intl-pl/`) i parametrami (`?si=...`), oraz URI `spotify:{typ}:{id}`. Parsowanie w `spotify/urls.py`, z testami.
- Inne typy (artysta, podcast) i krótkie linki `spotify.link` nie są obsługiwane w MVP. Mają dawać czytelny komunikat, nie wyjątek.
- Wymaga zalogowania (token użytkownika z 6.1).
- **Playlista:** `GET /playlists/{id}`. Jest pole `items`: kolekcja `spotify_playlist`, dalej jak w 6.2. Jeśli ta playlista już istnieje w bazie, nie twórz drugiej kolekcji i nie zmieniaj jej zaznaczenia. Nie ma pola `items`: nie twórz kolekcji, pokaż komunikat, że Spotify nie udostępnia zawartości cudzych playlist, z podpowiedzią kopii do własnej playlisty albo importu CSV.
- **Album:** `GET /albums/{id}` plus `GET /albums/{id}/tracks` z paginacją, kolekcja `spotify_album`. Obiekty utworów w albumie są uproszczone, więc dane albumu bierz z obiektu albumu. Uproszczone utwory albumu nie mają `external_ids` (sprawdzone, `docs/spotify-notes.md` sekcja 3), więc ISRC zawsze idzie przez `GET /tracks/{id}`, pojedynczo i po kolei (endpoint zbiorczy usunięto), z obsługą `429`. Album się nie zmienia, więc po udanym pobraniu nie odświeżaj go przy sync.
- **Utwór:** `GET /tracks/{id}`, dopisany na koniec wbudowanej kolekcji `singles` ("Single tracks"), bez powtórzeń.
- Dialog i CLI przyjmują wiele URL-i naraz. Każdy przetwarzaj osobno, błąd jednego nie przerywa pozostałych.

### 6.4 Plik CSV

Import nie wymaga Spotify ani sieci.

- **Odczyt:** kodowanie `utf-8-sig`, przy błędzie dekodowania `cp1250`. Separator wykrywany spośród przecinka, średnika i tabulatora (`csv.Sniffer`). Pierwszy wiersz to nagłówek.
- **Mapowanie kolumn** jest automatyczne, po nazwach nagłówków, bez rozróżniania wielkości liter:

| Pole | Rozpoznawane nagłówki |
|---|---|
| tytuł (wymagany) | `title`, `track name`, `track`, `name`, `song`, `tytuł` |
| artysta | `artist`, `artists`, `artist name`, `artist name(s)`, `wykonawca` |
| album | `album`, `album name` |
| długość | `length`, `duration`, `time`, `duration (ms)`, `track duration (ms)`, `duration_ms` |
| ISRC | `isrc` |
| id Spotify | `track uri`, `spotify uri`, `uri`, `track id`, `spotify id` |

- Lista aliasów to jedna stała w kodzie, łatwa do rozszerzenia. Gdy nie da się rozpoznać kolumny tytułu, wymagane jest mapowanie ręczne (dialog w GUI, `--map` w CLI).
- **Długość:** jednostka ustalana dla całej kolumny, nie dla wiersza. Wartości z dwukropkiem to `m:ss` albo `h:mm:ss`. Wartości liczbowe to milisekundy, gdy nagłówek zawiera `ms` albo mediana kolumny przekracza 20000, w przeciwnym razie sekundy. Jednostkę da się nadpisać ręcznie.
- **Artyści:** komórkę z kilkoma artystami dziel po średniku, a gdy go nie ma, po przecinku ze spacją. Do wyszukiwania idzie pierwszy. To heurystyka (nazwa z przecinkiem zostanie przecięta), dlatego podgląd importu pokazuje wynik.
- **Id Spotify:** przyjmij samo id, URI albo URL utworu. W MVP służy tylko do deduplikacji.
- Wiersze bez tytułu pomijaj i zliczaj. Liczba pominiętych trafia do podsumowania importu.
- **Kolekcja:** `kind = csv`, nazwa domyślnie z nazwy pliku, `source_ref` to ścieżka, `snapshot_id` to sha256 treści, mapowanie w `import_map_json`.
- **Ponowny import:** wskazanie pliku o ścieżce istniejącej kolekcji aktualizuje tę kolekcję, zamiast tworzyć drugą. Ta sama treść: nic się nie dzieje. Zmieniona treść: lista utworów kolekcji zastąpiona nową, a deduplikacja zachowuje to, co już pobrane. Brak pliku pod zapisaną ścieżką: kolekcja zostaje, akcja prosi o wskazanie pliku.

## 7. Silnik Sockseek - szczegóły

### Interfejs (engine/base.py)

```python
class DownloadEngine(Protocol):
    def run(self, items: list[EngineItem], on_event: Callable[[EngineEvent], None]) -> RunResult: ...
    def cancel(self) -> None: ...
```

Zdarzenia: `ItemSearching`, `ItemDownloading(percent, user, remote_path)`, `ItemDone(local_path)`, `ItemNotFound`, `ItemFailed(reason)`, `RateLimitWait`, `LogLine`, `RunFinished`. Każde niesie nasz `track_id`. Dzięki temu silnik da się kiedyś wymienić (np. na tryb demona) bez ruszania reszty.

### Adapter (engine/sockseek.py)

Wywołanie potwierdzone na Sockseeku 3.0.5 w fazie 0 (`docs/sockseek-notes.md`). Jedno wywołanie to jeden przebieg z polityki formatu niżej:

```
sockseek.exe <run.csv>
  --user=<u> --pass=<p>
  --no-config
  --output-dir <biblioteka>\.staging
  --format <lista przebiegu: flac albo wav,aiff,aif>
  --length-tol 3
  --strict-title --strict-artist
  --remove-ft
  --no-write-index --no-skip-existing
  --name-format "{row}"
  --progress-json
  -v
  --log-file <logs>\sockseek-<run_id>-<przebieg>.log
  --no-listen
```

Uwagi:

- `--no-config`, żeby zachowanie nie zależało od ewentualnego `sockseek.conf` użytkownika (sprawdzone: bez tej flagi Sockseek czyta config leżący obok exe).
- `--no-listen` zawsze (decyzja właściciela po ręcznych sprawdzeniach fazy 2, `docs/sockseek-notes.md` sekcja 11.1). Domyślny port nasłuchu 49998 leży w dynamicznym zakresie portów Windows i bywa zajęty przez inne programy, a wtedy Sockseek kończy się od razu błędem `Failed to start listening`. Bez nasłuchu zapora Windows o nic nie pyta, a pobieranie działa (3 z 4 utworów pobrane w sprawdzeniu). Za domowym routerem bez przekierowania portu nasłuch i tak nie przyjmuje połączeń, bo Sockseek nie ma UPnP. Opcjonalny port nasłuchu jest w roadmapie (sekcja 11).
- `--user=<u>` i `--pass=<p>` zawsze w formie ze znakiem `=`. W formie ze spacją hasło zaczynające się od `-` kończy się błędem użycia (kod 2), a komunikat błędu zawiera hasło albo jego fragment. Forma z `=` przyjmuje takie hasła.
- `--pref-format` dostaje tylko przebieg 2: `--pref-format aiff,aif`, żeby AIFF miał pierwszeństwo przed WAV-em (polityka formatu niżej, D-082). Przebieg 1 dopuszcza wyłącznie FLAC, więc nie ma w nim czego preferować.
- `-v` służy wyłącznie do odczytu stanu limitu wyszukiwań ze stderr. Nie zmienia zawartości stdout.
- Stan trzymamy w naszej bazie, więc indeks Sockseeka jest wyłączony, a do `run.csv` trafiają tylko utwory do pobrania. `run.csv` to nasz plik roboczy dla silnika, nie mylić z plikami CSV importowanymi przez użytkownika (sekcja 6.4).
- Kolumny `run.csv`: `Artist` to pierwszy artysta utworu, `Title` bez zmian, `Album`, `Length` w sekundach. UTF-8, bez pustych wierszy i bez znaków nowej linii w polach. `Title` nigdy nie jest pusty, bo wiersz z samym albumem Sockseek traktuje jako pobieranie całego albumu. Żadnej kolumny o nazwie `uri`, `id` ani `url`, bo Sockseek czyta ją jako URI utworu.
- Utwór bez znanej długości (import CSV bez kolumny długości) dostaje puste `Length`. Sockseek nie filtruje wtedy po długości (sprawdzone), więc ryzyko złej wersji jest większe. Pusty `Artist` wyłącza `--strict-artist` (sprawdzone).
- Mapowanie wyniku na `track_id`:
  - `{row}` w `--name-format` **liczy nagłówek**: pierwszy wiersz danych ma numer `2`. Liczone są rekordy CSV, nie linie fizyczne, dlatego `run.csv` nie może mieć pustych wierszy ani wieloliniowych pól. Wtedy `row = pozycja w run.csv + 2`.
  - Sukces: numer wiersza z nazwy pliku w `track_state.downloadPath`. Jest jednoznaczny także przy dwóch utworach o tym samym artyście i tytule.
  - Porażka (`track_state` bez `downloadPath`): dopasowanie `(artist, title)` do tabeli `index -> (artist, title)` ze zdarzenia `track_list` (`index = row - 2`). Wartości są już po przetworzeniu przez Sockseeka, np. bez `feat.` po `--remove-ft`. Identyczne pary rozstrzyga eliminacja: sukcesy są już przypisane przez `{row}`, a kilka identycznych porażek dostaje ten sam wynik.
  - Postęp: `download_progress` niesie tylko `jobId`. Przypisujemy go do aktywnego `download_start` o `size == totalBytes`. Przy dwóch pobraniach o identycznym rozmiarze procent może trafić do złego wiersza. To błąd wyłącznie kosmetyczny.
  - Pozycja bez `track_state` po zakończeniu procesu wraca do `pending`.
- Wynik z `track_state`: `Succeeded` przechodzi do postprocessingu (sekcja 8). `Failed` z `failureReason` równym `NoSearchResults` albo `NoMatchingResults` daje `not_found`. Każdy inny `Failed` (np. `AllDownloadsFailed`) daje `failed`. `Cancelled` wraca do `pending`. Kod wyjścia `1` oznacza także zwykły przebieg z nieznalezionymi utworami, więc stan bierzemy ze zdarzeń, a nie z kodu wyjścia.
- stdout zawiera też zwykłe linie tekstowe (podgląd listy utworów). Linie, które nie są obiektem JSON, idą do `LogLine`. Oba strumienie są w UTF-8.
- Stan oczekiwania na limit wyszukiwań (`RateLimitWait`): linia na stderr pasująca do wzorca `SongJob: rate limited: <artysta> - <tytuł>` (pojawia się tylko przy `-v`). Linii wznowienia nie ma, więc stan kończy się przy następnym `search_start`. Jeśli linia nie pasuje do wzorca, UI pokazuje zwykły stan wyszukiwania i nic się nie wywraca. Wzorca pilnuje test kontraktu na przypiętej wersji (faza 2).
- Hasło idzie argumentem przy starcie procesu. Nie loguj linii poleceń. Wytnij hasło z każdego logu, który zapisujesz (stdout, stderr i plik `--log-file`), także wtedy, gdy Sockseek go nie wypisuje. W fazie 0 `-v` i `-vv` w trybie mock nie wypisały hasła ani loginu, a źródła 3.0.5 nie logują hasła. Ręczne sprawdzenie prawdziwego logowania na początku fazy 2 (`docs/sockseek-notes.md`, sekcja 11.1): hasło pojawiło się 0 razy w stdout, stderr i `--log-file`, także przy udanym logowaniu, a login pojawia się na stderr przy `-v` w trzech liniach.
- Dwa limity czasu w adapterze:
  - **Start:** jeśli w zadanym czasie nie przyjdzie pierwsze zdarzenie JSON, adapter kończy proces i zgłasza błąd „nie mogę połączyć się z Soulseekiem”. Komunikat (po angielsku, przez `tr()`) podpowiada, co sprawdzić: internet, program zabezpieczający blokujący połączenia oraz możliwy ban konta. Zbanowane konto nie dostaje od serwera żadnej odpowiedzi na logowanie (dokumentacja protokołu Nicotine+: `BANNED` nie jest już wysyłane, serwer pomija odpowiedź), więc wygląda jak brak połączenia. Powód: przy błędzie sieci Sockseek się nie kończy, tylko ponawia logowanie w tle i nic nie wypisuje na stdout, a `track_list` pojawia się dopiero po zalogowaniu. Limit musi być dłuższy niż `--connect-timeout` Sockseeka (domyślnie 20 s).
  - **Bezczynność:** brak jakiejkolwiek linii na stdout i stderr przez czas z ustawienia `idle_timeout_minutes` w `settings.toml` (domyślnie 10 minut) kończy proces tak jak anulowanie, a przebieg kończy się błędem. Limit musi być dłuższy niż okno limitu wyszukiwań (220 s), bo podczas czekania na limit Sockseek nic nie wypisuje, a takiego procesu nie wolno ubijać. Domyślna wartość może się zmienić po ręcznym sprawdzeniu ciszy przy kolejce u innego użytkownika (faza 2).
  - Po ubiciu procesu przez którykolwiek limit czasu utwory pobrane wcześniej zostają `done`, a te w toku wracają do `pending` z niezmienionym `stage` i `attempts`, tak jak przy anulowaniu.
- Podproces uruchamiaj bez okna konsoli (`CREATE_NO_WINDOW`). Czytaj stdout i stderr linia po linii w wątkach roboczych.
- Anulowanie: zakończ proces, utwory w toku wróć do `pending` (z niezmienionym `stage` i `attempts`), usuń rekursywnie `.sockseek-staging` ze stagingu. Pliki `.incomplete` mają tam oryginalne nazwy zdalne, nie `{row}`.
- Jednocześnie działa najwyżej jeden proces Sockseeka (jedno logowanie na konto Soulseek). Przebiegi 1 i 2 idą po kolei.
- Limit wyszukiwań obowiązuje także między procesami (D-070). Każdy proces liczy tylko swoje wyszukiwania, więc przebieg 2 albo kolejne uruchomienie tuż po przebiegu dostałoby świeże 34. Runner zapisuje czas każdego `search_start` w tabeli `searches` i przed startem procesu: bez wyszukiwań w ostatnich 220 s startuje z domyślnymi limitami, gdy przebieg mieści się w reszcie okna, obniża `--searches-per-time` do tej reszty, a w przeciwnym razie, także gdy w oknie nie zostało ani jedno wyszukiwanie, czeka, aż okno będzie puste. Proces nigdy nie startuje z limitem 0 (D-086).

### Polityka formatu

Ustawienie `on_missing_flac` w `settings.toml`, dostępne w UI. Dwie wartości:

| Wartość | Zachowanie | Przebiegi Sockseeka |
|---|---|---|
| `lossless` (domyślna) | Jest FLAC: bierz FLAC. Nie ma: bierz AIFF albo WAV, AIFF w pierwszej kolejności | 1: `--format flac`, 2: `--format wav,aiff,aif --pref-format aiff,aif` |
| `not_found` | Jest FLAC: bierz FLAC. Nie ma: status `not_found` | tylko 1: `--format flac` |

Zasady:

- Obowiązuje wariant dwuprzebiegowy. Faza 0 pokazała, że jedno wywołanie z `--format flac,wav,... --pref-format flac` nie gwarantuje FLAC-a. Ranking Sockseeka stawia dopasowanie tytułu, albumu i artysty w ścieżce przed formatem, więc WAV z folderu nazwanego jak album wygrywa z FLAC-iem z innego folderu. Do tego po nieudanym pobraniu FLAC-a Sockseek od razu bierze następnego kandydata, także WAV (`docs/sockseek-notes.md`, sekcja 8).
- **AIFF przed WAV-em w przebiegu 2** (decyzja właściciela, D-082): rekordbox pokazuje w AIFF okładkę i komplet tagów, a w WAV-ie nie ma okładki ani numeru utworu (sekcja 8). `--pref-format aiff,aif` tylko porządkuje wyniki: AIFF wygrywa z WAV-em z tego samego folderu, ale WAV z folderu nazwanego jak album nadal wygrywa z AIFF z innego folderu, bo dopasowanie nazwy idzie przed formatem (sprawdzone na mocku, `docs/sockseek-notes.md` sekcja 8).
- **Etapy.** Każdy wiersz `downloads` ma `stage`: `flac` (szukamy tylko FLAC-a, przebieg 1) albo `fallback` (szukamy formatów zastępczych, przebieg 2). Nowy utwór zaczyna w etapie `flac`. `attempts` liczy porażki (`failed`) w bieżącym etapie.
- **Limit prób:** ustawienie `max_attempts` w `settings.toml`, dostępne w UI, domyślnie 3.
- **Przebieg 1** (`--format flac`): utwory z kolekcji uruchomienia (sekcja 8) w etapie `flac`, w statusie `pending` albo `failed` z `attempts < max_attempts`. Wyniki: `done`, `not_found` albo `failed` z `attempts + 1`. Utwór, którego FLAC został znaleziony, ale pobieranie się nie udało, zostaje w etapie `flac` i w kolejnych uruchomieniach dalej próbuje FLAC-a, aż wyczerpie próby.
- **Warunek „czeka na przebieg zastępczy”** to jedna funkcja w core: `on_missing_flac = lossless`, `stage = flac` oraz `status = not_found` albo `status = failed` z `attempts >= max_attempts`. Ta sama funkcja wybiera utwory do przejścia do etapu `fallback` i oznacza je w tabeli oraz raporcie (sekcja 9). Warunek nie zależy od zaznaczenia kolekcji. Filtr kolekcji uruchomienia dochodzi dopiero przy wyborze utworów do przebiegu (`docs/decisions.md`, D-005).
- **Przejście do etapu `fallback`:** tylko przy `on_missing_flac = lossless`, na początku przebiegu 2, na podstawie bazy. Każdy utwór z kolekcji uruchomienia spełniający warunek „czeka na przebieg zastępczy” dostaje `stage = fallback`, `status = pending`, `attempts = 0`. Nie ma znaczenia, w którym uruchomieniu dostał wcześniejszy status, więc przejście obejmuje też wyniki przebiegu 1 z tego samego uruchomienia.
- **Przebieg 2** (`--format wav,aiff,aif`, tylko przy `lossless`): utwory z kolekcji uruchomienia w etapie `fallback`, w statusie `pending` albo `failed` z `attempts < max_attempts`. Wyniki: `done`, `not_found` (ostateczny) albo `failed` z `attempts + 1` (ostateczny po wyczerpaniu prób). Utwór z etapu `fallback` nie jest już szukany jako FLAC. Nieudane pobranie WAV-a wraca przy kolejnym uruchomieniu prosto do przebiegu 2. Przebieg kosztuje dodatkowe wyszukiwanie wyłącznie dla utworów bez FLAC-a.
- **Anulowanie, limit czasu i awaria** w dowolnym przebiegu: utwory w toku (`searching`, `downloading`) wracają do `pending`, a `stage` i `attempts` zostają bez zmian. Przy starcie aplikacji tak samo traktujemy utwory, które zostały w stanie `searching` albo `downloading`. Dzięki temu przerwane uruchomienie nie wymaga osobnej obsługi. `not_found` z przebiegu 1 przechodzi do etapu `fallback` przy następnym uruchomieniu bez ponownego szukania FLAC-a, a przerwany przebieg 2 jest kontynuowany w etapie `fallback`.
- **`on_missing_flac = not_found`:** przebieg 2 się nie odpala. Na początku uruchomienia niepobrane utwory z etapu `fallback` (status inny niż `done`) wracają do `stage = flac`, `status = not_found`, `attempts = 0` (D-001). Zmiana ustawienia z powrotem na `lossless` znowu kwalifikuje je do etapu `fallback`.
- **Start uruchomienia:** uruchomienie startuje, gdy jest coś do przebiegu 1 albo, przy `lossless`, jest choć jeden utwór czekający na przebieg zastępczy lub do pobrania w etapie `fallback`. Pusty przebieg jest pomijany.
- **Ponowienie z raportu:** `stage = flac`, `attempts = 0`, `status = pending`. Utwór zaczyna znowu od FLAC-a. W oknie raportu ponowienie od razu pobiera wskazane utwory (sekcja 8, „Pobieranie wskazanych utworów”).
- **Wykluczone** (`excluded`, sekcja 5) nie trafiają do żadnego przebiegu, nie przechodzą do etapu `fallback` i nie zmienia ich cofnięcie przy `on_missing_flac = not_found`.
- `--format` porównuje tylko rozszerzenie pliku, dlatego AIFF ma na liście dwie wartości (`aiff` i `aif`).
- Formaty stratne (`mp3`, `aac`, `ogg`, `opus`) nigdy nie trafiają na listę. Nie ma ustawienia, które by je dopuszczało.
- ALAC i `m4a` są poza MVP w całości (polityka formatu, `verify`, `cover`, kolumna `format`, testy). `--format alac` nie łapie ALAC-a w `.m4a`, a `m4a` wpuszcza też AAC, którego Sockseek nie odróżnia od ALAC-a. Plan na później jest w sekcji 11.
- Ostateczną kontrolę robi krok `verify` na pobranym pliku, po faktycznym kodeku, nie po rozszerzeniu (sekcja 8). Chroni to np. przed plikiem stratnym z rozszerzeniem `.flac` albo `.wav`.
- Biblioteka może więc zawierać pliki FLAC, WAV i AIFF. Sprowadzenie ich do jednego formatu to zadanie konwersji z sekcji 11.

## 8. Pipeline jednego przebiegu

### Wybór kolekcji

Pobieranie obejmuje wyłącznie kolekcje, które użytkownik sam wskazał. Zasady:

- Każda kolekcja startuje odznaczona, bez wyjątków: z konta, z URL, z CSV i wbudowana `singles`. Import, dodanie linku, sync i ponowny import nie zmieniają zaznaczenia.
- **Kolekcje uruchomienia** to zestaw podany przy starcie: jedna lub kilka kolekcji wskazanych jawnie albo wszystkie zaznaczone. Sekcja 7 i kroki niżej działają tylko na nich.
- Nie ma polecenia ani przycisku „pobierz wszystko” ani „zaznacz wszystko”.
- **CLI:** `neonseek download` wymaga `--collection ID` (można podać kilka razy) albo `--selected`. Wywołanie bez żadnego z nich niczego nie pobiera, tylko wypisuje listę kolekcji (id, nazwa, liczniki, zaznaczenie) z podpowiedzią i kończy się kodem błędu. `--selected` przy braku zaznaczonych kolekcji też kończy się takim komunikatem.
- **GUI:** przycisk `DOWNLOAD` w nagłówku otwartej kolekcji pobiera tylko ją, niezależnie od checkboxów. Przycisk globalny `DOWNLOAD CHECKED` pobiera tylko zaznaczone i jest nieaktywny, gdy nic nie jest zaznaczone.
- **Potwierdzenie przed każdym startem:** lista kolekcji uruchomienia, liczba utworów do pobrania w przebiegu 1 (wybór z sekcji 7, bez utworów `done`) oraz szacowany czas samego wyszukiwania liczony z limitu Sockseeka: `ceil(N / 34) × 220 s` dla N utworów. To górne oszacowanie przebiegu 1. Nie obejmuje czasu pobierania ani wyszukiwań przebiegu 2, których liczby nie da się znać z góry, i tak ma być opisane w komunikacie. W GUI to okno dialogowe z przyciskami start i anuluj. W CLI to samo podsumowanie z pytaniem `[y/N]`, które pomija flaga `--yes`. Bez `--yes` i bez interaktywnego terminala (np. wejście z potoku) CLI niczego nie pobiera. Gdy `on_missing_flac = not_found`, podsumowanie nie wspomina o przebiegu WAV/AIFF, bo go nie będzie.
- **Wykluczone utwory** (`excluded`, sekcja 5) nie są pobierane ani przez `DOWNLOAD`, ani przez `DOWNLOAD CHECKED`, ani przez `neonseek download --collection` / `--selected`.

### Pobieranie wskazanych utworów

Decyzje właściciela D-125 i D-131. Zamiast kolekcji uruchomienie może dostać listę utworów:

- **GUI:** „Download this track only” w menu utworu (tabela i okno raportu, także na kilku zaznaczonych) oraz `RETRY SELECTED` w oknie raportu. Użytkownik nie musi szukać kolekcji, w której jest utwór. Gdy trwa inne pobieranie, oba są nieaktywne z podpowiedzią dlaczego.
- **CLI:** `neonseek download --track ID [--track ID ...] [--limit N] [--yes]` oraz `neonseek retry TRACK_ID ... [--collection ID] --download [--yes]`. Samo `retry` bez `--download` tylko ustawia utwory do ponowienia przy następnym pobraniu, tak jak dotąd.
- To samo potwierdzenie przed startem (lista wskazanych utworów, liczba utworów do wyszukania, szacowany czas) i ten sam limit wyszukiwań co przy kolekcjach. Odmowa niczego nie zmienia.
- Przygotowanie po potwierdzeniu, przed przebiegiem 1: `not_found` i `failed` dostają to samo co ponowienie z raportu (`stage = flac`, `attempts = 0`, `status = pending`). `excluded` wraca do statusu z `excluded_from`, a jeśli to `not_found` albo `failed`, dalej jak przy ponowieniu. `pending` zostaje bez zmian, także w etapie `fallback`. `done` jest pomijany, a w menu „Download this track only” jest dla niego nieaktywne.
- Po przebiegach powstają na nowo `.m3u8` wszystkich kolekcji, do których należą pobrane utwory.

1. Przygotowanie: przy `on_missing_flac = not_found` cofnij niepobrane utwory z etapu `fallback` do `flac` i `not_found` (sekcja 7). Wyczyść staging, także po ewentualnej awarii poprzedniego uruchomienia.
2. Wybierz utwory do przebiegu 1 (sekcja 7). Uruchomienie startuje, gdy ta lista jest niepusta albo gdy przy `lossless` są utwory czekające na przebieg zastępczy lub do pobrania w etapie `fallback`.
3. Przebieg 1, jeśli ma utwory: wygeneruj `run.csv` (w `logs\`, obok logu Sockseeka tego przebiegu, D-069) i uruchom silnik z `--format flac`. Zdarzenia zapisuj do bazy na bieżąco i przekazuj do UI.
4. Przebieg 2, tylko przy `lossless`: przenieś do etapu `fallback` utwory czekające na przebieg zastępczy, wybierz utwory z etapu `fallback` (sekcja 7), wygeneruj nowy `run.csv` i uruchom silnik z `--format wav,aiff,aif`, jeśli lista nie jest pusta.
5. Dla każdego `ItemDone` w obu przebiegach uruchom kroki postprocessingu po kolei. Do końca kroku `place` utwór ma status `downloading`, a `done` dostaje dopiero po przeniesieniu do biblioteki (D-004):
   - `verify`: mutagen rozpoznaje faktyczny format i kodek pliku, niezależnie od rozszerzenia. Dozwolone są FLAC, WAV i AIFF. Każdy inny format, w tym kodek stratny, oznacza `failed`. Zapisz format, długość, sample rate i bit depth. Długość musi się zgadzać z długością ze źródła w tolerancji, niezgodność oznacza `failed` z powodem. Gdy źródło nie podało długości, pomiń porównanie, zapisz długość z pliku i ustaw `length_verified = 0`.
   - `cover`: gdy plik nie ma osadzonej okładki, a utwór ma `cover_url`, osadź okładkę z pamięci podręcznej (FLAC: blok PICTURE, WAV i AIFF: ramka ID3 APIC). Nie nadpisuj istniejącej. Błąd tego kroku nie unieważnia pobrania, trafia tylko do logu.
   - `tag`: zapisz tagi według zasad z podsekcji „Tagi” niżej. Błąd tego kroku nie unieważnia pobrania, trafia tylko do logu, tak jak przy `cover`.
   - `place`: przenieś do `<biblioteka>\tracks\{pierwszy artysta} - {tytuł}.{rozszerzenie formatu}`. Nazwa oczyszczona pod Windows (znaki niedozwolone, nazwy zastrzeżone, długość ścieżki). Kolizja z innym utworem: sufiks ` [<6 znaków track_id>]`.
6. Po przebiegach wygeneruj na nowo `.m3u8` dla każdej kolekcji uruchomienia: UTF-8, ścieżki względne, kolejność jak w źródle, tylko utwory `done`.
7. Zapisz statystyki uruchomienia w `runs`, wyczyść staging.

Postprocessing to lista kroków o wspólnym interfejsie (`process(track_file, ctx) -> track_file`). W MVP są cztery kroki: `verify`, `cover`, `tag`, `place`. Zmiana nazw i konwersja z sekcji 11 dojdą jako kolejne kroki tej samej listy, więc nie wpisuj logiki kroków na sztywno w runnerze.

### Tagi (krok `tag`)

MVP zapisuje tagi w pobranych plikach tak, żeby czytał je rekordbox (decyzja właściciela, `docs/decisions.md`). Według instrukcji rekordboxa 7 rekordbox czyta ID3 w AIFF (i MP3), RIFF INFO w WAV i Vorbis comments we FLAC-u, a ID3 w WAV-ie nie (sekcja „Adding a track”, uwaga o plikach WAVE).

- **Kontenery:** FLAC: Vorbis comments. AIFF: ID3, ten sam tag co okładka. WAV: RIFF INFO (blok `LIST`/`INFO`, czyta go rekordbox) plus ID3 (ten sam tag co okładka, dla innych programów). Nigdy ID3 we FLAC-u. ID3 zapisujemy w wersji 2.3.
- **Znane ograniczenie WAV w rekordboxie** (sprawdzone na koniec fazy 2, D-081): rekordbox nie pokazuje okładki WAV-a (ramka ID3 APIC w WAV-ie jest dla niego niewidoczna, a RIFF INFO nie mieści obrazu) ani numeru utworu (`ITRK` pokazuje jako 0). Pozostałe pola RIFF INFO, także z polskimi znakami, są widoczne. Dlatego przebieg 2 preferuje AIFF (sekcja 7), README opisuje to ograniczenie (faza 4), a docelowym rozwiązaniem jest konwersja z roadmapy (sekcja 11, punkt 3).
- **Pola podstawowe** z bazy (`tracks`): tytuł, artyści, album, artysta albumu, data wydania, numer utworu i dysku, ISRC. Ustawienie `tags_from_source` w `settings.toml`, dostępne w UI:
  - `overwrite` (domyślne, decyzja właściciela D-083): nadpisuje pola danymi ze źródła, bo tagi od uploaderów są niespójne (kilku artystów sklejonych spacją albo średnikiem, różna pisownia), a dane ze źródła są jednolite. Każde zapisane pole ma w pliku dokładnie jedną wartość pod jedną nazwą: powtórzone pola (np. kilka `ARTIST`) i inne pisownie tego samego pola (`ALBUM ARTIST`, `YEAR`) znikają. Pole, którego źródło nie podało, zostaje w pliku bez zmian,
  - `fill`: uzupełnia tylko pola, które w pliku są puste,
  - `off`: pól podstawowych nie zapisuje.
- **Tagi użytkownika per kolekcja:** gatunek i komentarz (`collections.genre`, `collections.comment`). Domyślnie puste, czyli nic nie jest wpisywane. Ustawione zawsze nadpisują to pole w pliku, niezależnie od `tags_from_source`. Aplikacja niczego tu nie zgaduje i sama nie bierze nazwy playlisty.
- **Utwór w kilku kolekcjach:** gatunek to unikalne gatunki jego kolekcji połączone `", "` w jednym polu, w kolejności kolekcji. Komentarz tak samo. Liczą się wszystkie kolekcje utworu, nie tylko kolekcje uruchomienia.
- **Zmiana tagów kolekcji** sama nie rusza plików, tylko podpowiada zapis tagów od nowa (retag). Retag zapisuje tagi od nowa w już pobranych plikach (`done`) wskazanych kolekcji, bez pobierania, według bieżących ustawień i tagów kolekcji. Komunikat po retag przypomina, że w rekordboxie trzeba przeładować tagi utworów (`Reload Tag`), bo rekordbox nie odświeża ich sam.

Nazwy pól:

| Pole | FLAC (Vorbis) | AIFF i WAV (ID3v2.3) | WAV (RIFF INFO) |
|---|---|---|---|
| tytuł | `TITLE` | `TIT2` | `INAM` |
| artyści (jeden tekst, `", "`) | `ARTIST` | `TPE1` | `IART` |
| album | `ALBUM` | `TALB` | `IPRD` |
| artysta albumu | `ALBUMARTIST` **[WERYFIKUJ]** | `TPE2` **[WERYFIKUJ]** | brak pola |
| data wydania | `DATE` | `TYER` (rok) | `ICRD` |
| numer utworu | `TRACKNUMBER` | `TRCK` | `ITRK` (rekordbox pokazuje 0, znane ograniczenie WAV) |
| numer dysku | `DISCNUMBER` | `TPOS` | brak pola |
| ISRC | `ISRC` | `TSRC` | brak pola (`ISRC` w RIFF INFO znaczy „source”) |
| gatunek | `GENRE` | `TCON` | `IGNR` |
| komentarz | `COMMENT` | `COMM` (język `eng`, pusty opis) | `ICMT` |

Teksty RIFF INFO zapisujemy w UTF-8 z bajtem zerowym na końcu. Ręczne sprawdzenie w rekordboxie na koniec fazy 2 (`docs/rekordbox-checks.md`) potwierdziło we wszystkich trzech formatach tytuł, artystę, album, rok, gatunek i komentarz z polskimi znakami, a we FLAC-u i AIFF także numer utworu i okładkę. `Reload Tag` po retag odświeża gatunek i komentarz we wszystkich trzech formatach. Niesprawdzona została tylko kolumna Album Artist (`ALBUMARTIST`, `TPE2`), stąd zostawione znaczniki **[WERYFIKUJ]**. Gdy rekordbox nie pokaże pola, zmieniamy nazwę pola w kodzie i wpisujemy wynik tutaj.

### Usuwanie i przenoszenie plików

Decyzje właściciela D-127 i D-128, zasada z sekcji 0. Wspólne dla wszystkiego niżej:

- **Tylko pliki aplikacji.** Ruszamy wyłącznie pliki, które aplikacja sama umieściła w bibliotece: plik utworu `done` z `downloads.file_path` leżący w `<biblioteka>\tracks\` oraz plik `.m3u8` kolekcji w `<biblioteka>\playlists\`. Plik spoza biblioteki, inne pliki w jej folderach i pliki użytkownika nigdy nie są ruszane.
- **Tylko do Kosza.** Usuwanie idzie wyłącznie do Kosza Windows (`SHFileOperationW` z `FOF_ALLOWUNDO`, a do tego `FOF_WANTNUKEWARNING`, żeby Windows zapytał, zamiast usunąć trwale, gdyby jednak nie mógł użyć Kosza). Przed usunięciem aplikacja sprawdza, czy dysk ma Kosz: dysk wymienny (pendrive), sieciowy, napęd `subst`, wyłączony Kosz (zasada `NoRecycleFiles` albo „usuwaj od razu” dla dysku) i plik większy niż Kosz dysku dają odmowę z czytelnym komunikatem, a nie trwałe usunięcie. Testy używają atrapy Kosza, nigdy prawdziwego.
- **Potwierdzenie z liczbą plików** przed każdym usunięciem, w GUI w oknie, w CLI pytaniem `[y/N]` albo flagą `--yes`. Bez `--yes` i bez interaktywnego terminala CLI niczego nie usuwa.
- **Nie w trakcie pobierania.** Usuwanie, wykluczanie, przywracanie i przenoszenie biblioteki biorą tę samą blokadę co pobieranie (`download.lock`, D-068). Gdy trwa pobieranie, kończą się komunikatem, a w GUI są nieaktywne z podpowiedzią.

**Delete file (utwór).** Menu utworu i `neonseek track delete-file TRACK_ID ... [--yes]`. Plik idzie do Kosza, utwór dostaje `excluded` (sekcja 5), a `.m3u8` wszystkich kolekcji, do których należy, powstają od razu na nowo, żeby nie wskazywały na brakujący plik. Plik, którego już nie ma na dysku, niczego nie blokuje: utwór i tak dostaje `excluded`, a playlisty są odświeżane. Utwór, którego plik zniknął z biblioteki, nigdy nie zostaje `done`: gdy Kosz nie potwierdzi wpisu, utwór i tak dostaje `excluded`, a komunikat każe sprawdzić Kosz (decyzja właściciela, D-159).

**Remove (kolekcja).** Okno potwierdzenia ma opcję „also move downloaded files to the Recycle Bin”, domyślnie wyłączoną. Obejmuje tylko pliki utworów, które nie należą do żadnej innej kolekcji, plus plik `.m3u8` tej kolekcji. Utwory, których plik poszedł do Kosza, wracają do `pending` w etapie `flac`, bo nie należą już do żadnej kolekcji, a ponowne dodanie listy ma je znowu pobrać. CLI: `neonseek remove ID ... --delete-files [--yes]`.

**Zmiana folderu biblioteki.** W Ustawieniach zmiana folderu pyta, czy przenieść pobrane pliki (`MOVE FILES`), czy tylko zmienić folder. CLI: `neonseek library move FOLDER [--yes]` przenosi, a `neonseek settings library_dir FOLDER` tylko zmienia ustawienie i podpowiada przeniesienie. Przenoszenie:

1. Blokada pobierania przez cały czas. Nowy folder nie może być tym samym folderem ani leżeć w `tracks`, `playlists` albo `.staging` obecnej biblioteki. Przy innym dysku sprawdzamy wolne miejsce przed startem. Gdy któraś ścieżka w nowym folderze przekroczyłaby 259 znaków, przenoszenie jest odrzucane z listą plików (decyzja właściciela, D-162).
2. Najpierw uzgodnienie dziennika `file_moves` po ewentualnym przerwaniu (niżej).
3. Plik po pliku, tylko pliki aplikacji (wyżej): wiersz dziennika, potem przeniesienie, potem w jednej transakcji nowe `file_path` i usunięcie wiersza dziennika. Na tym samym dysku to zmiana nazwy. Na innym kopia do pliku tymczasowego obok celu, zmiana jego nazwy na docelową, dopiero potem usunięcie źródła. Istniejący plik w celu nigdy nie jest nadpisywany: nazwa dostaje sufiks ` [<6 znaków track_id>]` jak w kroku `place`, a gdy i ten jest zajęty, plik zostaje na miejscu z komunikatem.
4. Gdy wszystkie pliki przeszły, ustawienie `library_dir` dostaje nowy folder, pliki `.m3u8` kolekcji przechodzą do nowego `playlists\` i powstają tam na nowo. Gdy coś się nie udało albo użytkownik przerwał, ustawienie zostaje stare, a `.m3u8` w starym folderze powstają na nowo, więc wskazują pliki tam, gdzie naprawdę są. Ponowne przeniesienie do tego samego folderu kończy pracę.
5. Uzgodnienie dziennika (przy starcie aplikacji i przed każdym przenoszeniem): jest tylko plik docelowy, więc `file_path` dostaje cel. Jest tylko źródło, więc zostaje źródło, a niedokończona kopia tymczasowa jest usuwana. Są oba o tym samym rozmiarze, więc kopia była pełna: `file_path` dostaje cel, a źródło jest usuwane. Pliki, których aplikacja nie umieściła, zostają w starym folderze, a komunikat to mówi.

rekordbox pamięta stare ścieżki plików, więc komunikat po przeniesieniu podpowiada ponowny import playlist albo „Relocate” w rekordboxie.

## 9. UI

### Layout

- **Lewy panel:** lista kolekcji w grupach `SPOTIFY` (Polubione, playlisty konta), `LINKS` (playlisty i albumy z URL, Single tracks) i `CSV`. Każda pozycja: miniatura okładki albo ikona rodzaju źródła, nazwa, licznik `pobrane/wszystkie`, checkbox zaznaczenia (wybór do `DOWNLOAD CHECKED` i do sync treści Spotify), zawsze odznaczony po dodaniu kolekcji. Playlisty nieczytelne wyszarzone, z ikoną kłódki i podpowiedzią "skopiuj do własnej playlisty w Spotify albo zaimportuj CSV". Czytelność wynika z odpowiedzi API, nie z właściciela playlisty. W nowej aplikacji deweloperskiej zablokowane są cudze playlisty użytkowników, a w obu wariantach playlisty Spotify, prywatne i usunięte (sekcja 3). Na górze panelu przycisk `+ ADD`.
- **Dodawanie źródeł:** `+ ADD` otwiera dialog z dwoma trybami: wklejenie URL-i Spotify (pole wieloliniowe, jeden URL na linię) oraz wybór pliku CSV. Skróty: `Ctrl+V` z URL-em Spotify w schowku otwiera dialog z wypełnionym polem, a upuszczenie pliku `.csv` na okno zaczyna import. Każdy URL dostaje własny wynik: dodano, już istnieje albo błąd z powodem.
- **Dialog importu CSV:** podgląd pierwszych 20 wierszy po zmapowaniu, wykryte mapowanie kolumn z możliwością zmiany, jednostka długości, nazwa kolekcji, liczba wierszy pominiętych.
- **Menu kontekstowe kolekcji:** usuń z aplikacji (pliki w bibliotece zostają, chyba że użytkownik zaznaczy w potwierdzeniu „also move downloaded files to the Recycle Bin”, domyślnie wyłączone, sekcja 8), importuj ponownie (CSV), otwórz źródło.
- **Góra panelu głównego:** okładka i nazwa kolekcji, statystyki, przyciski `SYNC`, `DOWNLOAD` (pobiera brakujące utwory tylko tej kolekcji, niezależnie od checkboxów) i `OPEN FOLDER`. Pod nimi pola `GENRE` i `COMMENT` (tagi kolekcji z sekcji 8, domyślnie puste) i przycisk `WRITE TAGS`, który zapisuje tagi od nowa w już pobranych plikach tej kolekcji (retag). Zmiana pól sama nie rusza plików, tylko podpowiada `WRITE TAGS`. Komunikat po zapisie przypomina o przeładowaniu tagów w rekordboxie (`Reload Tag`).
- **Pobieranie zaznaczonych:** globalny przycisk `DOWNLOAD CHECKED` pobiera tylko zaznaczone kolekcje i jest nieaktywny, gdy nic nie jest zaznaczone. Nie ma „zaznacz wszystko” ani „pobierz wszystko”. Każdy start, z nagłówka i globalny, poprzedza okno potwierdzenia z sekcji 8 (lista kolekcji, liczba utworów, szacowany czas wyszukiwania).
- **Tabela utworów:** miniatura okładki, nr, tytuł, artysta, album, długość, status (chip z ikoną i tekstem), parametry pliku (np. `FLAC 44.1/16`). Filtr po statusie (także „Excluded”) i pole wyszukiwania (opis niżej). Utwór spełniający warunek „czeka na przebieg zastępczy” (sekcja 7) ma osobne oznaczenie zamiast zwykłego `not_found` albo `failed`, żeby nie wyglądał na ostatecznie nieznaleziony.
- **Statusy zrozumiałe bez instrukcji** (decyzja właściciela, D-129): status `pending` jest w oknie, w raporcie i w CLI pokazywany jako „not downloaded”, bo słowo „pending” wygląda, jakby coś właśnie trwało. W bazie zostaje `pending`. Każdy chip statusu ma podpowiedź, co status oznacza i co zrobić dalej (np. „not downloaded”: jeszcze nie pobierany, naciśnij `DOWNLOAD`). Teksty statusów i podpowiedzi są w core (`views`), CLI dostaje te same podpowiedzi z poleceniem zamiast przycisku.
- **Licznik kolekcji** `pobrane/wszystkie` nie liczy wykluczonych utworów, a podpowiedź licznika podaje ich liczbę.
- **Menu kontekstowe utworu** (decyzja właściciela, D-125), w tabeli utworów i na wierszach okna raportu, także na kilku zaznaczonych wierszach naraz. Każda pozycja ma odpowiednik w CLI:
  - **Download this track only:** pobiera tylko wskazane utwory (sekcja 8, „Pobieranie wskazanych utworów”), z tym samym potwierdzeniem i limitem wyszukiwań. Dla `not_found` i `failed` działa jak ponowienie z raportu, dla `excluded` przywraca utwór i pobiera, dla `done` jest nieaktywne. CLI: `neonseek download --track ID`.
  - **Properties:** okno tylko do odczytu ze wszystkim, co wiemy o utworze: dane ze źródła, ISRC, link Spotify, kolekcje, status z podpowiedzią, etap, liczba prób, ostatni błąd, ścieżka pliku, format, źródłowy użytkownik i ścieżka w Soulseeku. Przy kilku utworach lista po lewej. CLI: `neonseek track info ID ...`.
  - **Show in folder:** Eksplorator z zaznaczonym plikiem utworu. Aktywne tylko dla utworów, które mają plik. CLI: `neonseek track folder ID` wypisuje ścieżkę.
  - **Exclude from downloads / Include again:** wykluczenie utworów bez pliku i przywrócenie (sekcja 5). CLI: `neonseek track exclude ID ...`, `neonseek track include ID ...`.
  - **Delete file...:** plik do Kosza po potwierdzeniu z liczbą plików (sekcja 8). CLI: `neonseek track delete-file ID ... [--yes]`.
- **Dolny panel:** zwijana konsola z logiem na żywo w stylu terminala, globalny pasek postępu, wyraźny stan "czekam na limit wyszukiwań". Napis na pasku postępu jest czytelny w każdym motywie także na wypełnionej części paska: tę część rysuje kolor wybrany z tokenów motywu tak, żeby miał największy kontrast z całym gradientem paska (test: co najmniej 3).
- **Widok raportu:** utwory `not_found` i `failed` ze wszystkich kolekcji, to samo menu kontekstowe co tabela utworów, eksport listy do CSV. `RETRY SELECTED` od razu pobiera zaznaczone utwory (decyzja właściciela, D-131), z tym samym potwierdzeniem i limitem wyszukiwań co zwykłe pobieranie, bez szukania kolekcji. Gdy trwa inne pobieranie, przycisk jest nieaktywny z podpowiedzią dlaczego. Okno raportu nie blokuje głównego okna, więc postęp widać w logu, a po pobraniu raport odświeża się sam. Utwory czekające na przebieg zastępczy mają to samo oznaczenie co w tabeli utworów (ta sama funkcja-warunek).
- **Kreator pierwszego uruchomienia:** prowadzi nową osobę krok po kroku. Na górze pierwszego kroku jest wybór języka z etykietą w obu językach („Language / Język”), który od razu tłumaczy kreator i okno za nim (podsekcja „Język” niżej). (1) Konto Soulseek, dwie drogi, obie z testem połączenia, który zapisuje login dopiero po udanym logowaniu:
  - **Create a new account** (domyślna): użytkownik wpisuje tylko nazwę. Aplikacja nie generuje ani nie podpowiada nazw. Hasło generuje aplikacja (moduł `secrets`, długie) i nigdzie go nie pokazuje. Pierwsze logowanie zakłada konto. Zajęta nazwa (serwer odrzuca hasło, `INVALIDPASS`) daje komunikat z prośbą o inną nazwę, bez automatycznych ponowień. Nazwa musi spełniać ograniczenia serwera: niepusta, najwyżej 30 znaków, tylko drukowalne ASCII, bez spacji na początku i końcu, co sprawdzamy przed wysłaniem.
  - **Use my own account:** nazwa i hasło wpisane ręcznie. Hasło identyczne z nazwą daje ostrzeżenie (Sockseek wypisuje nazwę w logach, więc takie hasło trafiłoby do nich jawnym tekstem).

  (2) Katalog biblioteki. (3) Spotify, krok opcjonalny: instrukcja założenia aplikacji deweloperskiej z przyciskiem otwierającym dashboard, Redirect URI z przyciskiem kopiowania, pole na Client ID, informacja o wymaganym Premium i wyraźne "pomiń, użyję CSV". (4) Wybór motywu. Sockseek działa bez nasłuchu (`--no-listen`, sekcja 7), więc zapora Windows o nic nie pyta i kreator nie musi o niej uprzedzać.
- **Ustawienia:** wszystko z kreatora (konto Soulseek: nazwa zapisanego konta, obie drogi zmiany konta i test połączenia, hasło nigdzie niepokazywane) plus zachowanie przy braku FLAC-a, limit prób i limit bezczynności Sockseeka (sekcja 7), tagi ze źródła (`tags_from_source`: nadpisuj, co jest domyślne, uzupełniaj puste, wyłączone, sekcja 8), odświeżanie listy playlist Spotify przy starcie (`sync_on_start`), język (`language`, podsekcja niżej), motyw, wielkość tekstu (`ui_scale`, podsekcja niżej) oraz test obecności i wersji binarki Sockseek. Zmiana folderu biblioteki pyta, czy przenieść pobrane pliki (decyzja właściciela, D-128, sekcja 8). W trakcie pobierania folderu biblioteki nie da się zmienić. Bez skonfigurowanego Spotify grupa `SPOTIFY` i wklejanie URL-i pokazują przycisk logowania zamiast błędu.
- **Lista playlist przy starcie** (decyzja właściciela, D-130): gdy użytkownik jest zalogowany do Spotify i ustawienie `sync_on_start` jest włączone (domyślnie tak), okno po starcie odświeża w tle samą listę playlist konta, bez ich treści. Brak internetu nie daje błędu, tylko linię w logu. Nowe playlisty pojawiają się odznaczone. CLI: `neonseek sync --list-only`, ustawienie `neonseek settings sync_on_start true|false`.
- **About:** wersja aplikacji i Sockseeka, licencje, przycisk kopiowania diagnostyki (wersje, system, końcówka logu z wyciętymi sekretami), żeby dało się komuś pomóc zdalnie.

### Okładki i wyszukiwanie

- Okładki pobieraj z `cover_url` w tle do `%APPDATA%\NeonSeek\covers\`. Nazwa pliku to skrót URL-a, format taki, jaki przyszedł ze źródła, bez konwersji.
- W tabeli i w lewym panelu pokazuj pomniejszone wersje, ładowane leniwie poza wątkiem UI. Brak okładki: ikona zastępcza w kolorach motywu.
- Pole wyszukiwania nad tabelą (`Ctrl+F`) filtruje listę na żywo po tytule, artyście i albumie. Przełącznik zakresu: bieżąca kolekcja albo cała biblioteka.
- Utwory z CSV nie mają okładki, dopóki nie dojdzie pobieranie metadanych z internetu (sekcja 11).

### Motywy

Motyw to dane, nie kod: jeden plik TOML na motyw w `ui/theme/themes/`, każdy z tym samym zestawem tokenów. QSS powstaje z jednego szablonu `style.qss.tmpl`, w którym nie ma ani jednego koloru wpisanego na sztywno. Zmiana motywu działa bez restartu (nowy arkusz stylów plus ponowne zabarwienie ikon), a wybór zapisuje się w `settings.toml`. Domyślny jest `neon`.

| Motyw | Charakter |
|---|---|
| `neon` | Domyślny. Paleta właściciela: magenta i turkus na ciemnym fiolecie |
| `daylight` | Jasny. Te same barwy co `neon`, przyciemnione pod jasne tło |
| `mono` | Tylko czerń, biel i szarości, bez żadnej barwy |
| `phosphor` | Zielony terminal CRT z bursztynowym akcentem |
| `ice` | Zimny granat z błękitem i fioletem |
| `noir` | Czerń jak w `mono`, ale z barwnymi akcentami, chipami statusów i nagłówkami: róż, turkus i lawenda |

| Token | `neon` | `daylight` | `mono` | `phosphor` | `ice` | `noir` | Użycie |
|---|---|---|---|---|---|---|---|
| `bg.base` | `#1A1230` | `#F4F1FA` | `#000000` | `#050A06` | `#0B1622` | `#000000` | tło okna |
| `bg.surface` | `#241A42` | `#FCFBFE` | `#0E0E0E` | `#0B140D` | `#12202F` | `#0E0E0E` | panele, tabela |
| `bg.elevated` | `#2E2152` | `#E9E3F5` | `#1F1F1F` | `#122016` | `#1A2D40` | `#1F1F1F` | hover, zaznaczenie, dialogi |
| `border` | `#1F8F8C` | `#B9ADD2` | `#6E6E6E` | `#1F5A32` | `#2B4A63` | `#3A3A3A` | obramowania, linie siatki |
| `text.primary` | `#EDE9F7` | `#1A1230` | `#FFFFFF` | `#B6FFC8` | `#E6F1FA` | `#FFFFFF` | tekst główny |
| `text.muted` | `#9A8FB8` | `#5E5478` | `#A6A6A6` | `#5FA873` | `#8AA4B8` | `#A29BFE` | tekst drugoplanowy |
| `text.on_accent` | `#FFFFFF` | `#FFFFFF` | `#000000` | `#03140A` | `#06202E` | `#000000` | tekst na przycisku głównym |
| `accent.primary` | `#D6278F` | `#B81F7A` | `#FFFFFF` | `#33FF66` | `#38BDF8` | `#FF3D8B` | akcja główna, element aktywny, pobieranie w toku |
| `accent.secondary` | `#5FE3D3` | `#0F7A77` | `#BDBDBD` | `#FFB000` | `#A78BFA` | `#2DE2E6` | fokus, wyróżnienia, koniec gradientu postępu |
| `chip.bg` | `#6B1F6E` | `#EAD9EE` | `#1F1F1F` | `#12301C` | `#1E3A52` | `#2E0E20` | tło odznak (liczniki, format pliku) |
| `state.success` | `#5FE3D3` | `#0F7A77` | `#FFFFFF` | `#33FF66` | `#4ADE80` | `#3DDC84` | `done` |
| `state.queued` | `#BC5E94` | `#8A3A6A` | `#A6A6A6` | `#5FA873` | `#8AA4B8` | `#6EA8FF` | `pending`, kolejka |
| `state.warning` | `#F2C14E` | `#8A5A00` | `#FFFFFF` | `#FFB000` | `#FBBF24` | `#FFC24B` | `not_found` |
| `state.error` | `#FF5C5C` | `#C62828` | `#FFFFFF` | `#FF5C5C` | `#F87171` | `#FF5C5C` | `failed` |
| `glow` | tak | nie | nie | tak | tak | tak | neonowa poświata |
| `dark` | tak | nie | tak | tak | tak | tak | ciemny pasek tytułu okna |

Uwagi do palet:

- `neon`: hexy z palety właściciela są przybliżone, więc poprawia się je w jednym pliku TOML. Jedna świadoma zmiana: mauve rozjaśniony z `#A8477F` do `#BC5E94`, bo oryginał był za ciemny na tle paneli.
- `mono` nie ma żadnej barwy, więc stany odróżnia wyłącznie ikona i tekst. Zostaje wyłącznie czarno-biały (decyzja właściciela, D-132), a test pilnuje, że każdy jego kolor jest odcieniem szarości.
- `noir` (decyzja właściciela, D-132): tła jak w `mono`, ale akcenty, chipy statusów i nagłówki (`text.muted`) są barwne, tak jak w pozostałych motywach. Przechodzi to samo sprawdzenie kontrastu.

Zasady wspólne dla wszystkich motywów:

- Stan nigdy nie jest sygnalizowany samym kolorem. Chip stanu ma zawsze ikonę i tekst, obramowanie i ikonę w kolorze stanu, tło `bg.surface`, tekst `text.primary`.
- `tests/test_themes.py` sprawdza każdy motyw: komplet tokenów oraz minimalny kontrast WCAG. Progi: `text.primary` na `bg.base` i `bg.surface` co najmniej 7, na `bg.elevated` co najmniej 4.5, `text.muted` na `bg.surface` co najmniej 4.5, `text.on_accent` na `accent.primary` co najmniej 4.5, akcenty i kolory stanów na `bg.surface` co najmniej 3, a kolor napisu na wypełnionej części paska postępu na każdym punkcie gradientu `accent.primary` -> `accent.secondary` co najmniej 3. Sześć wbudowanych palet te progi spełnia.
- Motywy użytkownika: dodatkowe pliki TOML z `%APPDATA%\NeonSeek\themes\` pojawiają się na liście wyboru. Plik niekompletny albo błędny jest pomijany z wpisem w logu, nigdy nie wywraca aplikacji.
- Font monospace w całej aplikacji: JetBrains Mono dołączony do aplikacji (licencja OFL), ładowany przez `QFontDatabase`.
- Etykiety sekcji wersalikami z rozstrzelonymi literami. Ostre rogi (promień 0-2 px). Obramowania 1 px.
- Poświata (`QGraphicsDropShadowEffect` w kolorze `accent.primary`) tylko w motywach z `glow` i tylko na elemencie aktywnym oraz przycisku głównym. Nie na wierszach tabeli, bo zabije wydajność.
- Pasek postępu z gradientem `accent.primary` -> `accent.secondary`. Napis na nim jest rysowany dwoma kolorami: nad wypełnioną częścią kolorem z tokenów motywu o największym kontraście z całym gradientem, nad pustą `text.primary` (D-133).
- Ikony interfejsu to SVG barwione w kodzie na kolory tokenów, przy zmianie motywu barwione ponownie.
- Natywna ramka okna. Pasek tytułu ciemny albo jasny według tokenu `dark` (atrybut DWM `DWMWA_USE_IMMERSIVE_DARK_MODE`). Okno bezramkowe to ewentualny późniejszy polish, nie MVP.
- Długie operacje nigdy w wątku UI. Core działa w `QThread`, komunikacja sygnałami.
- Kółko myszy tylko przewija. Nad listą rozwijaną, polem liczbowym i zakładkami niczego nie zmienia (D-124).

### Wielkość tekstu

Decyzja właściciela (`docs/decisions.md`, D-115): powiększanie czcionki w całym oknie.

- Ustawienie `ui_scale` w `settings.toml` i w Ustawieniach: 90%, 100% (domyślne), 110%, 125%, 150%. Wartość spoza listy daje 100%. Z CLI: `neonseek settings ui_scale 125`.
- Skróty: `Ctrl` i plus powiększa, `Ctrl` i minus zmniejsza, `Ctrl+0` wraca do 100%.
- Działa bez restartu i zapisuje się od razu, tak jak motyw.
- Skaluje się wszystko razem: czcionka, wysokość wierszy tabeli i listy kolekcji, miniatury okładek, chipy statusów i przyciski. Rozmiar czcionki i odstępy są tokenami w szablonie QSS (`font_size`, `size_N`), bez wartości wpisanych na sztywno. Stałe zostają tylko obramowania 1 px i promień rogów.
- Przy 150% okno dalej da się używać: nic nie nachodzi na siebie, a teksty, które się nie mieszczą, są skracane z wielokropkiem. Tabela, która nie mieści minimalnych szerokości kolumn, przewija się w poziomie.

### Język

Decyzja właściciela (`docs/decisions.md`, D-163): interfejs po angielsku albo po polsku.

- Ustawienie `language` w `settings.toml`: `en` albo `pl`. Przy pierwszym uruchomieniu (brak ustawienia) aplikacja bierze polski, gdy język interfejsu Windows jest polski, w innym razie angielski, i zapisuje wybór. Z CLI: `neonseek settings language pl`.
- Zmiana w Ustawieniach (sekcja `LANGUAGE`, nazwy języków zawsze w ich własnym języku: „English”, „Polski”) i w pierwszym kroku kreatora. Działa bez restartu i zapisuje się od razu, tak jak motyw, także w trakcie pobierania: okno, otwarte okna dialogowe i teksty liczone przez core dostają nowy język. Linie, które już są w logu, zostają w języku, w którym powstały.
- Tłumaczenie obejmuje okno, komunikaty błędów, podpowiedzi, kreator i teksty CLI (także pomoc `--help`). Standardowe przyciski i menu Qt (np. „Tak”, „Nie”, menu pola tekstowego) tłumaczy plik tłumaczeń Qt dołączony do PySide6.
- Tłumaczenia są danymi w core (`src/neonseek/locales/pl.json`), bez Qt. Kluczem jest tekst angielski z kodu. Polski ma trzy formy liczby mnogiej (1 utwór, 2 utwory, 5 utworów), więc `ntr()` dostaje w katalogu trzy teksty.
- Polski ma brzmieć naturalnie, nie jak tłumaczenie słowo w słowo, i zwracać się do użytkownika bezpośrednio, tak jak angielski. Nazwy własne i techniczne zostają: Soulseek, Spotify, rekordbox, FLAC, WAV, AIFF, CSV, Client ID, Redirect URI, Reload Tag. Polecenia CLI, nazwy ustawień i ich wartości (`neonseek download`, `on_missing_flac`, `true`) się nie zmieniają.
- Polskie teksty są dłuższe. Przy 100% i 150% nic nie nachodzi na siebie, a szerokości liczone z tekstu (kolumna statusu, D-157) liczą się od nowa po zmianie języka.
- Testy: każdy tekst z `tr()` i `ntr()` ma tłumaczenie, pola do podstawienia (`{count}`, `{path}` ...) zgadzają się między językami, w katalogu nie ma tekstów, których kod już nie używa, a w kodzie okna (`src/neonseek/ui/`) nie ma tekstu dla użytkownika poza `tr()`.

### Instrukcja obsługi

Decyzja właściciela (`docs/decisions.md`, D-164).

- Jedno źródło na język: `docs/manual/en.md` i `docs/manual/pl.md` (Markdown), zrzuty ekranu w `docs/manual/images/<język>/` jako JPEG. Oba pliki mają te same rozdziały w tej samej kolejności.
- Przycisk `HELP` w górnym pasku okna otwiera instrukcję w języku interfejsu: spis treści z rozdziałów po lewej, treść po prawej, przycisk `OPEN PDF`. Okno (D-182): duże (około 85% ekranu), z maksymalizacją, pamięta rozmiar, położenie i ostatni rozdział (`window-state.json` w katalogu danych). Jeden rozdział naraz z przyciskami POPRZEDNI i NASTĘPNY, spis treści jako drzewo z podrozdziałami, wyszukiwarka (`Ctrl+F`) bez rozróżniania wielkości liter i znaków diakrytycznych, czytelna czcionka proporcjonalna, kolumna około 80 znaków, tabele z ramkami, ramki ostrzeżeń w kolorze motywu (`> [!WARNING] tytuł` w Markdownie), obrazki z podpisem i powiększeniem po kliknięciu. Nagłówki mają identyfikatory `{#id}`, te same w obu językach, więc zmiana języka zostaje w rozdziale, a Ustawienia (przycisk `?` przy sekcji), kreator, raport i pytanie o przeniesienie biblioteki otwierają właściwy rozdział. Markdown zamienia na HTML mały czytnik w core (`neonseek/manual_html.py`), wspólny dla okna i PDF-a (D-183). Okno nie blokuje głównego okna. Z CLI: `neonseek help` wypisuje spis treści, `neonseek help N` rozdział N, a `neonseek help --pdf` ścieżkę PDF-a, wszystko w języku interfejsu.
- PDF powstaje z tego samego źródła skryptem `scripts/build_manual.py`, po jednym na język, przez Qt (`QTextDocument` z Markdownu i `QPdfWriter`), bez nowych zależności. Gdy w paczce nie ma PDF-a (np. przy pracy na kodzie), `OPEN PDF` buduje go tą samą funkcją do `%APPDATA%\NeonSeek\manual\` i otwiera.
- Zrzuty ekranu robi skrypt `scripts/manual_screenshots.py` na wymyślonych danych przykładowych (żadnych danych właściciela), w obu językach, więc da się je odświeżyć po każdej zmianie okna.
- Instrukcja jest dla osoby, która nie jest programistą. Zawartość: instalacja, aktualizacja i odinstalowanie według fazy 4 (dane i biblioteka zostają) z ostrzeżeniem Windows SmartScreen przy pierwszym uruchomieniu, pierwsze uruchomienie i kreator, konto Soulseek, założenie aplikacji Spotify krok po kroku, import CSV i jego format, wybór kolekcji i pobieranie, znaczenie każdego statusu, raport i ponawianie, pobieranie pojedynczego utworu, wykluczanie i przywracanie, usuwanie plików (usunięcie w NeonSeek nie usuwa utworu z kolekcji rekordboxa), zmiana folderu biblioteki, tagi i `Reload Tag` w rekordboxie, ograniczenie WAV, język, motywy i wielkość tekstu, najczęstsze problemy (brak połączenia, możliwy ban konta, utwór nieznaleziony). Na końcu uczciwa informacja: zasady serwera Soulseek zakazują automatycznych klientów, każdy używa aplikacji na własną odpowiedzialność i pobiera tylko to, do czego ma prawo.

## 10. Fazy i kryteria akceptacji

### Faza 0 - szkielet i rozpoznanie

- Repo, `pyproject.toml`, ruff, pytest, struktura katalogów, `CLAUDE.md`.
- `scripts/fetch_sockseek.ps1` pobiera przypiętą wersję Sockseeka dla Windows do `vendor/sockseek/`.
- Spike: uruchom Sockseek z `--mock-files-dir` na małym CSV i z `--progress-json`. Zapisz w `docs/sockseek-notes.md` potwierdzone flagi, przykładowe linie JSON i wybrany sposób mapowania wiersza na utwór.

**Działa, gdy:** `uv run pytest` przechodzi, `docs/sockseek-notes.md` zawiera realne próbki wyjścia, a każde **[WERYFIKUJ]** z sekcji 3 i 7 ma odpowiedź.

### Faza 1 - źródła do bazy

Kolejność wewnątrz fazy: najpierw CSV (nie zależy od żadnego zewnętrznego API i od razu daje dane do fazy 2), potem konto Spotify, na końcu URL.

- `sources/ingest.py` z deduplikacją z sekcji 5, pokryte testami.
- CSV: `neonseek import csv <plik> [--name N] [--map pole=kolumna,...]`.
- Konto: `neonseek spotify login`, `neonseek sync [--collection ID]`.
- URL: `neonseek add <url> [<url> ...]`.
- Wspólne: `neonseek collections` (lista z rodzajem, licznikami i czytelnością).
- Fixture'y z realnych odpowiedzi Spotify i przykładowe pliki CSV (przecinek i średnik, długość w sekundach, milisekundach i `m:ss`, brak kolumny długości), testy parserów.
- Punkty Spotify z sekcji 12 i **[WERYFIKUJ]** z sekcji 6 sprawdzone na realnych odpowiedziach (przy pierwszym logowaniu właściciela), wynik w `docs/spotify-notes.md`.

**Działa, gdy:**

- **CSV:** plik z typowymi nagłówkami importuje się bez pytań o mapowanie, plik z nietypowymi da się zaimportować przez `--map`, ponowny import tej samej treści niczego nie zmienia, a całość działa bez skonfigurowanego Spotify.
- **Konto:** po logowaniu `sync` zapisuje wszystkie czytelne playlisty i Polubione z kompletem pól z sekcji 5 (w tym ISRC), cudze playlisty są oznaczone jako nieczytelne, drugi `sync` bez zmian na Spotify nie pobiera zawartości playlist ponownie, a po restarcie aplikacji nie trzeba logować się od nowa.
- **URL:** album i pojedynczy utwór trafiają do bazy z ISRC, własna playlista dodana przez URL nie tworzy drugiej kolekcji obok tej z konta, a cudza playlista kończy się czytelnym komunikatem i nie zostawia nic w bazie.
- **Deduplikacja:** ten sam utwór dodany z konta, przez URL i z CSV ma jeden wiersz w `tracks`.

### Faza 2 - pobieranie bez GUI

- **Na początku fazy, jednorazowo i ręcznie** (prawdziwy Soulseek, konto właściciela, poza testami): błędny login i hasło (kod wyjścia, komunikat), zakładanie konta na wolną nazwę i błąd przy zajętej nazwie, pytanie zapory Windows o `sockseek.exe` i pobieranie po odmowie (`--no-listen`), brak hasła w wyjściu `-v` przy prawdziwym logowaniu, a także czy Sockseek wypisuje cokolwiek, gdy pobieranie czeka w kolejce u innego użytkownika, i jak długo taka cisza trwa. Od tego ostatniego zależy domyślny limit bezczynności. Wynik do `docs/sockseek-notes.md`.
- Adapter Sockseeka z dwuprzebiegową polityką formatu, limitami czasu startu i bezczynności oraz odczytem stanu limitu wyszukiwań ze stderr. Do tego runner, `verify`, `cover`, `tag`, `place` i generowanie `.m3u8`.
- `neonseek download (--collection ID [--collection ID ...] | --selected) [--limit N] [--yes]`, `neonseek report`. Wybór kolekcji i potwierdzenie według sekcji 8.
- Tagi (sekcja 8): `neonseek collection tags ID [--genre TEKST] [--comment TEKST]` (pusty tekst czyści pole, wartości widać w `neonseek collections`) oraz `neonseek retag (--collection ID [--collection ID ...] | --selected)`, który zapisuje tagi od nowa w już pobranych plikach, bez pobierania. Ustawienie `tags_from_source` w `settings.toml`.
- Konto Soulseek w core i CLI (sekcja 9, kreator): `neonseek soulseek login --new` pyta tylko o nazwę, generuje długie hasło, loguje się (to zakłada konto) i zapisuje oba w Credential Manager dopiero po udanym logowaniu. Zwykłe `neonseek soulseek login` pyta o nazwę i hasło, z testem połączenia i ostrzeżeniem przy haśle identycznym z nazwą. `neonseek soulseek test` sprawdza zapisany login. Testy wyłącznie na atrapie procesu Sockseeka, bez łączenia z Soulseekiem. Kreator i ustawienia w fazie 3.
- Testy integracyjne na `--mock-files-dir`:
  - nieudane pobrania,
  - utwór dostępny jako FLAC i WAV jednocześnie, także gdy WAV leży w folderze nazwanym jak album, a FLAC nie,
  - nieudane pobranie FLAC-a przy dostępnym WAV (utwór zostaje `failed`),
  - utwór dostępny tylko jako WAV, tylko jako `.aiff` i tylko jako `.aif`,
  - utwór dostępny tylko jako MP3 i tylko jako `.m4a` (nie trafia do biblioteki),
  - przerwane uruchomienie w przebiegu 1: utwór dostępny tylko jako WAV kończy przebieg 1 jako `not_found`, uruchomienie zostaje przerwane przed przebiegiem 2, a następne pobiera go jako WAV bez ponownego szukania FLAC-a. Utwór w toku w chwili przerwania wraca do `pending` w etapie `flac`,
  - przerwane uruchomienie w przebiegu 2: utwór w toku wraca do `pending` w etapie `fallback`, a następne uruchomienie kończy go w przebiegu 2 bez szukania FLAC-a,
  - zmiana ustawienia z `not_found` na `lossless`: utwór `not_found` z etapu `flac` przechodzi przy następnym uruchomieniu do przebiegu 2, także gdy przebieg 1 nie ma nic do zrobienia,
  - zmiana ustawienia z `lossless` na `not_found`: niepobrany utwór z etapu `fallback` wraca do `flac` i `not_found`, a przebieg 2 się nie odpala,
  - nieudane pobranie WAV-a w przebiegu 2 (`--mock-files-fail-downloads`): utwór zostaje `failed` w etapie `fallback` z `attempts = 1`, a kolejne uruchomienie pobiera go bez ponownego szukania FLAC-a,
  - odzysk po awarii: utwory zostawione w stanie `searching` albo `downloading` wracają przy starcie do `pending` z niezmienionym `stage` i `attempts`,
  - krok `tag` na FLAC, WAV i AIFF (we FLAC-u Vorbis comments bez ID3, w AIFF ID3, w WAV-ie RIFF INFO i ID3), tryby `fill`, `overwrite` i `off`, utwór w dwóch kolekcjach (gatunki i komentarze połączone `", "`), błąd zapisu tagów, który nie unieważnia pobrania, oraz retag bez pobierania.

  „Bez ponownego szukania FLAC-a” sprawdzamy po zawartości `run.csv` przebiegu 1: utworu tam nie ma.
- Test kontraktu na przypiętej wersji: przy `-v` linia `SongJob: rate limited:` na stderr pasuje do wzorca adaptera. Test idzie na mocku z obniżonym `--searches-per-time`. Obniżanie limitów jest dozwolone, zakaz z sekcji 0 dotyczy podnoszenia, a mock nie łączy się z siecią.
- Limity czasu testowane na podstawionym procesie-atrapie zamiast Sockseeka.
- **Na koniec fazy, ręcznie, w rekordboxie:** import `.m3u8` ze ścieżkami względnymi i znakami spoza ASCII, widoczność okładki osadzonej w WAV i AIFF, pola tagów, które rekordbox pokazuje dla FLAC, WAV i AIFF (gatunek, komentarz, rok, artysta albumu, numer utworu, a w WAV-ie także znaki spoza ASCII w RIFF INFO) oraz czy przeładowanie tagów (`Reload Tag`) odświeża je po `retag`. Wynik do `docs/`, a pola, których rekordbox nie pokazuje, poprawiamy w tabeli z sekcji 8 i w kodzie.

**Działa, gdy:** `neonseek download` bez `--collection` i bez `--selected` niczego nie pobiera i wypisuje listę kolekcji, `--selected` bez zaznaczonych kolekcji też niczego nie pobiera, start pokazuje podsumowanie z liczbą utworów i szacowanym czasem wyszukiwania i bez `--yes` czeka na potwierdzenie, a pobierane są wyłącznie utwory z kolekcji uruchomienia. Na kolekcji testowej (około 20 utworów) każdy utwór kończy w statusie `done`, `not_found` albo `failed`, pliki `done` leżą w `tracks\` i są poprawnymi plikami FLAC, WAV albo AIFF o zgodnej długości, utwór dostępny w kilku formatach zawsze pobiera się jako FLAC (także gdy WAV leży w lepiej nazwanym folderze), utwór bez FLAC-a pobiera się jako WAV albo AIFF albo kończy jako `not_found` zależnie od ustawienia, utwór, którego FLAC-a nie udało się pobrać, zostaje `failed` i nie jest pobierany w innym formacie przed wyczerpaniem limitu prób, utwór `not_found` z przerwanego uruchomienia albo sprzed zmiany ustawienia na `lossless` trafia przy następnym uruchomieniu prosto do przebiegu zastępczego, bez ponownego szukania FLAC-a, utwór z etapu `fallback` (także po nieudanym pobraniu WAV-a) nie jest ponownie szukany jako FLAC bez ponowienia z raportu, plik stratny ani `.m4a` nigdy nie trafia do biblioteki, pobrany plik ma osadzoną okładkę, jeśli źródło ją podało, pobrany plik ma tagi ze źródła według `tags_from_source` oraz gatunek i komentarz kolekcji, jeśli są ustawione (utwór w kilku kolekcjach ma je połączone `", "`), we FLAC-u nie ma ID3, a WAV ma RIFF INFO, błąd zapisu tagów nie unieważnia pobrania, `collection tags` nie rusza plików i podpowiada `retag`, `retag` zapisuje tagi w pobranych plikach bez pobierania i przypomina o przeładowaniu tagów w rekordboxie, rekordbox pokazuje gatunek i komentarz z pobranych plików, `.m3u8` importuje się do rekordboxa, ponowne uruchomienie nie pobiera niczego drugi raz, utwór bez znanej długości pobiera się i jest oznaczony jako niezweryfikowany, przerwanie w połowie zostawia spójną bazę i pusty staging, brak połączenia z Soulseekiem kończy się czytelnym błędem po limicie czasu startu (komunikat wymienia też możliwy ban konta), a czekanie na limit wyszukiwań nie ubija procesu. Konto Soulseek (na atrapie procesu): `soulseek login --new` pyta tylko o nazwę, odrzuca nazwę niezgodną z ograniczeniami serwera bez łączenia, zapisuje nazwę i wygenerowane hasło dopiero po udanym logowaniu, przy zajętej nazwie prosi o inną bez ponawiania i niczego nie zapisuje, a hasła nigdzie nie wypisuje. Zwykłe `soulseek login` ostrzega przy haśle identycznym z nazwą.

### Faza 3 - GUI

- Layout, kreator pierwszego uruchomienia, okładki, wyszukiwarka i pięć motywów z sekcji 9, wszystkie akcje spięte z core przez wątki robocze.
- Tagi (sekcje 8 i 9): pola gatunku i komentarza w nagłówku kolekcji, przycisk `WRITE TAGS` zapisujący tagi do już pobranych plików kolekcji oraz ustawienie `tags_from_source` w Ustawieniach.
- `tests/test_themes.py` z kontrolą kompletności tokenów i kontrastu.

**Działa, gdy:** cały przepływ (logowanie, dodanie URL-a, import CSV z dialogiem mapowania, wybór kolekcji, sync, pobieranie, raport, ponowienie) da się przejść bez terminala, każda nowa kolekcja pojawia się odznaczona, `DOWNLOAD` w nagłówku pobiera tylko otwartą kolekcję niezależnie od checkboxów, `DOWNLOAD CHECKED` jest nieaktywny bez zaznaczonych kolekcji, nigdzie nie ma „zaznacz wszystko” ani „pobierz wszystko”, a każdy start poprzedza okno z listą kolekcji, liczbą utworów i szacowanym czasem wyszukiwania, aplikacja uruchamia się i pobiera z samego CSV bez skonfigurowanego Spotify, okno nie zamraża się podczas pobierania ani przy ładowaniu okładek, postęp i stan oczekiwania na limit są widoczne, anulowanie działa, wyszukiwarka filtruje listę na żywo, gatunek i komentarz kolekcji da się ustawić w nagłówku i zapisać do pobranych plików przyciskiem `WRITE TAGS` (komunikat przypomina o `Reload Tag` w rekordboxie), ustawienie `tags_from_source` jest w Ustawieniach, a każdy z pięciu motywów przełącza się bez restartu i przechodzi test kontrastu. Kreator pozwala założyć nowe konto Soulseek przez wpisanie samej nazwy (hasło generuje aplikacja, nigdzie go nie pokazuje) albo podać własne konto, oba z testem połączenia. Zajęta nazwa daje prośbę o inną. Ustawienia pokazują nazwę zapisanego konta i pozwalają je zmienić obiema drogami.

### Faza 3b - wymagania właściciela przed fazą 4

Decyzje właściciela D-125 do D-134 (`docs/decisions.md`). Każda funkcja okna ma odpowiednik w CLI. Testy bez łączenia z Soulseekiem i Spotify, usuwanie wyłącznie na atrapie Kosza.

- Menu kontekstowe utworu w tabeli i w oknie raportu, także na kilku zaznaczonych: „Download this track only”, „Properties”, „Show in folder”, „Exclude from downloads” / „Include again”, „Delete file...” (sekcja 9). Okno Properties tylko do odczytu.
- Status `excluded` z własnym chipem i filtrem, przywracanie, migracja schematu do wersji 3 (sekcja 5).
- Usuwanie do Kosza według zmienionej zasady z sekcji 0: „Delete file” na utworze, opcja „also move downloaded files to the Recycle Bin” przy usuwaniu kolekcji, odmowa na dysku bez Kosza, blokada w trakcie pobierania (sekcja 8).
- Zmiana folderu biblioteki w Ustawieniach z bezpiecznym przeniesieniem plików i dziennikiem `file_moves` (sekcja 8).
- „not downloaded” zamiast „pending” w oknie, raporcie i CLI oraz podpowiedzi na każdym chipie statusu (sekcja 9).
- Odświeżanie listy playlist Spotify przy starcie okna (`sync_on_start`, sekcja 9).
- `RETRY SELECTED` w raporcie od razu pobiera, `neonseek retry --download` w CLI (sekcja 8).
- Szósty motyw `noir`, `mono` dalej czarno-biały (sekcja 9).
- Poprawki: czytelny napis na pasku postępu w każdym motywie, liczba pojedyncza w „1 track starts again...”, przebieg WAV/AIFF w podsumowaniu tylko przy `on_missing_flac = lossless`.

**Działa, gdy:** menu utworu działa na jednym i na kilku zaznaczonych wierszach w tabeli i w raporcie, a nieaktywne pozycje mówią dlaczego. „Download this track only” pobiera wyłącznie wskazane utwory po tym samym potwierdzeniu i z tym samym limitem wyszukiwań co zwykłe pobieranie, dla `not_found` i `failed` zaczyna od FLAC-a, dla `excluded` przywraca utwór, a dla `done` jest nieaktywne. Properties pokazuje dane ze źródła, ISRC, link Spotify, kolekcje, status, etap, liczbę prób, ostatni błąd, ścieżkę pliku, format, źródłowego użytkownika i ścieżkę w Soulseeku. Show in folder jest aktywne tylko dla utworów z plikiem. Wykluczony utwór nie jest pobierany przez `DOWNLOAD` ani `DOWNLOAD CHECKED`, ma własny chip i da się go przywrócić do statusu sprzed wykluczenia. „Delete file” przenosi plik do Kosza po potwierdzeniu z liczbą plików, ustawia `excluded` i od razu odświeża `.m3u8` wszystkich kolekcji utworu. Na dysku bez Kosza aplikacja odmawia i niczego nie usuwa. Usunięcie kolekcji bez zaznaczonej opcji nie rusza plików, a z opcją przenosi do Kosza tylko pliki utworów spoza innych kolekcji i `.m3u8` tej kolekcji. Nic z tego nie działa w trakcie pobierania. Zmiana folderu biblioteki z przeniesieniem przenosi pliki aplikacji, a przerwanie w dowolnym miejscu nie gubi żadnego pliku i zostawia bazę oraz `.m3u8` zgodne z dyskiem po obu stronach. W trakcie pobierania przeniesienie jest zablokowane. Status `pending` jest wszędzie pokazywany jako „not downloaded”, a każdy chip statusu ma podpowiedź. Lista playlist Spotify odświeża się w tle przy starcie okna, bez treści playlist i bez błędu przy braku internetu, a nowe playlisty są odznaczone. `RETRY SELECTED` od razu pobiera zaznaczone utwory i jest nieaktywny w trakcie innego pobierania. `noir` przechodzi test kontrastu, `mono` ma tylko odcienie szarości. Napis na pasku postępu jest czytelny na wypełnionej części w każdym motywie.

### Faza 3c - język polski i instrukcja obsługi

Decyzje właściciela D-163 i D-164 (`docs/decisions.md`), sekcje 0, 2 i 9 („Język”, „Instrukcja obsługi”). Bez nowych zależności, a każda nowa zależność jest zgłaszana właścicielowi z licencją.

- **Język:** ustawienie `language` (`en`, `pl`), wybór przy pierwszym uruchomieniu według języka Windows, zmiana w Ustawieniach i w pierwszym kroku kreatora bez restartu, `neonseek settings language`. Katalog `src/neonseek/locales/pl.json` z tłumaczeniem każdego tekstu okna, komunikatów błędów, podpowiedzi, kreatora i CLI (także pomocy `--help`), polska liczba mnoga w `ntr()`, pliki tłumaczeń Qt dla standardowych przycisków. Skrypt `scripts/i18n_catalog.py` dopisuje do katalogu nowe teksty z kodu i pokazuje te bez tłumaczenia.
- **Instrukcja:** `docs/manual/en.md` i `docs/manual/pl.md`, przycisk `HELP` z oknem instrukcji (spis treści, `OPEN PDF`), `neonseek help`, skrypt PDF `scripts/build_manual.py` i skrypt zrzutów `scripts/manual_screenshots.py` (JPEG, oba języki, dane przykładowe).
- Zrzuty głównego okna, kreatora i Ustawień po polsku przy 100% i 150%, sprawdzone, że nic się nie rozjeżdża.

**Działa, gdy:** przy braku ustawienia `language` aplikacja startuje po polsku na Windows po polsku, a w innym razie po angielsku. Zmiana języka w Ustawieniach i w pierwszym kroku kreatora od razu tłumaczy okno, otwarte okna dialogowe i kreator, bez restartu, także w trakcie pobierania, i zostaje po ponownym uruchomieniu. CLI pisze w języku z ustawień, także `--help`. Każdy tekst z `tr()` i `ntr()` ma polskie tłumaczenie z tymi samymi polami do podstawienia, a w kodzie okna nie ma tekstu dla użytkownika poza `tr()` (testy). Przy 100% i 150% po polsku nic w głównym oknie, kreatorze i Ustawieniach nie nachodzi na siebie. `HELP` otwiera instrukcję w języku interfejsu ze spisem treści, a `OPEN PDF` otwiera PDF w tym języku. Instrukcja w obu językach ma te same rozdziały, `scripts/build_manual.py` buduje oba PDF-y, a `scripts/manual_screenshots.py` odświeża zrzuty w obu językach (testy).

### Faza 4 - exe i instalator

- PyInstaller w trybie onedir, okno bez konsoli, dołączone: `vendor/sockseek/`, fonty, ikony, motywy, szablon QSS, ikona `.ico`, katalog tłumaczeń `locales/`, pliki tłumaczeń Qt (`qtbase_pl.qm`) oraz instrukcja (`docs/manual`: Markdown, zrzuty i oba PDF-y). `scripts/build.ps1` buduje PDF-y instrukcji przed PyInstallerem (`scripts/build_manual.py`).
- Instalator Inno Setup (`installer/neonseek.iss`): instalacja per użytkownik bez uprawnień administratora (`PrivilegesRequired=lowest`), domyślnie do `%LOCALAPPDATA%\Programs\NeonSeek`, skrót w menu Start, opcjonalny skrót na pulpicie, strona z licencją, deinstalator.
- Deinstalacja usuwa program. Dane w `%APPDATA%\NeonSeek\` i bibliotekę muzyki zostawia.
- Instalacja nowszej wersji na starszą zachowuje dane, a baza migruje się sama przy starcie (sekcja 5).
- Numer wersji w jednym miejscu (`pyproject.toml`), widoczny w oknie About, w nazwie instalatora i we właściwościach exe.
- `scripts/build.ps1` robi wszystko od zera: testy, PyInstaller, instalator `dist\NeonSeek-Setup-<wersja>.exe` oraz wersję przenośną `dist\NeonSeek-<wersja>-portable.zip`.
- Licencje w paczce: `LICENSE` aplikacji (GPL-3.0-or-later, D-203) i `THIRD_PARTY_LICENSES` (Sockseek na AGPL-3.0 z odnośnikiem do źródeł dokładnie tej wersji, PySide6 i Qt na LGPL-3.0, JetBrains Mono na OFL, pozostałe zależności) oraz pełne teksty licencji wszystkich dołączonych pakietów, zebrane z ich katalogów `dist-info`. Paczka mówi też, gdzie leży kod źródłowy dokładnie tej wersji NeonSeek (GPL-3.0). Te same informacje w oknie About.
- `README.md` i `README.pl.md` (pierwsza wersja z przygotowania do publikacji, D-207) uzupełnić o: instalację z instalatora, ostrzeżenie SmartScreen, założenie aplikacji Spotify krok po kroku, konto Soulseek, format CSV oraz znane ograniczenie WAV w rekordboxie (bez okładki i numeru utworu, sekcja 8) i to, że po zmianie tagów kolekcji trzeba w rekordboxie użyć `Reload Tag`. Pełna treść jest już w instrukcji z fazy 3c, więc README może być krótki i odsyłać do niej (do PDF-a w obu językach). Opis instalacji w instrukcji sprawdzamy na zbudowanym instalatorze.
- Exe i instalator nie są podpisane cyfrowo, więc Windows SmartScreen pokaże ostrzeżenie przy pierwszym uruchomieniu. Podpis kodu jest poza zakresem MVP (sekcja 11). Windows 11 z włączonym Inteligentnym sterowaniem aplikacjami (Smart App Control) blokuje program bez podpisu bez możliwości „Uruchom mimo to”, co instrukcja opisuje (D-223).

**Stan (2026-10-14, gałąź `phase-4`):** zrobione według D-213 do D-223. Paczka onedir ma `NeonSeek.exe` (okno) i `neonseek-cli.exe` (ten sam program w CLI), instrukcję w `manual\` obok exe, `LICENSE.txt`, `THIRD_PARTY_LICENSES.txt` z pełnymi tekstami licencji i `README.txt` z adresem kodu źródłowego. Adres repozytorium to znacznik `REPO_URL` w `src/neonseek/project.py` (D-214). `build.ps1` buduje z `git archive HEAD` w katalogu poza profilem użytkownika, w środowisku bez jego ścieżek (D-219), a skan danych osobistych obejmuje tekst w exe, dll i `.pyc` (D-220). Instalator jest kompilowany przez `build.ps1` (Inno Setup 6.7.3) i sprawdzony z instrukcją, aktualizacją 0.1.0 na 0.1.1 i deinstalacją w folderze tymczasowym (D-226), a przy tej próbie wyszedł i został poprawiony błąd zamykania programu z otwartym kreatorem (D-225). Kryteria „Działa, gdy” na czystym koncie Windows (bez Pythona, SmartScreen, pełny przepływ) czekają na właściciela.

**Działa, gdy:** na koncie Windows bez Pythona i narzędzi deweloperskich instalator przechodzi bez pytania o administratora, aplikacja startuje z menu Start, kreator pierwszego uruchomienia prowadzi do działającej konfiguracji (także z nowym kontem Soulseek założonym przez wpisanie samej nazwy) i da się przejść pełny przepływ z fazy 3. Deinstalacja zostawia dane i bibliotekę. Instalacja wersji z wyższym numerem na istniejącą zachowuje kolekcje i stan pobrań. W paczce nie ma żadnych danych właściciela: ścieżek, loginów, Client ID ani tokenów.

### Faza 4b - Apple Music, YouTube i SoundCloud jako źródła list

Polecenie właściciela (2026-10-20), decyzje D-227 do D-237, rozpoznanie w `docs/playlist-sources-notes.md`. Z tych serwisów bierzemy wyłącznie listy utworów, dźwięk dalej tylko z Soulseeka. Albumy i utwory Apple przez iTunes Lookup API, playlisty Apple ze strony (najwyżej 300 utworów), playlisty i filmy YouTube oraz sety i utwory SoundCloud przez yt-dlp (jedyna nowa zależność, przypięta). Tytuły YouTube i SoundCloud dzielone na wykonawcę i tytuł, z ręczną poprawką z menu utworu i `neonseek track edit`. Import `.txt` z iTunes w imporcie CSV.

**Działa, gdy:** linki wszystkich trzech serwisów dodają się w tym samym polu co linki Spotify, bez logowania do Spotify, z wyciętymi parametrami śledzącymi, a kolekcja startuje odznaczona. Synchronizacja odświeża zaznaczone kolekcje tych serwisów. Zmiana struktury źródła, blokada albo niepełna lista dają czytelny komunikat, nic nie pada. Testy nie łączą się z siecią, a test kontraktowy pilnuje zbiorczych metadanych SoundCloud w przypiętej wersji yt-dlp. Zbudowany exe czyta wszystkie trzy źródła.

Snapshot `D:\NeonSeek-public` (D-209) trzeba odświeżyć przed publikacją, bo powstał przed fazą 4b.

## 11. Roadmapa po MVP (nie implementuj teraz)

1. **Metadane z internetu:** wyszukiwanie po ISRC (MusicBrainz, ewentualnie Discogs) i uzupełnianie pól, których źródło nie podało (np. utwory z CSV bez albumu, daty i numeru), oraz pól spoza MVP (np. wytwórnia), a także okładki dla utworów, które ich nie mają (np. z CSV). Pola ze źródła, gatunek i komentarz kolekcji zapisuje już MVP (sekcja 8, krok `tag`), tutaj dochodzą tylko nowe dane. Dane ze źródła z tabeli `tracks` jako baza, serwisy zewnętrzne jako uzupełnienie.
2. **Nazewnictwo i porządek:** szablony nazw plików i folderów, masowa zmiana nazw z podglądem przed zatwierdzeniem, aktualizacja `file_path` i `.m3u8`.
3. **Format:** konwersja przez ffmpeg i wybieralny format docelowy (FLAC, AIFF, WAV, MP3), ujednolicenie sample rate i bit depth. Tu też sprowadzenie do jednego formatu plików WAV i AIFF pobranych przy braku FLAC-a. To docelowe rozwiązanie znanego ograniczenia WAV w rekordboxie (brak okładki i numeru utworu, sekcja 8): WAV zamieniony na AIFF albo FLAC dostaje okładkę i komplet tagów. Formaty stratne dopiero tutaj, jako świadomy wybór użytkownika.
4. **ALAC w `.m4a`:** osobny przebieg Sockseeka po przebiegu 2, z `--format m4a` i warunkiem minimalnego bitrate (`--min-bitrate`), który odsiewa AAC, bo Sockseek nie odróżnia AAC od ALAC-a. Do rozstrzygnięcia: część klientów Soulseek nie podaje bitrate, a bez `--strict-conditions` plik o nieznanym bitrate przechodzi. `verify` dalej odrzuca AAC po faktycznym kodeku. Wymaga przywrócenia ALAC-a w polityce formatu, `verify`, `cover` (atom `covr`), kolumnie `format` i testach.
5. **Inne wersje utworu:** okno "znalazłem inne wersje, pobrać też?". Po wyszukaniu utworu aplikacja zbiera z wyników inne wersje tego samego tytułu (Extended Mix, Original Mix, Radio Edit, remiksy, live), pokazuje listę z długością, formatem i liczbą źródeł i pozwala zaznaczyć, które dobrać. Dobrane wersje to osobne utwory w `tracks`, powiązane z oryginałem. Wymaga dostępu do pełnej listy wyników wyszukiwania (do sprawdzenia: `--print json-all` albo API demona), a każde takie sprawdzenie zużywa wyszukiwanie z limitu.
6. **Jakość:** wykrywanie fałszywych plików bezstratnych (analiza widma).
7. **Silnik:** przejście na tryb demona Sockseeka, jeśli podproces okaże się za mało elastyczny.
8. **Kolejne źródła:** YouTube, SoundCloud i Apple Music są już w fazie 4b (przez yt-dlp i API Apple, D-227). Zostają URL-e Bandcamp i MusicBrainz przez ekstraktory Sockseeka. Wymaga maszynowo czytelnej listy utworów z Sockseeka (do sprawdzenia: `--print jobs` albo API demona). Do tego uzupełnianie utworów z CSV o metadane Spotify po id.
9. **Dystrybucja:** sprawdzanie dostępności nowej wersji, podpis cyfrowy exe i instalatora, widok siatki z dużymi okładkami. Polskie tłumaczenie interfejsu i instrukcja są już w fazie 3c. Kolejny język to nowy plik w `src/neonseek/locales/` (z regułą liczby mnogiej w `i18n.py`) i nowy plik instrukcji w `docs/manual/`, bez zmian w kodzie okna.
10. **Port nasłuchu:** opcjonalny port nasłuchu w ustawieniach (domyślnie wyłączony, czyli `--no-listen`) dla osób z przekierowanym portem na routerze. Wymaga numeru portu spoza dynamicznego zakresu Windows (49152–65535), obsługi błędu `Failed to start listening` z czytelnym komunikatem i uprzedzenia o pytaniu zapory Windows.
11. **Litery spoza ASCII w wyszukiwaniu:** dodatkowe wyszukiwanie z artystą i tytułem przepisanym na ASCII (np. `KRØMA` -> `KROMA`) dla utworów, które skończyły jako `not_found` i mają w artyście albo tytule litery spoza ASCII. Powód: Soulseek nie utożsamia `Ø` z `O`. Oba utwory artysty z `Ø` w nazwie z kolekcji testowej skończyły jako `NoSearchResults` (serwer zwrócił 0 wyników), a ręczne wyszukiwanie przez `O` dało dla pierwszego 6 wyników spełniających warunki dopasowania (wszystkie MP3 320 kbps, więc dziś bez pliku bezstratnego) i 0 dla drugiego (`docs/sockseek-notes.md`, sekcja 11.1). Sockseek 3.0.5 nie przepisuje tekstu sam: `--regex` zmienia tekst całego przebiegu, a `--artist-maybe-wrong` i `--desperate` szukają szerzej, a nie inaczej zapisanego tekstu. Do ustalenia przy wdrożeniu:
    - przepisanie: rozkład Unicode (NFKD) bez znaków diakrytycznych plus tabela liter, które się nie rozkładają (`Ø`, `ł`, `ß`, `Æ`), a dodatkowe wyszukiwanie tylko wtedy, gdy zapis ASCII różni się od oryginału,
    - miejsce w polityce formatu: osobny przebieg z własnym `run.csv` po przebiegu 1, a przy `lossless` także po przebiegu 2, z tą samą listą formatów co przebieg, którego `not_found` dotyczy,
    - zapis w bazie, że wariant ASCII był już szukany w danym etapie, żeby go nie powtarzać (zasada „nie szukaj drugi raz”), i zerowanie tej informacji przy ponowieniu z raportu,
    - koszt: jedno dodatkowe wyszukiwanie z limitu Sockseeka na utwór z takimi literami, liczone także w potwierdzeniu przed startem,
    - dopasowanie przy `--strict-title` i `--strict-artist`, bo pliki będą podpisane zapisem ASCII, oraz tagi i nazwa pliku w bibliotece dalej z oryginalnym zapisem ze źródła.

Wymagania wobec MVP wynikające z roadmapy: komplet metadanych ze źródeł w bazie, źródła za wspólnym interfejsem, postprocessing jako lista kroków, ścieżki plików tylko w bazie (nigdzie indziej nie zakładaj nazwy ani rozszerzenia pliku), kolory i format jako konfiguracja, a model danych nie zakłada, że z jednego utworu źródłowego powstaje na zawsze dokładnie jeden plik.

## 12. Lista rzeczy do zweryfikowania (fazy 0-2)

Szczegóły i próbki wyjścia dla Sockseeka: `docs/sockseek-notes.md`.

**Zweryfikowane w fazie 0** (Sockseek 3.0.5, tylko `--mock-files-dir`):

- ✅ Nazwa i zawartość paczki Sockseeka dla Windows w wydaniach, czy binarka jest samodzielna. Paczka `sockseek_3.0.5_win-x64.zip` zawiera `sockseek.exe` i `LICENSE`. Binarka jest samodzielna (.NET 10 single-file).
- ✅ Dokładne flagi z sekcji 7 na przypiętej wersji. Wszystkie istnieją, wywołanie z sekcji 7 potwierdzone.
- ✅ Schemat zdarzeń `--progress-json` i czy zawiera stan oczekiwania na limit wyszukiwań. Nie zawiera. Stan jest na stderr przy `-v`.
- ✅ Zachowanie `{row}` w `--name-format` dla wejścia CSV. Liczy nagłówek (pierwszy wiersz danych to `2`) i liczy rekordy CSV.
- ✅ Zachowanie Sockseeka dla wiersza `run.csv` z pustym `Length` i z pustym `Artist`. Pobiera bez filtra długości, a `--strict-artist` jest pomijany.
- ✅ Polityka formatu: czy `--format flac,wav,aiff,alac --pref-format flac` zawsze wybiera FLAC, gdy jest dostępny, czy wartości `aiff` i `alac` są przyjmowane i jak Sockseek odróżnia ALAC od AAC w `.m4a`. Wyniki: nie zawsze, stąd dwa przebiegi. `aiff` nie łapie `.aif`. `alac` nie łapie `.m4a`. ALAC od AAC nie jest odróżniany. Decyzje są w sekcji 7.

**Zweryfikowane w fazie 1** (konto właściciela, aplikacja w Development Mode, szczegóły w `docs/spotify-notes.md`):

- ✅ Realny kształt odpowiedzi `GET /playlists/{id}/items` i `GET /me/tracks`, maksymalny `limit`. Wpisy playlist mają jednocześnie `item` i `track`, a `/me/tracks` tylko `track`. Maksimum: 100 dla `/playlists/{id}/items`, 50 dla `/me/tracks`, `/me/playlists` i `/albums/{id}/tracks`.
- ✅ Czy utwory z `GET /albums/{id}/tracks` zawierają `external_ids`, czy ISRC trzeba dociągać przez `GET /tracks/{id}`. Nie zawierają, ISRC trzeba dociągać.
- ✅ Odpowiedź `GET /playlists/{id}` dla cudzej playlisty (kod i kształt), żeby komunikat błędu był trafny. W starszej aplikacji deweloperskiej (właściciela) playlista innego użytkownika zwraca 200 z pełną treścią. Playlista tworzona przez Spotify zwraca 404 `Resource not found`, identyczne jak dla nieistniejącego id. W nowej aplikacji ta sama playlista daje 200 bez pola `items`, a jej `/items` daje 403. Zależność od wieku aplikacji opisuje sekcja 3, a obie weryfikacje są w `docs/spotify-notes.md`.

**Zweryfikowane ręcznie na początku fazy 2** (prawdziwy Soulseek, szczegóły w `docs/sockseek-notes.md`, sekcja 11.1):

- ✅ Kod wyjścia i zachowanie Sockseeka przy błędnym loginie Soulseek. Złe hasło daje po około 1 s `Soulseek login failed: The server rejected login attempt: INVALIDPASS` na stderr i kod `1`. To błąd trwały, bez ponawiania w tle.
- ✅ Zakładanie konta Soulseek przez Sockseek. Pierwsze logowanie na wolną nazwę zakłada konto bez żadnego osobnego komunikatu (kod `0`). Zajęta nazwa z innym hasłem daje to samo `INVALIDPASS` co złe hasło, bo serwer tych przypadków nie odróżnia.
- ✅ Pytanie zapory Windows i pobieranie po odmowie. Domyślny port 49998 był zajęty przez inny program i Sockseek skończył się od razu błędem `Failed to start listening`. Z `--no-listen` okno zapory się nie pojawiło, w zaporze nie powstała żadna reguła, a pobieranie działa. Decyzja właściciela: zawsze `--no-listen` (sekcja 7), opcjonalny port w roadmapie (sekcja 11).
- ✅ Brak hasła w wyjściu `-v` przy prawdziwym logowaniu. Hasło pojawiło się 0 razy w stdout, stderr i `--log-file`. Login pojawia się w trzech liniach na stderr.
- ✅ (z kodu i na żywo przy pierwszym pobraniu kolekcji testowej, `docs/sockseek-notes.md` sekcja 11.1) Pobranie bez żadnego transferu od użytkownika Sockseek przerwał po 30 s (`--max-stale-time`), a najdłuższa cisza na wyjściu w całym pobraniu to 15,1 s, więc `idle_timeout_minutes` zostaje na 10 minutach. Wcześniejszy stan: Czy Sockseek wypisuje cokolwiek, gdy pobieranie czeka w kolejce u innego użytkownika. Z kodu 3.0.5: pobieranie bez zmiany stanu i bez postępu przez `--max-stale-time` (domyślnie 30 s) jest anulowane, a Sockseek przechodzi do kolejnego kandydata i wypisuje nowe `download_start`. Cisza przy kolejce trwa więc najwyżej około 30 s na kandydata, a `idle_timeout_minutes` zostaje na 10 minutach. W sprawdzeniu na 4 utworach kolejka się nie zdarzyła (najdłuższa cisza 9,7 s, przy czekaniu na wolne miejsce wyszukiwania), więc obserwację na żywo powtarzamy przy pierwszym pobraniu kolekcji testowej.

**Zweryfikowane ręcznie na koniec fazy 2** (rekordbox, właściciel, szczegóły w `docs/rekordbox-checks.md`):

- ✅ Czy rekordbox poprawnie importuje `.m3u8` ze ścieżkami względnymi i znakami spoza ASCII. Tak: obie playlisty zaimportowane, 15 utworów z playlisty testowej właściciela bez żadnego brakującego, nazwy z polskimi znakami poprawne. Ścieżki bezwzględne nie są potrzebne.
- ✅ Czy rekordbox pokazuje okładkę osadzoną w WAV i AIFF tak samo jak we FLAC. AIFF tak, WAV nie (znane ograniczenie, sekcja 8).
- ✅ Które pola tagów rekordbox pokazuje. FLAC i AIFF: okładka, gatunek, komentarz, rok, numer utworu, album, artysta, polskie znaki. WAV: gatunek, komentarz, rok, album, artysta i tytuł, ale numer utworu pokazuje jako 0, a okładki nie ma.
- ✅ Czy rekordbox czyta teksty RIFF INFO zapisane w UTF-8. Tak, polskie znaki w WAV-ie są poprawne.
- ✅ Czy przeładowanie tagów (`Reload Tag`) odświeża pola po `retag`. Tak, gatunek i komentarz zmieniły się we wszystkich trzech formatach.
- ⏳ Czy rekordbox pokazuje artystę albumu (`ALBUMARTIST` we FLAC-u, `TPE2` w AIFF). Kolumna Album Artist nie została sprawdzona.

## 13. Kroki ręczne

Kroki 1-4 robi każdy użytkownik, prowadzi go kreator pierwszego uruchomienia. Kroki 5-7 dotyczą tylko właściciela projektu.

1. Tylko dla źródeł Spotify (konto i URL), CSV tego nie wymaga: na https://developer.spotify.com/dashboard utworzyć aplikację (Web API), dodać Redirect URI `http://127.0.0.1:43821/callback`, skopiować Client ID.
2. Konto Soulseek: w kreatorze wybrać „create a new account” i wpisać wymyśloną przez siebie nazwę (hasło wygeneruje i zapamięta aplikacja) albo „use my own account” i podać nazwę i hasło istniejącego konta. Zalecane osobne konto tylko dla aplikacji.
3. Cudze playlisty, które mają być pobierane, skopiować do własnej playlisty w Spotify albo dostarczyć jako CSV.
4. Wskazać katalog biblioteki na dysku wewnętrznym.
5. Na maszynie budującej zainstalować Inno Setup 6 (https://jrsoftware.org/isdl.php). Build: `powershell -ExecutionPolicy Bypass -File scripts\build.ps1`.
6. Licencja wybrana: GPL-3.0-or-later (D-203), pełny tekst w `LICENSE`. Przed publikacją: ustawić w czystym repozytorium tożsamość gita na nick i adres noreply z GitHuba, odświeżyć migawkę i powtórzyć skan danych osobistych (D-202), wpisać link do wpłat w obu README, podmienić `REPO_URL` w `src/neonseek/project.py` na adres repozytorium (D-214), zbudować paczkę od nowa i oznaczyć wydanie w publicznym repozytorium tagiem `v<wersja>`.
7. Przed publikacją przeczytać Spotify Developer Policy, warunki AGPL-3.0 Sockseeka i zasady serwera Soulseek, które zakazują łączenia się automatycznym klientom i skryptom (`docs/sockseek-notes.md`, sekcja 11.1). Ten plan nie jest poradą prawną.
