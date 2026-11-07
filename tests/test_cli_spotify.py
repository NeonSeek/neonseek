from __future__ import annotations

import httpx

from neonseek import actions
from neonseek.cli import main


def _run(capsys, data_dir, *argv):
    code = main(["--data-dir", str(data_dir), *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_spotify_commands_without_setup_say_what_to_do(capsys, tmp_path):
    data = tmp_path / "data"
    code, _, err = _run(capsys, data, "sync")
    assert code == 1
    assert "Client ID in Settings" in err and "neonseek spotify client-id" in err
    code, _, err = _run(
        capsys, data, "add", "https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl"
    )
    assert code == 1 and "Client ID" in err

    code, out, _ = _run(capsys, data, "spotify", "client-id", "my-client")
    assert code == 0 and "http://127.0.0.1:43821/callback" in out
    code, out, _ = _run(capsys, data, "spotify", "client-id")
    assert out.strip() == "my-client"
    code, _, err = _run(capsys, data, "sync")
    assert code == 1 and "Log in to Spotify" in err and "neonseek spotify login" in err


def test_client_id_is_not_a_secret_and_token_is(capsys, tmp_path, _isolated):
    data = tmp_path / "data"
    _run(capsys, data, "spotify", "client-id", "my-client")
    assert 'spotify_client_id = "my-client"' in (data / "settings.toml").read_text("utf-8")
    ctx = actions.AppContext(data)
    ctx.token_store.save("refresh-secret")
    ctx.close()
    # the refresh token is only in the (fake) credential manager, never in files
    for path in data.rglob("*"):
        if path.is_file():
            assert b"refresh-secret" not in path.read_bytes(), path
    assert "refresh-secret" in _isolated.values.values()
    code, out, _ = _run(capsys, data, "spotify", "logout")
    assert code == 0 and not _isolated.values


def test_add_and_sync_print_results(capsys, tmp_path, fake_spotify, monkeypatch, _isolated):
    data = tmp_path / "data"
    monkeypatch.setattr(
        actions, "new_http_client", lambda: httpx.Client(transport=fake_spotify.transport())
    )
    _run(capsys, data, "spotify", "client-id", "cid")
    ctx = actions.AppContext(data)
    ctx.token_store.save("refresh-0")
    ctx.close()

    ids = fake_spotify.ids
    code, out, _ = _run(
        capsys,
        data,
        "add",
        f"https://open.spotify.com/album/{ids['multi_album']}",
        f"https://open.spotify.com/playlist/{ids['spotify_playlist']}",
    )
    assert code == 1
    assert "Added:" in out and "10 tracks" in out
    assert "Not added:" in out and "made by Spotify" in out

    code, out, _ = _run(capsys, data, "sync")
    assert code == 0
    assert "Spotify playlists on your account: 2 (2 new)." in out
    # the added album starts unselected like every collection
    assert "No Spotify collections are selected" in out
    code, out, _ = _run(capsys, data, "collections")
    assert "spotify_album" in out and "0/10" in out
    album_line = next(line for line in out.splitlines() if "spotify_album" in line)
    assert "[ ]" in album_line

    _run(capsys, data, "select", album_line.split()[0])
    code, out, _ = _run(capsys, data, "sync")
    assert code == 0 and "no changes on Spotify" in out  # albums are not read again
