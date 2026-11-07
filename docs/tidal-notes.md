# TIDAL jako źródło list utworów: strona osadzania

Sprawdzone na żywo 2026-11-04 (faza 4c, przed implementacją). Z TIDAL-a bierzemy wyłącznie listy utworów i metadane, audio dalej tylko z Soulseeka. Konkretne linki testowe, nazwy playlist, wykonawców i kont nie trafiają do repozytorium, zapisane strony w testach mają wymyślone dane (D-243). Decyzje: D-238 do D-245.

## Budowa strony

- Adresy: `https://embed.tidal.com/playlists/<uuid>`, `/albums/<id>`, `/tracks/<id>`. Bez konta i bez klucza. Odpowiedź `text/html`, gotowa z serwera. Skrypt strony (`app-*.js`) nie dociąga dalszych pozycji.
- Kraj wynika z adresu IP (`window.tidalCountryCode='PL'`), parametr `?countryCode=` nic nie zmienia.
- Dwa źródła danych na stronie listy:
  - HTML: `<list-item product-id="<id>" product-type="track|video">` ze slotami `index`, `title`, `artist` (jeden `<a href="https://tidal.com/artist/<id>">` na wykonawcę), `<time slot="duration">m:ss</time>` i odznakami (`explicit-badge`, `video-badge`).
  - JSON w `<script>`: `window.tidalMediaSessionMetadata = {"<trackId>": {"title", "artist", "album", "artwork": [{sizes, src}]}}`. `artist` to jeden napis z przecinkami (niejednoznaczny dla nazw z przecinkiem), `artwork` od 80x80 do 1280x1280 (JPEG).
- **Tytuł z wersją jest tylko w HTML**: slot `title` ma np. `"<utwór> (<wykonawca> Remix)"` albo `"<utwór> (Instrumental)"`, a `title` w JSON tylko sam utwór. Tytuł bierzemy z HTML, album i okładkę z JSON (po `product-id`).
- Na stronie albumu slot `artist` pomija głównego wykonawcę albumu (są tylko goście). Główny wykonawca jest w `<span class="media-artist">` nagłówka. Na stronie playlisty slot `artist` ma wszystkich wykonawców, a `media-artist` w nagłówku to stały napis „Artist”.
- Nazwa listy: `<h1 class="media-album">`, okładka listy: `srcset` obrazka `cover-art` (160 do 1080 px).
- Encje HTML (`&#39;`, `&#x2F;`, `&#x3D;`, `&amp;`) trzeba dekodować.
- Nie ma: ISRC, wersji jako osobnego pola, czasu dokładniejszego niż sekunda, daty wydania, liczby wszystkich pozycji. Czas powyżej godziny widać jako minuty (np. `26:00`).
- Pojedynczy utwór: `<h1 class="media-title">`, wykonawcy w `media-artist`, czas w sekundach w `<tidal-duration-time>`, album i okładka w JSON. Lista `list-item` jest pusta.
- Nieznane id albumu, playlisty albo utworu: HTTP 404 i strona błędu (`embed-player--showing-error`). Nieobsługiwany rodzaj (`/mixes/...`): HTTP 404 z XML `NoSuchKey`.
- Wideo w playliście: `product-type="video"` i slot `video-badge`, w JSON też obecne. Playlista złożona z samych teledysków daje 0 utworów.

## Limit 50

- **Strona osadzania podaje najwyżej 50 pozycji**, bez stronicowania. `?offset=`, `?page=`, `?limit=`, `?itemsOffset=` nic nie zmieniają (ta sama pierwsza pozycja, 50 pozycji).
- Sprawdzone: publiczne playlisty z ok. 950 pozycjami (według relacji użytkowników) dają 50, album ze 100 utworami daje 50. Krótsze listy (14, 16, 20, 24, 28 pozycji) są pełne.
- Strona nie podaje liczby wszystkich pozycji (ani w HTML, ani w JSON). Strona `tidal.com/playlist/<uuid>` to powłoka aplikacji z `og:title`, `og:description` i `og:image`, bez liczby utworów. oEmbed (`oembed.tidal.com/?url=`) zwraca tylko iframe.
- Obcięcia nie da się więc stwierdzić na pewno, tylko po liczbie: 50 pozycji oznacza „lista może być dłuższa” (D-240). Lista z dokładnie 50 utworami da ostrzeżenie na wyrost.
- Około 40 zapytań w kilka minut przeszło bez błędu. Limit zapytań nie jest znany.

## Sprawdzanie po zmianie strony

`uv run python scripts/tidal_embed_check.py <link> [<link> ...]` czyta podane linki na żywo i pokazuje, co parser z nich wyciągnął (liczba pozycji, wideo, pierwsze utwory, braki pól). Linków nie zapisujemy w repozytorium. Gdy TIDAL zmieni stronę, nową zapisaną stronę do testów robimy tak samo jak dotąd: znaczniki z prawdziwej strony, dane wymyślone.
