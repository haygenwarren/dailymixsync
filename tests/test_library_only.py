"""The supported workflow is library-only.

`sync`, `match` and `review` use songs already in the Music library, through
AppleScript, and nothing else. These tests pin that down: the normal path cannot
reach the Music window, cannot add a song to the library, needs no Accessibility
permission, and treats a song you do not have as something to leave out, not as a
failure.
"""

import ast
import builtins
import json
from pathlib import Path

import pytest
from fake_music import FakeMusic, FakePlaylist

from daily_mix_sync import cli, experimental, music_app, music_ui
from daily_mix_sync.models import AppleCandidate
from daily_mix_sync.music_app import MusicApp

SRC = Path(cli.__file__).parent
# Everything a normal sync runs through. None of it may know about the experimental side.
MAIN_PATH = [
    "sync.py", "matcher.py", "review.py", "importer.py", "database.py", "normalize.py",
    "models.py", "config.py", "music_app.py", "apple_music.py", "downloads.py",
]
EXPERIMENTAL = {"music_ui", "catalog", "experimental"}

DREAMS = AppleCandidate("B1", "Dreams", "Fleetwood Mac", "Rumours", 257_800)
HOTEL = AppleCandidate("E1", "Hotel California", "Eagles", "Hotel California", 391_376)
LET_IT_GO = AppleCandidate("C1", "Let It Go", "Idina Menzel", "Frozen (Original Motion Picture Soundtrack)", 223_840)
TRACKS = [
    {"title": "Dreams", "artist": "Fleetwood Mac", "album": "Rumours", "duration_ms": 257000, "spotify_track_id": "s1"},
    {"title": "Mr. Brightside", "artist": "The Killers", "album": "Hot Fuss", "duration_ms": 222000, "spotify_track_id": "s2"},
    {"title": 'Let It Go - From "Frozen"', "artist": "Idina Menzel", "album": "Frozen (Original Motion Picture Soundtrack)", "duration_ms": 224000, "spotify_track_id": "s3"},
    {"title": "Not There", "artist": "Nobody", "spotify_track_id": "s4"},
    {"title": "Hotel California", "artist": "Eagles", "album": "Hotel California", "duration_ms": 391000, "spotify_track_id": "s5"},
]
DEST = "Spotify Daily Mix 1"


def imported_modules(path: Path) -> set[str]:
    """Names of the package's own modules that a source file imports, at any depth."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level >= 1:
            names.update([node.module] if node.module else [alias.name for alias in node.names])
        elif isinstance(node, ast.Import):
            names.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
    return names


class Tripwire:
    """Stands where the Music window automation would be; any use at all is a failure."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("normal sync tried to create the Music window automation")


def nobody_at_the_keyboard(prompt=""):
    raise EOFError


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """An in-memory Music, and tripwires on everything experimental."""
    fake = FakeMusic(
        library=[DREAMS, HOTEL, LET_IT_GO],
        playlists=[FakePlaylist("LIKED00000000001", "Spotify Liked Songs", ["B1"])],
    )
    monkeypatch.setattr(cli, "MusicApp", lambda prefix: MusicApp(prefix, run=fake))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr(builtins, "input", nobody_at_the_keyboard)
    monkeypatch.setattr(experimental, "_catalog_ui", Tripwire)
    monkeypatch.setattr(experimental, "MusicCatalogUI", Tripwire)
    monkeypatch.setattr(music_ui, "MusicCatalogUI", Tripwire)
    monkeypatch.setattr(music_ui, "accessibility_allowed", Tripwire)
    monkeypatch.chdir(tmp_path)
    return fake


@pytest.fixture
def export(tmp_path):
    path = tmp_path / "daily_mix_1.json"
    path.write_text(json.dumps({"playlist_name": "Daily Mix 1", "tracks": TRACKS}), encoding="utf-8")
    return str(path)


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- structure --------------------------------------------------------------------


@pytest.mark.parametrize("name", MAIN_PATH)
def test_the_main_path_does_not_import_the_experimental_side(name):
    assert imported_modules(SRC / name) & EXPERIMENTAL == set()


def test_the_cli_only_registers_the_experimental_commands():
    source = (SRC / "cli.py").read_text(encoding="utf-8")
    assert imported_modules(SRC / "cli.py") & EXPERIMENTAL == {"experimental"}
    uses = [
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "experimental"
    ]
    assert uses == ["add_commands"]  # registered, and nothing else
    for word in ("music_ui", "MusicCatalogUI", "accessibility", "Add to Library"):
        assert word not in source


def test_sync_offers_no_way_to_add_songs_from_the_catalog(capsys):
    parser = cli._build_parser()
    options = {
        option
        for action in parser._subparsers._group_actions[0].choices["sync"]._actions
        for option in action.option_strings
    }
    assert options == {
        "-h", "--help", "--config", "-v", "--verbose", "--dry-run", "--yes", "--no-review",
        "--into", "--db", "--details",
    }


@pytest.mark.parametrize("flag", ["--catalog", "--no-catalog", "--experimental-catalog"])
def test_catalog_flags_are_not_accepted_by_sync(fake, export, capsys, flag):
    with pytest.raises(SystemExit) as stop:
        cli.main(["sync", export, "--yes", flag])
    assert stop.value.code == 2
    assert fake.calls == []


def test_every_command_that_reaches_the_window_says_experimental():
    parser = cli._build_parser()
    choices = parser._subparsers._group_actions[0].choices
    uses_experimental = {
        name for name, sub in choices.items()
        if sub.get_default("handler").__module__ == experimental.__name__
    }
    assert uses_experimental == {
        "experimental-ui-inspect", "experimental-catalog-search",
        "experimental-catalog-add-test", "experimental-catalog-fill",
    }
    assert all(name.startswith("experimental-") for name in uses_experimental)
    supported = set(choices) - uses_experimental
    assert supported == {
        "validate", "match", "review", "sync", "sync-downloads", "music-test", "music-playlists",
        "music-find", "music-add-test", "music-clear-test",
    }


# --- behaviour --------------------------------------------------------------------


def test_sync_uses_only_the_library_and_leaves_missing_songs_out(fake, export, capsys):
    library_before = dict(fake.library)
    code, out, err = run(capsys, "sync", export, "--yes")
    assert (code, err) == (0, "")  # songs you do not have are not an error
    assert fake.playlist(DEST).track_ids == ["B1", "E1"]  # export order among what you have
    assert fake.library == library_before  # nothing was added to the library
    assert "Failed" not in out and "error" not in out.lower()
    assert "Tracks in Spotify export:   5\n" in out
    assert "Not in library:             2\n" in out
    assert (
        "\nNot in your Music library, so left out:\n"
        "  - Mr. Brightside — The Killers\n"
        "  - Not There — Nobody\n"
        "  Only songs already in your library are used. Nothing is added to it.\n"
    ) in out
    assert out.endswith("\n✓ Playlist updated and verified.\n")


def test_sync_sends_music_nothing_but_the_known_applescript(fake, export, capsys):
    run(capsys, "sync", export, "--yes")
    known = set(fake._handlers)
    assert set(fake.scripts_sent()) <= known
    for script in fake.scripts_sent():
        assert "System Events" not in script  # no window automation
        assert "Add to Library" not in script
    # The only things changed are the destination playlist's own contents.
    assert {script for script, _ in fake.changes()} <= {
        music_app._CREATE_PLAYLIST, music_app._CLEAR_PLAYLIST, music_app._ADD_TRACKS,
    }
    assert {args[0] if script is music_app._CREATE_PLAYLIST else args[1] for script, args in fake.changes()} == {DEST}


@pytest.mark.parametrize("command", [("match",), ("review",), ("sync", "--dry-run"), ("sync", "--yes")])
def test_supported_commands_never_touch_the_window_or_ask_about_accessibility(
    fake, export, capsys, command
):
    """The tripwires in the fixture would raise if any of them did."""
    name, *options = command
    code, _, err = run(capsys, name, export, *options)
    assert (code, err) == (0, "")


@pytest.fixture
def second_export(tmp_path):
    path = tmp_path / "daily_mix_2.json"
    path.write_text(
        json.dumps({"playlist_name": "Daily Mix 2", "tracks": list(reversed(TRACKS))}), encoding="utf-8"
    )
    return str(path)


@pytest.mark.parametrize("options", [("--dry-run",), ("--yes",), ("--yes", "--no-review")])
def test_a_sync_of_several_exports_is_as_library_only_as_a_sync_of_one(
    fake, export, second_export, capsys, options
):
    """The tripwires in the fixture would raise if the window were touched."""
    library_before = dict(fake.library)
    code, out, err = run(capsys, "sync", export, second_export, *options)
    assert (code, err) == (0, "")
    assert fake.library == library_before  # nothing was added to the library
    assert set(fake.scripts_sent()) <= set(fake._handlers)
    for script in fake.scripts_sent():
        assert "System Events" not in script
        assert "Add to Library" not in script
    assert {script for script, _ in fake.changes()} <= {
        music_app._CREATE_PLAYLIST, music_app._CLEAR_PLAYLIST, music_app._ADD_TRACKS,
    }
    changed = {args[0] if script is music_app._CREATE_PLAYLIST else args[1] for script, args in fake.changes()}
    assert changed == (set() if options == ("--dry-run",) else {DEST, "Spotify Daily Mix 2"})
    assert fake.playlist("Spotify Liked Songs").track_ids == ["B1"]
    # Songs that are not in the library are left out of both, and that is not an error.
    assert out.count("Only songs already in your library are used. Nothing is added to it.") == 2


@pytest.mark.parametrize("options", [("--list",), ("--dry-run",), ("--yes",)])
def test_syncing_from_a_downloads_folder_is_as_library_only_as_any_sync(
    fake, export, second_export, capsys, tmp_path, options
):
    """The tripwires in the fixture would raise if the window were touched."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    for path in (export, second_export):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        (downloads / Path(path).name).write_text(json.dumps(data), encoding="utf-8")
    library_before = dict(fake.library)
    code, _, err = run(capsys, "sync-downloads", "--downloads-dir", str(downloads), *options)
    assert (code, err) == (0, "")
    assert fake.library == library_before  # nothing was added to the library
    assert set(fake.scripts_sent()) <= set(fake._handlers)
    for script in fake.scripts_sent():
        assert "System Events" not in script
        assert "Add to Library" not in script
    changed = {args[0] if script is music_app._CREATE_PLAYLIST else args[1] for script, args in fake.changes()}
    assert changed == ({DEST, "Spotify Daily Mix 2"} if options == ("--yes",) else set())
    assert fake.playlist("Spotify Liked Songs").track_ids == ["B1"]
    if options == ("--list",):
        assert fake.calls == []  # listing does not reach Music at all


def test_finding_exports_reads_files_and_nothing_else():
    """downloads.py has no way to change a file, start a program or reach the network."""
    tree = ast.parse((SRC / "downloads.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module if node.level == 0 else f".{node.module}")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert imported == {
        "__future__", "json", "logging", "re", "unicodedata", "dataclasses", "datetime", "pathlib",
        ".importer", ".sync",
    }, "no subprocess, no sockets, no shutil, and nothing that talks to Music"

    changes_files = {
        "unlink", "rename", "rmdir", "mkdir", "touch", "chmod", "write_text", "write_bytes",
        "open", "symlink_to", "hardlink_to", "move", "copy", "remove",
    }
    used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    used |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not used & changes_files, sorted(used & changes_files)
    assert {"read_bytes", "iterdir", "stat"} <= used, "it lists the folder and reads files"


def test_sync_of_several_exports_still_has_no_catalog_option(fake, export, second_export, capsys):
    with pytest.raises(SystemExit):
        cli.main(["sync", export, second_export, "--catalog"])
    assert fake.calls == []


def test_validate_needs_neither_music_nor_any_permission(fake, export, capsys):
    code, out, _ = run(capsys, "validate", export)
    assert code == 0 and "5 usable track(s)" in out
    assert fake.calls == []


def test_manual_review_offers_library_candidates_only(fake, export, capsys, monkeypatch):
    asked = []

    def answer(prompt=""):
        asked.append(prompt)
        return "1"

    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(builtins, "input", answer)
    code, out, _ = run(capsys, "review", export)
    assert code == 0
    assert "Needs review (1 of 1)" in out and " 1. Let It Go" in out
    assert "catalog" not in out.lower() and "Add to Library" not in out
    assert asked == ["Selection: "]  # one question, about a song already in the library


def test_a_song_that_left_the_library_is_dropped_not_fetched(fake, export, capsys):
    run(capsys, "sync", export, "--yes")
    del fake.library["E1"]
    fake.playlist(DEST).track_ids.remove("E1")
    code, out, err = run(capsys, "sync", export, "--yes")
    assert code == 0
    assert "Stale mappings:             1\n" in out
    assert "  - Hotel California — Eagles\n" in out  # now listed as not in the library
    assert fake.playlist(DEST).track_ids == ["B1"]
    assert "E1" not in fake.library


def test_dry_run_changes_nothing_at_all(fake, export, capsys):
    library_before = dict(fake.library)
    code, out, _ = run(capsys, "sync", export, "--dry-run")
    assert code == 0 and out.rstrip().endswith("No changes made (--dry-run).")
    assert fake.changes() == [] and fake.library == library_before
    assert all(p.name != DEST for p in fake.playlists)


def test_a_failed_write_is_still_rolled_back(fake, export, capsys):
    fake.playlists.append(FakePlaylist("DEST000000000001", DEST, ["C1"]))
    fake.fail_on[music_app._ADD_TRACKS] = [1]
    code, _, err = run(capsys, "sync", export, "--yes")
    assert code == 1
    assert "✓ Its previous contents were restored (1 track(s))." in err
    assert fake.playlist(DEST).track_ids == ["C1"]
