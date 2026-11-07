"""account sync and pasted links on the fake spotify, phase 1 acceptance."""

from __future__ import annotations

import json

import pytest

from neonseek import actions
from neonseek.sources import spotify_common, spotify_url
from neonseek.sources.base import (
    KIND_SINGLES,
    KIND_SPOTIFY_ALBUM,
    KIND_SPOTIFY_LIKED,
    KIND_SPOTIFY_PLAYLIST,
)
from neonseek.spotify.auth import NoClientIdError, NotLoggedInError
from spotify_fake import LEGACY_APP

TRACK_COLUMNS = (
    "spotify_id",
    "isrc",
    "title",
    "artists_json",
    "album",
    "album_artists_json",
    "release_date",
    "duration_ms",
    "track_number",
    "disc_number",
    "cover_url",
    "explicit",
    "match_key",
)


def _rows(ctx, sql, *args):
    return ctx.repo.conn.execute(sql, args).fetchall()


def _collection(ctx, kind, external_id=None):
    if external_id is None:
        return ctx.repo.find_collection_by_kind(kind)
    return ctx.repo.find_collection(kind, external_id)


def _select_all_spotify(ctx):
    ids = [r[0] for r in _rows(ctx, "SELECT id FROM collections WHERE kind LIKE 'spotify%'")]
    actions.set_selected(ctx, ids, True)


def test_first_sync_lists_playlists_without_content(spotify_ctx, fake_spotify):
    report = actions.sync_spotify(spotify_ctx)
    ids = fake_spotify.ids
    assert report.playlists_listed == 2 and report.playlists_new == 2
    assert report.collections == []  # nothing selected yet
    own = _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, ids["own_playlist"])
    followed = _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, ids["followed_playlist"])
    liked = _collection(spotify_ctx, KIND_SPOTIFY_LIKED, "neonseek-test-user")
    assert (own["origin"], own["is_readable"], own["selected"]) == ("account", 1, 0)
    assert own["snapshot_id"] is None  # content not read
    assert own["track_count"] == 5
    # someone else's playlist is probed once: an older app reads it (200), a new one not (403)
    readable = fake_spotify.variant == LEGACY_APP
    assert followed["is_readable"] == int(readable) and followed["owner_id"] == "other-user-1"
    assert followed["selected"] == 0
    assert report.locked == ([] if readable else ["Followed Playlist 2"])
    assert liked["name"] == "Liked Songs"
    assert fake_spotify.counts()[f"/playlists/{ids['followed_playlist']}/items"] == 1
    assert fake_spotify.counts()[f"/playlists/{ids['own_playlist']}/items"] == 0


def test_sync_saves_full_fields_and_second_sync_reads_nothing(spotify_ctx, fake_spotify):
    actions.sync_spotify(spotify_ctx)
    own = _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, fake_spotify.ids["own_playlist"])
    liked = _collection(spotify_ctx, KIND_SPOTIFY_LIKED)
    actions.set_selected(spotify_ctx, [own["id"], liked["id"]], True)

    report = actions.sync_spotify(spotify_ctx)
    outcomes = {s.collection_id: s for s in report.collections}
    assert outcomes[own["id"]].outcome == spotify_common.UPDATED
    assert outcomes[own["id"]].result.tracks_total == 5
    assert outcomes[liked["id"]].result.tracks_total == 4

    tracks = _rows(spotify_ctx, "SELECT * FROM tracks")
    assert len(tracks) == 9
    for column in TRACK_COLUMNS:
        assert all(row[column] is not None for row in tracks), column
    entries = spotify_ctx.repo.collection_entries(own["id"])
    assert [e["position"] for e in entries] == [0, 1, 2, 3, 4]
    assert all(e["added_at"] for e in entries)

    before = len(fake_spotify.requests)
    report = actions.sync_spotify(spotify_ctx)
    assert {s.outcome for s in report.collections} == {spotify_common.UNCHANGED}
    second = fake_spotify.requests[before:]
    assert not [r for r in second if "/items" in r]
    # the liked check is one page, no paging through all saved tracks
    assert [r for r in second if r.startswith("/me/tracks")] == ["/me/tracks?offset=0&limit=50"]


def test_changed_playlist_replaces_links_but_keeps_tracks(spotify_ctx, fake_spotify):
    ids = fake_spotify.ids
    actions.sync_spotify(spotify_ctx)
    own = _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, ids["own_playlist"])
    actions.sync_spotify(spotify_ctx, collection_id=own["id"])  # explicit, not selected
    assert len(spotify_ctx.repo.collection_entries(own["id"])) == 5

    listing = fake_spotify.body("me_playlists")
    listing["items"][0]["snapshot_id"] = "changed"
    page = fake_spotify.body("playlist_items.own.p0")
    page["items"] = page["items"][:1]
    page["next"] = None
    fake_spotify.override("/me/playlists", 200, listing)
    fake_spotify.override(f"/playlists/{ids['own_playlist']}/items", 200, page)

    report = actions.sync_spotify(spotify_ctx, collection_id=own["id"])
    sync = report.collections[0]
    assert sync.outcome == spotify_common.UPDATED
    assert sync.result.links_removed == 4
    assert len(spotify_ctx.repo.collection_entries(own["id"])) == 1
    assert spotify_ctx.repo.count("tracks") == 5
    assert spotify_ctx.repo.count("downloads") == 5
    assert spotify_ctx.repo.get_collection(own["id"])["snapshot_id"] == "changed"


@pytest.mark.parametrize("status", [403, 404])
def test_unshared_playlist_is_marked_locked(spotify_ctx, fake_spotify, status):
    ids = fake_spotify.ids
    path = f"/playlists/{ids['followed_playlist']}/items"
    fake_spotify.override(path, status, {"error": {"status": status, "message": "x"}})
    report = actions.sync_spotify(spotify_ctx)
    followed = _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, ids["followed_playlist"])
    assert followed["is_readable"] == 0
    assert report.locked == ["Followed Playlist 2"]

    # selected anyway: tried again on every sync, so a passing problem never sticks
    actions.set_selected(spotify_ctx, [followed["id"]], True)
    report = actions.sync_spotify(spotify_ctx)
    assert report.collections[0].outcome == spotify_common.LOCKED
    assert "CSV" in report.collections[0].message
    fake_spotify.overrides.clear()
    fake_spotify.override(path, 200, {"items": [], "next": None, "total": 0})
    report = actions.sync_spotify(spotify_ctx)
    assert report.collections[0].outcome == spotify_common.UPDATED
    assert _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, ids["followed_playlist"])["is_readable"]


def test_liked_change_is_noticed(spotify_ctx, fake_spotify):
    actions.sync_spotify(spotify_ctx)
    liked = _collection(spotify_ctx, KIND_SPOTIFY_LIKED)
    actions.set_selected(spotify_ctx, [liked["id"]], True)
    actions.sync_spotify(spotify_ctx)
    page = fake_spotify.body("me_tracks.p0")
    page["total"] = 1
    page["items"] = page["items"][1:]
    page["next"] = None
    fake_spotify.override("/me/tracks", 200, page)
    sync = actions.sync_spotify(spotify_ctx).collections[0]
    assert sync.outcome == spotify_common.UPDATED and sync.result.links_removed == 3


def test_sync_needs_client_id_and_login(ctx, fake_spotify):
    with pytest.raises(NoClientIdError):
        actions.sync_spotify(ctx)
    actions.set_spotify_client_id(ctx, "cid")
    with pytest.raises(NotLoggedInError):
        actions.sync_spotify(ctx)


def test_new_client_id_forgets_old_login(spotify_ctx):
    actions.set_spotify_client_id(spotify_ctx, "other-app")
    assert spotify_ctx.token_store.load() is None


# pasted links


def _url(kind, item_id, suffix=""):
    return f"https://open.spotify.com/{kind}/{item_id}{suffix}"


def test_album_link_gets_isrc_for_every_track(spotify_ctx, fake_spotify):
    album_id = fake_spotify.ids["multi_album"]
    [result] = actions.add_spotify_links(spotify_ctx, [_url("album", album_id)])
    assert result.outcome == spotify_url.ADDED and result.tracks == 10
    row = _collection(spotify_ctx, KIND_SPOTIFY_ALBUM, album_id)
    assert row["selected"] == 0 and row["origin"] == "url"
    assert row["source_ref"] == f"https://open.spotify.com/album/{album_id}"
    tracks = _rows(spotify_ctx, "SELECT * FROM tracks")
    assert len(tracks) == 10
    for column in TRACK_COLUMNS:
        assert all(r[column] is not None for r in tracks), column
    # isrc comes from one request per track, the batch endpoint is not used
    counts = fake_spotify.counts()
    assert sum(n for path, n in counts.items() if path.startswith("/tracks/")) == 10
    assert counts["/tracks"] == 0

    # albums do not change: adding again and syncing reads nothing more
    before = len(fake_spotify.requests)
    [again] = actions.add_spotify_links(spotify_ctx, [_url("album", album_id)])
    assert again.outcome == spotify_url.EXISTS
    assert len(fake_spotify.requests) == before
    actions.sync_spotify(spotify_ctx)
    assert not [r for r in fake_spotify.requests[before:] if "/albums/" in r]


def test_track_link_goes_to_singles_once(spotify_ctx, fake_spotify):
    track_id = fake_spotify.ids["liked_first_track"]
    first, second = actions.add_spotify_links(
        spotify_ctx, [_url("track", track_id), f"spotify:track:{track_id}"]
    )
    assert (first.outcome, second.outcome) == (spotify_url.ADDED, spotify_url.EXISTS)
    singles = _collection(spotify_ctx, KIND_SINGLES)
    assert len(spotify_ctx.repo.collection_entries(singles["id"])) == 1
    row = _rows(spotify_ctx, "SELECT isrc FROM tracks")[0]
    assert row["isrc"]


def test_own_playlist_link_does_not_create_second_collection(spotify_ctx, fake_spotify):
    playlist_id = fake_spotify.ids["own_playlist"]
    actions.sync_spotify(spotify_ctx)
    own = _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, playlist_id)
    [result] = actions.add_spotify_links(
        spotify_ctx, [_url("intl-pl/playlist", playlist_id, "?nd=1")]
    )
    assert result.collection_id == own["id"]
    row = spotify_ctx.repo.get_collection(own["id"])
    # adding a link never changes the selection
    assert (row["origin"], row["selected"]) == ("account", 0)
    assert len(spotify_ctx.repo.collection_entries(own["id"])) == 5
    assert (
        len(_rows(spotify_ctx, "SELECT * FROM collections WHERE kind = ?", KIND_SPOTIFY_PLAYLIST))
        == 2
    )

    # once read, the same link only selects it again
    before = len(fake_spotify.requests)
    [again] = actions.add_spotify_links(spotify_ctx, [_url("playlist", playlist_id)])
    assert again.outcome == spotify_url.EXISTS
    assert not [r for r in fake_spotify.requests[before:] if "/items" in r]


def test_playlist_link_without_account_sync(spotify_ctx, fake_spotify):
    playlist_id = fake_spotify.ids["own_playlist"]
    [result] = actions.add_spotify_links(spotify_ctx, [_url("playlist", playlist_id)])
    assert result.outcome == spotify_url.ADDED and result.tracks == 5
    row = _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, playlist_id)
    assert (row["origin"], row["selected"]) == ("url", 0)
    # a later account sync adopts it as an account playlist instead of adding a second one
    actions.sync_spotify(spotify_ctx)
    rows = _rows(spotify_ctx, "SELECT origin FROM collections WHERE external_id = ?", playlist_id)
    assert [r["origin"] for r in rows] == ["account"]


def _assert_nothing_saved(ctx):
    assert ctx.repo.count("collections") == 0
    assert ctx.repo.count("tracks") == 0


def test_spotify_made_playlist_link(spotify_ctx, fake_spotify):
    # real answer for a playlist made by spotify: 404, same as a playlist that does not exist
    playlist_id = fake_spotify.ids["spotify_playlist"]
    [result] = actions.add_spotify_links(spotify_ctx, [_url("playlist", playlist_id)])
    assert result.outcome == spotify_common.FAILED
    assert "made by Spotify" in result.message and "CSV" in result.message
    _assert_nothing_saved(spotify_ctx)


def test_someone_elses_playlist_link(spotify_ctx, fake_spotify):
    # real answers: an older app reads it, a new app gets the playlist without items
    playlist_id = fake_spotify.ids["followed_playlist"]
    if fake_spotify.variant == LEGACY_APP:
        # the fixture keeps 2 real entries of the 45, served as the whole /items answer
        entries = fake_spotify.body("playlist.followed")["items"]["items"]
        page = {"items": entries, "next": None, "total": len(entries)}
        fake_spotify.override(f"/playlists/{playlist_id}/items", 200, page)
    [result] = actions.add_spotify_links(spotify_ctx, [_url("playlist", playlist_id)])
    if fake_spotify.variant == LEGACY_APP:
        assert result.outcome == spotify_url.ADDED and result.tracks == 2
        row = _collection(spotify_ctx, KIND_SPOTIFY_PLAYLIST, playlist_id)
        assert row["owner_id"] == "other-user-1" and row["is_readable"] == 1
        return
    assert result.outcome == spotify_common.FAILED
    assert "own playlist" in result.message and "CSV" in result.message
    _assert_nothing_saved(spotify_ctx)
    assert fake_spotify.counts()[f"/playlists/{playlist_id}/items"] == 0


def test_playlist_without_items_link(spotify_ctx, fake_spotify):
    # the new app shape, forced on both variants
    playlist_id = fake_spotify.ids["followed_playlist"]
    body = fake_spotify.body("playlist.followed")
    body.pop("items", None)
    body.pop("tracks", None)
    fake_spotify.override(f"/playlists/{playlist_id}", 200, body)
    [result] = actions.add_spotify_links(spotify_ctx, [_url("playlist", playlist_id)])
    assert result.outcome == spotify_common.FAILED
    assert "own playlist" in result.message and "CSV" in result.message
    _assert_nothing_saved(spotify_ctx)


def test_playlist_403_link(spotify_ctx, fake_spotify):
    playlist_id = fake_spotify.ids["followed_playlist"]
    fake_spotify.override(f"/playlists/{playlist_id}", 403, {"error": {"status": 403}})
    [result] = actions.add_spotify_links(spotify_ctx, [_url("playlist", playlist_id)])
    assert result.outcome == spotify_common.FAILED and "CSV" in result.message
    _assert_nothing_saved(spotify_ctx)


def test_many_links_one_bad(spotify_ctx, fake_spotify):
    ids = fake_spotify.ids
    results = actions.add_spotify_links(
        spotify_ctx,
        [
            "https://open.spotify.com/artist/0OdUWJ0sBjDrqHygGUXeCF",
            _url("playlist", ids["spotify_playlist"]),
            "",
            _url("album", "0000000000000000000000"),
            _url("track", ids["liked_first_track"]),
        ],
    )
    assert [r.outcome for r in results] == ["failed", "failed", "failed", "added"]
    assert "artist link" in results[0].message
    assert "could not find this album" in results[2].message


def test_dedup_across_account_link_and_csv(spotify_ctx, fake_spotify, tmp_path):
    """phase 1: the same track from the account, a link and a csv is one row in tracks."""
    ids = fake_spotify.ids
    track = fake_spotify.body(f"track.{ids['liked_first_track']}")

    actions.sync_spotify(spotify_ctx)
    liked = _collection(spotify_ctx, KIND_SPOTIFY_LIKED)
    actions.sync_spotify(spotify_ctx, collection_id=liked["id"])
    actions.add_spotify_links(
        spotify_ctx,
        [_url("track", ids["liked_first_track"]), _url("album", ids["single_album"])],
    )
    csv_path = tmp_path / "mine.csv"
    seconds = round(track["duration_ms"] / 1000)
    csv_path.write_text(
        f"Artist;Title;Length\n{track['artists'][0]['name']};{track['name']};"
        f"{seconds // 60}:{seconds % 60:02d}\n",
        encoding="utf-8",
    )
    outcome = actions.import_csv(spotify_ctx, csv_path)
    assert outcome.result.tracks_existing == 1

    rows = _rows(
        spotify_ctx,
        "SELECT t.id FROM tracks t WHERE t.spotify_id = ? OR t.match_key = "
        "(SELECT match_key FROM tracks WHERE spotify_id = ?)",
        ids["liked_first_track"],
        ids["liked_first_track"],
    )
    assert len(rows) == 1
    linked = _rows(
        spotify_ctx,
        "SELECT c.kind FROM collection_tracks ct JOIN collections c ON c.id = ct.collection_id "
        "WHERE ct.track_id = ? ORDER BY c.kind",
        rows[0]["id"],
    )
    assert [r["kind"] for r in linked] == ["csv", "singles", "spotify_album", "spotify_liked"]
    assert json.loads(
        _rows(spotify_ctx, "SELECT artists_json FROM tracks WHERE id = ?", rows[0]["id"])[0][0]
    )


def test_every_collection_starts_unselected(spotify_ctx, fake_spotify, csv_dir):
    """PLAN.md section 8: only collections the user points at are ever downloaded."""
    ids = fake_spotify.ids
    actions.sync_spotify(spotify_ctx)
    actions.add_spotify_links(
        spotify_ctx,
        [
            _url("album", ids["multi_album"]),
            _url("track", ids["liked_first_track"]),
            _url("playlist", ids["own_playlist"]),
        ],
    )
    actions.import_csv(spotify_ctx, csv_dir / "no_length.csv")
    rows = _rows(spotify_ctx, "SELECT kind, selected FROM collections")
    kinds = {r["kind"] for r in rows}
    assert {"spotify_playlist", "spotify_liked", "spotify_album", "singles", "csv"} <= kinds
    assert all(r["selected"] == 0 for r in rows)
