"""tidal links read from the public embed pages, on saved pages with made up data (D-238)."""

from __future__ import annotations

import json
import re

import httpx
import pytest
from mutagen.flac import FLAC

from download_env import needs_sockseek
from neonseek import actions, cli, outcomes, views
from neonseek.pipeline import runner
from neonseek.sources import links as weblinks
from neonseek.sources import tidal, web_url
from neonseek.sources.base import KIND_SINGLES, KIND_TIDAL_ALBUM, KIND_TIDAL_PLAYLIST
from web_fake import TIDAL_ALBUM_ID, TIDAL_PLAYLIST_ID, TIDAL_TRACK_ID, text, tidal_page

PLAYLIST_URL = f"https://tidal.com/playlist/{TIDAL_PLAYLIST_ID}"
ALBUM_URL = f"https://tidal.com/album/{TIDAL_ALBUM_ID}"
TRACK_URL = f"https://tidal.com/track/{TIDAL_TRACK_ID}"


def _add(ctx, *links):
    return actions.add_links(ctx, list(links))


def _tracks(ctx, collection_id):
    return ctx.repo.conn.execute(
        "SELECT t.* FROM collection_tracks ct JOIN tracks t ON t.id = ct.track_id "
        "WHERE ct.collection_id = ? ORDER BY ct.position",
        (collection_id,),
    ).fetchall()


def _names(rows):
    return [(json.loads(r["artists_json"] or "[]"), r["title"]) for r in rows]


# links


@pytest.mark.parametrize(
    ("link", "kind", "item", "clean"),
    [
        (f"https://tidal.com/playlist/{TIDAL_PLAYLIST_ID}?u", "playlist", TIDAL_PLAYLIST_ID,
         PLAYLIST_URL),
        (f"https://tidal.com/playlist/{TIDAL_PLAYLIST_ID}/u", "playlist", TIDAL_PLAYLIST_ID,
         PLAYLIST_URL),
        (f"https://www.tidal.com/browse/playlist/{TIDAL_PLAYLIST_ID.upper()}?utm_source=x",
         "playlist", TIDAL_PLAYLIST_ID, PLAYLIST_URL),
        (f"listen.tidal.com/playlist/{TIDAL_PLAYLIST_ID}", "playlist", TIDAL_PLAYLIST_ID,
         PLAYLIST_URL),
        ("https://tidal.com/browse/album/900000100/u?play=true", "album", "900000100", ALBUM_URL),
        ("https://listen.tidal.com/album/900000100?u", "album", "900000100", ALBUM_URL),
        ("https://tidal.com/album/900000100/track/900000101", "track", "900000101", TRACK_URL),
        ("https://listen.tidal.com/album/900000100/track/900000101/u", "track", "900000101",
         TRACK_URL),
        ("https://tidal.com/browse/track/900000101?u&si=share", "track", "900000101", TRACK_URL),
    ],
)  # fmt: skip
def test_tidal_links_keep_only_what_they_mean(link, kind, item, clean):
    assert weblinks.service_of(link) == weblinks.TIDAL
    ref = weblinks.parse_web_ref(link)
    assert (ref.service, ref.kind, ref.id, ref.url) == ("tidal", kind, item, clean)


@pytest.mark.parametrize(
    "link", ["https://tidal.com/mix/0123abcd", "https://listen.tidal.com/artist/800000001/u"]
)
def test_tidal_mixes_and_artists_say_they_are_not_supported(link):
    with pytest.raises(weblinks.UnsupportedWebLinkError) as caught:
        weblinks.parse_web_ref(link)
    assert "mixes and artist pages cannot be added" in caught.value.message


@pytest.mark.parametrize(
    "link",
    [
        "https://tidal.com/video/910000001",
        "https://tidal.com/playlist/not-a-playlist-id",
        "https://tidal.com/album/abc",
        "https://tidal.com/",
    ],
)
def test_other_tidal_pages_say_what_to_paste(link):
    with pytest.raises(weblinks.UnsupportedWebLinkError) as caught:
        weblinks.parse_web_ref(link)
    assert "not a playlist, an album or a track" in caught.value.message


def test_tidal_links_are_found_in_pasted_text():
    pasted = f"look {PLAYLIST_URL}?u and https://tidal.com/mix/x {ALBUM_URL}"
    assert weblinks.find_links(pasted) == [f"{PLAYLIST_URL}?u", "https://tidal.com/mix/x",
                                           ALBUM_URL]  # fmt: skip


# reading


def test_tidal_playlist_keeps_versions_and_leaves_videos_out(web, apple):
    (result,) = _add(web, f"{PLAYLIST_URL}?u")
    assert result.outcome == "added" and result.kind == KIND_TIDAL_PLAYLIST
    assert result.name == "Fixture Night Mix" and result.tracks == 4
    # names on tidal are exact: no warning about guessed names, only the left out video
    assert result.notes == ["1 video of this list was left out, NeonSeek reads only tracks."]
    rows = _tracks(web, result.collection_id)
    assert _names(rows) == [
        (["Fixture Artist One"], "Neon Fixture Song"),
        (["Fixture Singer", "Fixture Producer"], "Duet Fixture"),
        (["Fixture Band"], "Night Fixture (Fixture Remix)"),
        (["Fixture Beatmaker"], "Loop=Fixture (Instrumental) & Rock 'n' Roll / Dub"),
    ]
    first, last = rows[0], rows[3]
    assert (first["album"], first["duration_ms"], first["explicit"]) == (
        "Fixture Album One", 276000, 0)  # fmt: skip
    assert first["cover_url"].endswith("/640x640.jpg")
    assert first["isrc"] is None
    assert (last["album"], last["duration_ms"], last["explicit"]) == (
        "Fixture Beats – Anniversary", 729000, 1)  # fmt: skip
    summary = next(c for c in actions.list_collections(web) if c.id == result.collection_id)
    assert not summary.selected  # every new collection starts unselected
    assert summary.source_ref == PLAYLIST_URL
    assert web.repo.find_track_by_source_id("tidal:900000003")["id"] == rows[2]["id"]
    assert web.repo.find_track_by_source_id("tidal:910000001") is None  # the video
    assert apple.tidal.requests == [f"https://embed.tidal.com/playlists/{TIDAL_PLAYLIST_ID}"]
    lines = outcomes.texts(outcomes.add_lines([result]))
    assert "1 video of this list was left out" in lines


def test_tidal_album_puts_its_artist_on_every_track(web):
    (result,) = _add(web, ALBUM_URL)
    assert result.outcome == "added" and result.kind == KIND_TIDAL_ALBUM and result.tracks == 3
    assert result.name == "Fixture Headliner - Fixture Revival" and not result.notes
    rows = _tracks(web, result.collection_id)
    assert _names(rows) == [
        (["Fixture Headliner"], "Opening Fixture"),
        (["Fixture Headliner", "Fixture Guest"], "Fixture Guest (feat. Fixture Guest)"),
        (["Fixture Headliner"], "Fixture Finale"),
    ]
    assert json.loads(rows[1]["album_artists_json"]) == ["Fixture Headliner"]
    assert rows[1]["album"] == "Fixture Revival"
    row = web.repo.get_collection(result.collection_id)
    assert row["snapshot_id"] == web_url.ALBUM_COMPLETE
    assert row["image_url"].endswith("/1080x1080.jpg")


def test_tidal_track_link_goes_to_single_tracks_without_a_copy(web):
    (album,) = _add(web, ALBUM_URL)
    (song,) = _add(web, f"https://listen.tidal.com/album/{TIDAL_ALBUM_ID}/track/{TIDAL_TRACK_ID}")
    assert song.outcome == "added" and song.kind == "track" and not song.notes
    assert song.collection_id == web.repo.find_collection_by_kind(KIND_SINGLES)["id"]
    # the same tidal id: the single is the album's first track, not a new one
    assert web.repo.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 3
    single = web.repo.find_track_by_source_id(f"tidal:{TIDAL_TRACK_ID}")
    assert single["id"] == _tracks(web, album.collection_id)[0]["id"]


def test_tidal_list_added_twice_is_kept_once(web):
    (first,) = _add(web, PLAYLIST_URL)
    (second,) = _add(web, f"https://www.tidal.com/browse/playlist/{TIDAL_PLAYLIST_ID}/u")
    assert second.outcome == "exists" and second.collection_id == first.collection_id
    assert web.repo.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 4


def test_tidal_requests_keep_a_pause(web, apple):
    _add(web, PLAYLIST_URL, ALBUM_URL, TRACK_URL)
    assert len(apple.tidal.requests) == 3
    # two pauses: before the second and before the third request
    waits = [s for s in web.sleeps if s > 0]
    assert len(waits) == 2 and all(0 < s <= tidal.PAUSE_S for s in waits)


def test_tidal_shows_at_most_50_and_says_so(web, apple):
    playlist = "11111111-2222-4333-8444-555555555555"
    apple.tidal.add_page(f"/playlists/{playlist}", tidal_page(50))
    (result,) = _add(web, f"https://tidal.com/playlist/{playlist}")
    assert result.outcome == "added" and result.tracks == 50
    assert any("at most 50 tracks" in n and "CSV" in n for n in result.notes)
    lines = outcomes.texts(outcomes.add_lines([result]))
    assert "at most 50 tracks" in lines
    # an incomplete list does not block the next sync: it is read again, the same content
    # is not saved again
    actions.set_selected(web, [result.collection_id], True)
    before = len(apple.tidal.requests)
    (sync,) = actions.sync(web).web
    assert sync.outcome == "unchanged" and len(apple.tidal.requests) == before + 1
    # a shorter list reads without the warning
    apple.tidal.add_page(f"/playlists/{playlist}", tidal_page(49))
    (sync,) = actions.sync(web).web
    assert sync.outcome == "updated" and not sync.notes


def test_tidal_album_of_50_is_read_again_on_sync(web, apple):
    apple.tidal.add_page("/albums/900000200", tidal_page(50, first_id=900200001))
    (result,) = _add(web, "https://tidal.com/album/900000200")
    assert any("at most 50 tracks" in n for n in result.notes)
    assert web.repo.get_collection(result.collection_id)["snapshot_id"] != web_url.ALBUM_COMPLETE


def test_complete_tidal_album_is_not_read_again(web, apple):
    (result,) = _add(web, ALBUM_URL)
    actions.set_selected(web, [result.collection_id], True)
    before = len(apple.tidal.requests)
    (sync,) = actions.sync(web).web
    assert sync.outcome == "unchanged" and len(apple.tidal.requests) == before


def test_tidal_sync_reads_selected_playlists_without_spotify(web):
    (result,) = _add(web, PLAYLIST_URL)
    actions.set_selected(web, [result.collection_id], True)
    outcome = actions.sync(web)
    assert outcome.spotify is None and [s.outcome for s in outcome.web] == ["unchanged"]
    lines = outcomes.texts(outcomes.all_sync_lines(outcome).lines)
    assert "no changes on TIDAL" in lines


# failures are messages, nothing crashes


def test_unknown_tidal_id_is_a_readable_failure_and_the_rest_is_added(web):
    first, second = _add(web, "https://tidal.com/album/900009999", PLAYLIST_URL)
    assert first.outcome == "failed"
    assert "TIDAL could not find this album" in first.message and "public" in first.message
    assert second.outcome == "added"


@pytest.mark.parametrize(
    ("problem", "words"),
    [
        (httpx.ConnectError("no route"), "No connection to TIDAL"),
        (httpx.ReadTimeout("slow"), "TIDAL did not answer in time"),
        (429, "TIDAL refused the request (HTTP 429)"),
        (403, "TIDAL refused the request (HTTP 403)"),
        (503, "TIDAL has a problem right now"),
        (302, "TIDAL may have changed its pages"),
    ],
)
def test_tidal_network_trouble_is_a_readable_failure(web, apple, problem, words):
    if isinstance(problem, int):
        apple.tidal.status = problem
    else:
        apple.tidal.error = problem
    (result,) = _add(web, PLAYLIST_URL)
    assert result.outcome == "failed" and words in result.message


def test_tidal_blocked_during_sync_fails_alone(web, apple):
    (result,) = _add(web, PLAYLIST_URL)
    actions.set_selected(web, [result.collection_id], True)
    apple.tidal.status = 429
    (sync,) = actions.sync(web).web
    assert sync.outcome == "failed" and "HTTP 429" in sync.message
    lines = outcomes.all_sync_lines(actions.sync(web))
    assert lines.failed  # shown as a failure, the app goes on
    assert len(_tracks(web, result.collection_id)) == 4  # what was read stays


def _without_items(page: str) -> str:
    return re.sub(r"<list-item\b.*?</list-item>", "", page, flags=re.S)


@pytest.mark.parametrize(
    "page",
    [
        "<html><body>a new tidal</body></html>",
        # entries are there, but the title slot is gone
        text("tidal_embed_playlist.html").replace('slot="title"', 'slot="name"'),
        # the list is renamed
        text("tidal_embed_playlist.html").replace("media-item-list", "track-list"),
    ],
    ids=["other page", "no title slot", "list renamed"],
)
def test_tidal_page_in_a_new_form_is_a_readable_failure(web, apple, page):
    apple.tidal.add_page(f"/playlists/{TIDAL_PLAYLIST_ID}", page)
    (result,) = _add(web, PLAYLIST_URL)
    assert result.outcome == "failed"
    assert "TIDAL may have changed its pages" in result.message and "CSV" in result.message


def test_empty_tidal_playlist_is_added_empty(web, apple):
    apple.tidal.add_page(
        f"/playlists/{TIDAL_PLAYLIST_ID}", _without_items(text("tidal_embed_playlist.html"))
    )
    (result,) = _add(web, PLAYLIST_URL)
    assert result.outcome == "added" and result.tracks == 0 and not result.notes


def test_tidal_track_page_without_a_title_is_a_readable_failure(web, apple):
    page = text("tidal_embed_track.html").replace("media-title", "media-heading")
    apple.tidal.add_page(f"/tracks/{TIDAL_TRACK_ID}", page)
    (result,) = _add(web, TRACK_URL)
    assert result.outcome == "failed" and "TIDAL may have changed its pages" in result.message


# the contract: what the parser makes of the saved embed pages. the pages keep the markup of
# real ones (docs/tidal-notes.md). when tidal changes its pages, save a new page with made up
# data in the same way, and this test shows what broke.


def test_embed_contract_playlist():
    found = tidal.parse_list(text("tidal_embed_playlist.html"), TIDAL_PLAYLIST_ID, album=False)
    assert (found.name, found.entries, found.videos, found.complete) == (
        "Fixture Night Mix", 5, 1, True)  # fmt: skip
    assert found.image_url.endswith("/1080x1080.jpg")
    first = found.tracks[0]
    assert first == tidal.TrackRecord(
        title="Neon Fixture Song",
        artists=("Fixture Artist One",),
        album="Fixture Album One",
        duration_ms=276000,
        cover_url="https://resources.tidal.com/images/00000000/0000/0000/0000/000000000001/"
        "640x640.jpg",
        explicit=False,
        source_id="tidal:900000001",
    )
    # the version is only in the html title, the metadata has the bare title
    assert found.tracks[2].title == "Night Fixture (Fixture Remix)"


def test_embed_track_without_artist_links_takes_the_metadata_artists():
    page = text("tidal_embed_playlist.html")
    page = re.sub(
        r'(<span slot="artist">)<a href="https://tidal.com/artist/800000002".*?(</span>)',
        r"\1\2",
        page,
        count=1,
        flags=re.S,
    )
    found = tidal.parse_list(page, TIDAL_PLAYLIST_ID, album=False)
    assert found.tracks[1].artists == ("Fixture Singer", "Fixture Producer")
    # no artist anywhere: the track stays, the user can name it
    page = page.replace('"artist": "Fixture Singer, Fixture Producer"', '"artist": ""')
    found = tidal.parse_list(page, TIDAL_PLAYLIST_ID, album=False)
    assert found.tracks[1].artists == () and found.tracks[1].title == "Duet Fixture"


def test_embed_contract_album_and_track():
    album = tidal.parse_list(text("tidal_embed_album.html"), TIDAL_ALBUM_ID, album=True)
    assert [t.artists for t in album.tracks] == [
        ("Fixture Headliner",),
        ("Fixture Headliner", "Fixture Guest"),
        ("Fixture Headliner",),
    ]
    assert [t.explicit for t in album.tracks] == [True, False, False]
    track = tidal.parse_track(text("tidal_embed_track.html"), TIDAL_TRACK_ID)
    assert (track.title, track.artists, track.album, track.duration_ms, track.explicit) == (
        "Opening Fixture's Edit",
        ("Fixture Headliner",),
        "Fixture Revival",
        208000,
        True,
    )
    with pytest.raises(tidal.TidalError):
        tidal.parse_list(text("tidal_embed_error.html"), "x", album=False)


@pytest.mark.parametrize(
    ("text_value", "ms"),
    [("4:36", 276000), ("12:09", 729000), ("1:02:03", 3723000), ("", None), ("x:10", None),
     ("0:00", None)],
)  # fmt: skip
def test_embed_durations(text_value, ms):
    assert tidal.duration_ms(text_value) == ms


# a track tidal shows without any artist, as on some real playlists


def _artistless_page() -> str:
    """the saved playlist with only its second entry, whose artist is gone from the html and
    the metadata."""
    page = text("tidal_embed_playlist.html")
    items = re.findall(r"<list-item\b.*?</list-item>", page, flags=re.S)
    keep = next(i for i in items if 'product-id="900000002"' in i)
    keep = re.sub(r'(<span slot="artist">).*?(</span>)', r"\1\2", keep, count=1, flags=re.S)
    page = re.sub(
        r'(<media-item-list role="list">).*?(</media-item-list>)',
        lambda m: m.group(1) + keep + m.group(2),
        page,
        count=1,
        flags=re.S,
    )
    return page.replace('"artist": "Fixture Singer, Fixture Producer"', '"artist": ""')


@needs_sockseek
def test_tidal_track_without_artist_downloads_with_a_clean_name(web, apple, env):
    apple.tidal.add_page(f"/playlists/{TIDAL_PLAYLIST_ID}", _artistless_page())
    (result,) = _add(web, PLAYLIST_URL)
    assert result.outcome == "added" and result.tracks == 1
    (row,) = _tracks(web, result.collection_id)
    assert row["artists_json"] in (None, "[]") and row["title"] == "Duet Fixture"
    env.file("Some Uploader/Fixture Duets/02 - Duet Fixture.flac", 191)
    outcome = env.run([result.collection_id])
    assert outcome.ended == runner.COMPLETED
    state = env.state(row["id"])
    assert state["status"] == "done"
    # soulseek is searched by the title alone, the artist column stays empty
    (searched,) = env.run_csv(outcome.run_id, 1)
    assert (searched["Artist"], searched["Title"]) == ("", "Duet Fixture")
    assert env.library_files() == ["Duet Fixture.flac"]
    assert runner.track_label(row) == "Duet Fixture"
    audio = FLAC(state["file_path"])
    assert audio["TITLE"] == ["Duet Fixture"] and audio["ALBUM"] == ["Fixture Duets"]
    assert "ARTIST" not in audio and "ALBUMARTIST" not in audio
    playlist = env.library / "playlists" / "Fixture Night Mix.m3u8"
    lines = playlist.read_text(encoding="utf-8").splitlines()
    assert "#EXTINF:191,Duet Fixture" in lines and "..\\tracks\\Duet Fixture.flac" in lines
    assert not any(" - " in line for line in lines)


# cli and window texts


def test_cli_adds_and_syncs_a_tidal_link(web, capsys):
    code = cli.main(["--data-dir", str(web.data_dir), "add", f"{PLAYLIST_URL}?u"])
    out = capsys.readouterr().out
    assert code == 0 and "Added: Fixture Night Mix" in out and "1 video" in out
    cli.main(["--data-dir", str(web.data_dir), "select", "1"])
    capsys.readouterr()
    code = cli.main(["--data-dir", str(web.data_dir), "sync"])
    assert code == 0 and "[1] Fixture Night Mix: no changes on TIDAL." in capsys.readouterr().out


def test_tidal_kinds_have_names_and_sync_labels():
    assert views.collection_kind_label(KIND_TIDAL_PLAYLIST, "url") == "TIDAL playlist"
    assert views.collection_kind_label(KIND_TIDAL_ALBUM, "url") == "TIDAL album"
    assert views.sync_label(KIND_TIDAL_ALBUM) == "Read again from TIDAL"
    assert views.is_web(KIND_TIDAL_PLAYLIST) and not views.is_spotify(KIND_TIDAL_PLAYLIST)
    assert weblinks.service_name(weblinks.TIDAL) == "TIDAL"
