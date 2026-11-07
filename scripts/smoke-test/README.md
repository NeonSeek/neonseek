# Testy dymne NeonSeek

Automatyczne sprawdzenie zainstalowanego NeonSeeka na **komputerze testowym** (D-255). Nie jest częścią instalatora ani wersji portable.

Skrypt instaluje, aktualizuje i odinstalowuje NeonSeeka bezpośrednio w systemie, na którym działa, więc rusza tylko z jawnym przełącznikiem `-TestMachine`. Bez niego wypisuje tylko plan i kończy pracę. Nie uruchamiaj go na komputerze, na którym używasz NeonSeeka na co dzień.

Skrypt jest pisany pod Windows PowerShell 5.1 (`powershell.exe`, jest w każdym Windows 10 i 11) z trybem ścisłym. Każda lista, którą liczy, przechodzi przez `@(...)`, bo w 5.1 pojedynczy obiekt i pusty wynik nie mają `.Count` (D-261).

## Co robi

| Krok | Co sprawdza |
|---|---|
| Pliki testu | Oba instalatory, plik linków i CSV są na miejscu, w pliku linków nie zostały znaczniki `<...>` (ani w linkach, ani w liczbach). |
| Konto Soulseek zapisane | `cmdkey /list` pokazuje wpisy `soulseek-username` i `soulseek-password` NeonSeeka. Hasło nie jest czytane. Bez konta skrypt staje przed instalacją i mówi, żeby raz przejść kreator. |
| Cicha instalacja 0.1.0 | `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS`, kod wyjścia 0, wersja na liście programów i z `neonseek-cli --version`. Działa na czystym systemie i na istniejącą instalację. Folder: gdy NeonSeek jest już zainstalowany, instalator zawsze bierze folder tej instalacji, inaczej `%LOCALAPPDATA%\Programs\NeonSeek`. Plan na starcie podaje faktyczny folder. |
| Biblioteka i konto w CLI | Ustawia folder biblioteki testu i sprawdza `soulseek status`. |
| Import CSV, kolekcja odznaczona | `sample.csv` (3 wymyślone utwory) daje kolekcję z 3 utworami, nie zaznaczoną. |
| Link: … | Każdy link z pliku linków: `neonseek add`, potem z `collections --json` liczba pozycji listy (`entries`, z powtórzeniami) i różnych utworów (`tracks`: ten sam utwór kilka razy na liście to jeden utwór). Wynik pokazuje obie liczby. Oczekiwane w pliku linków: `tracks` (dokładnie tyle różnych utworów), `min_tracks` (co najmniej tyle), `entries` (dokładnie tyle pozycji) albo `more_entries_than` (ponad tyle pozycji). |
| Pobranie 1 utworu z TIDAL-a | Pierwszy utwór kolekcji z `download_one` (`download --limit 1 --yes`), na koncie zapisanym przez NeonSeeka: jedno wyszukiwanie, domyślne limity. Wymaga pliku audio w bibliotece i licznika pobranych 1. Limit 20 minut (`-DownloadMinutes`). |
| Aktualizacja do 0.1.1 przy uruchomionej aplikacji | Uruchamia `NeonSeek.exe`, czeka na okno, instaluje 0.1.1 po cichu. Instalator ma zamknąć aplikację, wersja musi być 0.1.1, kolekcje zostają. |
| Odinstalowanie, dane i biblioteka zostają | Deinstalator po cichu. Program znika z dysku i z listy programów, a dane testu, biblioteka, `%APPDATA%\NeonSeek` i konto w Menedżerze poświadczeń zostają bez zmian. |

Kroki zależne od instalacji dostają POMINIĘTO, gdy instalacja się nie udała.

Dane i biblioteka testu leżą w osobnym folderze (`--data-dir`), więc Twoje dane w `%APPDATA%\NeonSeek` na komputerze testowym nie są używane, tylko porównane przed i po teście. Wyjątkiem jest konto Soulseek: Menedżer poświadczeń jest jeden dla całego konta Windows, więc test loguje się na konto zapisane przez kreator.

## Przygotowanie (raz)

1. Na komputerze testowym zainstaluj NeonSeeka, przejdź kreator i w kroku 1 zapisz osobne konto testowe. Skrypt używa tego konta, nie pyta o hasło i niczego nie zapisuje.
2. Skopiuj na komputer testowy jeden folder z:
   - `smoke-test.ps1`, `links.public.json`, `sample.csv` (ten folder z repozytorium),
   - `NeonSeek-Setup-0.1.0.exe` (z `dist\`) i `NeonSeek-Setup-0.1.1.exe` (z `dist\test-0.1.1\`).
3. Zamknij NeonSeeka.

## Uruchomienie

```powershell
powershell -ExecutionPolicy Bypass -File smoke-test.ps1 -TestMachine
```

Skrypt wypisuje plan i czeka 10 sekund, Ctrl+C przerywa. Opcje:

- `-SetupDir <folder>`: gdzie leżą instalatory (domyślnie obok skryptu),
- `-OldVersion 0.1.0 -NewVersion 0.1.1`: wersje instalatorów,
- `-LinksFile <plik>`: własne linki (niżej),
- `-WorkDir <folder>`: dane, biblioteka i wyniki testu (domyślnie `%USERPROFILE%\NeonSeek-smoke\<data-godzina>`),
- `-DownloadMinutes 20`: limit pobrania jednego utworu,
- `-SkipUninstall`: NeonSeek zostaje zainstalowany.

Sprawdzenie samego skryptu, na dowolnym komputerze:

```powershell
powershell -ExecutionPolicy Bypass -File smoke-test.ps1 -SelfTest
```

`-SelfTest` uruchamia tylko funkcje oceniające wyniki (lista kolekcji z JSON-a, pliki audio, nowe kolekcje, linki, CSV, pobranie, odinstalowanie, licznik błędów, folder instalacji) na przykładowych danych: pusta lista, jeden obiekt i wiele elementów. Niczego nie instaluje, nie uruchamia NeonSeeka i nie zapisuje plików. Kod wyjścia 0, gdy wszystko się zgadza. To samo robi test w repozytorium.

## Wynik

W `<WorkDir>\results\`:

- `summary.txt`: tabela Krok / Wynik (OK, BŁĄD, POMINIĘTO) / Szczegóły, ta sama co na ekranie,
- `smoke-test.log`: każde polecenie z pełnym wyjściem i kodem wyjścia, w UTF-8 (`neonseek-cli` pisze do potoku i do pliku zawsze w UTF-8, D-260),
- `setup-0.1.0.log`, `setup-0.1.1.log`: logi instalatora Inno Setup.

Kod wyjścia skryptu: 0, gdy nie ma żadnego BŁĘDU, 1, gdy jest, 2 bez `-TestMachine`.

Hasło Soulseek nie trafia do żadnego z tych plików: skrypt go nie czyta, a NeonSeek i Sockseek go nie wypisują (NeonSeek wycina je z logów Sockseeka). Nazwa konta (`soulseek status`) jest w logu, bo nie jest tajna.

Po teście NeonSeek jest odinstalowany (chyba że użyjesz `-SkipUninstall`). Folder testu zostaje, usuń go ręcznie, gdy nie jest już potrzebny.

## Linki

`links.public.json` to publiczne odpowiedniki linków z ręcznego testu właściciela (Daft Punk: albumy i oficjalne listy, sprawdzone na żywo 2026-11-07):

| Rodzaj | Link | Oczekiwane |
|---|---|---|
| Apple Music, album | Discovery, `music.apple.com/us/album/discovery/697194953` | 14 |
| Apple Music, playlista | Daft Punk Essentials, `pl.74657640b88c4587a426160f7441de46` | co najmniej 10 |
| YouTube, playlista ponad 200 | przesłane filmy oficjalnego kanału, `list=UU_kRDKYrUlrbtrSiyu5Tflg` | ponad 200 pozycji listy (na drugim komputerze 353 pozycje i 293 różne utwory, bo powtórzone filmy scalają się w jeden utwór) |
| SoundCloud, zestaw | Discovery, `daftpunkofficialmusic/sets/discovery-8` | 14 |
| TIDAL, playlista | Daft Punk Essentials, `abd35446-6a67-4f2b-9f92-0636742c63cb` | co najmniej 20 |
| TIDAL, album | Homework, `tidal.com/album/161010` | 16, z niego pobierany jest 1 utwór |

Playlisty zmieniają kuratorzy, a kanał dostaje nowe filmy, więc mają minimum zamiast dokładnej liczby. Przy YouTube liczą się pozycje listy, bo sprawdzane jest czytanie listy ponad 200 pozycji (dzielenie na strony), a liczba różnych utworów zależy od powtórzeń.

Linki z Twojego ręcznego testu nie leżą w repozytorium (D-243). Skopiuj `links.example.json` **poza repozytorium**, w miejsce znaczników `<...>` wpisz linki i oczekiwane liczby (przy liście, w której ten sam utwór może być kilka razy, `entries`, czyli pozycje) i uruchom skrypt z `-LinksFile <kopia>`.

## Co zostaje ręcznie

- **SmartScreen.** Skrypt robi `Unblock-File` na kopiach instalatorów, bo okna SmartScreen nie da się kliknąć ze skryptu. Pierwsze uruchomienie pobranego instalatora sprawdź ręcznie: „Więcej informacji”, potem „Uruchom mimo to”, i czy nazwa pliku się zgadza.
- **Pierwsze logowanie do Spotify i sync.** Logowanie odbywa się w przeglądarce na koncie Spotify, a test nie może łączyć się ze Spotify. Ręcznie: własny Client ID w Ustawieniach, ZALOGUJ DO SPOTIFY, sync playlist i polubionych.
- **Ocena wyglądu.** `uv run python scripts/ui_screenshots.py` robi zrzuty JPG każdego kroku kreatora, głównego okna, Ustawień i About przy skali 100, 125 i 150%, poza repozytorium, z `report.txt` o mieszczeniu się na ekranie. Obejrzeć je trzeba samemu.
- **Na prawdziwym ekranie** (D-252): zmiana rozmiaru okien z rogu naraz w pionie i w poziomie oraz kreator na drugim komputerze (DALEJ aktywne zaraz po zapisaniu konta, licznik sekund przy sprawdzaniu).
