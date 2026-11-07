"""apple music, youtube and soundcloud as sources of track lists (D-227 to D-233). everything
runs on made up answers in fixtures/web, nothing goes online."""

from __future__ import annotations

import json

import httpx
import pytest
import yt_dlp

from neonseek import actions, cli, config, outcomes, views
from neonseek.db import repo as db
from neonseek.pipeline import runner
from neonseek.sources import csv_file, web_url, ytdlp
from neonseek.sources import links as weblinks
from neonseek.sources.base import (
    KIND_APPLE_ALBUM,
    KIND_APPLE_PLAYLIST,
    KIND_SINGLES,
    KIND_SOUNDCLOUD_SET,
    KIND_YOUTUBE_PLAYLIST,
)
from neonseek.sources.titles import clean_channel, split_artist_title
from web_fake import (
    PLAYLIST_ID,
    FakeSoundCloudNet,
    apple_page,
    fake_song,
    load,
    soundcloud_fixture_ids,
)

ALBUM_URL = "https://music.apple.com/pl/album/fixture-tunes-ep/1000000001"
PLAYLIST_URL = f"https://music.apple.com/pl/playlist/fixture-mix/{PLAYLIST_ID}"
YOUTUBE_URL = "https://www.youtube.com/playlist?list=PLfixture00000000000000000000000001"
VIDEO_URL = "https://youtu.be/fixtureSolo?si=SHAREfixture"
SOUNDCLOUD_URL = "https://soundcloud.com/fixture-uploader/sets/fixture-set"


def _tracks(ctx, collection_id):
    rows = ctx.repo.conn.execute(
        "SELECT t.* FROM collection_tracks ct JOIN tracks t ON t.id = ct.track_id "
        "WHERE ct.collection_id = ? ORDER BY ct.position",
        (collection_id,),
    ).fetchall()
    return [(json.loads(r["artists_json"] or "[]"), r["title"]) for r in rows], rows


def _add(ctx, *links):
    return actions.add_links(ctx, list(links))


# links


@pytest.mark.parametrize(
    ("link", "service", "kind", "item", "clean"),
    [
        (
            "https://music.apple.com/pl/album/fixture-tunes-ep/1000000001?si=abc&utm_source=x",
            "apple", "album", "1000000001",
            "https://music.apple.com/pl/album/1000000001",
        ),
        (
            "music.apple.com/us/album/fixture/1000000001?i=1000000003&ls=1",
            "apple", "track", "1000000003",
            "https://music.apple.com/us/song/1000000003",
        ),
        (
            "https://music.apple.com/song/first-fixture/1000000002",
            "apple", "track", "1000000002",
            "https://music.apple.com/us/song/1000000002",
        ),
        (
            f"https://music.apple.com/pl/playlist/fixture-mix/{PLAYLIST_ID}?ref=share",
            "apple", "playlist", PLAYLIST_ID,
            f"https://music.apple.com/pl/playlist/{PLAYLIST_ID}",
        ),
        (
            "https://music.apple.com/pl/playlist/mine/pl.u-AbCdEfGh123?si=x",
            "apple", "playlist", "pl.u-AbCdEfGh123",
            "https://music.apple.com/pl/playlist/pl.u-AbCdEfGh123",
        ),
        (
            "https://youtube.com/playlist?list=PLfixture01&si=SHARE&feature=shared",
            "youtube", "playlist", "PLfixture01",
            "https://www.youtube.com/playlist?list=PLfixture01",
        ),
        (
            "https://www.youtube.com/watch?v=fixtureVid1&list=PLfixture01&index=3&pp=x",
            "youtube", "playlist", "PLfixture01",
            "https://www.youtube.com/playlist?list=PLfixture01",
        ),
        (
            "https://www.youtube.com/watch?v=fixtureVid1&list=RDfixtureVid1&start_radio=1",
            "youtube", "track", "fixtureVid1",
            "https://www.youtube.com/watch?v=fixtureVid1",
        ),
        (
            "https://youtu.be/fixtureVid1?si=SHARE&t=30",
            "youtube", "track", "fixtureVid1",
            "https://www.youtube.com/watch?v=fixtureVid1",
        ),
        (
            "https://music.youtube.com/watch?v=fixtureVid1&feature=share",
            "youtube", "track", "fixtureVid1",
            "https://www.youtube.com/watch?v=fixtureVid1",
        ),
        (
            "https://www.youtube.com/shorts/fixtureVid1?feature=share",
            "youtube", "track", "fixtureVid1",
            "https://www.youtube.com/watch?v=fixtureVid1",
        ),
        (
            "https://soundcloud.com/fixture-uploader/sets/fixture-set?si=abc&utm_source=clipboard",
            "soundcloud", "playlist", "fixture-uploader/sets/fixture-set",
            "https://soundcloud.com/fixture-uploader/sets/fixture-set",
        ),
        (
            "https://m.soundcloud.com/fixture-label/night-drive?in=fixture-uploader/sets/x&ref=y",
            "soundcloud", "track", "fixture-label/night-drive",
            "https://soundcloud.com/fixture-label/night-drive",
        ),
        (
            "https://soundcloud.com/fixture-uploader/sets/private-set/s-AbCdE?si=1",
            "soundcloud", "playlist", "fixture-uploader/sets/private-set/s-AbCdE",
            "https://soundcloud.com/fixture-uploader/sets/private-set/s-AbCdE",
        ),
    ],
)  # fmt: skip
def test_links_keep_only_what_they_mean(link, service, kind, item, clean):
    ref = weblinks.parse_web_ref(link)
    assert (ref.service, ref.kind, ref.id) == (service, kind, item)
    assert ref.url == clean
    for junk in ("si=", "utm_", "ref=", "feature=", "pp=", "index=", "in="):
        assert junk not in ref.url


@pytest.mark.parametrize(
    "link",
    [
        "https://music.apple.com/pl/artist/neon-fixture-artist/2000000001",
        "https://music.apple.com/pl/browse",
        "https://www.youtube.com/@fixturechannel",
        "https://www.youtube.com/channel/UCfixture000000000000001",
        "https://soundcloud.com/fixture-uploader",
        "https://soundcloud.com/fixture-uploader/likes",
        "https://soundcloud.com/discover/sets/x",
    ],
)
def test_links_of_other_pages_say_what_to_paste(link):
    with pytest.raises(weblinks.UnsupportedWebLinkError) as caught:
        weblinks.parse_web_ref(link)
    assert "paste it again" in caught.value.message or "cannot be added" in caught.value.message


def test_find_links_in_pasted_text_keeps_order():
    pasted = (
        "look https://youtu.be/fixtureVid1?si=x and "
        "https://open.spotify.com/playlist/0NeonFixturePlaylist01 plus "
        f"{SOUNDCLOUD_URL} {ALBUM_URL} https://example.com/x"
    )
    assert weblinks.find_links(pasted) == [
        "https://youtu.be/fixtureVid1?si=x",
        "https://open.spotify.com/playlist/0NeonFixturePlaylist01",
        SOUNDCLOUD_URL,
        ALBUM_URL,
    ]
    assert weblinks.service_of("spotify:track:11dFghVXANMlKmJXsNCbNl") == weblinks.SPOTIFY
    assert weblinks.service_of("hello") is None


# titles of uploads


@pytest.mark.parametrize(
    ("title", "channel", "artists", "song"),
    [
        ("Neon Artist - First Song (Official Video)", "Neon Artist", ["Neon Artist"], "First Song"),
        ("Neon Artist - Song [Official Music Video] [4K]", "X", ["Neon Artist"], "Song"),
        ("Neon Artist - Song (Lyrics)", "Lyrics Channel", ["Neon Artist"], "Song"),
        ("Neon Artist - Song (Neon Remix) [FREE DL]", "x", ["Neon Artist"], "Song (Neon Remix)"),
        ("Neon Artist - Song (Extended Mix)", "x", ["Neon Artist"], "Song (Extended Mix)"),
        ("Neon Artist - Song (Live in Oslo)", "x", ["Neon Artist"], "Song (Live in Oslo)"),
        ("Song - Remastered 2011", "Neon Band - Topic", ["Neon Band"], "Song - Remastered 2011"),
        ("Only A Title", "FixtureSingerVEVO", ["Fixture Singer"], "Only A Title"),
        ("Only A Title", "BandOfficial", ["Band"], "Only A Title"),
        ("Only A Title (Official Audio)", "Fixture Uploads", ["Fixture Uploads"], "Only A Title"),
        ("Artist ft. Guest - Duet", "x", ["Artist", "Guest"], "Duet"),
        ("Artist - Duet feat. Guest (Official Video)", "x", ["Artist", "Guest"], "Duet"),
        ("Artist - Duet (feat. Guest) [Official Audio]", "x", ["Artist"], "Duet (feat. Guest)"),
        ("One, Two & Three - Song", "x", ["One", "Two", "Three"], "Song"),
        ("Duo & Partner - Song", "x", ["Duo & Partner"], "Song"),
        ("Artist with Guest - Song", "x", ["Artist", "Guest"], "Song"),
        ("Artist – Song", "x", ["Artist"], "Song"),
        ("Artist 'Quoted Song' Official", "x", ["Artist"], "Quoted Song"),
        ("Premiere: Artist - Track", "Fixture Blog", ["Artist"], "Track"),
        ("5. Artist - Track", "x", ["Artist"], "Track"),
        ("Artist - Track | Fixture Film | Fixture TV", "x", ["Artist"], "Track"),
        ("Track - Fixture Channel", "Fixture Channel", ["Fixture Channel"], "Track"),
        ("Clip [Part - Two] Extra", "Uploader", ["Uploader"], "Clip [Part - Two] Extra"),
        ("Artist - Track #shorts #music", "x", ["Artist"], "Track"),
        ("Artist - Track free download", "x", ["Artist"], "Track"),
        ("Artist - Utwór (Oficjalny Teledysk)", "x", ["Artist"], "Utwór"),
    ],
)  # fmt: skip
def test_artist_and_title_from_upload_titles(title, channel, artists, song):
    parsed = split_artist_title(title, channel)
    assert (list(parsed.artists), parsed.title) == (artists, song)


def test_channel_names_lose_their_decoration():
    assert clean_channel("Fixture Band - Topic") == "Fixture Band"
    assert clean_channel("FixtureBandVEVO") == "Fixture Band"
    assert clean_channel("FixtureBandMusic") == "Fixture Band"
    assert clean_channel("Fixture Records Music") == "Fixture Records Music"
    assert clean_channel("FIXTURE") == "FIXTURE"


# apple music


def test_apple_album_comes_from_the_lookup_api(web, apple):
    (result,) = _add(web, ALBUM_URL + "?si=share")
    assert result.outcome == "added" and result.kind == KIND_APPLE_ALBUM and result.tracks == 3
    assert result.name == "Neon Fixture Artist - Fixture Tunes - EP"
    assert not result.notes  # apple names are exact, no warning about guessing
    lookup = [r for r in apple.requests if "lookup" in r]
    assert len(lookup) == 1 and "entity=song" in lookup[0] and "country=pl" in lookup[0]
    assert "limit=200" in lookup[0]
    summary = next(c for c in actions.list_collections(web) if c.id == result.collection_id)
    assert not summary.selected  # every new collection starts unselected
    assert summary.source_ref == "https://music.apple.com/pl/album/1000000001"
    assert summary.genre == "Electronic"
    names, rows = _tracks(web, result.collection_id)
    assert names == [(["Neon Fixture Artist"], t) for t in ("First Fixture", "Second Fixture",
                                                            "Third Fixture")]  # fmt: skip
    first = rows[0]
    assert (first["album"], first["track_number"], first["disc_number"]) == (
        "Fixture Tunes - EP",
        1,
        1,
    )
    assert (first["release_date"], first["duration_ms"]) == ("2021-12-03", 332486)
    assert first["cover_url"].endswith("/640x640bb.jpg")
    assert rows[1]["explicit"] == 1
    assert web.repo.find_track_by_source_id("apple:1000000002")["id"] == first["id"]


def test_soulseek_search_drops_ep_and_single_from_the_album(web):
    (result,) = _add(web, ALBUM_URL)
    _, rows = _tracks(web, result.collection_id)
    item = runner.engine_item(web.repo.track_states([rows[0]["id"]])[rows[0]["id"]])
    assert item.album == "Fixture Tunes"
    assert rows[0]["album"] == "Fixture Tunes - EP"  # the stored album stays as apple names it
    assert runner.search_album("Lone Fixture - Single") == "Lone Fixture"
    assert runner.search_album("Single") == "Single"


def test_apple_song_link_goes_to_single_tracks_without_a_copy(web):
    (album,) = _add(web, ALBUM_URL)
    (song,) = _add(web, "https://music.apple.com/pl/album/fixture-tunes-ep/1000000001?i=1000000003")
    assert song.outcome == "added" and song.kind == "track"
    assert song.name == "Neon Fixture Artist - Second Fixture"
    singles = web.repo.find_collection_by_kind(KIND_SINGLES)
    assert song.collection_id == singles["id"]
    assert web.repo.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 3


def test_apple_album_over_200_tracks_reads_the_rest_from_its_page(web, apple):
    huge = {"collectionId": 1000000500, "collectionName": "Huge Fixture Box", "trackCount": 205}
    apple.albums["1000000500"] = {**apple.albums["1000000001"], **huge}
    songs = [fake_song(1000001000 + n, 1000000500, n) for n in range(1, 206)]
    apple.songs.update({str(s["trackId"]): s for s in songs})
    apple.add_page("/pl/album/1000000500", apple_page("1000000500", songs, "Huge Fixture Box"))
    (result,) = _add(web, "https://music.apple.com/pl/album/huge-fixture-box/1000000500")
    assert result.outcome == "added" and result.tracks == 205
    names, _ = _tracks(web, result.collection_id)
    assert names[0][1] == "Fixture Song 1" and names[-1][1] == "Fixture Song 205"
    row = web.repo.get_collection(result.collection_id)
    assert row["snapshot_id"] == web_url.ALBUM_COMPLETE


def test_apple_playlist_comes_from_its_page_with_lookup_details(web, apple):
    (result,) = _add(web, PLAYLIST_URL)
    assert result.outcome == "added" and result.kind == KIND_APPLE_PLAYLIST
    assert result.name == "Fixture Mix" and result.tracks == 3 and not result.notes
    names, rows = _tracks(web, result.collection_id)
    assert names == [
        (["Neon Fixture Artist"], "First Fixture"),
        (["Second Fixture Band"], "Lone Fixture"),
        (["Third Fixture Act"], "Page Only Fixture"),  # the lookup does not know it
    ]
    assert rows[0]["track_number"] == 1  # details from the lookup
    assert (rows[2]["album"], rows[2]["duration_ms"]) == ("Gone Fixture Album", 180000)
    assert rows[2]["cover_url"].endswith("/640x640bb.jpg")
    row = web.repo.get_collection(result.collection_id)
    assert row["owner_id"] == "Fixture Curator" and row["image_url"].endswith("640x640bb.jpg")


def test_apple_playlist_over_300_tracks_says_so(web, apple):
    songs = [fake_song(1000002000 + n, 1000000600, n) for n in range(1, 301)]
    apple.songs.update({str(s["trackId"]): s for s in songs})
    playlist = "pl.ffffffffffffffffffffffffffffffff"
    apple.add_page(f"/pl/playlist/{playlist}", apple_page(playlist, songs, next_page=True))
    (result,) = _add(web, f"https://music.apple.com/pl/playlist/big/{playlist}")
    assert result.outcome == "added" and result.tracks == 300
    assert any("first 300 tracks" in note and ".txt" in note for note in result.notes)
    lines = outcomes.texts(outcomes.add_lines([result]))
    assert "first 300 tracks" in lines
    # 300 ids, two lookups of at most 150
    assert len([r for r in apple.requests if "lookup" in r]) == 2


@pytest.mark.parametrize(
    "page",
    [
        "<html><body>new apple music</body></html>",
        '<script type="application/json" id="serialized-server-data">{"data": []}</script>',
        '<script type="application/json" id="serialized-server-data">{not json</script>',
        '<script type="application/json" id="serialized-server-data">{"data":[{"data":'
        '{"sections":[{"itemKind":"containerDetailHeaderLockup","items":[{"trackCount":5}]}]}}]}'
        "</script>",
    ],
)
def test_apple_page_in_a_new_form_is_a_readable_failure(web, apple, page):
    apple.add_page(f"/pl/playlist/{PLAYLIST_ID}", page)
    (result,) = _add(web, PLAYLIST_URL)
    assert result.outcome == "failed"
    assert "Apple may have changed" in result.message and ".txt" in result.message


@pytest.mark.parametrize(
    ("problem", "words"),
    [
        (httpx.ConnectError("no route"), "No connection to Apple Music"),
        (httpx.ReadTimeout("slow"), "did not answer in time"),
        (429, "refused the request (HTTP 429)"),
        (503, "has a problem right now"),
    ],
)
def test_apple_network_trouble_is_a_readable_failure(web, apple, problem, words):
    if isinstance(problem, int):
        apple.status = problem
    else:
        apple.error = problem
    (result,) = _add(web, ALBUM_URL)
    assert result.outcome == "failed" and words in result.message


def test_apple_album_missing_in_that_country(web):
    (result,) = _add(web, "https://music.apple.com/de/album/nothing/1000009999")
    assert result.outcome == "failed"
    assert "could not find this album" in result.message and "(DE)" in result.message


# youtube


def test_youtube_playlist_entries_become_tracks(web):
    (result,) = _add(web, YOUTUBE_URL + "&si=SHARE")
    assert result.outcome == "added" and result.kind == KIND_YOUTUBE_PLAYLIST
    assert result.name == "Fixture Playlist" and result.tracks == 5
    names, rows = _tracks(web, result.collection_id)
    assert names == [
        (["Neon Fixture Artist"], "First Song"),
        (["Fixture Band"], "Second Song - Remastered 2011"),
        (["Fixture Singer"], "Third Song"),
        (["Fixture DJ"], "Live Set 2025"),
        (["Fixture Artist", "Guest Singer"], "Duet Song"),
    ]
    assert rows[0]["duration_ms"] == 201000 and rows[0]["cover_url"] is None
    notes = " ".join(result.notes)
    assert "2 videos of this playlist are unavailable" in notes
    assert "1 entry is longer than 20 minutes" in notes
    assert "Edit artist and title" in notes  # lower accuracy, say so
    summary = next(c for c in actions.list_collections(web) if c.id == result.collection_id)
    assert not summary.selected and summary.track_count == 7
    assert web.repo.find_track_by_source_id("youtube:fixtureVid1") is not None
    assert web.fake.calls == [("extract", YOUTUBE_URL)]  # the clean link, never audio


def test_youtube_video_uses_youtube_music_names(web):
    (result,) = _add(web, VIDEO_URL)
    assert result.outcome == "added" and result.kind == "track"
    assert result.name == "Neon Fixture Artist - Solo Song"
    row = web.repo.find_track_by_source_id("youtube:fixtureSolo")
    assert (row["title"], row["album"], row["duration_ms"]) == (
        "Solo Song",
        "Solo Fixture Album",
        222000,
    )
    assert web.fake.calls == [("extract", "https://www.youtube.com/watch?v=fixtureSolo")]


def _download_error(message: str, status: int | None = None) -> Exception:
    """what yt-dlp raises: DownloadError around ExtractorError around HTTPError."""
    from yt_dlp.networking import Response
    from yt_dlp.networking.exceptions import HTTPError
    from yt_dlp.utils import DownloadError, ExtractorError

    cause = None
    if status is not None:
        import io

        cause = HTTPError(Response(io.BytesIO(b""), "https://x", {}, status=status))
    inner = ExtractorError(message, cause=cause, expected=True)
    return DownloadError(f"ERROR: {message}", exc_info=(type(inner), inner, None))


@pytest.mark.parametrize(
    ("error", "words"),
    [
        (_download_error("Unable to download API page: HTTP Error 404", 404), "could not find"),
        (_download_error("This playlist is private"), "does not share this playlist"),
        (_download_error("Sign in to confirm you're not a bot"), "does not share this playlist"),
        (_download_error("HTTP Error 429: Too Many Requests", 429), "Wait an hour"),
        (_download_error("Unable to download webpage: getaddrinfo failed"), "No connection to"),
        (KeyError("contents"), "answered in a form NeonSeek cannot read"),
        (RuntimeError("something new"), "newer version of NeonSeek"),
    ],
)  # fmt: skip
def test_youtube_failures_are_readable_and_stop_nothing(web, error, words):
    web.fake.answer(YOUTUBE_URL, error)
    first, second = _add(web, YOUTUBE_URL, ALBUM_URL)
    assert first.outcome == "failed" and words in first.message
    assert second.outcome == "added"  # the next link is added anyway


def test_sync_reads_web_collections_without_spotify(web):
    (yt,) = _add(web, YOUTUBE_URL)
    (sc,) = _add(web, SOUNDCLOUD_URL)
    actions.set_selected(web, [yt.collection_id, sc.collection_id], True)
    outcome = actions.sync(web)
    assert outcome.spotify is None and not outcome.spotify_skipped
    assert [s.outcome for s in outcome.web] == ["unchanged", "unchanged"]
    lines = outcomes.texts(outcomes.all_sync_lines(outcome).lines)
    assert "no changes on YouTube" in lines and "no changes on SoundCloud" in lines
    assert "longer than 20 minutes" not in lines  # notes come again only with changes


def test_sync_keeps_names_the_user_corrected(web):
    (result,) = _add(web, YOUTUBE_URL)
    row = web.repo.find_track_by_source_id("youtube:fixtureVid6")
    web.repo.update_download(row["id"], {"status": db.NOT_FOUND, "attempts": 2})
    actions.edit_track(web, row["id"], "Fixture Artist", "Duet Song (Radio Edit)")
    assert web.repo.get_download(row["id"])["status"] == db.PENDING  # searched again
    # the playlist changes on youtube: one more entry, so it is read and saved again
    playlist = load("ytdlp_youtube_playlist.json")
    playlist["entries"].append({**playlist["entries"][0], "id": "fixtureVid8",
                                "title": "Neon Fixture Artist - Eighth Song"})  # fmt: skip
    web.fake.answer(YOUTUBE_URL, playlist)
    (sync,) = actions.sync(web, result.collection_id).web
    assert sync.outcome == "updated" and sync.result.tracks_new == 1
    names, _ = _tracks(web, result.collection_id)
    assert (["Fixture Artist"], "Duet Song (Radio Edit)") in names
    assert web.repo.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 6


def test_sync_without_spotify_and_without_web_collections_asks_for_spotify(web):
    from neonseek.spotify.auth import NoClientIdError

    with pytest.raises(NoClientIdError):
        actions.sync(web)


# soundcloud


def test_soundcloud_set_with_batched_details(web):
    (result,) = _add(web, SOUNDCLOUD_URL + "?utm_source=clipboard")
    assert result.outcome == "added" and result.kind == KIND_SOUNDCLOUD_SET
    assert result.name == "Fixture Set" and result.tracks == 2
    names, rows = _tracks(web, result.collection_id)
    assert names == [
        (["Neon Fixture Artist"], "Night Drive (Fixture Remix)"),  # the label filled the artist
        (["Fixture Producer"], "Deep Water"),  # no artist anywhere: the uploader
    ]
    assert rows[0]["isrc"] == "XX0000000011" and rows[0]["duration_ms"] == 301500
    assert rows[0]["release_date"] == "2019-10-04"
    assert rows[0]["cover_url"] == "https://i1.sndcdn.com/artworks-fixture0011-t500x500.jpg"
    assert any("1 track of this set could not be read" in n for n in result.notes)
    assert [c[0] for c in web.fake.calls] == ["extract", "batch"]
    assert web.sleeps == []


def test_soundcloud_reads_one_by_one_with_pauses_when_the_batch_breaks(web):
    web.fake.batch_broken = True
    saved = load("ytdlp_soundcloud.json")
    entries = saved["set"]["entries"]
    many = [{**entries[i % 2], "id": str(900000100 + i)} for i in range(12)]
    for i, entry in enumerate(many):
        track = dict(saved["tracks"][str(entries[i % 2]["id"])])
        track.update(id=entry["id"], webpage_url=f"https://soundcloud.com/x/t{i}",
                     title=f"Fixture Track {i}")  # fmt: skip
        entry["url"] = track["webpage_url"]
        web.fake.tracks[entry["id"]] = track
    web.fake.answer(SOUNDCLOUD_URL, {**saved["set"], "entries": many, "playlist_count": 12})
    (result,) = _add(web, SOUNDCLOUD_URL)
    assert result.outcome == "added" and result.tracks == 12
    assert any("track by track" in n for n in result.notes)
    # 12 tracks in chunks of 5: two pauses between the three chunks
    assert web.sleeps == [web_url.SLOW_PAUSE_S, web_url.SLOW_PAUSE_S]
    assert [c[0] for c in web.fake.calls].count("resolve") == 12


def test_soundcloud_track_link_goes_to_single_tracks(web):
    info = load("ytdlp_soundcloud.json")["tracks"]["900000012"]
    web.fake.answer("https://soundcloud.com/fixture-producer/deep-water", info)
    (result,) = _add(web, "https://soundcloud.com/fixture-producer/deep-water?si=x")
    assert result.outcome == "added" and result.name == "Fixture Producer - Deep Water"


def test_ytdlp_contract_on_saved_soundcloud_answers(monkeypatch):
    """the real yt-dlp code on made up soundcloud answers: the flat set and the batched track
    details (inner parts of yt-dlp, D-232). fails when an update of yt-dlp changes them, then
    run scripts/make_web_fixtures.py and check the difference."""
    net = FakeSoundCloudNet()
    monkeypatch.setattr(yt_dlp.YoutubeDL, "urlopen", lambda ydl, request: net.urlopen(ydl, request))
    extractor = ytdlp.YtDlp(cache_dir=None)
    found_set = extractor.extract(SOUNDCLOUD_URL)
    tracks = extractor.soundcloud_tracks(soundcloud_fixture_ids())
    saved = load("ytdlp_soundcloud.json")
    assert found_set["_type"] == "playlist" and found_set["title"] == saved["set"]["title"]
    assert [e["id"] for e in found_set["entries"]] == [e["id"] for e in saved["set"]["entries"]]
    assert [e["url"] for e in found_set["entries"]] == [e["url"] for e in saved["set"]["entries"]]
    assert sorted(tracks) == sorted(saved["tracks"]) == ["900000011", "900000012"]
    for track_id, info in tracks.items():
        expected = saved["tracks"][track_id]
        for key in ("title", "uploader", "duration", "artists", "isrc", "cover_url",
                    "release_timestamp", "webpage_url"):  # fmt: skip
            assert info.get(key) == expected.get(key), (track_id, key)
    # one request for all details, and no request per cover
    assert [r for r in net.requests if "/tracks" in r] == [
        "GET https://api-v2.soundcloud.com/tracks"
    ]
    assert not [r for r in net.requests if "artworks" in r]


def test_ytdlp_cache_stays_in_the_data_folder(ctx, tmp_path):
    ctx.ytdlp_factory = ytdlp.YtDlp
    extractor = ctx.web().extractor
    assert extractor.cache_dir == config.cache_dir(ctx.data_dir) / "yt-dlp"
    options = extractor._options()
    assert options["cachedir"] == str(ctx.data_dir / "cache" / "yt-dlp")
    assert options["extract_flat"] == "in_playlist" and options["skip_download"]
    assert "cookiefile" not in options and "cookiesfrombrowser" not in options


# itunes and the music app export


ITUNES_HEADER = (
    "Name\tArtist\tComposer\tAlbum\tGrouping\tWork\tMovement Number\tMovement Count\t"
    "Movement Name\tGenre\tSize\tTime\tDisc Number\tDisc Count\tTrack Number\tTrack Count\t"
    "Year\tDate Modified\tDate Added\tBit Rate\tSample Rate\tVolume Adjustment\tKind\t"
    "Equalizer\tComments\tPlays\tLast Played\tSkips\tLast Skipped\tMy Rating\tLocation"
)


def _itunes_row(title: str, artist: str, album: str, seconds: int) -> str:
    cells = [""] * 31
    cells[0], cells[1], cells[3], cells[11] = title, artist, album, str(seconds)
    cells[22] = "MPEG audio file"
    return "\t".join(cells)


def test_itunes_unicode_text_export_imports(tmp_path):
    path = tmp_path / "Fixture Playlist.txt"
    rows = [ITUNES_HEADER, _itunes_row("Zażółć Fixture", "Neon Artist, Guest", "Album", 245),
            _itunes_row("Second", "Solo", "Other", 61)]  # fmt: skip
    # unicode text from itunes: utf-16 with a bom, old mac line ends
    path.write_bytes(("\r".join(rows) + "\r").encode("utf-16"))
    parsed = csv_file.parse_csv(path)
    assert parsed.table.encoding == "utf-16" and parsed.table.delimiter == "\t"
    assert parsed.column_names() == {"title": "Name", "artist": "Artist", "album": "Album",
                                     "length": "Time"}  # fmt: skip
    assert parsed.length_unit == "s"
    first = parsed.tracks[0]
    assert (first.title, first.artists, first.duration_ms) == (
        "Zażółć Fixture", ("Neon Artist", "Guest"), 245000)  # fmt: skip


def test_itunes_export_with_polish_headers_without_bom(tmp_path):
    path = tmp_path / "lista.txt"
    header = "Nazwa\tWykonawca\tKompozytor\tAlbum\tGatunek\tCzas\tRok"
    row = "Pierwszy\tNeon Fixture Artist\t\tAlbum\tTechno\t200\t2020"
    path.write_bytes(f"{header}\r\n{row}\r\n".encode("utf-16-le"))
    parsed = csv_file.parse_csv(path)
    assert parsed.column_names() == {"title": "Nazwa", "artist": "Wykonawca", "album": "Album",
                                     "length": "Czas"}  # fmt: skip
    assert parsed.tracks[0].duration_ms == 200000


# cli


def _cli(capsys, ctx, *args):
    code = cli.main(["--data-dir", str(ctx.data_dir), *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_adds_and_syncs_without_spotify(web, capsys):
    code, out, _ = _cli(capsys, web, "add", ALBUM_URL, YOUTUBE_URL)
    assert code == 0 and "Added: Neon Fixture Artist - Fixture Tunes - EP" in out
    assert "Added: Fixture Playlist" in out and "unavailable" in out
    code, out, _ = _cli(capsys, web, "sync")
    assert code == 0 and "No collections are selected" in out
    code, out, _ = _cli(capsys, web, "select", "1")
    code, out, err = _cli(capsys, web, "sync")
    assert (
        code == 0
        and "[1] Neon Fixture Artist - Fixture Tunes - EP: no changes on Apple Music." in out
    )


def test_cli_mixed_links_without_spotify_add_the_others(web, capsys):
    spotify = "https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl"
    code, out, _ = _cli(capsys, web, "add", spotify, ALBUM_URL)
    assert code == 1
    assert "Not added: https://open.spotify.com/track/" in out and "Client ID" in out
    assert "Added: Neon Fixture Artist - Fixture Tunes - EP" in out


def test_cli_track_edit(web, capsys):
    _add(web, YOUTUBE_URL)
    row = web.repo.find_track_by_source_id("youtube:fixtureVid3")
    code, out, _ = _cli(capsys, web, "track", "edit", str(row["id"]), "--artist", "Fixture Singer",
                        "--title", "Third Song (Acoustic)")  # fmt: skip
    assert code == 0 and "is now: Fixture Singer - Third Song (Acoustic)" in out
    code, _, err = _cli(capsys, web, "track", "edit", str(row["id"]), "--title", "  ")
    assert code == 1 and "cannot be empty" in err
    code, _, err = _cli(capsys, web, "track", "edit", str(row["id"]))
    assert code == 1 and "--artist" in err


def test_edit_refuses_a_downloaded_track(web):
    _add(web, ALBUM_URL)
    row = web.repo.find_track_by_source_id("apple:1000000002")
    web.repo.update_download(row["id"], {"status": db.DONE, "file_path": "x.flac"})
    with pytest.raises(actions.TrackEditError) as caught:
        actions.edit_track(web, row["id"], "A", "B")
    assert "Delete the file first" in caught.value.message


# what the window shows


def test_track_menu_offers_edit_for_one_track_without_a_file(web):
    _add(web, ALBUM_URL)
    ids = [web.repo.find_track_by_source_id(f"apple:100000000{n}")["id"] for n in (2, 3)]
    rows = list(views.tracks_for(web, ids, {}).values())
    one = views.track_choice(rows[:1], downloading=False)
    assert one.targets(views.MENU_EDIT) == ids[:1]
    assert views.menu_reason(views.MENU_EDIT, one) is None
    assert views.menu_label(views.MENU_EDIT, one) == "Edit artist and title..."
    two = views.track_choice(rows, downloading=False)
    assert views.menu_reason(views.MENU_EDIT, two) == "Choose one track to correct."
    busy = views.track_choice(rows[:1], downloading=True)
    assert views.menu_reason(views.MENU_EDIT, busy) == views.running_reason()


def test_kinds_have_names_and_sync_labels():
    assert views.collection_kind_label(KIND_YOUTUBE_PLAYLIST, "url") == "YouTube playlist"
    assert views.collection_kind_label(KIND_APPLE_ALBUM, "url") == "Apple Music album"
    assert views.sync_label(KIND_SOUNDCLOUD_SET) == "Read again from SoundCloud"
    assert views.sync_label(KIND_APPLE_PLAYLIST) == "Read again from Apple Music"
    assert views.sync_label("csv") is None
    assert views.is_web(KIND_YOUTUBE_PLAYLIST) and not views.is_spotify(KIND_YOUTUBE_PLAYLIST)
