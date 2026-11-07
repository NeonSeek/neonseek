# NeonSeek

Pełna specyfikacja: [PLAN.md](PLAN.md). Wyniki rozpoznania Sockseeka: [docs/sockseek-notes.md](docs/sockseek-notes.md).

## Zasady (PLAN.md, sekcja 0)

- Idź fazami z sekcji 10, po kolei. Kolejna faza dopiero wtedy, gdy kryteria akceptacji poprzedniej przechodzą.
- **[WERYFIKUJ]** = nie zgadywać. Sprawdzić na przypiętej wersji narzędzia (`sockseek --help`, realna odpowiedź API) i zapisać wynik w `docs/`.
- Core nie importuje Qt (pilnuje tego `tests/test_architecture.py`). GUI to cienka warstwa na core. Każda funkcja dostępna z GUI musi dać się uruchomić z CLI.
- Testy nigdy nie łączą się z prawdziwym Soulseekiem ani Spotify. Soulseek tylko przez `--mock-files-dir`, Spotify tylko przez zapisane fixture'y JSON.
- Nie podnosić limitów wyszukiwania Sockseeka ponad domyślne (34 wyszukiwania na 220 s). Serwer banuje na 30 minut.
- Sekrety (hasło Soulseek, refresh token Spotify) tylko w Windows Credential Manager przez `keyring`. Nigdy w repo, w logach ani w plikach konfiguracyjnych. Nie logować linii poleceń Sockseeka i wycinać hasło z każdego zapisywanego logu.
- Komentarze w kodzie po angielsku, proste, małymi literami.
- Ikony UI jako SVG (barwione w kodzie pod motyw), ikona exe jako `.ico`, okładki w formacie, w jakim przyszły ze źródła.
- Aplikacja ma być gotowa do rozdania innym: żadnych ścieżek, kont ani identyfikatorów właściciela w kodzie i paczce. Konfiguracja przez UI, dane każdego użytkownika w jego profilu Windows (`%APPDATA%\NeonSeek\`).
- Każdy użytkownik podaje własne konto Soulseek i własny Spotify Client ID. Żadnego wbudowanego, wspólnego Client ID.
- Interfejs po angielsku i po polsku (ustawienie `language`, D-163). Każdy tekst okna, komunikat, podpowiedź, kreator i tekst CLI (także `--help`) przez `tr()`, liczba mnoga przez `ntr()`. Tłumaczenia to dane w core (`src/neonseek/locales/pl.json`), bez Qt. Nowy tekst w kodzie dostaje od razu polskie tłumaczenie (`uv run python scripts/i18n_catalog.py` dopisuje go do katalogu). Polski naturalny, nie słowo w słowo, a nazwy własne i techniczne (Soulseek, Spotify, rekordbox, FLAC, Reload Tag), polecenia CLI i nazwy ustawień zostają bez tłumaczenia.
- Instrukcja obsługi (D-164): `docs/manual/en.md` i `docs/manual/pl.md`, te same rozdziały w obu. Zmiana opisanej funkcji zmienia obie wersje w tym samym kroku.
- Komunikaty błędów mówią, co zrobić dalej. Użytkownik nie jest programistą i nie ma terminala.
- Pliki biblioteki usuwa tylko użytkownik: na wyraźne polecenie, po potwierdzeniu z liczbą plików, wyłącznie do Kosza Windows i tylko pliki, które aplikacja sama tam umieściła. Na dysku bez Kosza odmowa. Jedyny wyjątek (D-153): przy przenoszeniu biblioteki na inny dysk plik źródłowy jest usuwany trwale, ale dopiero gdy kopia jest sprawdzona (ten sam rozmiar co oryginał), a okno pytające o przeniesienie mówi to użytkownikowi. Usuwanie i przenoszenie plików nie działa w trakcie pobierania. Testy tylko na atrapie Kosza.
- Licencja projektu: GPL-3.0-or-later (D-203), otwarty kod na GitHubie. Każdą nową zależność zgłaszamy właścicielowi z licencją, musi być zgodna z GPL-3.0-or-later i trafia do `THIRD_PARTY_LICENSES`. Sockseek (AGPL-3.0) zostaje osobnym programem uruchamianym jako podproces.
- Gałąź `open-source` i tagi `open-*` to archiwum wersji z otwartym kodem: nie wolno ich zmieniać ani usuwać.

## Decyzje

- Drobne decyzje implementacyjne podejmuj sam i zapisuj w [docs/decisions.md](docs/decisions.md) z jednym zdaniem uzasadnienia.
- Kieruj się czterema zasadami:
  1. Nie szukaj drugi raz czegoś, czego wynik już znasz.
  2. Przejściowa porażka nie może trwale zablokować utworu.
  3. Aplikacja nie usuwa plików sama: tylko na wyraźne polecenie, po potwierdzeniu z liczbą plików, zawsze do Kosza Windows (nigdy trwale) i tylko pliki, które sama umieściła w bibliotece. Jedyny wyjątek (D-153): przy przenoszeniu biblioteki na inny dysk plik źródłowy jest usuwany trwale, ale dopiero gdy kopia jest sprawdzona (ten sam rozmiar co oryginał), a okno pytające o przeniesienie mówi to użytkownikowi.
  4. Stan w bazie zawsze pozwala wznowić pracę.
- Zatrzymuj się i pytaj tylko wtedy, gdy decyzja zmienia zakres fazy, bezpieczeństwo danych albo przeczy planowi.
- Na końcu każdej fazy pokaż listę podjętych decyzji.

## Komendy

```powershell
uv sync                                   # środowisko (python 3.12 z .python-version)
uv run pytest                             # testy
uv run ruff check .                       # lint
uv run ruff format .                      # formatowanie (pliki .md są wyłączone)
uv run neonseek-gui [--data-dir <katalog>] # okno, domyślnie dane z %APPDATA%\NeonSeek

# zrzut okna albo dialogu bez ekranu (motywy, układ), np. --dialog settings|wizard|csv=<plik>
uv run python scripts/gui_screenshot.py --data-dir <katalog> --out shot.png [--theme ice] [--lang pl]

# zrzuty wyglądu przy skali Windows 100/125/150% (kroki kreatora, główne okno, Ustawienia,
# About, lista licencji), tylko JPG, poza repo (domyślnie %TEMP%), report.txt mówi, czy okna się mieszczą
uv run python scripts/ui_screenshots.py [--out <katalog poza repo>] [--scales 100,125,150] [--lang en,pl]

# przypięty sockseek (wersja i sha256 w scripts/sockseek-pin.json) do vendor/sockseek/
powershell -ExecutionPolicy Bypass -File scripts\fetch_sockseek.ps1 [-Force]

# katalog tłumaczeń: dopisuje nowe teksty z kodu, pokazuje brakujące tłumaczenia
uv run python scripts/i18n_catalog.py [--check]

# instrukcja: zrzuty ekranu (JPEG, oba języki, dane przykładowe) i PDF-y do docs/manual/pdf/
uv run python scripts/manual_screenshots.py
uv run python scripts/build_manual.py

# testy dymne: TYLKO na komputerze testowym (instaluje i odinstalowuje), opis w scripts/smoke-test/README.md
# powershell -ExecutionPolicy Bypass -File scripts\smoke-test\smoke-test.ps1 -TestMachine

# spike fazy 0: sockseek tylko na --mock-files-dir, wymaga ffmpeg w PATH
uv run python scripts/spike_sockseek.py --work <pusty katalog> [--only main rank1 ...]
```

Zmiana wersji Sockseeka: zaktualizować `scripts/sockseek-pin.json`, uruchomić `fetch_sockseek.ps1 -Force`, powtórzyć spike i uaktualnić `docs/sockseek-notes.md` przed zmianą adaptera.
