"""parser on real (anonymized) responses, see docs/spotify-notes.md."""

from __future__ import annotations

from neonseek.spotify import models
from spotify_fake import LEGACY_APP, NEW_APP


def test_playlist_entries_use_item(fake_spotify):
    page = fake_spotify.body("playlist_items.own.p0")
    entry = page["items"][0]
    # an older app gets both names, a new app only the new one, the parser reads the new one
    if fake_spotify.variant == LEGACY_APP:
        assert entry["item"] == entry["track"]
    else:
        assert "track" not in entry
    tracks, skipped = models.parse_entries(page["items"])
    assert skipped == 0
    track = tracks[0]
    assert track.spotify_id == entry["item"]["id"]
    assert track.isrc == entry["item"]["external_ids"]["isrc"]
    assert track.added_at == entry["added_at"]
    assert track.title and track.artists and track.album and track.album_artists
    assert track.duration_ms and track.release_date and track.cover_url
    assert track.track_number and track.disc_number
    assert track.explicit in (True, False)


def test_saved_tracks_have_only_track(fake_spotify):
    page = fake_spotify.body("me_tracks.p0")
    assert set(page["items"][0]) == {"added_at", "track"}
    tracks, skipped = models.parse_entries(page["items"])
    assert skipped == 0 and len(tracks) == 2
    assert all(t.isrc for t in tracks)


def test_item_wins_over_track():
    entry = {
        "item": {"id": "new", "name": "New", "type": "track"},
        "track": {"id": "old", "name": "Old", "type": "track"},
    }
    assert models.parse_entry(entry).spotify_id == "new"


def test_episodes_local_files_and_gaps_are_skipped(fake_spotify):
    real = fake_spotify.body("playlist_items.own.p0")["items"][0]
    episode = {"added_at": "x", "item": {"id": "e", "name": "Ep", "type": "episode"}}
    local = {"added_at": "x", "is_local": True, "item": {"id": None, "name": "Mine.mp3"}}
    local_inner = {"item": {"id": "l", "name": "L", "type": "track", "is_local": True}}
    removed = {"added_at": "x", "item": None, "track": None}
    tracks, skipped = models.parse_entries([episode, real, local, local_inner, removed])
    assert len(tracks) == 1 and skipped == 4


def test_album_tracks_take_album_from_album_object(fake_spotify):
    album = fake_spotify.body("album.multi")
    simple = fake_spotify.body("album_tracks.multi.p0")["items"][0]
    assert "external_ids" not in simple and "album" not in simple
    track = models.parse_track(simple, album=album)
    assert track.album == album["name"]
    assert track.release_date == album["release_date"]
    assert track.cover_url == models.best_image(album["images"])
    assert track.isrc is None


def test_full_track(fake_spotify):
    ids = fake_spotify.ids
    obj = fake_spotify.body(f"track.{ids['liked_first_track']}")
    track = models.parse_track(obj)
    assert track.isrc == obj["external_ids"]["isrc"]
    assert track.album == obj["album"]["name"]


def test_best_image_takes_largest():
    images = [
        {"url": "s", "width": 64, "height": 64},
        {"url": "l", "width": 640, "height": 640},
        {"url": "m", "width": 300, "height": 300},
    ]
    assert models.best_image(images) == "l"
    assert models.best_image([]) is None


def test_someone_elses_playlist_object(fake_spotify):
    followed = fake_spotify.body("playlist.followed")
    # older app: full content. new app: metadata only, no items key at all
    assert models.has_content(followed) is (fake_spotify.variant == LEGACY_APP)
    if fake_spotify.variant == NEW_APP:
        assert "items" not in followed and "tracks" not in followed


def test_removed_fields_are_gone_only_for_new_apps(fake_spotify):
    track = fake_spotify.body(f"track.{fake_spotify.ids['liked_first_track']}")
    old_fields = {"available_markets", "popularity"}
    if fake_spotify.variant == NEW_APP:
        assert not old_fields & set(track)
    else:
        assert old_fields <= set(track)
    # the parser needs none of them
    assert models.parse_track(track).isrc


def test_listing_and_full_playlist_objects(fake_spotify):
    listing = fake_spotify.body("me_playlists")["items"][0]
    full = fake_spotify.body("playlist.own")
    # the listing only has a count, the full object has the first page of content
    assert not models.has_content(listing)
    assert models.has_content(full)
    assert models.content_total(listing) == models.content_total(full)
    assert listing["snapshot_id"] == full["snapshot_id"]
    record = models.playlist_record(full, origin="url")
    assert record.external_id == fake_spotify.ids["own_playlist"]
    assert record.owner_id == "neonseek-test-user"
    assert record.track_count == full["items"]["total"]
