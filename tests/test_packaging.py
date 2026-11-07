"""phase 4: the package without building it. source address, paths of the installed program,
license texts, the exe version and the installer script."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import neonseek
from neonseek import actions, manual, project

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repo_address_is_in_one_place():
    # the addresses (D-265) are written only in neonseek/project.py, the marker is gone
    assert project.REPO_URL == "https://github.com/NeonSeek/neonseek"
    assert project.DONATION_LINK == "https://buymeacoffee.com/neonseek"
    for text in (r"""["']REPO_URL["']""", "github.com/NeonSeek", "buymeacoffee.com"):
        found = re.compile(text, re.IGNORECASE)
        places = [
            path.relative_to(ROOT).as_posix()
            for folder in ("src", "scripts", "installer")
            for path in (ROOT / folder).rglob("*")
            if path.is_file()
            and path.suffix in (".py", ".ps1", ".iss", ".spec")
            and found.search(path.read_text(encoding="utf-8-sig"))
        ]
        assert places == ([] if "REPO_URL" in text else ["src/neonseek/project.py"]), text
    assert project.source_url("1.2.3") == f"{project.REPO_URL}/tree/v1.2.3"
    assert project.source_url(neonseek.__version__) in actions.source_code()


def test_installed_program_finds_manual_and_licenses_next_to_the_exe(monkeypatch, tmp_path):
    exe = tmp_path / "NeonSeek" / "NeonSeek.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert manual.manual_dir() == exe.parent / "manual"
    assert actions.license_texts() == exe.parent / "THIRD_PARTY_LICENSES.txt"


def test_source_checkout_points_to_the_repository():
    assert manual.manual_dir() == ROOT / "docs" / "manual"
    assert actions.license_texts() == ROOT / "THIRD_PARTY_LICENSES"
    assert actions.license_texts().is_file()


def test_license_texts_of_everything_that_ships(tmp_path):
    collect = _script("collect_licenses")
    names = {re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower() for d in collect.runtime_packages()}
    assert {"httpx", "keyring", "mutagen", "platformdirs", "pyside6-essentials"} <= names
    assert not names & {"neonseek", "pytest", "ruff", "pyinstaller", "setuptools"}
    sockseek = tmp_path / "sockseek"
    sockseek.mkdir()
    (sockseek / "SOURCE.txt").write_text("Sockseek 3.0.5 (AGPL-3.0)\nSource: x", encoding="utf-8")
    (sockseek / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE", encoding="utf-8")
    text = collect.third_party_text(sockseek)
    for needle in (
        "GNU AFFERO GENERAL PUBLIC LICENSE",
        "SIL OPEN FONT LICENSE",
        "GNU LESSER GENERAL PUBLIC LICENSE",
        "pyside-setup.git/tag/?h=v6.",
        "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",
        "OpenSSL",
        "bootloader",
        "Mozilla Public License",  # certifi
    ):
        assert needle in text, needle
    # every package that ships has its own section with a license text
    for name in names - {"pyside6-essentials", "shiboken6"}:
        assert re.search(rf"^{name.replace('-', '[-_.]')} \d", text, re.I | re.M), name
    readme = collect.readme_text("1.2.3")
    assert project.source_url("1.2.3") in readme and "Kod źródłowy" in readme


def test_exe_version_numbers():
    exe_version = _script("exe_version")
    assert exe_version.numbers("0.1.0") == (0, 1, 0, 0)
    assert exe_version.numbers("1.2.3rc1") == (1, 2, 3, 0)


def test_installer_script():
    raw = (ROOT / "installer" / "neonseek.iss").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # utf-8 with bom, or iscc reads polish as ansi
    iss = raw.decode("utf-8-sig")
    for line in (
        "PrivilegesRequired=lowest",
        r"DefaultDirName={autopf}\NeonSeek",
        "AppVersion={#AppVersion}",
        "OutputBaseFilename=NeonSeek-Setup-{#AppVersion}",
        "SetupIconFile=neonseek.ico",
        'Name: "pl"; MessagesFile: "compiler:Languages\\Polish.isl"',
        "{#SourceUrl}",
    ):
        assert line in iss, line
    # the version and the address come from the build, nothing of them is written here
    assert not re.search(r"(?m)^AppVersion=\d", iss) and "github.com" not in iss
    # uninstalling never touches the data folder or the library
    assert "{userappdata}" not in iss and "[UninstallDelete]" not in iss
    assert (ROOT / "installer" / "neonseek.ico").read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_exe_icon_is_the_logo(qapp):
    from neonseek.ui.theme.loader import LOGO_SIZES

    ico = (ROOT / "installer" / "neonseek.ico").read_bytes()
    count = int.from_bytes(ico[4:6], "little")
    sizes = [ico[6 + 16 * n] or 256 for n in range(count)]
    assert tuple(sizes) == LOGO_SIZES
    # the committed ico is what scripts/build_icon.py makes from the svgs now (D-247)
    assert ico == _script("build_icon").ico_bytes()
    assert not (ROOT / "scripts" / "make_icon.py").exists()


@pytest.mark.parametrize("language", ["en", "pl"])
def test_manual_install_chapter_matches_the_installer(language):
    text = (ROOT / "docs" / "manual" / f"{language}.md").read_text(encoding="utf-8")
    chapter = text.split("{#install}", 1)[1].split("\n## ", 1)[0]
    assert r"%LOCALAPPDATA%\Programs\NeonSeek" in chapter
    assert "NeonSeek-Setup-" in chapter and "-portable.zip" in chapter
    assert "SmartScreen" in chapter and "Smart App Control" in chapter


def test_ui_screenshots_stay_outside_the_repo_and_are_jpeg(tmp_path):
    # D-253: pictures of the look never land in the repository, and never as png
    shots = _script("ui_screenshots")
    for inside in (ROOT, ROOT / "docs" / "shots"):
        with pytest.raises(SystemExit, match="outside the repository"):
            shots._outside_repo(inside)
    assert shots._outside_repo(tmp_path / "shots") == (tmp_path / "shots").resolve()
    text = (ROOT / "scripts" / "ui_screenshots.py").read_text(encoding="utf-8")
    assert ".png" not in text.lower() and '"PNG"' not in text
    saver = (ROOT / "scripts" / "manual_screenshots.py").read_text(encoding="utf-8")
    assert 'pixmap.save(str(path), "JPG", JPEG_QUALITY)' in saver
    assert set(shots.SCREENS) == {100, 125, 150}


SMOKE = ROOT / "scripts" / "smoke-test"


def test_smoke_test_does_nothing_without_the_switch():
    # D-255: installs and uninstalls, so only on a test machine and only when told so
    text = (SMOKE / "smoke-test.ps1").read_text(encoding="utf-8-sig")
    guard = text.index("if (-not $TestMachine)")
    actions = ("New-Item ", "Install-NeonSeek ", "Start-Process ", "Invoke-Cli ", "Copy-Item ",
               "Invoke-Logged ", "Set-Content ", "Add-Content ", "Remove-Item ", "Unblock-File -",
               "cmdkey")  # fmt: skip
    first_action = min(text.find(word, guard) for word in actions if word in text[guard:])
    assert "exit 2" in text[guard:first_action]
    # before the guard only the plan, the read only registry entry and the self test (D-261)
    assert not [word for word in actions if word in text[:guard]]
    assert text.index("if ($SelfTest)") < text.index("$plan = @") < guard
    # the password is never asked for, read or saved
    for word in ("Read-Host", "Get-Credential", "CredRead", "SecureString", "cmdkey /add",
                 "keyring", "--pass"):  # fmt: skip
        assert word.lower() not in text.lower(), word
    assert (SMOKE / "smoke-test.ps1").read_bytes().startswith(b"\xef\xbb\xbf")


def test_smoke_test_links_and_package():
    public = json.loads((SMOKE / "links.public.json").read_text(encoding="utf-8"))["links"]
    assert len(public) >= 6
    counts = ("tracks", "min_tracks", "entries", "more_entries_than")
    for link in public:
        assert link["url"].startswith("https://"), link
        assert sum(key in link for key in counts) == 1, link
    [one] = [link for link in public if link.get("download_one")]
    assert "tidal.com" in one["url"] and "tracks" in one
    # the channel changes and repeats merge into one track: over 200 places, not a number
    [youtube] = [link for link in public if "youtube.com" in link["url"]]
    assert youtube["more_entries_than"] == 200
    # the template has no links and no numbers of the owner's lists, only <...> (D-264)
    example = json.loads((SMOKE / "links.example.json").read_text(encoding="utf-8"))["links"]
    assert all(link["url"].startswith("<") for link in example)
    for link in example:
        assert sum(key in link for key in counts) == 1, link
        for key in ("tracks", "entries"):
            assert str(link.get(key, "<")).startswith("<"), link
    # the smoke test is not part of the program
    spec = (ROOT / "installer" / "neonseek.spec").read_text(encoding="utf-8")
    iss = (ROOT / "installer" / "neonseek.iss").read_text(encoding="utf-8-sig")
    assert "smoke" not in spec.lower() and "smoke" not in iss.lower()


def _windows_powershell(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                           "Bypass", *args], capture_output=True, timeout=120)  # fmt: skip


@pytest.mark.skipif(sys.platform != "win32", reason="windows powershell")
def test_smoke_test_checks_its_verdicts_in_windows_powershell():
    # D-261: the functions that judge the results, on one object, an empty list and many,
    # in powershell 5.1 with strict mode. nothing is installed, run or written
    done = _windows_powershell("-File", str(SMOKE / "smoke-test.ps1"), "-SelfTest")
    out = done.stdout.decode("utf-8", errors="replace")
    assert done.returncode == 0, out + done.stderr.decode("utf-8", errors="replace")
    assert ", 0 bledow, PowerShell 5." in out


@pytest.mark.skipif(sys.platform != "win32", reason="windows powershell")
def test_smoke_test_parses_in_windows_powershell():
    # parsed only, never run
    script = str(SMOKE / "smoke-test.ps1").replace("'", "''")
    command = (
        "$e = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script}', [ref]$null, [ref]$e); $e.Count"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, text=True, timeout=60)  # fmt: skip
    assert done.stdout.strip() == "0", done.stdout + done.stderr


@pytest.mark.parametrize(
    ("name", "phrases"),
    [
        ("README.md", ["NeonSeek does not host or share any files.", "respecting copyright",
                       "The installer is not digitally signed",
                       "**More info**, then **Run anyway**"]),
        ("README.pl.md", ["NeonSeek nie hostuje ani nie udostępnia żadnych plików.",
                          "przestrzeganie praw autorskich", "Instalator nie jest podpisany cyfrowo",
                          "**Więcej informacji**, a potem **Uruchom mimo to**"]),
    ],
)  # fmt: skip
def test_readme_disclaimer_and_smartscreen(name, phrases):
    text = (ROOT / name).read_text(encoding="utf-8")
    for phrase in phrases:
        assert phrase in text, phrase
    # nothing about the firewall: downloads run without listening (D-050)
    assert not re.search(r"firewall|zapor", text, re.IGNORECASE)
