from __future__ import annotations

import json

import pytest

from neonseek.sources import ingest
from neonseek.sources.base import (
    KIND_CSV,
    KIND_SPOTIFY_PLAYLIST,
    ORIGIN_ACCOUNT,
    ORIGIN_FILE,
    ORIGIN_URL,
    CollectionRecord,
    TrackRecord,
)
from neonseek.sources.ingest import add_single, ingest_collection, match_key, resolve_track


@pytest.mark.parametrize(
    ("artist", "title", "expected"),
    [
        ("Neon Arcade", "Midnight Drive", "neon arcade|midnight drive"),
        ("Neon Arcade", "Glass Hearts (feat. Lumen)", "neon arcade|glass hearts"),
        ("Neon Arcade", "Glass Hearts [ft. Lumen]", "neon arcade|glass hearts"),
        ("Neon Arcade", "Glass Hearts feat. Lumen", "neon arcade|glass hearts"),
        ("Neon Arcade", "Glass Hearts (Featuring Lumen)", "neon arcade|glass hearts"),
        (
            "Neon Arcade",
            "Glass Hearts (feat. Lumen) [Extended Mix]",
            "neon arcade|glass hearts extended mix",
        ),
        (
            "Neon Arcade",
            "Glass Hearts feat. Lumen - Extended Mix",
            "neon arcade|glass hearts extended mix",
        ),
        ("Neon Arcade", "Glass Hearts (Remix feat. Lumen)", "neon arcade|glass hearts remix"),
        ("Neon Arcade", "Glass Hearts - Live", "neon arcade|glass hearts live"),
        ("Neon Arcade feat. Lumen", "Glass Hearts", "neon arcade|glass hearts"),
        ("AC/DC", "T.N.T.", "ac dc|t n t"),
        ("Zażółć", "Kraków  Nights!!", "zażółć|kraków nights"),
        # composed and decomposed forms are the same text
        ("Café", "X", "café|x"),
        ("Loft", "Defeat Soft", "loft|defeat soft"),
        (None, "Only Title", "|only title"),
        ("STRASSE", "Straße", "strasse|strasse"),
    ],
)
def test_match_key(artist, title, expected):
    assert match_key(artist, title) == expected


def _rec(title="Midnight Drive", artist="Neon Arcade", **kw) -> TrackRecord:
    return TrackRecord(title=title, artists=(artist,) if artist else (), **kw)


def test_new_track_gets_pending_download(repo):
    with repo.transaction():
        track_id, created = resolve_track(repo, _rec(duration_ms=245000))
    assert created
    row = repo.conn.execute("SELECT * FROM downloads WHERE track_id = ?", (track_id,)).fetchone()
    assert (row["status"], row["stage"], row["attempts"]) == ("pending", "flac", 0)


def test_rule1_same_spotify_id(repo):
    with repo.transaction():
        a, _ = resolve_track(repo, _rec(spotify_id="0NeonFixtureTrack00001", duration_ms=245000))
        # different name and length, the id still wins
        b, created = resolve_track(
            repo, _rec(title="Other", spotify_id="0NeonFixtureTrack00001", duration_ms=1000)
        )
    assert a == b and not created


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        (245000, 248000, True),
        (245000, 248001, False),
        (245000, None, True),
        (None, 245000, True),
    ],
)
def test_rule2_isrc_with_length_tolerance(repo, first, second, same):
    with repo.transaction():
        a, _ = resolve_track(
            repo, _rec(spotify_id="0NeonFixtureTrack00001", isrc="QZAAA2600001", duration_ms=first)
        )
        b, _ = resolve_track(
            repo,
            _rec(
                title="Renamed On Single",
                spotify_id="0NeonFixtureTrack00009",
                isrc="qzaaa2600001",
                duration_ms=second,
            ),
        )
    assert (a == b) is same


def test_rule3_name_match_needs_one_side_without_ids(repo):
    with repo.transaction():
        spotify, _ = resolve_track(
            repo, _rec(spotify_id="0NeonFixtureTrack00001", isrc="QZAAA2600001", duration_ms=245000)
        )
        # csv row without ids, within tolerance
        csv_row, created = resolve_track(repo, _rec(title="Midnight Drive!", duration_ms=243500))
    assert csv_row == spotify and not created


def test_rule3_does_not_glue_two_spotify_tracks(repo):
    with repo.transaction():
        a, _ = resolve_track(repo, _rec(spotify_id="0NeonFixtureTrack00001", duration_ms=245000))
        b, created = resolve_track(
            repo, _rec(spotify_id="0NeonFixtureTrack00002", duration_ms=245000)
        )
    assert a != b and created


def test_rule3_isrc_only_tracks_are_not_glued(repo):
    with repo.transaction():
        a, _ = resolve_track(repo, _rec(isrc="QZAAA2600001", duration_ms=245000))
        b, created = resolve_track(repo, _rec(isrc="QZAAA2600099", duration_ms=245000))
    assert a != b and created


def test_rule3_respects_length(repo):
    with repo.transaction():
        a, _ = resolve_track(repo, _rec(duration_ms=245000))
        b, created = resolve_track(repo, _rec(duration_ms=300000))
    assert a != b and created


def test_rule3_keeps_versions_apart(repo):
    with repo.transaction():
        a, _ = resolve_track(repo, _rec(title="Midnight Drive"))
        b, created = resolve_track(repo, _rec(title="Midnight Drive (Extended Mix)"))
    assert a != b and created


def test_match_fills_only_empty_fields(repo):
    with repo.transaction():
        # csv row with isrc but without artist, matched later by rule 2
        track_id, _ = resolve_track(
            repo, TrackRecord(title="Midnight Drive", album="From CSV", isrc="QZAAA2600001")
        )
        assert repo.get_track(track_id)["match_key"] == "|midnight drive"
        same, _ = resolve_track(
            repo,
            TrackRecord(
                title="Midnight Drive",
                artists=("Neon Arcade",),
                album="Night Shift",
                duration_ms=245000,
                spotify_id="0NeonFixtureTrack00001",
                isrc="QZAAA2600001",
                cover_url="https://i.scdn.co/image/x",
                explicit=False,
            ),
        )
    assert same == track_id
    row = repo.get_track(track_id)
    assert row["album"] == "From CSV"  # filled before, not overwritten
    assert row["spotify_id"] == "0NeonFixtureTrack00001"
    assert row["isrc"] == "QZAAA2600001"
    assert row["duration_ms"] == 245000
    assert json.loads(row["artists_json"]) == ["Neon Arcade"]
    assert row["explicit"] == 0
    # artists arrived later, so the key now has the artist
    assert row["match_key"] == "neon arcade|midnight drive"


def _collection(tracks, **kw) -> CollectionRecord:
    values = {
        "kind": KIND_SPOTIFY_PLAYLIST,
        "origin": ORIGIN_ACCOUNT,
        "name": "Mix",
        "external_id": "0NeonFixturePlaylist01",
        "snapshot_id": "snap1",
        "tracks": tracks,
    }
    values.update(kw)
    return CollectionRecord(**values)


def test_collection_content_is_replaced_and_tracks_stay(repo):
    a = _rec(title="A", spotify_id="0NeonFixtureTrack0000A")
    b = _rec(title="B", spotify_id="0NeonFixtureTrack0000B")
    c = _rec(title="C", spotify_id="0NeonFixtureTrack0000C")
    first = ingest_collection(repo, _collection([a, b]))
    assert first.created and first.tracks_new == 2
    second = ingest_collection(repo, _collection([c, a], snapshot_id="snap2"))
    assert not second.created
    assert (second.tracks_new, second.tracks_existing, second.links_removed) == (1, 1, 1)
    entries = repo.collection_entries(first.collection_id)
    titles = [repo.get_track(e["track_id"])["title"] for e in entries]
    assert titles == ["C", "A"]
    # the removed track keeps its row and its download row
    assert repo.count("tracks") == 3
    assert repo.count("downloads") == 3
    assert repo.get_collection(first.collection_id)["snapshot_id"] == "snap2"


def test_same_track_twice_in_one_list(repo):
    a = _rec(title="A", duration_ms=1000)
    result = ingest_collection(
        repo, _collection([a, a], kind=KIND_CSV, origin=ORIGIN_FILE, external_id=None)
    )
    assert result.tracks_total == 2
    assert result.tracks_new == 1
    assert repo.count("tracks") == 1


def test_metadata_only_record_keeps_content_and_snapshot(repo):
    first = ingest_collection(repo, _collection([_rec(title="A")]))
    ingest_collection(repo, _collection(None, name="Renamed", snapshot_id="new"))
    row = repo.get_collection(first.collection_id)
    assert row["name"] == "Renamed"
    assert row["snapshot_id"] == "snap1"
    assert len(repo.collection_entries(first.collection_id)) == 1


def test_url_add_keeps_account_origin_and_selection(repo):
    first = ingest_collection(repo, _collection(None))
    assert repo.get_collection(first.collection_id)["selected"] == 0
    again = ingest_collection(
        repo,
        _collection(None, origin=ORIGIN_URL, source_ref="https://open.spotify.com/playlist/x"),
    )
    row = repo.get_collection(again.collection_id)
    assert again.collection_id == first.collection_id
    assert row["origin"] == ORIGIN_ACCOUNT
    assert row["selected"] == 0
    assert repo.count("collections") == 1


def test_singles_without_repeats(repo):
    first = add_single(repo, _rec(title="A", spotify_id="0NeonFixtureTrack0000A"))
    second = add_single(repo, _rec(title="B", spotify_id="0NeonFixtureTrack0000B"))
    again = add_single(repo, _rec(title="A", spotify_id="0NeonFixtureTrack0000A"))
    assert first.collection_id == second.collection_id == again.collection_id
    assert again.already_listed and not first.already_listed
    entries = repo.collection_entries(first.collection_id)
    assert [e["position"] for e in entries] == [0, 1]
    row = repo.get_collection(first.collection_id)
    assert (row["kind"], row["origin"], row["track_count"]) == ("singles", "builtin", 2)


def test_delete_collection_keeps_tracks_and_downloads(repo):
    result = ingest_collection(repo, _collection([_rec(title="A"), _rec(title="B")]))
    with repo.transaction():
        repo.delete_collection(result.collection_id)
    assert repo.count("collections") == 0
    assert repo.count("collection_tracks") == 0
    assert repo.count("tracks") == 2
    assert repo.count("downloads") == 2


def test_normalize_isrc():
    assert ingest.normalize_isrc(" us-abc-12-34567 ") == "USABC1234567"
    assert ingest.normalize_isrc("n/a") is None
    assert ingest.normalize_isrc("") is None
