"""The setup wizard emits a reproducible command, not subtitle downloads."""

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import requests
import typer
from typer.testing import CliRunner

from jimaku_cli import cli
from jimaku_cli.api import Entry, EntryFlags, FileEntry, JimakuError
from jimaku_cli.download import match
from jimaku_cli.search import app, choose, release_pattern


@pytest.fixture(autouse=True)
def fzf_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub only the terminal UI: test input is one-based indices per prompt.

    Space-separated indices represent items marked in that order. Manual title
    prompts still use real Typer input. Real fzf is exercised by the PTY tests.
    """
    def select(
        args: list[str], *, input: bytes, **_options: object
    ) -> subprocess.CompletedProcess[bytes]:
        rows = input.decode("utf-8").splitlines()
        for number, row in enumerate(rows, 1):
            label = row.split("\t", 1)[1]
            typer.echo(f"{number}. {label}", err=True)
        prompt = next(arg.removeprefix("--prompt=") for arg in args if arg.startswith("--prompt="))
        typer.echo(prompt, err=True)
        answer = sys.stdin.readline()
        if not answer:
            raise KeyboardInterrupt
        indices = [int(value) - 1 for value in answer.split()]
        if "--multi" not in args:
            assert len(indices) == 1, "entry selection must be single-choice"
        selected = "".join(rows[index] + "\n" for index in indices).encode("utf-8")
        return subprocess.CompletedProcess(args, 0, stdout=selected)

    monkeypatch.setattr("jimaku_cli.search.subprocess.run", select)


class SearchClient:
    api_key = "fixture-key"

    def __init__(self, listings: dict[int | None, list[FileEntry] | Exception]) -> None:
        self.entries: list[Entry] = [
            Entry(42, "Show", "2026-01-01T00:00:00Z", EntryFlags(anime=True))
        ]
        self.listings = listings
        self.searched: list[tuple[str, bool]] = []
        self.listed: list[tuple[int, int | None]] = []

    def search_entries(self, query: str, *, anime: bool) -> list[Entry]:
        self.searched.append((query, anime))
        return self.entries

    def get_files(self, entry_id: int, episode: int | None = None) -> list[FileEntry]:
        self.listed.append((entry_id, episode))
        result = self.listings.get(episode, [])
        if isinstance(result, Exception):
            raise result
        return result

    def download_file(self, *_args: object) -> None:
        raise AssertionError("search must not download files")


def subtitle(name: str) -> FileEntry:
    return FileEntry("https://example.invalid/subtitle", name, 8, "2026-01-01T00:00:00Z")


def releases(command: list[str]) -> list[str]:
    return [command[i + 1] for i, arg in enumerate(command) if arg == "--release"]


def test_search_selects_an_entry_with_fzf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Show - 01.mkv").touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})
    # Escaped names need no reverse lookup to identify the selected entry.
    client.entries = [
        Entry(42, "Show\n", "2026-01-01T00:00:00Z"),
        Entry(99, r"Show\n", "2026-01-01T00:00:00Z"),
    ]
    calls: list[tuple[list[str], list[str]]] = []

    def select(
        args: list[str], *, input: bytes, **options: object
    ) -> subprocess.CompletedProcess[bytes]:
        assert options["stdout"] == subprocess.PIPE
        assert options["stderr"] is None  # fzf needs the inherited terminal, not a pipe.
        assert options["check"] is False
        assert "capture_output" not in options
        rows = input.decode("utf-8").splitlines()
        calls.append((rows, args))
        row = rows[1] if len(calls) == 1 else rows[0]
        return subprocess.CompletedProcess(args, 0, stdout=(row + "\n").encode("utf-8"))

    monkeypatch.setattr("jimaku_cli.search.subprocess.run", select)
    result = CliRunner().invoke(app, [str(tmp_path)], obj=client, input="")

    assert result.exit_code == 0, result.stderr
    assert client.listed == [(99, 1)]
    assert len(calls) == 2
    assert "--no-multi" in calls[0][1]
    assert "--multi" in calls[1][1]
    assert "--sort" in calls[0][1]
    assert r"Show\n" in calls[0][0][1]
    assert "\n" not in calls[0][0][1]
    assert result.stdout.startswith("jimaku download ")
    assert result.stdout.count("\n") == 1


def test_search_selects_multiple_releases_in_mark_order(tmp_path: Path) -> None:
    for episode in (1, 2, 3):
        (tmp_path / f"Show - {episode:02}.mkv").touch()
    client = SearchClient({
        1: [subtitle("[Alpha] Show - 01.srt"), subtitle("[Beta] Show - 01.srt")],
        2: [subtitle("[Beta] Show - 02.ass")],
        3: [subtitle("[Gamma] Show - 03.srt")],
    })

    result = CliRunner().invoke(
        app, [str(tmp_path)], obj=client, input="1\n2 1\n1\n"
    )

    assert result.exit_code == 0, result.stderr
    assert releases(shlex.split(result.stdout)) == ["Beta", "Alpha", "Gamma"]
    # Any selected release still covers an episode; no per-release completeness check.
    assert result.stderr.count("Subtitle release:") == 2
    assert client.listed == [(42, 1), (42, 2), (42, 3)]
    assert result.stdout.count("\n") == 1


@pytest.mark.parametrize("download_all", [False, True])
def test_multi_selection_deduplicates_releases_and_replays_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, download_all: bool
) -> None:
    (tmp_path / "Show - 01.mkv").touch()
    client = SearchClient({1: [
        subtitle("[Alpha] Show - 01.srt"),
        subtitle("[Beta] Show - 01.srt"),
        subtitle("[Alpha] Show - 01.ass"),
    ]})
    def make_client(**_kwargs: object) -> SearchClient:
        return client

    def save_subtitle(_url: str, destination: Path) -> None:
        _ = destination.write_bytes(b"fixture")

    monkeypatch.setattr(cli, "api_key", "fixture-key")
    monkeypatch.setattr(cli, "JimakuClient", make_client)
    runner = CliRunner()
    search = runner.invoke(
        cli.app,
        ["search", str(tmp_path), "--all" if download_all else "--no-all",
         "--no-rename", "--no-overwrite", "--no-align", "--no-strip-ih"],
        input="1\n2 3 1\n",
    )

    assert search.exit_code == 0, search.stderr
    assert releases(shlex.split(search.stdout)) == ["Beta", "Alpha"]
    monkeypatch.setattr(client, "download_file", save_subtitle)
    downloaded = runner.invoke(cli.app, shlex.split(search.stdout)[1:], catch_exceptions=False)

    assert downloaded.exit_code == 0, downloaded.stderr
    assert downloaded.stdout == ""
    assert (tmp_path / "[Beta] Show - 01.srt").read_bytes() == b"fixture"
    assert (tmp_path / "[Alpha] Show - 01.srt").exists() is download_all
    assert (tmp_path / "[Alpha] Show - 01.ass").exists() is download_all


def test_search_accumulates_releases_in_episode_order(tmp_path):
    for episode in (10, 2, 1):
        (tmp_path / f"[Local] Show - {episode}.mkv").touch()
    client = SearchClient({
        1: [subtitle("[First] Show - 01.srt")],
        2: [subtitle("[First] Show - 02.srt")],
        10: [subtitle("[Second] Show - 10.ass")],
    })

    result = CliRunner().invoke(
        app, [str(tmp_path)], obj=client, input="1\n1\n1\n", catch_exceptions=False
    )

    assert result.exit_code == 0, result.stderr
    command = shlex.split(result.stdout)
    assert command[:3] == ["jimaku", "download", str(tmp_path.resolve())]
    assert command[command.index("--id") + 1] == "42"
    assert releases(command) == ["First", "Second"]
    assert client.searched == [("Show", True)]
    assert client.listed == [(42, 1), (42, 2), (42, 10)]
    assert "[First] Show - 01.srt" in result.stderr
    assert "[Second] Show - 10.ass" in result.stderr
    assert result.stdout.count("\n") == 1
    assert not list(tmp_path.glob("*.srt"))


@pytest.mark.parametrize("anime, query", [
    (True, "Wedding Impossible 2024"),
    (False, "Wedding Impossible"),
])
def test_search_uses_genre_specific_primary_title(
    tmp_path: Path, anime: bool, query: str
) -> None:
    (tmp_path / "Wedding.Impossible.2024.S01E07.1080p.WEB-DL.mkv").touch()
    client = SearchClient({7: [subtitle("[Group] Wedding Impossible - 07.srt")]})

    result = CliRunner().invoke(
        app, [str(tmp_path), "--anime" if anime else "--no-anime"],
        obj=client, input="1\n1\n", catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stderr
    assert client.searched == [(query, anime)]
    assert "Search title:" not in result.stderr
    assert result.stdout.startswith("jimaku download ")
    assert result.stdout.count("\n") == 1


@pytest.mark.parametrize("filename, anime, episode, queries", [
    (
        "[Animax fix] Initial D Fifth Stage - ACT.04 因縁のリベンジバトル.ja.mkv",
        True, 4, ["Initial D Fifth Stage - ACT", "Initial D Fifth Stage"],
    ),
    (
        "作りたい女と食べたい女（０６） - [1440-1920x1080@KFMVFR.hevc10_crf 20][字].mkv",
        False, 6, ["作りたい女と食べたい女（０６）", "作りたい女と食べたい女"],
    ),
])
def test_search_retries_other_parser_after_empty_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    filename: str, anime: bool, episode: int, queries: list[str],
) -> None:
    (tmp_path / filename).touch()
    client = SearchClient({episode: [subtitle("[Group] Show - 01.srt")]})

    def search_entries(query: str, *, anime: bool) -> list[Entry]:
        client.searched.append((query, anime))
        return client.entries if query == queries[-1] else []

    monkeypatch.setattr(client, "search_entries", search_entries)
    result = CliRunner().invoke(
        app, [str(tmp_path), "--anime" if anime else "--no-anime"],
        obj=client, input="1\n1\n", catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stderr
    assert client.searched == [(query, anime) for query in queries]
    assert "Search title:" not in result.stderr
    assert result.stdout.startswith("jimaku download ")
    assert result.stdout.count("\n") == 1


@pytest.mark.parametrize("anime", [True, False])
def test_search_prompts_for_a_title_after_both_parsers_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, anime: bool
) -> None:
    (tmp_path / "Wedding.Impossible.2024.S01E07.1080p.WEB-DL.mkv").touch()
    client = SearchClient({7: [subtitle("[Group] Wedding Impossible - 07.srt")]})

    def search_entries(query: str, *, anime: bool) -> list[Entry]:
        client.searched.append((query, anime))
        return client.entries if query == "Correct title" else []

    monkeypatch.setattr(client, "search_entries", search_entries)
    result = CliRunner().invoke(
        app, [str(tmp_path), "--anime" if anime else "--no-anime"],
        obj=client, input="   \nStill wrong\nCorrect title\n1\n1\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stderr
    queries = ["Wedding Impossible 2024", "Wedding Impossible"]
    if not anime:
        queries.reverse()
    assert client.searched == [
        (query, anime) for query in [*queries, "Still wrong", "Correct title"]
    ]
    assert "Search title:" in result.stderr
    assert "Still wrong" in result.stderr
    assert "Correct title" in result.stderr
    assert result.stdout.startswith("jimaku download ")
    assert result.stdout.count("\n") == 1
    assert "Correct title" not in result.stdout
    assert releases(shlex.split(result.stdout)) == ["Group"]


@pytest.mark.parametrize("fail_at", [0, 1, 2])
@pytest.mark.parametrize("error", [
    JimakuError(429, "retry later"), requests.Timeout("search timed out"),
])
def test_search_api_errors_stop_instead_of_trying_another_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    fail_at: int, error: Exception,
) -> None:
    (tmp_path / "Wedding.Impossible.2024.S01E07.1080p.WEB-DL.mkv").touch()
    client = SearchClient({})
    queries = ["Wedding Impossible 2024", "Wedding Impossible", "Manual title"]

    def search_entries(query: str, *, anime: bool) -> list[Entry]:
        client.searched.append((query, anime))
        if len(client.searched) == fail_at + 1:
            raise error
        return []

    monkeypatch.setattr(client, "search_entries", search_entries)
    result = CliRunner().invoke(
        app, [str(tmp_path), "--download"], obj=client, input="Manual title\n",
    )

    assert result.exit_code == 1
    assert str(error) in result.stderr
    assert ("Search title:" in result.stderr) is (fail_at == 2)
    assert client.searched == [(query, True) for query in queries[:fail_at + 1]]
    assert client.listed == []
    assert result.stdout == ""


@pytest.mark.parametrize("run_download", [False, True])
def test_cancelling_manual_title_search_emits_nothing(
    tmp_path: Path, run_download: bool,
) -> None:
    (tmp_path / "Wedding.Impossible.2024.S01E07.1080p.WEB-DL.mkv").touch()
    client = SearchClient({})
    client.entries = []

    result = CliRunner().invoke(
        app, [str(tmp_path), "--download" if run_download else "--no-download"],
        obj=client, input="Manual title\n",
    )

    assert result.exit_code == 1
    assert "Search title:" in result.stderr
    assert client.searched == [
        ("Wedding Impossible 2024", True), ("Wedding Impossible", True),
        ("Manual title", True),
    ]
    assert client.listed == []
    assert result.stdout == ""


def test_search_uses_guessit_when_anitopy_has_no_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "[Local] Show - 01.mkv").touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})
    # Exercise a missing Anitopy title while leaving GuessIt and the CLI real.
    monkeypatch.setattr("jimaku_cli.search.anitopy.parse", lambda name: {"episode_number": "01"})

    result = CliRunner().invoke(
        app, [str(tmp_path)], obj=client, input="1\n1\n", catch_exceptions=False
    )

    assert result.exit_code == 0, result.stderr
    assert client.searched == [("Show", True)]
    assert "Searching for Show" in result.stderr
    assert result.stdout.startswith("jimaku download ")
    assert result.stdout.count("\n") == 1


@pytest.mark.parametrize("title", [None, "", "   ", ["Show"]])
def test_search_rejects_unusable_titles_before_api_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: object
) -> None:
    (tmp_path / "S01E01.mkv").touch()
    client = SearchClient({})
    monkeypatch.setattr("jimaku_cli.search.anitopy.parse", lambda name: {"anime_title": title})

    result = CliRunner().invoke(app, [str(tmp_path)], obj=client)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "error: could not determine a title from S01E01.mkv" in result.stderr
    assert client.searched == client.listed == []


@pytest.mark.parametrize("title", [None, "", "   ", ["Show"]])
def test_search_recovers_with_manual_title_when_parsing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: object
) -> None:
    (tmp_path / "S01E01.mkv").touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})
    monkeypatch.setattr(
        "jimaku_cli.search.anitopy.parse", lambda name: {"anime_title": title}
    )

    result = CliRunner().invoke(
        app, [str(tmp_path)], obj=client, input="Show\n1\n1\n",
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stderr
    assert client.searched == [("Show", True)]
    assert client.listed == [(42, 1)]
    assert "Search title:" in result.stderr
    assert result.stdout.startswith("jimaku download ")
    assert result.stdout.count("\n") == 1


@pytest.mark.parametrize("anime", [True, False])
def test_empty_results_suggest_live_action_only_for_anime_search(
    tmp_path: Path, anime: bool
) -> None:
    (tmp_path / "Show - 01.mkv").touch()
    client = SearchClient({})
    client.entries = []

    result = CliRunner().invoke(
        app, [str(tmp_path), "--anime" if anime else "--no-anime"], obj=client
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "error: no entries found for 'Show'" in result.stderr
    assert ("Use --no-anime to search live action" in result.stderr) is anime
    assert client.searched == [("Show", anime)]


def test_unknown_release_becomes_an_episode_independent_regex(tmp_path):
    for episode in (1, 2):
        (tmp_path / f"Show - {episode:02}.mkv").touch()
    first = subtitle("Show - 01 (CR 1920x1080 x264 AAC).srt")
    second = subtitle("Show - 02 (CR 1920x1080 x264 AAC).ass")
    client = SearchClient({1: [first], 2: [second]})

    result = CliRunner().invoke(
        app, [str(tmp_path)], obj=client, input="1\n1\n"
    )

    assert result.exit_code == 0, result.stderr
    [pattern] = releases(shlex.split(result.stdout))
    assert pattern.startswith("re:")
    assert match(pattern, first.name)
    assert match(pattern, second.name)
    assert not match(pattern, "Show - 02 (Netflix 1920x1080 x264 AAC).srt")
    assert not match(pattern, "Show - 02 (CR 1280x720 x264 AAC).srt")
    assert result.stderr.count("Subtitle release:") == 1


@pytest.mark.parametrize("filename, later_episode, changed_metadata", [
    (
        "Show - 264 (CR 1920x1080 x264 AAC).srt",
        "Show - 265 (CR 1920x1080 x264 AAC).ass",
        "Show - 264 (CR 1920x1080 x265 AAC).srt",
    ),
    (
        "Show - 1080 (CR 1920x1080 x264 AAC).srt",
        "Show - 1081 (CR 1920x1080 x264 AAC).srt",
        "Show - 1080 (CR 1920x720 x264 AAC).srt",
    ),
    (
        "Show.S01E01 (CR 1920x1080 x264 AAC).srt",
        "Show.S01E12 (CR 1920x1080 x264 AAC).srt",
        "Show.S02E01 (CR 1920x1080 x264 AAC).srt",
    ),
    (
        "Show - 01 (CR 1920x1080 x264 AAC).srt",
        "Show - 123 (CR 1920x1080 x264 AAC).srt",
        "Other Show - 01 (CR 1920x1080 x264 AAC).srt",
    ),
])
def test_release_pattern_generalizes_only_the_episode(filename, later_episode, changed_metadata):
    pattern = release_pattern(filename)

    assert pattern.startswith("re:")
    assert match(pattern, filename)
    assert match(pattern, later_episode)
    assert not match(pattern, changed_metadata)


@pytest.mark.parametrize("filename", [
    "Show 01 - 01 (CR 1920x1080 x264 AAC).srt",
    "Show - 01 - 01 (CR 1920x1080 x264 AAC).srt",
    "Show 01 - 01v2 (CR 1920x1080 x264 AAC).srt",
    "Show - 264v2 (CR 1920x1080 h.264 AAC).ass",
    # GuessIt does not confirm an episode span for this versioned SxxExx spelling.
    "Show.S01E01v2 (CR 1920x1080 x264 AAC).srt",
])
def test_release_pattern_keeps_exact_stem_when_episode_span_is_uncertain(filename):
    pattern = release_pattern(filename)

    assert pattern == "re:^" + re.escape(Path(filename).stem) + r"\.[^.]+$"
    assert match(pattern, filename)


def test_versioned_release_pattern_preserves_revision_and_metadata() -> None:
    filename = "Show - 01v2 (CR 1920x1080 x264 AAC).srt"
    pattern = release_pattern(filename)

    assert match(pattern, filename)
    assert match(pattern, filename.replace("01v2", "02v2").replace(".srt", ".ass"))
    assert not match(pattern, filename.replace("v2", "v3"))
    assert not match(pattern, filename.replace("x264", "x265"))


@pytest.mark.parametrize("enabled", [True, False])
def test_search_passes_handling_options_to_the_emitted_command(tmp_path, enabled):
    directory = tmp_path / "Jack's 日本語 $(not-a-command)"
    directory.mkdir()
    (directory / "Show - 01.mkv").touch()
    client = SearchClient({1: [
        subtitle("[SrtGroup] Show - 01.srt"),
        subtitle("[AssGroup] Show - 01.ass"),
    ]})
    client.entries.append(Entry(99, "Other Show", "2026-01-01T00:00:00Z"))
    switches = ["all", "rename", "overwrite", "align", "strip-ih"]
    flags = [f"--{'' if enabled else 'no-'}{name}" for name in switches]

    result = CliRunner().invoke(
        app, [str(directory), "--no-anime", "--prefer-format", "ASS", *flags],
        obj=client, input="2\n1\n", catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stderr
    command = shlex.split(result.stdout)
    assert command[:3] == ["jimaku", "download", str(directory.resolve())]
    assert command[command.index("--id") + 1] == "99"
    assert command[command.index("--prefer-format") + 1] == "ass"
    assert releases(command) == ["AssGroup"]
    assert all(flag in command for flag in flags)
    assert "--no-anime" not in command
    assert client.searched == [("Show", False)]
    assert client.listed == [(99, 1)]


@pytest.mark.parametrize("case", ["missing", "file", "empty", "no-entries"])
def test_unusable_search_input_reports_an_error_without_a_command(tmp_path, case):
    directory = tmp_path / "videos"
    client = SearchClient({})
    if case == "file":
        directory.touch()
    elif case != "missing":
        directory.mkdir()
        if case == "no-entries":
            (directory / "Show - 01.mkv").touch()
            client.entries = []
        else:
            (directory / "not-a-video.txt").touch()
            (directory / "not-a-file.mkv").mkdir()

    result = CliRunner().invoke(app, [str(directory)], obj=client)

    assert result.exit_code in (1, 2)
    assert result.stdout == ""
    expected = {
        "missing": "Invalid value", "file": "Invalid value",
        "empty": "no video files", "no-entries": "no entries found",
    }[case]
    assert expected in result.stderr
    assert client.listed == []
    assert client.searched == ([("Show", True)] if case == "no-entries" else [])


@pytest.mark.parametrize("empty_only", [False, True])
def test_missing_subtitles_are_reported_without_prompting(tmp_path, empty_only):
    (tmp_path / "Show - 01.mkv").touch()
    (tmp_path / "Show - 02.mkv").touch()
    client = SearchClient({
        1: [subtitle("Show - 01.zip")],
        2: [] if empty_only else [subtitle("[Group] Show - 02.srt")],
    })

    result = CliRunner().invoke(
        app, [str(tmp_path)], obj=client, input="1\n1\n", catch_exceptions=False
    )

    assert "error: Show - 01.mkv: no subtitles available" in result.stderr
    assert client.listed == [(42, 1), (42, 2)]
    assert "Show - 01.zip" not in result.stderr
    if empty_only:
        assert result.exit_code == 1
        assert result.stdout == ""
        assert "Subtitle release:" not in result.stderr
    else:
        assert result.exit_code == 0, result.stderr
        assert releases(shlex.split(result.stdout)) == ["Group"]
        assert result.stderr.count("Subtitle release:") == 1


@pytest.mark.parametrize("error", [JimakuError(429, "retry later"), requests.Timeout("timeout")])
def test_listing_failure_continues_but_never_emits_a_partial_command(tmp_path, error):
    for episode in (1, 2, 3):
        (tmp_path / f"Show - {episode:02}.mkv").touch()
    client = SearchClient({
        1: [subtitle("[First] Show - 01.srt")],
        2: error,
        3: [subtitle("[Second] Show - 03.srt")],
    })

    result = CliRunner().invoke(
        app, [str(tmp_path)], obj=client, input="1\n1\n1\n"
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert str(error) in result.stderr
    assert "Show - 02.mkv" in result.stderr
    assert client.listed == [(42, 1), (42, 2), (42, 3)]


def test_directory_read_failure_is_reported_on_stderr(tmp_path, monkeypatch):
    def unreadable(path):
        raise PermissionError("directory read denied")

    monkeypatch.setattr(Path, "iterdir", unreadable)
    client = SearchClient({})
    result = CliRunner().invoke(app, [str(tmp_path)], obj=client)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "directory read denied" in result.stderr
    assert client.searched == []


def test_unreadable_episode_is_not_treated_as_a_movie(tmp_path):
    (tmp_path / "Show.S01.mkv").touch()
    client = SearchClient({None: [subtitle("[Group] Show - 01.srt")]})

    result = CliRunner().invoke(app, [str(tmp_path)], obj=client, input="1\n1\n")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "could not determine an episode number" in result.stderr
    assert client.listed == []


def test_movie_search_defaults_to_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "A Silent Voice.MKV").touch()
    client = SearchClient({None: [subtitle("A Silent Voice.srt")]})

    result = CliRunner().invoke(app, [], obj=client, input="1\n1\n", catch_exceptions=False)

    assert result.exit_code == 0, result.stderr
    assert client.searched == [("A Silent Voice", True)]
    assert client.listed == [(42, None)]
    assert shlex.split(result.stdout)[2] == str(tmp_path.resolve())
    [pattern] = releases(shlex.split(result.stdout))
    assert match(pattern, "A Silent Voice.srt")


def test_numberless_videos_follow_numbered_episodes(tmp_path):
    for name in ("A Movie.mkv", "Show - 01.mkv", "Z Movie.mkv"):
        (tmp_path / name).touch()
    client = SearchClient({
        1: [subtitle("[Group] Show - 01.srt")],
        None: [subtitle("[Group] Movie.srt")],
    })

    result = CliRunner().invoke(app, [str(tmp_path)], obj=client, input="1\n1\n")

    assert result.exit_code == 0, result.stderr
    assert client.searched == [("Show", True)]
    assert client.listed == [(42, 1), (42, None), (42, None)]


@pytest.mark.parametrize("answers", ["", "1\n", "1\n1\n"])
@pytest.mark.parametrize("flags", [[], ["--download"]])
def test_cancelled_search_leaves_stdout_empty(
    tmp_path: Path, answers: str, flags: list[str]
) -> None:
    (tmp_path / "Show - 01.mkv").touch()
    (tmp_path / "Show - 02.mkv").touch()
    client = SearchClient({
        1: [subtitle("[First] Show - 01.srt")],
        2: [subtitle("[Second] Show - 02.srt")],
    })

    result = CliRunner().invoke(app, [str(tmp_path), *flags], obj=client, input=answers)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Aborted" in result.stderr


@pytest.mark.parametrize("value", [None, "", "--filter=Show --print-query --multi"])
@pytest.mark.parametrize("outcome", ["success", "cancel", "launch-error"])
def test_selection_isolated_from_shell_fzf_defaults(
    monkeypatch: pytest.MonkeyPatch, value: str | None, outcome: str
) -> None:
    defaults = ("FZF_DEFAULT_OPTS", "FZF_DEFAULT_OPTS_FILE")
    for name in defaults:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    original = dict(os.environ)

    def select(
        args: list[str], *, input: bytes, env: dict[str, str], **_options: object
    ) -> subprocess.CompletedProcess[bytes]:
        # Only the child environment is filtered; even during selection the parent
        # retains absent, empty, and nonempty values without a restoration window.
        filtered_correctly = env == {
            name: value for name, value in original.items() if name not in defaults
        }
        parent_unchanged = dict(os.environ) == original
        # Keep pytest failure introspection from displaying unrelated credentials.
        assert filtered_correctly
        assert parent_unchanged
        if outcome == "cancel":
            raise KeyboardInterrupt
        if outcome == "launch-error":
            raise OSError("fzf launch failed")
        return subprocess.CompletedProcess(args, 0, stdout=input)

    monkeypatch.setattr("jimaku_cli.search.subprocess.run", select)
    if outcome == "cancel":
        with pytest.raises(typer.Abort):
            _ = choose("Entry", ["Show"])
    elif outcome == "launch-error":
        with pytest.raises(typer.Exit) as error:
            _ = choose("Entry", ["Show"])
        assert error.value.exit_code == 1
    else:
        assert choose("Entry", ["Show"]) == [0]
    parent_unchanged = dict(os.environ) == original
    assert parent_unchanged


def test_fzf_launch_failure_is_reported_without_a_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Show - 01.mkv").touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})

    def unavailable(*_args: object, **_kwargs: object) -> str:
        raise FileNotFoundError("fzf executable missing")

    monkeypatch.setattr("jimaku_cli.search.subprocess.run", unavailable)
    result = CliRunner().invoke(app, [str(tmp_path), "--download"], obj=client)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "error: could not run fzf: fzf executable missing" in result.stderr
    assert client.listed == []


@pytest.mark.parametrize("returncode, stdout", [(0, b""), (1, b""), (2, b"0\tShow\n"), (130, b"")])
def test_unsuccessful_fzf_selection_aborts_without_a_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: bytes
) -> None:
    (tmp_path / "Show - 01.mkv").touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})
    def no_match(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, returncode, stdout=stdout)

    monkeypatch.setattr("jimaku_cli.search.subprocess.run", no_match)

    result = CliRunner().invoke(app, [str(tmp_path)], obj=client)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert client.listed == []
    assert "Aborted" in result.stderr


@pytest.mark.parametrize("flags", [["--release", "Group"], ["--prefer-format", "zip"]])
def test_invalid_options_are_rejected_before_searching(tmp_path, flags):
    client = SearchClient({})

    result = CliRunner().invoke(app, [str(tmp_path), *flags], obj=client)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert client.searched == client.listed == []


def test_api_labels_cannot_inject_terminal_control_characters(tmp_path):
    (tmp_path / "Show - 01.mkv").touch()
    client = SearchClient({1: [
        subtitle("[Group] Show - 01.srt"),
        subtitle("evil\n\x1b[31m.srt"),
    ]})
    client.entries = [Entry(42, "Show\n\x1b[31m", "2026-01-01T00:00:00Z")]

    result = CliRunner().invoke(app, [str(tmp_path)], obj=client, input="1\n1\n")

    assert result.exit_code == 0, result.stderr
    assert r"Show\n\x1b[31m" in result.stderr
    assert r"evil\n\x1b[31m.srt" in result.stderr
    assert "\x1b" not in result.stderr
    assert result.stdout.count("\n") == 1


def test_emitted_command_preserves_ansi_directory(tmp_path):
    directory = tmp_path / "Show\x1b[31m"
    directory.mkdir()
    (directory / "Show - 01.mkv").touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})
    client.entries = [Entry(42, directory.name, "2026-01-01T00:00:00Z")]

    result = CliRunner().invoke(
        app, [str(directory)], obj=client, input="1\n1\n", catch_exceptions=False
    )

    assert result.exit_code == 0, result.stderr
    assert shlex.split(result.stdout)[:3] == ["jimaku", "download", str(directory.resolve())]
    assert result.stdout.count("\n") == 1
    assert r"Show\x1b[31m" in result.stderr
    assert "\x1b" not in result.stderr


def test_emitted_command_runs_through_the_real_download_command(tmp_path, monkeypatch):
    (tmp_path / "Show - 01.mkv").touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})
    monkeypatch.setattr(cli, "api_key", "fixture-key")
    monkeypatch.setattr(cli, "JimakuClient", lambda **kwargs: client)
    runner = CliRunner()
    search = runner.invoke(
        cli.app,
        ["search", str(tmp_path), "--no-all", "--no-rename", "--no-overwrite",
         "--no-align", "--no-strip-ih", "--prefer-format", "srt"],
        input="1\n1\n", catch_exceptions=False,
    )
    assert search.exit_code == 0, search.stderr
    assert not list(tmp_path.glob("*.srt"))

    # Only the remote transfer is a fixture; replay the actual generated arguments.
    monkeypatch.setattr(client, "download_file", lambda url, dest: dest.write_bytes(b"subtitle"))
    download = runner.invoke(cli.app, shlex.split(search.stdout)[1:], catch_exceptions=False)

    assert download.exit_code == 0, download.stderr
    assert download.stdout == ""
    assert (tmp_path / "[Group] Show - 01.srt").read_bytes() == b"subtitle"
    assert client.listed == [(42, 1), (42, 1)]


def test_unparsed_release_command_replay_preserves_codec(tmp_path, monkeypatch):
    for episode in (264, 265):
        (tmp_path / f"Show - {episode}.mkv").touch()
    first = subtitle("Show - 264 (CR 1920x1080 x264 AAC).srt")
    second = subtitle("Show - 265 (CR 1920x1080 x264 AAC).ass")
    wrong_codec = subtitle("Show - 265 (CR 1920x1080 x265 AAC).srt")
    client = SearchClient({264: [first], 265: [wrong_codec, second]})
    monkeypatch.setattr(cli, "api_key", "fixture-key")
    monkeypatch.setattr(cli, "JimakuClient", lambda **kwargs: client)
    runner = CliRunner()

    search = runner.invoke(
        cli.app,
        ["search", str(tmp_path), "--no-all", "--no-rename", "--no-overwrite",
         "--no-align", "--no-strip-ih", "--prefer-format", "srt"],
        input="1\n1\n", catch_exceptions=False,
    )

    assert search.exit_code == 0, search.stderr
    assert search.stderr.count("Subtitle release:") == 1
    assert len(releases(shlex.split(search.stdout))) == 1
    assert not list(tmp_path.glob("*.srt"))
    assert not list(tmp_path.glob("*.ass"))

    # Replay the real matcher: an unrelated codec must not win on format preference.
    monkeypatch.setattr(client, "download_file", lambda url, dest: dest.write_bytes(b"subtitle"))
    download = runner.invoke(cli.app, shlex.split(search.stdout)[1:], catch_exceptions=False)

    assert download.exit_code == 0, download.stderr
    assert download.stdout == ""
    assert (tmp_path / first.name).read_bytes() == b"subtitle"
    assert not (tmp_path / wrong_codec.name).exists()
    assert (tmp_path / second.name).read_bytes() == b"subtitle"
    assert client.listed == [(42, 264), (42, 265), (42, 264), (42, 265)]


def test_versioned_release_command_replay_preserves_dotted_codec(tmp_path, monkeypatch):
    (tmp_path / "Show - 264v2.mkv").touch()
    selected = subtitle("Show - 264v2 (CR 1920x1080 h.264 AAC).ass")
    wrong_codec = subtitle("Show - 264v2 (CR 1920x1080 h.265 AAC).srt")
    client = SearchClient({264: [selected, wrong_codec]})
    monkeypatch.setattr(cli, "api_key", "fixture-key")
    monkeypatch.setattr(cli, "JimakuClient", lambda **kwargs: client)
    runner = CliRunner()

    search = runner.invoke(
        cli.app,
        ["search", str(tmp_path), "--no-all", "--no-rename", "--no-overwrite",
         "--no-align", "--no-strip-ih", "--prefer-format", "srt"],
        input="1\n2\n", catch_exceptions=False,
    )

    assert search.exit_code == 0, search.stderr
    assert f"2. {selected.name}" in search.stderr
    assert not list(tmp_path.glob("*.srt"))
    assert not list(tmp_path.glob("*.ass"))

    # Only external I/O is a fixture; replay must not prefer a different codec.
    monkeypatch.setattr(client, "download_file", lambda url, dest: dest.write_bytes(b"subtitle"))
    download = runner.invoke(cli.app, shlex.split(search.stdout)[1:], catch_exceptions=False)

    assert download.exit_code == 0, download.stderr
    assert download.stdout == ""
    assert not (tmp_path / wrong_codec.name).exists()
    assert (tmp_path / selected.name).read_bytes() == b"subtitle"
    assert client.listed == [(42, 264), (42, 264)]


@pytest.mark.parametrize("flag", ["--download", "-d"])
def test_search_can_download_after_selecting_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    (tmp_path / "Show - 01.mkv").touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})

    def save_subtitle(_url: str, destination: Path) -> None:
        _ = destination.write_bytes(b"fixture subtitle")

    monkeypatch.setattr(client, "download_file", save_subtitle)
    result = CliRunner().invoke(
        app,
        [str(tmp_path), flag, "--no-all", "--no-rename", "--no-overwrite",
         "--no-align", "--no-strip-ih", "--prefer-format", "srt"],
        obj=client, input="1\n1\n", catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stderr
    assert (tmp_path / "[Group] Show - 01.srt").read_bytes() == b"fixture subtitle"
    assert client.listed == [(42, 1), (42, 1)]
    command = shlex.split(result.stdout)
    assert command[:3] == ["jimaku", "download", str(tmp_path.resolve())]
    assert releases(command) == ["Group"]
    assert flag not in command
    assert result.stdout.count("\n") == 1
    assert "[download]" in result.stderr


@pytest.mark.parametrize("flags", [[], ["--no-download"], ["--download", "--no-download"]])
def test_search_download_opt_out_only_emits_the_command(
    tmp_path: Path, flags: list[str]
) -> None:
    video = tmp_path / "Show - 01.mkv"
    video.touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})

    result = CliRunner().invoke(
        app, [str(tmp_path), *flags], obj=client, input="1\n1\n", catch_exceptions=False
    )

    assert result.exit_code == 0, result.stderr
    assert list(tmp_path.iterdir()) == [video]
    assert client.listed == [(42, 1)]
    assert result.stdout.startswith("jimaku download ")
    assert result.stdout.count("\n") == 1
    assert "[download]" not in result.stderr


def test_search_download_receives_all_selected_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for episode in (1, 2):
        (tmp_path / f"Show - {episode:02}.mkv").touch()
    client = SearchClient({
        1: [subtitle("[First] Show - 01.srt"), subtitle("[First] Show - 01.ass")],
        2: [subtitle("[Second] Show - 02.ass")],
    })
    calls: list[dict[str, object]] = []

    def record_download(_ctx: typer.Context, **options: object) -> None:
        calls.append(options)

    monkeypatch.setattr("jimaku_cli.search.download", record_download)
    result = CliRunner().invoke(
        app,
        [str(tmp_path), "--download", "--prefer-format", "ASS", "--all",
         "--rename", "--overwrite", "--align", "--strip-ih"],
        obj=client, input="1\n1\n1\n", catch_exceptions=False,
    )

    assert result.exit_code == 0, result.stderr
    assert calls == [{
        "entry_id": 42, "directory": tmp_path.resolve(),
        "release": ["First", "Second"], "prefer_format": "ass",
        "download_all": True, "rename": True, "overwrite": True,
        "align": True, "strip_ih": True, "quiet": False, "verbose": False,
    }]


def test_search_propagates_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "Show - 01.mkv"
    video.touch()
    client = SearchClient({1: [subtitle("[Group] Show - 01.srt")]})

    def fail_transfer(_url: str, _destination: Path) -> None:
        raise requests.ConnectionError("fixture transfer failed")

    monkeypatch.setattr(client, "download_file", fail_transfer)
    result = CliRunner().invoke(
        app, [str(tmp_path), "--download"], obj=client,
        input="1\n1\n", catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert result.stdout.startswith("jimaku download ")
    assert result.stdout.count("\n") == 1
    assert "[failed]" in result.stderr
    assert "fixture transfer failed" in result.stderr
    assert list(tmp_path.iterdir()) == [video]
