from __future__ import annotations

import pytest

from neonseek.spotify.urls import (
    SpotifyRef,
    UnsupportedLinkError,
    parse_spotify_ref,
    parse_track_id,
)

PID = "37i9dQZF1DXcBWIGoYBM5M"
AID = "4aawyAB9vmqN3uQ7FjRGTy"
TID = "11dFghVXANMlKmJXsNCbNl"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"https://open.spotify.com/playlist/{PID}", SpotifyRef("playlist", PID)),
        (
            f"https://open.spotify.com/playlist/{PID}?utm_source=copy-link",
            SpotifyRef("playlist", PID),
        ),
        (
            f"https://open.spotify.com/intl-pl/album/{AID}?nd=1&utm_medium=x",
            SpotifyRef("album", AID),
        ),
        (f"https://open.spotify.com/intl-pt-br/track/{TID}", SpotifyRef("track", TID)),
        (f"http://open.spotify.com/track/{TID}/", SpotifyRef("track", TID)),
        (f"open.spotify.com/track/{TID}", SpotifyRef("track", TID)),
        (f"  https://OPEN.spotify.com/track/{TID}#x  ", SpotifyRef("track", TID)),
        (f"https://open.spotify.com/user/someone/playlist/{PID}", SpotifyRef("playlist", PID)),
        (f"spotify:playlist:{PID}", SpotifyRef("playlist", PID)),
        (f"spotify:album:{AID}", SpotifyRef("album", AID)),
        (f"spotify:track:{TID}", SpotifyRef("track", TID)),
        (f"spotify:user:someone:playlist:{PID}", SpotifyRef("playlist", PID)),
    ],
)
def test_supported_forms(text, expected):
    assert parse_spotify_ref(text) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("https://open.spotify.com/artist/0OdUWJ0sBjDrqHygGUXeCF", "artist link"),
        ("https://open.spotify.com/show/5CfCWKI5pZ28U0uOzXkDHe", "show link"),
        ("https://open.spotify.com/episode/5CfCWKI5pZ28U0uOzXkDHe", "episode link"),
        ("spotify:artist:0OdUWJ0sBjDrqHygGUXeCF", "artist link"),
        ("https://spotify.link/AbCdEf", "Short spotify.link"),
        ("https://www.youtube.com/watch?v=x", "does not look like"),
        ("not a link", "does not look like"),
        ("", "does not look like"),
        ("https://open.spotify.com/", "does not look like"),
        ("https://open.spotify.com/track/", "incomplete"),
        ("https://open.spotify.com/track/short", "incomplete"),
        ("spotify:track:bad", "incomplete"),
        ("spotify:user:someone:collection", "does not look like"),
    ],
)
def test_unsupported_forms_give_readable_errors(text, message):
    with pytest.raises(UnsupportedLinkError, match=message):
        parse_spotify_ref(text)


def test_uri_property():
    assert SpotifyRef("track", TID).uri == f"spotify:track:{TID}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (TID, TID),
        (f"spotify:track:{TID}", TID),
        (f"https://open.spotify.com/track/{TID}?nd=1", TID),
        (f"spotify:album:{AID}", None),
        ("garbage", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_track_id(text, expected):
    assert parse_track_id(text) == expected
