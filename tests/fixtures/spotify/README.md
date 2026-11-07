# Spotify fixtures

Real Spotify Web API responses captured on 2026-10-07 with `scripts/make_spotify_fixtures.py` on the owner's account, PKCE login, development mode. The API answers differently depending on the age of the developer app (PLAN.md section 3, `docs/spotify-notes.md`), so there are two sets made by the same script:

| Folder | Developer app | What differs |
|---|---|---|
| `legacy_app/` | created before February 2026 | other users' playlists readable, old and new field names together (`item` and `track`, `items` and `tracks`), removed fields still present |
| `new_app/` | created now, what every new NeonSeek user gets | other users' playlists: object without `items` and 403 on `/items`, only new field names, removed fields gone |

`tests/spotify_fake.py` answers API calls from one folder by path and `offset`. The `fake_spotify` fixture is parametrized, so every Spotify test runs on both sets.

Each file is `{"request": {"path", "params"}, "status", "body"}`. The script anonymizes the responses in memory and refuses to write anything if a replaced value is still present.

## Anonymized

- The user: id `neonseek-test-user`, name `Test User`, `account_id`, profile images and followers.
- Other users (playlist owners, `added_by`): `other-user-N` and `Other User N`.
- Playlists: id `0NeonFixturePlaylistNN`, name `Own Playlist N` or `Followed Playlist N`, empty description, placeholder image, follower count 0.
- No `si` share parameter anywhere.
- Dates: every `added_at` is a made up date from 2025-03-01 on, one day apart, in the same order as the real ones (D-210).
- Playlist `snapshot_id`: `AAAANeonFixtureSnapshotNN000000`, same length as the real one.

- Catalog (D-212): tracks, albums and artists are a made up catalog written by `scripts/fake_spotify_catalog.py` after the capture. Ids `0NeonFxTrk…`, `0NeonFxAlb…`, `0NeonFxArt…`, names `Fixture Track NN`, `Fixture Album NN`, `Fixture Artist NN` (Polish-looking names where the real one had letters outside ASCII, version suffixes such as `- Extended Mix` kept), ISRC `QZNFX26NNNNN`, UPC of the same length, cover urls with a made up hash, label and copyrights `Fixture Records`. Release dates, durations, track and disc numbers, markets and the shape stay real.
- The Spotify-made playlist id is `37i9dQZF1NeonFixture01`.

## Shortened (shape unchanged)

- `available_markets` (legacy only): cut to the first 2 country codes, we never read it.
- `me_playlists.json`: only 2 of the 43 playlists kept (one own, one followed from another user), `total = 2`, `next = null`.
- `playlist.followed.json` (legacy only, the new app sends no content): the embedded `items` and `tracks` pages keep 2 of their entries.
- `me_tracks.p0.json` and `me_tracks.p1.json`: first two pages of `limit=2`, `total = 4`, `next = null` on the second page.

## Files (same names in both folders)

| File | Request |
|---|---|
| `me.json` | `GET /me` |
| `me_playlists.json` | `GET /me/playlists?limit=50` |
| `playlist.own.json` | `GET /playlists/{own}` (5 tracks) |
| `playlist_items.own.p0..p2.json` | `GET /playlists/{own}/items?limit=2`, real paging |
| `playlist.followed.json` | `GET /playlists/{followed}`, another user's playlist |
| `playlist_items.followed.probe.json` | `GET /playlists/{followed}/items?limit=1` (legacy 200, new 403) |
| `playlist.spotify_made.json` | `GET /playlists/37i9dQZF1NeonFixture01`, real 404 |
| `me_tracks.p0.json`, `me_tracks.p1.json` | `GET /me/tracks?limit=2` |
| `album.single.json`, `album_tracks.single.p0.json` | album (single) of the first liked track |
| `album.multi.json`, `album_tracks.multi.p0..p1.json` | album with 10 tracks, `limit=5` paging |
| `track.<id>.json` | `GET /tracks/{id}` for every track of both albums |
| `_ids.json` | ids the tests refer to |
