# Apple Music, YouTube i SoundCloud jako źródła list utworów: rozpoznanie

Sprawdzone na żywo 2026-10-20 (faza 4b, przed implementacją). Z tych źródeł bierzemy wyłącznie listy utworów, audio dalej tylko z Soulseeka. Konkretne linki testowe, nazwy playlist, kanałów i kont nie trafiają do repozytorium, zapisane odpowiedzi w testach mają wymyślone dane.

## Apple Music: album i pojedynczy utwór (iTunes Lookup API)

- `https://itunes.apple.com/lookup?id=<albumId>&entity=song&country=<kraj>`, bez klucza. Odpowiedź `text/javascript`, treść to JSON. Pierwszy wynik to `wrapperType: collection` (album, ma `trackCount`), dalej `wrapperType: track`.
- Pola utworu są wszystkie, o które chodzi: `trackName`, `artistName`, `collectionName`, `trackNumber`, `discNumber`, `trackTimeMillis`, `primaryGenreName`, `releaseDate`, `artworkUrl100`, do tego `trackId`, `trackCount`, `discCount`, `isStreamable`.
- Bez `limit` Lookup zwraca najwyżej 50 utworów, z `limit=200` najwyżej 200 (większy `limit` nic nie zmienia). Album z 294 utworami: 200 z Lookup, wszystkie 294 w HTML strony albumu (`serialized-server-data`, bez stronicowania).
- Lookup przyjmuje wiele id po przecinku (150 id w jednym zapytaniu: 150 wyników), więc brakujące utwory dużego albumu i utwory playlisty dostają te same pola co utwory albumu.
- Pojedynczy utwór: `lookup?id=<trackId>` zwraca jeden `track`. Link `/album/...?i=<trackId>` i `/song/<slug>/<trackId>`.
- Nieznane id: HTTP 200 i `resultCount: 0` (nie błąd HTTP).
- Nazwa albumu ma dopiski `" - EP"` i `" - Single"` (np. `collectionName: "<nazwa> - EP"`).
- Okładka: `artworkUrl100` kończy się na `100x100bb.jpg`, większy rozmiar przez podmianę tej części adresu (JPEG ze źródła).

## Apple Music: playlista (HTML strony)

- Strona ma dwa bloki: `<script type="application/ld+json" id="schema:music-playlist">` (`MusicPlaylist`) i `<script type="application/json" id="serialized-server-data">`.
- JSON-LD ma tylko nazwę utworu, adres i czas (`PT3M28S`), **bez wykonawcy**, więc źródłem jest `serialized-server-data`.
- `serialized-server-data`: `data[0].intent.$kind = "PlaylistDetailPageIntent"`, `data[0].data.sections[]`. Sekcja z `itemKind: "trackLockup"` ma utwory, nagłówek (`containerDetailHeaderLockup`) ma `title` i `trackCount`. Utwór: `title`, `artistName`, `duration` (ms), `contentDescriptor.identifiers.storeAdamID` (id utworu), `tertiaryLinks[0].title` (album, z dopiskiem `- Single`/`- EP`), `artwork.dictionary.url` (szablon `{w}x{h}bb.{f}`), `isDisabled`.
- **Strona podaje najwyżej 300 utworów.** Redakcyjna playlista z 500 utworami: 300 w sekcji, a `trackCount`, `numTracks` w JSON-LD, `music:song_count` i stopka mówią „300”. Prawdziwa liczba nie występuje nigdzie poza opisem tekstowym.
- Obcięcie widać po `data[0].data.nextIntent` z `$kind: "PlaylistPaginationIntent"` i `url: /v1/catalog/<kraj>/playlists/<id>/tracks?...&offset=300`. Przy playlistach do 300 utworów tego klucza nie ma.
- Dalsze strony: `api.music.apple.com` i `amp-api.music.apple.com` zwracają 401 bez tokenu deweloperskiego. `?offset=` w adresie strony nic nie zmienia, `embed.music.apple.com` zwraca pustą powłokę. Tokenu strony nie ma w HTML ani w głównym pakiecie JS.
- Niesprawdzone: playlista użytkownika (`pl.u-...`), sprawdzone były tylko playlisty redakcyjne (`pl.<hex>`).

## yt-dlp

- Wersja 2026.8.19, licencja Unlicense (domena publiczna). Wymaganych zależności brak. Wszystko inne (`brotli`, `certifi`, `mutagen`, `pycryptodomex`, `requests`, `urllib3`, `websockets`, `yt-dlp-ejs`, `curl-cffi`, `deno`) to opcjonalne dodatki, których nie instalujemy. W 1046 plikach `.py` pakietu nie ma nagłówków innych licencji.
- Domyślnie zapisuje pamięć podręczną w `~/.cache/yt-dlp` (np. `soundcloud/client_id.json`), czyli poza `%APPDATA%\NeonSeek`. Trzeba podać `cachedir`.

### YouTube

- Playlista, `extract_flat: "in_playlist"`: 218 z 218 pozycji w 3 s (kilka zapytań). Pola pozycji: `id`, `url`, `title`, `channel`, `uploader` (= `channel`), `duration` (s), `view_count`, `channel_id`. Usunięty lub niedostępny film: pozycja jest, ale `title`, `channel` i `duration` to `null`.
- W liście płaskiej nie ma `artist`/`track`. Te pola (z metadanych YouTube Music) są tylko przy pełnym odczycie pojedynczego filmu.
- Na 218 pozycji: 157 tytułów ma `" - "`, 9 kanałów kończy się na `" - Topic"` (tytuł = sam utwór, wykonawca = kanał bez dopisku), 70 tytułów ma dopiski w nawiasach (Official Video, Lyrics, Remastered, Live…), 50 nie ma ani `" - "`, ani kanału Topic. 13 pozycji ma ponad 20 min (miksy, sety na żywo).
- Pojedynczy film (`watch?v=`, `youtu.be/...?si=`): działa bez środowiska JS (tylko ostrzeżenie „No supported JavaScript runtime”, które dotyczy formatów audio/wideo). Z `process=False` są 3 zapytania i pola `title`, `channel`, `duration`, a często też `artists`, `track`, `album`, `release_year`.
- `watch?v=X&list=PL...`: yt-dlp domyślnie zwraca playlistę. `music.youtube.com/playlist?list=`: działa (przekierowanie na youtube.com, ostrzeżenie).
- Nieistniejąca playlista: `DownloadError` po 3 ponowieniach (404/400).

### SoundCloud

- Set, `extract_flat: "in_playlist"`: 8 z 8 pozycji, ale **bez tytułów i wykonawców**, tylko `id` i `url` (część to adres strony utworu, część to adres API `api-v2.soundcloud.com/tracks/<id>`).
- Pełny odczyt każdej pozycji (`extract_flat: False`, bez pobierania): 8 z 8 z polami, 35 zapytań w 7,8 s (ok. 4 na utwór, bo czyta też adresy strumieni).
- Ekstrakcja płaska + jedno zapytanie `api-v2.soundcloud.com/tracks?ids=<id,...>` przez ekstraktor SoundCloud yt-dlp (jego `client_id`) i `_extract_info_dict(..., extract_flat=True)`: 8 z 8 z tymi samymi polami, 11 zapytań w 1,2 s (większość to jednorazowe szukanie `client_id`). Używa wewnętrznych metod yt-dlp, które mogą się zmienić przy aktualizacji.
- Pola: `title`, `uploader` (wrzucający), `duration` (s, ułamek), `genres`, `artists` (z `publisher_metadata.artist`, gdy wydawca je podał, w próbce 6 z 8), w surowej odpowiedzi też `publisher_metadata.isrc`, `label_name`, `release_date`, `artwork_url`.
- Pojedynczy utwór: 5 zapytań, `title`, `uploader`, `duration`, `artists`. Ostrzeżenie „Original download format is only available for registered users” jest bez znaczenia (nie pobieramy).
- Nieistniejący set: `DownloadError` z `HTTP Error 404`.

## Wnioski z implementacji (faza 4b)

- `_extract_info_dict` yt-dlp przy okładce wysyła zapytanie HEAD o wersję `original` (osobno dla każdego utworu). Droga zbiorcza wycina więc okładkę z danych przed mapowaniem i sama buduje adres `-t500x500.jpg`. Cały set to wtedy 5 zapytań: strona `soundcloud.com` i pakiet JS (szukanie `client_id`, potem z pamięci podręcznej), `resolve`, jedno HEAD o miniaturę setu i jedno `tracks?ids=`.
- Strony Apple bez sluga (`/album/<id>`, `/playlist/<id>`, `/song/<id>`) przekierowują na pełny adres, a domyślny User-Agent httpx dostaje tę samą stronę co przeglądarka.
- Szczegóły decyzji: `docs/decisions.md`, D-227 do D-237.
