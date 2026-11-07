# Ręczne sprawdzenia w rekordboxie (koniec fazy 2)

Sprawdzenia z PLAN.md (sekcja 10, faza 2, i sekcja 12, „Czeka na koniec fazy 2”). Wyniki wpisujemy tutaj, a pola tagów, których rekordbox nie pokazuje, poprawiamy w tabeli z sekcji 8 planu i w kodzie (`pipeline/postprocess/tag.py`).

## Materiał

- **Biblioteka:** `<biblioteka>\` (folder biblioteki właściciela), playlista `playlists\<playlista testowa>.m3u8` z 15 plikami FLAC (pierwsze prawdziwe pobranie, 2026-10-08), gatunek kolekcji `Hardcore Techno`.
- **Zestaw testowy:** `<biblioteka>-rekordbox-check\`, zbudowany przez `scripts/make_rekordbox_check.py` tym samym kodem okładki i tagów co pobieranie. Trzy pliki z tonem 440 Hz, 20 s, 44,1 kHz/16 bit, w nazwach polskie znaki. Okładki w różnych kolorach: FLAC różowy, WAV turkusowy, AIFF żółty. Każdy plik ma wszystkie pola z tabeli w sekcji 8:

| Pole w rekordboxie | Oczekiwana wartość |
|---|---|
| Title | `Zażółć gęślą jaźń (FLAC)`, `(WAV)` albo `(AIFF)` |
| Artist | `NeonSeek Test, Artystka Ż` |
| Album | `Album Ąę Test` |
| Album Artist (jeśli rekordbox ma taką kolumnę) | `NeonSeek Album Artist` |
| Year | `2021` |
| Track Number | `7` |
| Disc Number (zakładka Info) | `2` (WAV nie ma tego pola w RIFF INFO) |
| Genre | `Hardcore Techno` |
| Comments | `komentarz ąęś` |

## Kroki

1. **Playlista ze ścieżkami względnymi.** `File` > `Import` > `Import Playlist`, plik `<biblioteka>\playlists\<playlista testowa>.m3u8`. Oczekiwane: 15 utworów w kolejności ze Spotify, żaden nie jest oznaczony jako brakujący plik.
2. **Playlista ze znakami spoza ASCII.** Tak samo plik `<biblioteka>-rekordbox-check\playlists\NeonSeek test ż.m3u8`. Oczekiwane: playlista `NeonSeek test ż` z trzema utworami, polskie znaki w nazwach poprawne.
3. **Okładki.** W playliście z kroku 2 kolumna z okładką albo zakładka `Artwork` w oknie informacji o utworze: FLAC różowy, WAV turkusowy, AIFF żółty. W playliście z kroku 1 okładki uploaderów (JPEG).
4. **Pola tagów.** W playliście z kroku 2 włącz kolumny prawym przyciskiem na nagłówku listy: `Genre`, `Comments`, `Year`, `Track Number`, `Album`, a jeśli jest, `Album Artist`. Dla każdego z trzech formatów zapisz, które pola są widoczne, które puste, a które mają popsute polskie znaki. Szczególnie WAV: rekordbox czyta w nim tylko RIFF INFO zapisane w UTF-8.
5. **Gatunek w prawdziwej kolekcji.** W playliście z kroku 1 kolumna `Genre`: wszystkie 15 utworów `Hardcore Techno`.
6. **Przeładowanie tagów po retag.** W terminalu w katalogu repo: `uv run python scripts/make_rekordbox_check.py --out "<biblioteka>-rekordbox-check" --retag` (zmienia gatunek na `Industrial Techno` i komentarz na `po retag: łódź`). Najpierw sprawdź, że rekordbox nadal pokazuje stare wartości. Potem zaznacz trzy utwory testowe, prawy przycisk, `Reload Tag`. Oczekiwane: nowe gatunek i komentarz we wszystkich trzech formatach.

Retag sprawdzamy na zestawie testowym, a nie na prawdziwej kolekcji, bo wyczyszczenie komentarza kolekcji nie usuwa go z plików (D-066), więc testowy komentarz zostałby w prawdziwych plikach.

Po sprawdzeniach zestaw testowy i jego playlistę można usunąć z rekordboxa i z dysku.

## Wyniki (2026-10-08, sprawdził właściciel)

Wersja rekordboxa nie została zapisana. Disc Number w zakładce Info nie był osobno sprawdzany.

| Krok | FLAC | WAV | AIFF |
|---|---|---|---|
| 1. `.m3u8` ze ścieżkami względnymi | ✅ 15 utworów, żadnego brakującego | | |
| 2. `.m3u8` ze znakami spoza ASCII | ✅ | ✅ | ✅ |
| 3. Okładka | ✅ | ❌ brak | ✅ |
| 4. Title / Artist / Album | ✅ | ✅ | ✅ |
| 4. Album Artist | ⏳ niesprawdzone | brak pola | ⏳ niesprawdzone |
| 4. Year | ✅ | ✅ | ✅ |
| 4. Track Number | ✅ | ❌ pokazuje 0 | ✅ |
| 4. Genre / Comments | ✅ | ✅ | ✅ |
| 4. Polskie znaki | ✅ | ✅ (RIFF INFO w UTF-8 działa) | ✅ |
| 5. Gatunek w kolekcji | ✅ | | |
| 6. `Reload Tag` po retag | ✅ | ✅ | ✅ |

Wnioski (decyzje właściciela D-081 do D-083, `docs/decisions.md`):

- Brak okładki i numeru utworu w WAV-ie to znane ograniczenie rekordboxa: ID3 w WAV-ie jest dla niego niewidoczne, a `ITRK` z RIFF INFO pokazuje jako 0. Opis trafia do README (faza 4), a docelowym rozwiązaniem jest konwersja z roadmapy (PLAN.md, sekcja 11, punkt 3).
- Przebieg 2 preferuje AIFF przed WAV-em (`--pref-format aiff,aif`), bo AIFF niesie w rekordboxie okładkę i komplet tagów.
- Znaczniki [WERYFIKUJ] w tabeli pól (PLAN.md, sekcja 8) zostały tylko przy artyście albumu (`ALBUMARTIST`, `TPE2`).
