# Spotify Web API: wyniki fazy 1

Stan na 2026-10-07. Sprawdzone na realnym koncie właściciela, logowanie PKCE ze scope'ami z PLAN.md (sekcja 6.1), wyłącznie endpointy do odczytu. Zachowanie API zależy od wieku aplikacji deweloperskiej (PLAN.md, sekcja 3), dlatego są dwa warianty:

- **Starsza aplikacja** (główna aplikacja właściciela, założona przed lutym 2026, Spotify odroczył dla niej zmiany): rozdziały 1-6.
- **Nowa aplikacja** (założona teraz na tym samym koncie, taką dostanie każdy nowy użytkownik NeonSeek): rozdział 7, z porównaniem obu wariantów.

Obie aplikacje działają w Development Mode.

- Sondowanie: [scripts/capture_spotify.py](../scripts/capture_spotify.py). Zapisuje surowe odpowiedzi do wskazanego katalogu poza repo, bo zawierają dane osobowe.
- Fixture'y: [scripts/make_spotify_fixtures.py](../scripts/make_spotify_fixtures.py) pobiera mały zestaw odpowiedzi, anonimizuje je w pamięci i zapisuje do [tests/fixtures/spotify/legacy_app/](../tests/fixtures/spotify/legacy_app/) i [tests/fixtures/spotify/new_app/](../tests/fixtures/spotify/new_app/). Testy Spotify przechodzą na obu zestawach. Pochodzenie i zmiany każdego pliku opisuje [README fixture'ów](../tests/fixtures/spotify/README.md).
- Konto testowe: kilkadziesiąt playlist na liście (głównie własne, kilka obserwowanych playlist innych użytkowników, żadnej współtworzonej) i kilkaset Polubionych.

## 1. Logowanie

- Authorization Code z PKCE i Redirect URI `http://127.0.0.1:43821/callback` działa bez client secret. Lokalny serwer wstaje tylko na czas logowania. Zapytania `/favicon.ico` od przeglądarki są ignorowane.
- Token endpoint zwraca `access_token` z `expires_in = 3600` i `refresh_token` (131 znaków). Refresh token trafia do Windows Credential Manager (usługa `NeonSeek`, nazwa `spotify-refresh-token`). Access token jest tylko w pamięci.
- **Rotacja:** odświeżenie przez `grant_type=refresh_token` zwróciło ten sam refresh token, co sprawdzono porównaniem skrótu przed i po. Kod i tak zapisuje każdy nowy, jeśli przyjdzie, bo przy PKCE rotacja jest dozwolona.
- Każde polecenie CLI to nowy proces, który odświeża token z Credential Manager. Ponowne logowanie po restarcie nie jest potrzebne.

## 2. Kształty odpowiedzi

Wszystkie listy to obiekt stronicowania `{href, items, limit, next, offset, previous, total}`.

| Endpoint | Status | Co zawiera |
|---|---|---|
| `GET /me` | 200 | `id`, `display_name`, `images`, `followers`, `external_urls`, `href`, `type`, `uri` oraz nowe pole `account_id`. Bez `country`, `product` i `email`, bo nie prosimy o te scope'y |
| `GET /me/playlists` | 200 | Każda playlista, własna i cudza, ma **oba** pola `items: {href, total}` i `tracks: {href, total}`. Tu jest tylko licznik, bez treści |
| `GET /playlists/{id}` | 200 | Pełny obiekt z **dwoma** pełnymi obiektami stronicowania `items` i `tracks` (po 100 pierwszych wpisów). `next` w osadzonym stronicowaniu wskazuje stary endpoint `/playlists/{id}/tracks?offset=100`, więc go nie używamy i treść czytamy przez `/items` |
| `GET /playlists/{id}/items` | 200 | Wpis: `added_at`, `added_by`, `is_local`, `primary_color` oraz **oba** pola `item` i `track` z identycznym obiektem utworu. `next` wskazuje `/items` |
| `GET /me/tracks` | 200 | Wpis ma **tylko** `added_at` i `track`. Pola `item` tu nie ma |
| `GET /albums/{id}` | 200 | `album_type`, `artists`, `copyrights`, `external_ids` (`upc`), `genres`, `images`, `label`, `name`, `popularity`, `release_date`, `release_date_precision`, `total_tracks` i stronicowanie utworów pod starą nazwą `tracks` |
| `GET /albums/{id}/tracks` | 200 | Uproszczone utwory **bez `external_ids`** i bez `album` |
| `GET /tracks/{id}` | 200 | Pełny utwór z `external_ids.isrc` |
| nieistniejące id (playlista, album, utwór) | 404 | `{"error": {"status": 404, "message": "Resource not found"}}` |
| `limit` powyżej maksimum | 400 | `{"error": {"status": 400, "message": "Invalid limit"}}` |

Obiekt utworu: `album`, `artists`, `available_markets`, `disc_number`, `duration_ms`, `explicit`, `external_ids`, `external_urls`, `href`, `id`, `is_local`, `name`, `popularity`, `preview_url` (zawsze `null`), `track_number`, `type`, `uri`. We wpisach playlist są dodatkowo flagi `episode` i `track`, a w Polubionych `is_playable`. Pola `linked_from` nie ma.

## 3. Odpowiedzi na [WERYFIKUJ] z sekcji 6 i punkty z sekcji 12

### 6.2 krok 3: kryterium czytelności playlisty

**Żadne z dwóch kryteriów z planu nie wystarcza.**

- `owner.id == me.id` albo `collaborative == true` **oznaczyłoby jako zablokowane playlisty, które da się przeczytać**. Wszystkie obserwowane playlisty innych użytkowników zwróciły 200 z pełną treścią z `GET /playlists/{id}` i z `GET /playlists/{id}/items`. To samo dało `neonseek add` jednej z nich na pustej bazie: 45 utworów z ISRC.
- Obecność `items` na liście z `/me/playlists` niczego nie mówi, bo to pole ma każda playlista, także cudza (tam jest tylko `{href, total}`).

Przyjęte kryterium (D-027): czytelność wynika z odpowiedzi na żądanie treści.

| Sytuacja | Wynik |
|---|---|
| Playlista własna albo współtworzona | czytelna, bez dodatkowego zapytania |
| Cudza playlista widziana pierwszy raz | jedno zapytanie `GET /playlists/{id}/items?limit=1`. 200 oznacza czytelną, 403 albo 404 zablokowaną, a inny błąd nie zmienia stanu |
| Pobranie treści kończy się 403 albo 404 | playlista dostaje `is_readable = 0`, zapisana treść zostaje |
| Zaznaczona zablokowana playlista | próba odczytu przy każdym `sync`, więc przejściowa porażka niczego nie blokuje na stałe |
| Wklejony URL | `GET /playlists/{id}` musi zwrócić `items` jako obiekt stronicowania z listą. 403, 404 albo brak `items` to komunikat i nic w bazie |

### 6.2 krok 4: maksymalny `limit`

Sprawdzone wysłaniem wartości granicznej i o jeden większej:

| Endpoint | Maksimum | Wartość o 1 większa |
|---|---|---|
| `GET /playlists/{id}/items` | **100** | 400 `Invalid limit` |
| `GET /me/tracks` | **50** | 400 |
| `GET /me/playlists` | **50** | 400 |
| `GET /albums/{id}/tracks` | **50** | 400 |

Stałe są w `sources/spotify_common.py`.

### 6.3: czy uproszczone utwory albumu mają `external_ids`

**Nie mają**, ani w `GET /albums/{id}/tracks`, ani w stronicowaniu osadzonym w `GET /albums/{id}`. ISRC dociągamy przez `GET /tracks/{id}` pojedynczo i po kolei. Album z 10 utworami to 12 zapytań (album, lista, 10 utworów). Utwór, dla którego `/tracks/{id}` daje 404, zostaje z danymi z albumu i bez ISRC, a inny błąd przerywa dodawanie albumu bez zapisu czegokolwiek.

### Sekcja 12: realny kształt `GET /playlists/{id}/items` i `GET /me/tracks`, maksymalny `limit`

Opisane w sekcji 2 i w tabeli limitów powyżej. Parser czyta `item`, a gdy go nie ma, `track`. To jest potrzebne, bo `/me/tracks` ma wyłącznie `track`.

### Sekcja 12: `GET /playlists/{id}` dla cudzej playlisty

| Playlista | Odpowiedź |
|---|---|
| Cudza playlista użytkownika (obserwowane) | **200 z pełną treścią**, tak jak własna |
| Playlista tworzona przez Spotify (w fixture'ach `37i9dQZF1NeonFixture01`) | **404** `Resource not found`, także dla `/items` |
| Nieistniejące id | identyczne **404** jak wyżej, więc nie da się ich odróżnić |

Link podany jako „publiczna playlista użytkownika” okazał się playlistą zalogowanego konta (`owner.id == me.id`). Dlatego przypadek cudzej playlisty sprawdziliśmy na obserwowanych. Prowadzą do tego samego endpointu.

Komunikaty dla użytkownika (oba proponują kopię do własnej playlisty albo CSV):

- **404:** „Spotify does not share this playlist. Playlists made by Spotify itself (like Daily Mix, Discover Weekly, Release Radar or editorial playlists), private playlists of other people and deleted playlists cannot be read. Copy the tracks into your own playlist in Spotify, or import them from a CSV file.”
- **403 albo brak `items`:** „Spotify does not share the tracks of this playlist. Copy its tracks into your own playlist in Spotify, or import them from a CSV file.”

## 4. Rozbieżności z PLAN.md (rozstrzygnięte)

Właściciel wyjaśnił je wiekiem aplikacji: starsza aplikacja nie podlega jeszcze zmianom z lutego 2026. Rozdział 7 to potwierdził, a PLAN.md (sekcje 2, 3, 9 i 12) opisuje teraz oba warianty. Kod opiera się na odpowiedziach API, więc działa w obu.

1. **Sekcje 2 i 3, cudze playlisty.** Plan zakłada, że API nie zwraca ich zawartości. Na tym koncie i w tej aplikacji zwraca pełną zawartość playlist innych użytkowników. Nieczytelne są playlisty tworzone przez Spotify (404). Kłódka w UI pojawi się więc głównie przy nich oraz przy prywatnych i usuniętych playlistach. Nowa aplikacja dostaje dokładnie ograniczenie z planu (rozdział 7).
2. **Sekcja 3, przemianowane pola.** API podaje stare i nowe nazwy równocześnie w obiektach playlist i we wpisach playlist. `/me/tracks` ma tylko `track`, a album ma tylko `tracks`.
3. **Sekcja 3, usunięte pola i endpointy.** `available_markets` i `popularity` nadal są w obiekcie utworu, a `GET /playlists/{id}/tracks` i `GET /tracks?ids=` nadal odpowiadają 200. Nie używamy żadnego z nich.
4. **`/me/playlists` ukrywa część playlist.** `total` jest o 2 większe niż liczba pozycji na stronach, a ostatnia ma `next = null`. Najpewniej to playlisty tworzone przez Spotify, które i tak dawałyby 404. Nie potwierdzone. Nie polegamy na `total` przy stronicowaniu, tylko na `next`.

## 5. Kryteria fazy 1 na żywo (konto właściciela)

| Sprawdzenie | Wynik |
|---|---|
| Pierwszy `sync` | Wszystkie playlisty i Polubione zapisane jako kolekcje, cudze sprawdzone jednym zapytaniem każda, wszystkie czytelne. Treść niepobrana, bo nic nie było zaznaczone |
| `sync` po zaznaczeniu wszystkiego | Ponad tysiąc utworów w niecałe pół minuty, **każde z 13 pól `tracks` wypełnione w 100%**, w tym ISRC. Kilka Polubionych miało inne id Spotify, ale ten sam ISRC i zgodną długość co inny polubiony utwór, więc skleiła je reguła 2 |
| Drugi `sync` bez zmian | **4 zapytania**: odświeżenie tokenu, `/me`, `/me/playlists` i pierwsza strona `/me/tracks`. Zero zapytań o treść playlist, wszystkie kolekcje bez zmian |
| `add` albumu (z `/intl-pl/` i `?si=`) | 10 utworów, wszystkie z ISRC. W bazie zapisany kanoniczny URL bez `si` |
| `add` utworu | dopisany do `Single tracks` |
| `add` własnej playlisty, która jest już z konta | ta sama kolekcja, bez drugiego wiersza. Wtedy link ją zaznaczał, a od D-042 zaznaczenie się nie zmienia |
| `add` playlisty tworzonej przez Spotify | komunikat 404 z sekcji 3, nic w bazie |
| `add` linku do artysty | komunikat, że obsługiwane są playlisty, albumy i utwory |
| Deduplikacja | jeden utwór z Polubionych, z albumu przez URL, z linku do utworu i z CSV (artysta, tytuł, `m:ss`) ma **jeden** wiersz w `tracks` |

## 6. Czego nie dało się sprawdzić na tym koncie

- **Playlisty współtworzone:** na koncie nie ma żadnej. Traktujemy je jak własne, zgodnie z planem.
- **Obserwowane playlisty tworzone przez Spotify:** na liście ich nie widać (zob. punkt 4.4). Gdyby się pojawiły, `sync` dostanie 404 i oznaczy je jako zablokowane, bez przerywania reszty.
- **Odcinki podcastów i pliki lokalne w playlistach:** w danych ich nie ma. Pomijanie testujemy na syntetycznych wpisach (`type: episode`, `is_local: true`, pusty `item`).
- **429, `Retry-After` i `QUOTA_EXCEEDED`:** nie wystąpiły. Obsługa jest przetestowana na atrapie: czekanie do 120 s, a dłużej to komunikat „spróbuj później”.
- **403** i **playlista bez pola `items`**: w starszej aplikacji nie wystąpiły. W nowej to realne odpowiedzi dla cudzych playlist (rozdział 7), które pokrywają fixture'y `new_app`.

## 7. Nowa aplikacja (założona po lutym 2026)

Stan na 2026-10-07. Druga aplikacja deweloperska założona teraz na tym samym koncie: Development Mode, ten sam Redirect URI, te same scope'y. Sondowanie i próby na żywo powtórzone na pustej bazie, tym samym kodem.

### 7.1 Różnice między wariantami

Logowanie (PKCE, redirect, scope'y, czas życia tokenu) działa tak samo w obu.

| Element | Starsza aplikacja | Nowa aplikacja |
|---|---|---|
| Obiekt playlisty na `/me/playlists` | `items` i `tracks`, oba `{href, total}` | **tylko `items`** `{href, total}`, także dla cudzych |
| `GET /playlists/{id}` własnej | pełne `items` i `tracks` | **tylko `items`** |
| `GET /playlists/{id}` cudzej playlisty użytkownika | 200 z pełną treścią | **200 bez pola `items`** (same metadane, bez `tracks`) |
| `GET /playlists/{id}/items` cudzej playlisty | 200 | **403** `{"error": {"status": 403, "message": "Forbidden"}}` |
| Playlista Spotify albo nieistniejące id | 404 `Resource not found` | 404 `Resource not found` (bez zmian) |
| Wpis playlisty | `item` i `track` | **tylko `item`** |
| Wpis `GET /me/tracks` | tylko `track` | tylko `track` (bez zmian) |
| Stronicowanie w `GET /albums/{id}` | `tracks` | `tracks` (bez zmian) |
| Obiekt utworu | ma `available_markets`, `popularity`, `preview_url` | **bez nich**, ma `is_playable` |
| Obiekt albumu | ma `label`, `popularity`, `available_markets` | **bez nich** |
| `GET /playlists/{id}/tracks` (stary) | 200 | **403** `Forbidden` |
| `GET /tracks?ids=` (stary) | 200 | **403** `Forbidden` |
| `external_ids` w uproszczonych utworach albumu | brak | brak |
| Maksymalny `limit` | 100 dla `/items`, 50 dla `/me/tracks`, `/me/playlists` i `/albums/{id}/tracks` | identyczny |
| `/me/playlists`: `total` i liczba pozycji | `total` o 2 większe | tak samo |
| `GET /me` | z `account_id` | identyczne |

### 7.2 [WERYFIKUJ] w nowej aplikacji

- **Kryterium czytelności (6.2):** obecność `items` na liście z `/me/playlists` dalej niczego nie mówi, bo cudze playlisty też je mają (`{href, total}`). W nowej aplikacji rozstrzyga jedno zapytanie `/items?limit=1` z wynikiem 403, a przy URL brak `items` w `GET /playlists/{id}`. Kryterium z D-027 działa w obu wariantach bez gałęzi zależnej od wieku aplikacji.
- **Maksymalny `limit`:** bez zmian (tabela wyżej).
- **`external_ids` w utworach albumu:** dalej brak, ISRC przez `GET /tracks/{id}` po kolei.
- **Cudza playlista:** 200 bez `items` i 403 na `/items`, czyli dokładnie kształt z sekcji 3 planu. Playlista Spotify daje 404 jak dotąd.

### 7.3 Na żywo, nowa aplikacja, pusta baza

| Sprawdzenie | Wynik |
|---|---|
| Pierwszy `sync` | Wszystkie playlisty i Polubione. Własne czytelne, cudze **zablokowane** po jednym zapytaniu (403). Wszystkie kolekcje odznaczone (D-042) |
| `sync` po zaznaczeniu wszystkich | Własne kolekcje zaktualizowane, zablokowane z komunikatem o kopii do własnej playlisty albo CSV. Ponad tysiąc utworów, **każde z 13 pól wypełnione w 100%** |
| Drugi `sync` bez zmian | Własne bez zmian, cudze zablokowane. Zapytania: `/me`, `/me/playlists`, pierwsza strona `/me/tracks` i po jednym `/items` z wynikiem 403 na każdą zablokowaną playlistę, która była zaznaczona (D-028). Zero zapytań o treść. Token był już w pamięci procesu, więc bez odświeżania |
| `add` albumu | 10 utworów, wszystkie z ISRC, 12 zapytań. Kolekcja odznaczona |
| `add` utworu | dopisany do `Single tracks`, kolekcja odznaczona |
| `add` własnej playlisty, która jest już z konta | ta sama kolekcja, zaznaczenie bez zmian |
| `add` cudzej playlisty użytkownika | komunikat „Spotify does not share the tracks of this playlist…”, jedno zapytanie `GET /playlists/{id}`, bez `/items`. Nowej kolekcji nie ma (na tej bazie playlista była już z konta jako zablokowana, a na pustej bazie test `new_app` sprawdza, że nic nie powstaje) |
| `add` playlisty Spotify | komunikat 404 jak w starszej aplikacji |
| CSV z utworem z Polubionych i albumu | dopasowany do istniejącego, nadal **jeden** wiersz w `tracks`. Kolekcja CSV odznaczona |

### 7.4 Wnioski

- **Kod nie wymagał zmian:** wszystkie odpowiedzi nowej aplikacji szły ścieżkami, które już istniały, czyli brak `items`, 403 na `/items`, wpis tylko z `item` i brak usuniętych pól. Nie używamy też żadnego endpointu, który w nowej aplikacji daje 403.
- **Fixture'y:** drugi zestaw w `tests/fixtures/spotify/new_app/`, z tego samego skryptu i z tą samą anonimizacją. `fake_spotify` w testach jest parametryzowany wariantem, więc każdy test Spotify biegnie na prawdziwych odpowiedziach obu aplikacji, a testy zależne od wariantu sprawdzają jego własne zachowanie.
- **Dla użytkowników:** prawie każdy nowy użytkownik będzie miał nową aplikację. Kłódka pojawi się u niego przy każdej cudzej playliście, więc podpowiedź o kopii do własnej playlisty albo CSV jest główną ścieżką, a nie wyjątkiem (kreator i README w fazach 3 i 4).
