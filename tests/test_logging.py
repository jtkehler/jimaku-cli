"""The logging contract.

Two rules have to stay in step: an outcome worth printing is worth failing over, and
one that is not is neither. These pin both, plus the stream split that will let
`search` put its emitted command on stdout alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from jimaku_cli import cli, postprocess
from jimaku_cli.api import FileEntry, JimakuError
from jimaku_cli.download import app
from jimaku_cli.files import app as files_app
from jimaku_cli.search import app as search_app

runner = CliRunner()

SUBTITLE_BODY = "1\n00:00:01,000 --> 00:00:02,000\n(shinko) ohayou\n"


def video_name(episode: int) -> str:
    return f"[Group] Show - {episode:02d} [1080p].mkv"


def subtitle_name(episode: int) -> str:
    return f"[Group] Show - {episode:02d} [1080p].ja.srt"


def remote(episode: int) -> FileEntry:
    return FileEntry(
        url=f"https://example.invalid/{episode}",
        name=subtitle_name(episode),
        size=len(SUBTITLE_BODY),
        last_modified="2024-01-01T00:00:00Z",
    )


class StubClient:
    """Stands in for JimakuClient over a canned listing.

    `listings` maps an episode number to what `get_files` should do: a list of
    entries, or an exception to raise. `download_errors` maps a destination filename
    to the failure its transfer should raise.
    """

    def __init__(
        self,
        listings: dict[int | None, list[FileEntry] | Exception] | None = None,
        download_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.listings = listings or {}
        self.download_errors = download_errors or {}
        self.downloaded: list[Path] = []

    def get_files(self, entry_id: int, episode: int | None = None) -> list[FileEntry]:
        result = self.listings.get(episode, [])
        if isinstance(result, Exception):
            raise result
        return result

    def download_file(self, url: str, dest: Path) -> None:
        error = self.download_errors.get(dest.name)
        if error is not None:
            raise error
        dest.write_text(SUBTITLE_BODY, encoding="utf-8")
        self.downloaded.append(dest)


@pytest.fixture
def library(tmp_path: Path):
    """Build a directory of empty video files for the given episode numbers."""

    def build(*episodes: int) -> Path:
        for episode in episodes:
            (tmp_path / video_name(episode)).write_bytes(b"")
        return tmp_path

    return build


def run(
    directory: Path,
    client: StubClient,
    *args: str,
    release: str = "re:.",
    strip_ih: bool = False,
    align: bool = False,
):
    """Invoke `download` against a stub, pinning every option the tests rely on.

    `--release re:.` matches anything, so the ambient config file cannot reach in and
    change which candidates a test sees.
    """
    return runner.invoke(
        app,
        [
            "--id",
            "1",
            str(directory),
            "--release",
            release,
            "--no-rename",
            "--no-overwrite",
            "--strip-ih" if strip_ih else "--no-strip-ih",
            "--align" if align else "--no-align",
            *args,
        ],
        obj=client,
        catch_exceptions=False,
    )


def test_a_written_subtitle_is_reported_by_default(library):
    directory = library(1)

    result = run(directory, StubClient({1: [remote(1)]}))

    assert "[download]" in result.stderr
    assert subtitle_name(1) in result.stderr


def test_progress_never_reaches_stdout(library):
    directory = library(1)

    result = run(directory, StubClient({1: [remote(1)]}))

    assert result.stdout == ""


def test_a_skipped_subtitle_prints_nothing_by_default(library):
    directory = library(1)
    (directory / subtitle_name(1)).write_text(SUBTITLE_BODY, encoding="utf-8")

    result = run(directory, StubClient({1: [remote(1)]}))

    assert result.stderr == ""
    assert result.stdout == ""


def test_a_missing_episode_prints_nothing_by_default(library):
    directory = library(1)

    result = run(directory, StubClient({1: []}))

    assert result.stderr == ""
    assert result.stdout == ""


def test_a_transfer_failure_is_reported_by_default(library):
    directory = library(1)
    client = StubClient(
        {1: [remote(1)]},
        download_errors={subtitle_name(1): JimakuError(503, "Service Unavailable")},
    )

    result = run(directory, client)

    assert "[failed]" in result.stderr
    assert "503" in result.stderr


def test_verbose_reports_a_skipped_subtitle(library):
    directory = library(1)
    (directory / subtitle_name(1)).write_text(SUBTITLE_BODY, encoding="utf-8")

    result = run(directory, StubClient({1: [remote(1)]}), "--verbose")

    assert "[skip]" in result.stderr
    assert subtitle_name(1) in result.stderr


def test_verbose_reports_a_missing_episode(library):
    directory = library(1)

    result = run(directory, StubClient({1: []}), "--verbose")

    assert "[missing]" in result.stderr
    assert video_name(1) in result.stderr


def test_a_missing_episode_alone_exits_zero(library):
    """A season the provider has not uploaded yet is not a failure."""
    directory = library(1, 2)

    result = run(directory, StubClient({1: [], 2: []}))

    assert result.exit_code == 0


def test_a_fully_skipped_run_exits_zero(library):
    directory = library(1)
    (directory / subtitle_name(1)).write_text(SUBTITLE_BODY, encoding="utf-8")

    result = run(directory, StubClient({1: [remote(1)]}))

    assert result.exit_code == 0


def test_a_transfer_failure_exits_nonzero(library):
    directory = library(1)
    client = StubClient(
        {1: [remote(1)]},
        download_errors={subtitle_name(1): JimakuError(503, "Service Unavailable")},
    )

    result = run(directory, client)

    assert result.exit_code != 0


def test_an_unreadable_episode_number_exits_nonzero(library, tmp_path):
    (tmp_path / "Show.S01E-broken.1080p.WEB-DL.mkv").write_bytes(b"")

    result = run(tmp_path, StubClient())

    assert result.exit_code != 0


def test_a_strip_failure_is_tagged_failed(library, monkeypatch):
    directory = library(1)
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("bad encoding")))

    result = run(directory, StubClient({1: [remote(1)]}), strip_ih=True)

    assert "[failed]" in result.stderr
    assert "bad encoding" in result.stderr


def test_a_strip_failure_exits_nonzero(library, monkeypatch):
    directory = library(1)
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("bad encoding")))

    result = run(directory, StubClient({1: [remote(1)]}), strip_ih=True)

    assert result.exit_code != 0


def test_a_strip_failure_leaves_the_subtitle_in_place(library, monkeypatch):
    """A postprocessing failure must not discard a subtitle that already landed."""
    directory = library(1)
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("bad encoding")))

    run(directory, StubClient({1: [remote(1)]}), strip_ih=True)

    assert (directory / subtitle_name(1)).read_text(encoding="utf-8") == SUBTITLE_BODY


def test_an_align_failure_is_tagged_failed(library, monkeypatch):
    directory = library(1)
    monkeypatch.setattr(
        postprocess, "sync_subtitle", _raising(RuntimeError("ffmpeg missing"))
    )

    result = run(directory, StubClient({1: [remote(1)]}), align=True)

    assert "[failed]" in result.stderr
    assert "ffmpeg missing" in result.stderr


def test_a_failed_strip_still_gets_aligned(library, monkeypatch):
    directory = library(1)
    aligned: list[Path] = []
    monkeypatch.setattr(postprocess, "strip_ih", _raising(ValueError("bad encoding")))
    monkeypatch.setattr(
        postprocess, "sync_subtitle", lambda subtitle, video: aligned.append(subtitle)
    )

    run(directory, StubClient({1: [remote(1)]}), strip_ih=True, align=True)

    assert aligned == [directory / subtitle_name(1)]


def _raising(error: Exception):
    """A postprocessing step that always fails with `error`."""

    def step(*args, **kwargs):
        raise error

    return step


def test_a_run_that_wrote_something_ends_with_a_summary(library):
    directory = library(1)

    result = run(directory, StubClient({1: [remote(1)]}))

    assert "summary: 1 downloaded, 0 failed" in result.stderr


def test_the_summary_counts_every_outcome_when_verbose(library):
    directory = library(1, 2)
    (directory / subtitle_name(1)).write_text(SUBTITLE_BODY, encoding="utf-8")

    result = run(directory, StubClient({1: [remote(1)], 2: [remote(2)]}), "--verbose")

    assert "summary: 1 downloaded, 1 skipped, 0 missing, 0 failed" in result.stderr


def test_a_missing_api_key_is_reported_on_stderr(monkeypatch):
    monkeypatch.setattr(cli, "api_key", None)

    result = runner.invoke(cli.app, ["download", "--id", "1"])

    assert result.exit_code != 0
    assert "JIMAKU_API_KEY" in result.stderr
    assert result.stdout == ""


def test_an_api_failure_in_files_is_reported_on_stderr():
    result = runner.invoke(files_app, ["1"], obj=_ExplodingClient())

    assert result.exit_code != 0
    assert "upstream exploded" in result.stderr


def test_an_api_failure_in_search_is_reported_on_stderr():
    result = runner.invoke(search_app, ["frieren"], obj=_ExplodingClient())

    assert result.exit_code != 0
    assert "upstream exploded" in result.stderr


class _ExplodingClient:
    """Every call fails, so the command's own error path is what gets exercised."""

    def get_files(self, entry_id: int, episode: int | None = None):
        raise JimakuError(500, "upstream exploded")

    def search_entries(self, query: str):
        raise JimakuError(500, "upstream exploded")


def test_a_directory_that_is_not_one_exits_nonzero_on_stderr(tmp_path):
    """Bad invocation is not a per-file outcome: it ends the run."""
    result = run(tmp_path / "absent", StubClient())

    assert result.exit_code != 0
    assert "is not a directory" in result.stderr
    assert result.stdout == ""


def test_a_directory_with_no_videos_exits_nonzero_on_stderr(tmp_path):
    result = run(tmp_path, StubClient())

    assert result.exit_code != 0
    assert "no video files" in result.stderr
    assert result.stdout == ""


def test_one_bad_file_does_not_abort_the_batch(library):
    """The failure is reported and the next video is still attempted."""
    directory = library(1, 2)
    client = StubClient(
        {1: JimakuError(500, "listing exploded"), 2: [remote(2)]},
    )

    result = run(directory, client)

    assert "listing exploded" in result.stderr
    assert (directory / subtitle_name(2)).exists()
    assert result.exit_code != 0
